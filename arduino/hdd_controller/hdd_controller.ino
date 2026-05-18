// ============================================================
//  HDD Controller — Rotor (Platter) + Actuator Arm
//  Serial Command Protocol (9600 baud, '\n' terminated):
//
//  Physical connections to ESP32 (PLACEHOLDER):
//    PIN 4  (TRIGGER_OUT) → ESP32 GPIO18  [level shifter 5V→3.3V]
//    PIN 2  (SS TX)       → ESP32 GPIO16  [level shifter 5V→3.3V]
//
//  PC -> Arduino:
//    SET maxS <val>             (0-255)
//    SET targetStepD <val>      (ms)
//    SET startStepD <val>       (ms)
//    SET rotationsPerMove <val>
//    SET minArmPWM <val>
//    SET maxArmPWM <val>
//    SET pwmStep <val>
//    SET kickAmount <val>
//    SET kickDuration <val>
//    CMD SCAN                   (allow arm scan once constant speed reached)
//    CMD STOP                   (halt everything)
//    CMD RESET                  (full reset sequence)
//    GET STATUS                 (reply with current params as key:value lines)
//
//  Arduino -> PC:
//    ACK <cmd>                  (echo of received command)
//    STATUS: key=value ...      (response to GET STATUS)
//    existing Serial.println() log lines
// ============================================================

#include <SoftwareSerial.h>

// [ESP32 동기화 핀 설정 (PLACEHOLDER)]
const int PIN_TRIGGER_OUT = 4;   // 상 변화 시 pulse → ESP32 GPIO18
const int PIN_SS_RX       = 7;   // SoftwareSerial RX (ARM_EN=3 충돌 방지)
const int PIN_SS_TX       = 2;   // SoftwareSerial TX → ESP32 GPIO16

SoftwareSerial esp32Serial(PIN_SS_RX, 2);

// [로터(플래터) 핀 설정]
const int pinU = 8;
const int pinV = 10;
const int pinW = 12;
const int ENA  = 5;
const int ENB  = 6;

// [액추에이터 암 핀 설정]
const int ARM_IN1 = 11;
const int ARM_IN2 = 13;
const int ARM_EN  = 9;   // Timer1 OC1A — 10-bit PWM (구: pin 3 / Timer2 / 8-bit)

// [로터 제어 및 가속 변수]
int           maxS           = 110;
unsigned long startStepD     = 50;
unsigned long targetStepD    = 10;
unsigned long currentStepD   = 50;
bool          isConstantSpeed = false;

// [2-revolution 스캔 상태 머신]
// 매 arm 위치마다: sync rev(UART+trigger) 1바퀴 → data rev(trigger only) 1바퀴
int  scanStepCount = 0;     // 현재 rev 내 누적 step 수
bool inDataPhase   = false; // false=sync rev, true=data rev

// [액추에이터 PWM 위치 제어 변수]  (800-level 스케일: 0-799)
// ICR1=799 → f_PWM=20kHz (불가청), 800 레벨 (구 8-bit 256 레벨의 3.1×)
// 구 8-bit 값 × 799/255 ≈ × 3.1  (20→63, 60→188)
int currentArmPWM    = 63;
int minArmPWM        = 63;
int maxArmPWM        = 188;
int pwmStep          = 1;

// [킥스타트 설정]
int kickAmount   = 157;   // 800-level 스케일 (구 50 × 3.1)
int kickDuration = 100;

// [런타임 플래그]
bool scanEnabled = false;   // CMD SCAN 으로 활성화
bool stopFlag    = false;   // CMD STOP 으로 설정

// [상 변화 카운터] — trigger 마다 1씩 증가, STEPS_PER_REV 마다 0으로 리셋
const int STEPS_PER_REV = 18;  // 3상 × 6극 = 18 상변화/rev
unsigned int thetaStep = 0;

// ============================================================
//  암 PWM 헬퍼 (Timer1 OC1A, pin 9, 800-level Fast PWM @ 20 kHz)
// ============================================================
inline void setArmPWM(int value) {
  OCR1A = (unsigned int)constrain(value, 0, 799);
}

// ============================================================
//  유틸리티
// ============================================================
void longDelay(unsigned long ms) {
  for (unsigned long i = 0; i < ms; i++) {
    delayMicroseconds(1000);
    // 블로킹 딜레이 중에도 Serial 커맨드를 처리
    if (Serial.available()) processSerial();
  }
}

// ============================================================
//  Serial 커맨드 처리
// ============================================================
void processSerial() {
  if (!Serial.available()) return;
  String line = Serial.readStringUntil('\n');
  line.trim();
  if (line.length() == 0) return;

  // ---- SET <key> <value> ----
  if (line.startsWith("SET ")) {
    String rest  = line.substring(4);
    int    space = rest.indexOf(' ');
    if (space < 0) return;
    String key = rest.substring(0, space);
    long   val = rest.substring(space + 1).toInt();

    if      (key == "maxS")            maxS            = (int)val;
    else if (key == "targetStepD")     targetStepD     = (unsigned long)val;
    else if (key == "startStepD")      startStepD      = (unsigned long)val;
    else if (key == "minArmPWM")       minArmPWM       = (int)val;
    else if (key == "maxArmPWM")       maxArmPWM       = (int)val;
    else if (key == "pwmStep")         pwmStep         = (int)val;
    else if (key == "kickAmount")      kickAmount      = (int)val;
    else if (key == "kickDuration")    kickDuration    = (int)val;

    Serial.print("ACK SET ");
    Serial.println(key);
    return;
  }

  // ---- CMD <command> ----
  if (line.startsWith("CMD ")) {
    String cmd = line.substring(4);
    cmd.trim();

    if (cmd == "SCAN") {
      scanEnabled   = true;
      stopFlag      = false;
      inDataPhase   = false;
      scanStepCount = 0;
      Serial.println("ACK CMD SCAN");
    } else if (cmd == "STOP") {
      stopFlag    = true;
      scanEnabled = false;
      // 즉시 모든 출력 핀 끄기
      analogWrite(ENA, 0);
      analogWrite(ENB, 0);
      digitalWrite(pinU, LOW); digitalWrite(pinV, LOW); digitalWrite(pinW, LOW);
      setArmPWM(0);
      digitalWrite(ARM_IN1, LOW); digitalWrite(ARM_IN2, LOW);
      Serial.println("ACK CMD STOP");
    } else if (cmd == "RESET") {
      stopFlag    = false;
      scanEnabled = false;
      isConstantSpeed = false;
      currentStepD    = startStepD;
      performFullReset();
      Serial.println("ACK CMD RESET");
    }
    return;
  }

  // ---- GET STATUS ----
  if (line.startsWith("GET STATUS")) {
    Serial.print("STATUS:");
    Serial.print(" maxS=");           Serial.print(maxS);
    Serial.print(" startStepD=");     Serial.print(startStepD);
    Serial.print(" targetStepD=");    Serial.print(targetStepD);
    Serial.print(" currentStepD=");   Serial.print(currentStepD);
    Serial.print(" minArmPWM=");      Serial.print(minArmPWM);
    Serial.print(" maxArmPWM=");      Serial.print(maxArmPWM);
    Serial.print(" pwmStep=");        Serial.print(pwmStep);
    Serial.print(" kickAmount=");     Serial.print(kickAmount);
    Serial.print(" kickDuration=");   Serial.print(kickDuration);
    Serial.print(" isConstantSpeed="); Serial.print(isConstantSpeed ? 1 : 0);
    Serial.print(" scanEnabled=");    Serial.print(scanEnabled ? 1 : 0);
    Serial.print(" currentArmPWM=");  Serial.println(currentArmPWM);
    return;
  }
}

// ============================================================
//  로터 제어
// ============================================================
void alignRotor() {
  Serial.println(">>> [ROTOR ALIGN] Holding U-Phase...");
  digitalWrite(pinU, HIGH);
  digitalWrite(pinV, LOW);
  digitalWrite(pinW, LOW);
  analogWrite(ENA, maxS);
  analogWrite(ENB, maxS);
  delay(1000);
}

// sendUART=true  : sync rev — UART(r,theta) blocking 전송 완료 후 trigger 발사
// sendUART=false : data rev — trigger만 발사, UART 없음
void setPhase(bool u, bool v, bool w, int speed, bool sendUART) {
  if (stopFlag) return;
  digitalWrite(pinU, u);
  digitalWrite(pinV, v);
  digitalWrite(pinW, w);
  analogWrite(ENA, speed);
  analogWrite(ENB, speed);

  thetaStep++;
  if (thetaStep >= STEPS_PER_REV) thetaStep = 0;

  if (sendUART) {
    // blocking 전송 완료 후 trigger 발사 → ESP32 pending_theta가 현재 값으로 확정됨
    esp32Serial.print(currentArmPWM);
    esp32Serial.print(',');
    esp32Serial.print(thetaStep);
    esp32Serial.print('\n');
  }

  // Trigger pulse (≈10 µs)
  digitalWrite(PIN_TRIGGER_OUT, HIGH);
  delayMicroseconds(10);
  digitalWrite(PIN_TRIGGER_OUT, LOW);

  longDelay(currentStepD);
}

// ============================================================
//  암 제어
// ============================================================
void setArmDirection(int power) {
  if (power > 0) {
    digitalWrite(ARM_IN1, HIGH);
    digitalWrite(ARM_IN2, LOW);
  } else if (power < 0) {
    digitalWrite(ARM_IN1, LOW);
    digitalWrite(ARM_IN2, HIGH);
  } else {
    digitalWrite(ARM_IN1, LOW);
    digitalWrite(ARM_IN2, LOW);
  }
}

void performFullReset() {
  Serial.println("\n--- Starting Full Reset Sequence ---");

  // 1. 암 리셋
  Serial.println(">>> [ARM RESET] Returning to start...");
  currentArmPWM = minArmPWM;
  setArmDirection(currentArmPWM);
  setArmPWM(abs(currentArmPWM) + 25);
  delay(300);
  setArmPWM(abs(currentArmPWM));

  // 2. 로터 정지 및 재정렬
  isConstantSpeed = false;
  currentStepD    = startStepD;
  scanStepCount   = 0;
  inDataPhase     = false;
  thetaStep       = 0;
  alignRotor();

  Serial.println("--- Reset Complete. Re-accelerating... ---\n");
}

void updateArmPosition() {
  currentArmPWM += pwmStep;

  if (currentArmPWM <= maxArmPWM) {
    setArmDirection(currentArmPWM);
    int absPWM  = abs(currentArmPWM);
    int kickPWM = min(799, absPWM + kickAmount);

    setArmPWM(kickPWM);
    delay(kickDuration);
    setArmPWM(absPWM);

    // ── 킥 진동으로 인한 로터 슬립 보정 ──────────────────────────
    // U상(HIGH,LOW,LOW)으로 강제 고정 → 로터를 theta=0 기준점에 재정렬
    analogWrite(ENA, maxS);
    analogWrite(ENB, maxS);
    digitalWrite(pinU, HIGH);
    digitalWrite(pinV, LOW);
    digitalWrite(pinW, LOW);
    delay(currentStepD * 3);  // 3 스텝 주기 대기 → 로터 안정화
    thetaStep = 0;            // 물리 위치(U상=0)에 맞춰 카운터 리셋

    Serial.print(">>> [SCAN] Arm PWM: ");
    Serial.println(currentArmPWM);
  } else {
    // 암이 끝에 도달 → 스캔 완료 신호 후 전체 리셋
    Serial.println("SCAN COMPLETE");
    performFullReset();
  }
}

// ============================================================
//  Setup / Loop
// ============================================================
void setup() {
  Serial.begin(9600);
  esp32Serial.begin(57600);
  pinMode(ENA,          OUTPUT); pinMode(ENB,     OUTPUT);
  pinMode(pinU,         OUTPUT); pinMode(pinV,    OUTPUT); pinMode(pinW, OUTPUT);
  pinMode(ARM_IN1,      OUTPUT); pinMode(ARM_IN2, OUTPUT); pinMode(ARM_EN, OUTPUT);
  pinMode(PIN_TRIGGER_OUT, OUTPUT);
  digitalWrite(PIN_TRIGGER_OUT, LOW);

  // Timer0: 핀 5(ENA), 6(ENB) PWM 주파수 높이기 (prescaler=1)
  TCCR0B = (TCCR0B & 0b11111000) | 0x01;

  // Timer1: Fast PWM, TOP=ICR1 on OC1A (pin 9 = ARM_EN)
  //   WGM mode 14: Fast PWM, TOP=ICR1  (WGM13|WGM12|WGM11 = 1)
  //   COM1A1=1, COM1A0=0: OC1A non-inverting
  //   COM1B=00: OC1B disconnected → pin 10 (pinV) 순수 디지털 유지
  //   ICR1=799, CS10=1 → f_PWM = 16 MHz / 800 = 20 kHz (불가청)
  //   해상도: 800 레벨 (구 8-bit 256 레벨의 3.1×)
  ICR1   = 799;
  TCCR1A = (1 << COM1A1) | (1 << WGM11);
  TCCR1B = (1 << WGM13) | (1 << WGM12) | (1 << CS10);
  OCR1A  = 0;

  performFullReset();
}

void loop() {
  // Serial 커맨드 폴링
  processSerial();

  // STOP 중이면 아무것도 안 함
  if (stopFlag) return;

  // 1. 로터 회전 구동 (3상 full-step) — sync rev: UART+trigger / data rev: trigger only
  bool doUART = !inDataPhase;
  setPhase(HIGH, LOW, LOW, maxS, doUART); if (stopFlag) return;
  setPhase(LOW, HIGH, LOW, maxS, doUART); if (stopFlag) return;
  setPhase(LOW, LOW, HIGH, maxS, doUART); if (stopFlag) return;

  // 2. 가속 로직
  if (!isConstantSpeed) {
    if (currentStepD > targetStepD) {
      if      (currentStepD > 80) currentStepD -= 5;
      else if (currentStepD > 40) currentStepD -= 2;
      else                        currentStepD -= 1;
    } else {
      currentStepD    = targetStepD;
      isConstantSpeed = true;
      Serial.println(">>> [SPEED REACHED] Constant speed. Starting Scan.");
    }
  }

  // 3. 2-revolution 스캔 상태 머신
  if (isConstantSpeed && scanEnabled) {
    scanStepCount += 3;
    if (!inDataPhase && scanStepCount >= STEPS_PER_REV) {
      // Sync rev 완료 → ESP32에 DATA 마커 전송 후 data rev 시작
      scanStepCount = 0;
      thetaStep     = 0;
      esp32Serial.print("DATA,");
      esp32Serial.print(currentArmPWM);
      esp32Serial.print('\n');  // blocking 전송
      inDataPhase = true;
      Serial.print(">>> [SYNC→DATA] r="); Serial.println(currentArmPWM);
    } else if (inDataPhase && scanStepCount >= STEPS_PER_REV) {
      // Data rev 완료 → arm 이동, sync rev 재시작
      scanStepCount = 0;
      inDataPhase   = false;
      updateArmPosition();
    }
  }
}
