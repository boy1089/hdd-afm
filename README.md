# HDD Controller

HDD 플래터 기반 표면 스캐너. Arduino로 로터(platter)와 암(arm)을 구동하고, ESP32로 strain gauge 측정 + Z-axis voice coil PI 제어를 수행한다.

---

## 시스템 아키텍처

```
PC
├── /dev/cu.usbserial-1120  ←→  Arduino Uno
│                                 └─ rotor (3-phase) + arm (DC PWM)
│                                 └─ 상 변화마다 trigger pulse + r,theta 전송
│
└── /dev/cu.usbserial-0001  ←→  ESP32
                                  └─ strain gauge ADC (3.5mm 이어폰 잭)
                                  └─ Z-axis voice coil (이어폰 코일 H-bridge)
                                  └─ PI 제어 루프 (on-board)
                                  └─ trigger 수신 시 strain 평균 + r/θ → PC 전송
```

### 동기화 방식
Arduino `setPhase()` 실행 시마다:
1. `PIN_TRIGGER_OUT(4)` → HIGH 펄스 (10 µs) → ESP32 `GPIO18` (인터럽트)
2. SoftwareSerial TX `PIN2` → `"<r>,<theta>\n"` → ESP32 `GPIO16` (UART2 RX)

ESP32는 인터럽트 발생 시 직전 구간 strain 평균을 계산해 `TRIG:` 메시지를 PC로 전송.
PC GUI는 `TRIG:` 수신 즉시 테이블에 적재 → **소프트웨어 타이밍 불필요**.

---

## 하드웨어 연결

| Arduino 핀 | ESP32 핀 | 신호 | 주의 |
|---|---|---|---|
| GPIO 4 (D4) | GPIO 18 | Trigger pulse (10 µs HIGH) | **레벨 시프팅 필수** (5V → 3.3V) |
| GPIO 2 (D2) | GPIO 16 (UART2 RX) | r,theta 직렬 전송 (9600 baud) | **레벨 시프팅 필수** (5V → 3.3V) |
| GND | GND | 공통 기준 전위 | — |

### Arduino 핀 배치 (기존 + 신규)
| 핀 | 기능 |
|---|---|
| D5 (ENA), D6 (ENB) | 로터 PWM 출력 |
| D8 (U), D10 (V), D12 (W) | 로터 3-phase |
| D3 (ARM_EN), D11 (ARM_IN1), D13 (ARM_IN2) | 암 H-bridge |
| **D4** | **Trigger 출력 → ESP32** (신규) |
| **D2** | **SoftwareSerial TX → ESP32** (신규) |
| D3 | SoftwareSerial RX (미사용, SS 초기화 필요) |

### ESP32 핀 배치 (placeholder — 파트 부착 후 수정)
| 핀 | 기능 |
|---|---|
| GPIO 34 | Strain gauge ADC (3.5mm 잭, input-only) |
| GPIO 25 | Voice coil IN1 |
| GPIO 26 | Voice coil IN2 |
| GPIO 27 | Voice coil EN (LEDC PWM ch.0) |
| **GPIO 18** | **Trigger 입력 ← Arduino** |
| **GPIO 16** | **UART2 RX ← Arduino SoftwareSerial** |
| GPIO 17 | UART2 TX (미사용) |

---

## 파일 구조

```
hdd-controller/
├── arduino/
│   ├── hdd_controller/
│   │   └── hdd_controller.ino   # Arduino: rotor + arm + trigger 출력
│   └── esp32_controller/
│       └── esp32_controller.ino # ESP32: strain gauge + Z PI 제어
├── gui/
│   ├── arduino_comm.py          # Arduino 시리얼 통신 (QThread)
│   ├── esp32_comm.py            # ESP32 시리얼 통신 (QThread, TRIG: 파싱)
│   ├── main_window.py           # 듀얼 MCU GUI (탭: Arduino / ESP32 / Merged Data)
│   ├── main.py                  # 진입점 (--arduino-port, --esp32-port)
│   └── requirements.txt
├── scripts/
│   ├── build_upload.py          # 빌드/업로드 (--device arduino|esp32)
│   └── setup.sh
└── run.sh
```

---

## 시리얼 프로토콜

### Arduino (9600 baud)
```
PC → Arduino:
  SET maxS <0-255>          로터 최대 속도
  SET targetStepD <ms>      목표 스텝 딜레이
  SET startStepD <ms>       시작 스텝 딜레이
  SET rotationsPerMove <n>  암 이동 간격 (회전수)
  SET minArmPWM / maxArmPWM / pwmStep
  SET kickAmount / kickDuration
  CMD SCAN                  스캔 시작
  CMD STOP                  전체 정지
  CMD RESET                 초기화 시퀀스
  GET STATUS                파라미터 조회

Arduino → PC:
  ACK <cmd>
  STATUS: key=value ...
  >>> [SCAN] Arm PWM: <val>
  >>> [SPEED REACHED] ...
```

### ESP32 (115200 baud)
```
PC → ESP32:
  SET Z_TARGET <0-4095>     목표 Z 위치 (ADC 단위)
  SET KP <val×100>          PI Kp (예: 150 = 1.50)
  SET KI <val×100>          PI Ki
  SET STRAIN_GAIN <val×1000> ADC → 위치 스케일
  SET STRAIN_OFFSET <val>   ADC 영점 오프셋
  SET TRIG_MODE <0|1>       0=trigger log only, 1=continuous+trigger
  CMD STOP                  voice coil 정지
  CMD HOME                  Z_TARGET=0으로 이동
  GET STATUS                파라미터 조회

ESP32 → PC:
  ACK <cmd>
  TRIG: r=<armPWM> theta=<rotStep> strain_avg=<avg> n=<count>
  Z_STATUS: target=<t> actual=<a> output=<pwm>    (TRIG_MODE=1)
  STATUS: key=value ...
```

### Merged Data (CSV)
| 컬럼 | 설명 |
|---|---|
| Timestamp | HH:MM:SS.mmm |
| r (arm PWM) | 현재 암 PWM 위치 (radial) |
| θ (rot step) | 현재 회전 카운터 (angular) |
| Strain avg | 직전 trigger 구간 strain ADC 평균 |
| N samples | 평균에 사용된 샘플 수 |
| Z target | 당시 설정된 Z 목표값 |

---

## 실행 방법

### 빠른 실행
```bash
bash run.sh [--skip-setup] [--skip-upload]
```

### 수동 실행
```bash
# Arduino 펌웨어 업로드
python3 scripts/build_upload.py --device arduino --port /dev/cu.usbserial-1120

# ESP32 펌웨어 업로드
python3 scripts/build_upload.py --device esp32 --port /dev/cu.usbserial-0001

# GUI 실행
cd gui
python3 main.py \
  --arduino-port /dev/cu.usbserial-1120 \
  --esp32-port   /dev/cu.usbserial-0001
```

### 의존성 설치
```bash
pip install -r gui/requirements.txt
```

---

## GUI 사용법

1. **Arduino 연결**: 상단 좌측 바 → 포트 선택 → Connect
2. **ESP32 연결**: 상단 우측 바 → 포트 선택 → Connect (baud 115200)
3. **ESP32 PI 설정**: "ESP32" 탭 → PI Parameters → Kp/Ki/Gain/Offset 입력 → Apply PI Params
4. **Z 위치 설정**: Z Target 입력 → Set Z Target
5. **스캔 시작**: "Arduino" 탭 → SCAN 버튼 (RESET→가속→등속 도달 후 암 이동 시작)
6. **데이터 확인**: "Merged Data" 탭에서 실시간 r/θ/strain 테이블 확인
7. **CSV 내보내기**: Merged Data 탭 → Export CSV…

---

## 개발 메모

- Arduino `SoftwareSerial` (9600 baud)은 고속 스텝 딜레이와 간섭 가능 → `targetStepD` ≥ 15ms 권장
- ESP32 ADC는 비선형 특성이 있음 → `STRAIN_GAIN` / `STRAIN_OFFSET` calibration 필요
- Voice coil 과전류 주의: `KP` 값 과도 시 코일 소손 위험 → 낮은 값에서 시작
- GPIO 레벨 시프팅 미적용 시 ESP32 손상 가능 (5V 내성 없음)
