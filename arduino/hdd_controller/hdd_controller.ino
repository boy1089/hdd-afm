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

SoftwareSerial esp32Serial(PIN_SS_RX, PIN_SS_TX);

// [로터(플래터) 핀 설정]
const int pinU = 8;
const int pinV = 10;
const int pinW = 12;
const int ENA  = 5;
const int ENB  = 6;

// [액추에이터 암 핀 설정]
const int ARM_IN1 = 11;
const int ARM_IN2 = 13;
const int ARM_EN  = 3;

// [로터 제어 및 가속 변수]
int           maxS           = 110;
unsigned long startStepD     = 50;
unsigned long targetStepD    = 10;
unsigned long currentStepD   = 50;
bool          isConstantSpeed = false;

// [회전 간격 제어 변수]
int rotationsPerMove = 40;
int rotationCounter  = 0;

// [액추에이터 PWM 위치 제어 변수]
int currentArmPWM    = 20;
int minArmPWM        = 20;
int maxArmPWM        = 60;
int pwmStep          = 1;

// [킥스타트 설정]
int kickAmount   = 50;
int kickDuration = 100;

// [런타임 플래그]
bool scanEnabled = false;   // CMD SCAN 으로 활성화
bool stopFlag    = false;   // CMD STOP 으로 설정

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
    else if (key == "rotationsPerMove") rotationsPerMove = (int)val;
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
      scanEnabled = true;
      stopFlag    = false;
      Serial.println("ACK CMD SCAN");
    } else if (cmd == "STOP") {
      stopFlag    = true;
      scanEnabled = false;
      // 즉시 모든 출력 핀 끄기
      analogWrite(ENA, 0);
      analogWrite(ENB, 0);
      digitalWrite(pinU, LOW); digitalWrite(pinV, LOW); digitalWrite(pinW, LOW);
      analogWrite(ARM_EN, 0);
      digitalWrite(ARM_IN1, LOW); digitalWrite(ARM_IN2, LOW);
      Serial.println("ACK CMD STOP");
    } else if (cmd == "RESET") {
      stopFlag    = false;
      scanEnabled = false;
      isConstantSpeed = false;
      currentStepD    = startStepD;
      rotationCounter = 0;
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
    Serial.print(" rotationsPerMove="); Serial.print(rotationsPerMove);
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

void setPhase(bool u, bool v, bool w, int speed) {
  if (stopFlag) return;
  digitalWrite(pinU, u);
  digitalWrite(pinV, v);
  digitalWrite(pinW, w);
  analogWrite(ENA, speed);
  analogWrite(ENB, speed);

  // ── Trigger pulse + r/theta 전송 → ESP32 ──────────────────────────────
  // Trigger: brief HIGH pulse (≈10 µs)
  digitalWrite(PIN_TRIGGER_OUT, HIGH);
  delayMicroseconds(10);
  digitalWrite(PIN_TRIGGER_OUT, LOW);

  // r,theta 전송 (SoftwareSerial, 9600 baud)
  esp32Serial.print(currentArmPWM);
  esp32Serial.print(',');
  esp32Serial.print(rotationCounter);
  esp32Serial.print('\n');

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
  analogWrite(ARM_EN, abs(currentArmPWM) + 25);
  delay(300);
  analogWrite(ARM_EN, abs(currentArmPWM));

  // 2. 로터 정지 및 재정렬
  isConstantSpeed = false;
  currentStepD    = startStepD;
  rotationCounter = 0;
  alignRotor();

  Serial.println("--- Reset Complete. Re-accelerating... ---\n");
}

void updateArmPosition() {
  currentArmPWM += pwmStep;

  if (currentArmPWM <= maxArmPWM) {
    setArmDirection(currentArmPWM);
    int absPWM  = abs(currentArmPWM);
    int kickPWM = min(255, absPWM + kickAmount);

    analogWrite(ARM_EN, kickPWM);
    delay(kickDuration);
    analogWrite(ARM_EN, absPWM);

    Serial.print(">>> [SCAN] Arm PWM: ");
    Serial.println(currentArmPWM);
  } else {
    // 암이 끝에 도달 → 전체 리셋
    performFullReset();
  }
}

// ============================================================
//  Setup / Loop
// ============================================================
void setup() {
  Serial.begin(9600);
  esp32Serial.begin(9600);
  pinMode(ENA,          OUTPUT); pinMode(ENB,     OUTPUT);
  pinMode(pinU,         OUTPUT); pinMode(pinV,    OUTPUT); pinMode(pinW, OUTPUT);
  pinMode(ARM_IN1,      OUTPUT); pinMode(ARM_IN2, OUTPUT); pinMode(ARM_EN, OUTPUT);
  pinMode(PIN_TRIGGER_OUT, OUTPUT);
  digitalWrite(PIN_TRIGGER_OUT, LOW);

  // PWM 주파수 높이기 (핀 5, 6, 3)
  TCCR0B = (TCCR0B & 0b11111000) | 0x01;
  TCCR2B = (TCCR2B & 0b11111000) | 0x01;

  performFullReset();
}

void loop() {
  // Serial 커맨드 폴링
  processSerial();

  // STOP 중이면 아무것도 안 함
  if (stopFlag) return;

  // 1. 로터 회전 구동 (3-phase step)
  setPhase(HIGH, LOW, LOW, maxS);
  if (stopFlag) return;
  setPhase(LOW, HIGH, LOW, maxS);
  if (stopFlag) return;
  setPhase(LOW, LOW, HIGH, maxS);
  if (stopFlag) return;

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

  // 3. scanEnabled && 등속 → 암 스캔
  if (isConstantSpeed && scanEnabled) {
    rotationCounter++;
    if (rotationCounter >= rotationsPerMove) {
      updateArmPosition();
      rotationCounter = 0;
    }
  }
}
