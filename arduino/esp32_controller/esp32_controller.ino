// ============================================================
//  ESP32 Controller — Strain Gauge + Z-Axis Voice Coil
//  Serial Command Protocol (115200 baud, '\n' terminated):
//
//  PC -> ESP32:
//    SET Z_TARGET <val>       (0-4095, strain-gauge ADC units)
//    SET KP <val_x100>        (Kp * 100, integer; e.g. 100 = Kp 1.0)
//    SET KI <val_x100>        (Ki * 100, integer)
//    SET STRAIN_GAIN <val>    (ADC raw → position scale factor * 1000)
//    SET STRAIN_OFFSET <val>  (ADC raw zero offset)
//    SET TRIG_MODE <0|1>      (0 = trigger-only log, 1 = continuous + trigger log)
//    CMD STOP                 (halt voice coil, freeze PI)
//    CMD HOME                 (move to z_target=0, hold)
//    GET STATUS               (reply current params)
//
//  ESP32 -> PC:
//    ACK <cmd>
//    TRIG: r=<r> theta=<theta> strain_avg=<avg> n=<count>
//    Z_STATUS: target=<t> actual=<a> output=<pwm>   (optional, TRIG_MODE=1)
//    STATUS: key=value ...
//
//  Hardware connections (PLACEHOLDER — reassign after parts attached):
//    GPIO34  — Strain gauge ADC input (3.5mm jack, ADC1_CH6, input-only)
//    GPIO25  — Voice coil H-bridge IN1
//    GPIO26  — Voice coil H-bridge IN2
//    GPIO27  — Voice coil H-bridge EN (PWM, LEDC ch 0)
//    GPIO18  — Trigger input from Arduino (rising edge interrupt)
//    GPIO16  — UART2 RX from Arduino SoftwareSerial (r,theta data)
//    GPIO17  — UART2 TX (not used for receive-only, kept for symmetry)
//
//  !! LEVEL SHIFTING REQUIRED !!
//    Arduino GPIO (5 V) -> voltage divider or level shifter -> ESP32 GPIO (3.3 V)
// ============================================================

#include <HardwareSerial.h>

// ── Pin definitions (PLACEHOLDER) ───────────────────────────────────────────
static const int PIN_STRAIN      = 34;   // ADC1_CH6, input-only
static const int PIN_COIL_IN1    = 25;
static const int PIN_COIL_IN2    = 26;
static const int PIN_COIL_EN     = 27;   // LEDC PWM
static const int PIN_TRIG_IN     = 18;   // Trigger from Arduino
static const int PIN_UART2_RX    = 16;   // UART2 RX ← Arduino SS TX
static const int PIN_UART2_TX    = 17;   // UART2 TX (unused)

// ── LEDC (PWM) ─────────────────────────────────────────────────────────────────
static const int LEDC_FREQ_HZ    = 20000;
static const int LEDC_RESOLUTION = 8;    // 0-255

// ── UART2 for Arduino r,theta receive ───────────────────────────────────────
HardwareSerial ArduinoSerial(2);   // UART2

// ── Parameters ───────────────────────────────────────────────────────────────
volatile int   z_target       = 0;
float          kp              = 1.0f;
float          ki              = 0.05f;
float          strain_gain     = 1.0f;    // raw * gain = position
int            strain_offset   = 0;
int            trig_mode       = 0;       // 0=trigger log only, 1=continuous+trigger

// ── PI state ─────────────────────────────────────────────────────────────────
float          integral        = 0.0f;
bool           stop_flag       = false;

// ── Trigger / accumulator state ──────────────────────────────────────────────
volatile bool  trig_fired      = false;
volatile int   pending_r       = 0;
volatile int   pending_theta   = 0;

long           strain_sum      = 0;
int            strain_count    = 0;
int            strain_snapshot_avg = 0;
int            strain_snapshot_n   = 0;
int            snapshot_r      = 0;
int            snapshot_theta  = 0;

// ── Sample buffer: 64 evenly-distributed ADC readings per trigger interval ────
// 460800 baud: 64 samples × ~5 bytes ≈ 7 ms TX — fits within a 10 ms trigger interval
// Stride-based even sampling: stride = max(1, prev_count / BUF_SIZE)
// so samples span the full interval rather than clustering at the end.
static const int STRAIN_BUF_SIZE  = 64;
static int    strain_buf[STRAIN_BUF_SIZE];
static int    strain_buf_write = 0;   // linear write index (0..STRAIN_BUF_SIZE-1)
static int    strain_stride    = 1;   // samples to skip between writes (updated after each trigger)
static int    strain_stride_ctr= 0;   // counts up to strain_stride

// ── UART receive buffer for Arduino r,theta ──────────────────────────────────
static char    uart_buf[32];
static int     uart_buf_pos    = 0;

// ============================================================
//  ISR — fired on rising edge of trigger pin
// ============================================================
void IRAM_ATTR onTrigger() {
  // Snapshot accumulated strain data and position
  // (actual processing happens in loop to avoid Serial in ISR)
  trig_fired = true;
}

// ============================================================
//  Serial command processing (USB serial, PC)
// ============================================================
void processSerial() {
  if (!Serial.available()) return;
  String line = Serial.readStringUntil('\n');
  line.trim();
  if (line.length() == 0) return;

  if (line.startsWith("SET ")) {
    String rest  = line.substring(4);
    int    space = rest.indexOf(' ');
    if (space < 0) return;
    String key = rest.substring(0, space);
    String valStr = rest.substring(space + 1);

    if (key == "Z_TARGET") {
      z_target  = valStr.toInt();
      integral  = 0.0f;
    } else if (key == "KP") {
      kp = valStr.toInt() / 100.0f;
    } else if (key == "KI") {
      ki = valStr.toInt() / 100.0f;
    } else if (key == "STRAIN_GAIN") {
      strain_gain   = valStr.toInt() / 1000.0f;
    } else if (key == "STRAIN_OFFSET") {
      strain_offset = valStr.toInt();
    } else if (key == "TRIG_MODE") {
      trig_mode = valStr.toInt();
    }
    Serial.print("ACK SET "); Serial.println(key);
    return;
  }

  if (line.startsWith("CMD ")) {
    String cmd = line.substring(4);
    cmd.trim();
    if (cmd == "STOP") {
      stop_flag = true;
      ledcWrite(PIN_COIL_EN, 0);
      digitalWrite(PIN_COIL_IN1, LOW);
      digitalWrite(PIN_COIL_IN2, LOW);
      Serial.println("ACK CMD STOP");
    } else if (cmd == "HOME") {
      stop_flag = false;
      z_target  = 0;
      integral  = 0.0f;
      Serial.println("ACK CMD HOME");
    }
    return;
  }

  if (line.startsWith("GET STATUS")) {
    int actual_raw = analogRead(PIN_STRAIN) - strain_offset;
    Serial.print("STATUS:");
    Serial.print(" z_target=");   Serial.print(z_target);
    Serial.print(" kp=");         Serial.print((int)(kp * 100));
    Serial.print(" ki=");         Serial.print((int)(ki * 100));
    Serial.print(" strain_gain="); Serial.print((int)(strain_gain * 1000));
    Serial.print(" strain_offset="); Serial.print(strain_offset);
    Serial.print(" trig_mode=");  Serial.print(trig_mode);
    Serial.print(" stop=");       Serial.print(stop_flag ? 1 : 0);
    Serial.print(" actual_raw="); Serial.println(actual_raw);
    return;
  }
}

// ============================================================
//  UART2 receive — parse "<r>,<theta>\n" from Arduino
// ============================================================
void processArduinoUART() {
  while (ArduinoSerial.available()) {
    char c = (char)ArduinoSerial.read();
    if (c == '\n' || c == '\r') {
      uart_buf[uart_buf_pos] = '\0';
      if (uart_buf_pos > 0) {
        // Parse "<r>,<theta>"
        char *comma = strchr(uart_buf, ',');
        if (comma) {
          *comma = '\0';
          pending_r     = atoi(uart_buf);
          pending_theta = atoi(comma + 1);
        }
      }
      uart_buf_pos = 0;
    } else {
      if (uart_buf_pos < (int)sizeof(uart_buf) - 1) {
        uart_buf[uart_buf_pos++] = c;
      }
    }
  }
}

// ============================================================
//  Voice coil drive  (signed -255..255)
// ============================================================
void driveCoil(int power) {
  if (power > 0) {
    digitalWrite(PIN_COIL_IN1, HIGH);
    digitalWrite(PIN_COIL_IN2, LOW);
    ledcWrite(PIN_COIL_EN, min(power, 255));
  } else if (power < 0) {
    digitalWrite(PIN_COIL_IN1, LOW);
    digitalWrite(PIN_COIL_IN2, HIGH);
    ledcWrite(PIN_COIL_EN, min(-power, 255));
  } else {
    digitalWrite(PIN_COIL_IN1, LOW);
    digitalWrite(PIN_COIL_IN2, LOW);
    ledcWrite(PIN_COIL_EN, 0);
  }
}

// ============================================================
//  Setup
// ============================================================
void setup() {
  Serial.setTxBufferSize(512);   // enough for 64-sample TRIG: line at 460800
  Serial.begin(460800);

  // UART2 for Arduino r,theta (read-only)
  ArduinoSerial.begin(57600, SERIAL_8N1, PIN_UART2_RX, PIN_UART2_TX);

  // Voice coil pins
  pinMode(PIN_COIL_IN1, OUTPUT);
  pinMode(PIN_COIL_IN2, OUTPUT);
  ledcAttach(PIN_COIL_EN, LEDC_FREQ_HZ, LEDC_RESOLUTION);

  // Strain gauge ADC (input-only pin, no pinMode needed)
  analogReadResolution(12);   // 0-4095
  analogSetAttenuation(ADC_11db);  // 0-3.3 V range

  // Trigger interrupt
  pinMode(PIN_TRIG_IN, INPUT);
  attachInterrupt(digitalPinToInterrupt(PIN_TRIG_IN), onTrigger, RISING);

  Serial.println("ESP32 Controller ready.");
}

// ============================================================
//  Loop
// ============================================================
void loop() {
  processSerial();
  processArduinoUART();

  // ── Continuous strain sampling: accumulator + stride-based even distribution ──
  int raw = analogRead(PIN_STRAIN) - strain_offset;
  strain_sum   += raw;
  strain_count += 1;
  // Write to buffer only every strain_stride samples so the STRAIN_BUF_SIZE
  // slots are spread evenly across the full trigger interval.
  strain_stride_ctr++;
  if (strain_stride_ctr >= strain_stride && strain_buf_write < STRAIN_BUF_SIZE) {
    strain_stride_ctr = 0;
    strain_buf[strain_buf_write++] = raw;
  }

  // ── Handle trigger event ─────────────────────────────────────────────────
  if (trig_fired) {
    trig_fired = false;

    // Snapshot position and accumulator atomically
    noInterrupts();
    snapshot_r       = pending_r;
    snapshot_theta   = pending_theta;
    interrupts();

    if (strain_count > 0) {
      strain_snapshot_avg = (int)(strain_sum / strain_count);
      strain_snapshot_n   = strain_count;
    } else {
      strain_snapshot_avg = raw;
      strain_snapshot_n   = 1;
    }
    strain_sum   = 0;
    strain_count = 0;

    // Emit merged record with individual samples
    int buf_total = strain_buf_write;
    // Update stride for NEXT interval: fill ~STRAIN_BUF_SIZE slots across the interval
    if (strain_snapshot_n > 0) {
      strain_stride = max(1, strain_snapshot_n / STRAIN_BUF_SIZE);
    }
    strain_buf_write   = 0;
    strain_stride_ctr  = 0;

    Serial.print("TRIG: r=");           Serial.print(snapshot_r);
    Serial.print(" theta=");            Serial.print(snapshot_theta);
    Serial.print(" strain_avg=");       Serial.print(strain_snapshot_avg);
    Serial.print(" n=");                Serial.print(strain_snapshot_n);
    Serial.print(" samples=");
    for (int _i = 0; _i < buf_total; _i++) {
      if (_i > 0) Serial.print(",");
      Serial.print(strain_buf[_i]);
    }
    Serial.println();
  }

  // ── PI control loop (Z-axis voice coil) ─────────────────────────────────
  if (!stop_flag) {
    int actual = analogRead(PIN_STRAIN) - strain_offset;
    float error = (float)(z_target - actual);
    integral   += error * ki;
    // Anti-windup: clamp integral
    integral    = constrain(integral, -255.0f, 255.0f);
    int output  = (int)(kp * error + integral);
    output      = constrain(output, -255, 255);
    driveCoil(output);

    if (trig_mode == 1) {
      Serial.print("Z_STATUS: target="); Serial.print(z_target);
      Serial.print(" actual=");          Serial.print(actual);
      Serial.print(" output=");          Serial.println(output);
    }
  }
}
