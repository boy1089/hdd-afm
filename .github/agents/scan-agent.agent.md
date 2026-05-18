---
description: "HDD 스캔 실행 → 이미지 분석 → 코드 개선. Use when: scan, 스캔, analyze scan image, 스캔하고 분석, scan and analyze"
tools:
  - run_in_terminal
  - view_image
  - read_file
  - insert_edit_into_file
  - replace_string_in_file
---

사용자가 "스캔하고 분석해줘", "scan and analyze", "scan 실행해줘" 등을 입력하면
아래 절차를 **빠짐없이 순서대로** 실행한다.

## 절차

### 1. 스캔 실행

터미널에서 다음 명령을 실행한다:

```
python3 scripts/headless_scan.py \
  --arduino-port /dev/cu.usbserial-120 \
  --esp32-port   /dev/cu.usbserial-0001 \
  --output       scans/scan_latest.png \
  [--set KEY=VALUE ...]
```

기본 파라미터는 Arduino 펌웨어 기본값(maxArmPWM=188, pwmStep=1 등)을 사용한다.
코드 분석 후 파라미터 변경이 필요하다고 판단되면 `--set` 옵션으로 전달한다.
예: `--set maxArmPWM=300 --set pwmStep=5 --set targetStepD=15`

- `SAVED: scans/scan_latest.png` 출력이 나올 때까지 대기한다.
- 오류가 발생하면 오류 내용을 그대로 보고하고 중단한다.

### 2. 이미지 분석

`view_image` 도구로 `scans/scan_latest.png`를 읽는다.

#### 정상 이미지 기준 (이 두 조건을 반드시 먼저 확인한다)

**[기준 A] θ 방향 연속성**
- 각 반경(ring)의 점들이 θ = 0°~360° 에 걸쳐 **부드럽게 이어진** 띠처럼 보여야 한다.
- 비정상: 18개 점이 점점이 분리되어 보이는 경우 (마치 이산적인 18개 섹터가 구분되어 보임).
  → 이는 θ 보간 또는 scatter plot 크기(s) 설정 문제다.

**[기준 B] 방사형 선(spoke)은 정확히 2개**
- 전체 이미지에서 반경 방향으로 뻗은 선(spoke, 직선 패턴)은 **2개만** 있어야 한다.
- 비정상: 2개 이외의 추가 방사선, 혹은 특정 θ 위치에서 색상이 갑자기 튀는 세로 줄무늬가 보이는 경우.
  → 이는 특정 θ 인덱스에서 값이 비정상적으로 높거나 낮은 것(spike/drop)이다.

다음 항목도 함께 판단한다:

| 항목 | 판단 기준 |
|------|-----------|
| **[기준 A] θ 연속성** | 각 ring이 점점이가 아닌 연속된 띠로 보이는가 |
| **[기준 B] Spoke 개수** | 방사형 직선이 정확히 2개인가 (초과 시 spike/drop) |
| **Dark band** | 특정 각도 범위에서 연속으로 어두운(파랑/검정) 섹터 |
| **Coverage** | 반경 방향으로 균등하게 데이터가 채워져 있는가 |

### 3. 코드 분석 및 수정

판단 결과에 따라 해당 파일을 읽고 수정한다.

#### [기준 A 위반] θ 방향이 점점이로 보이는 경우

`gui/main_window.py`의 `_flush_polar_plot` 또는 `scripts/headless_scan.py`의 `save_polar_plot`을 읽는다.
- scatter plot의 점 크기 `s` 파라미터를 키워 점들이 이어 보이도록 수정
- 또는 `theta` 값에 보간(interpolation)을 추가해 18개 이산 포인트 사이를 채움
- 각 REV 당 18포인트이므로 인접 포인트 간 θ 간격은 360°/18 = 20°임을 기준으로 점 크기를 계산

#### [기준 B 위반] 방사형 선이 2개 초과인 경우 (spike/drop)

`gui/esp32_comm.py` 또는 `arduino/esp32_controller/esp32_controller.ino`를 읽는다.
- 특정 θ 인덱스(0~17)에서 값이 튀는 것이므로, 해당 인덱스의 raw 값을 확인
- `gui/main_window.py`의 `_on_rev_received`에서 IQR 또는 중앙값 기반 이상치 필터 추가
  - 같은 ring(r) 내 18개 값 중 3σ 이상 벗어난 값을 중앙값으로 대체

#### Spike가 많은 경우 (이상치 비율 > 5%)

`gui/main_window.py`의 `_on_rev_received` 또는 `_flush_polar_plot`을 읽는다.
- `vmin/vmax` percentile 클리핑이 2/98인지 확인
- 필요 시 더 공격적인 클리핑(5/95) 또는 IQR 기반 필터를 `_on_rev_received`에 추가

#### Dark band (특정 각도 저신호) 가 있는 경우

`arduino/esp32_controller/esp32_controller.ino`를 읽는다.
- 해당 각도가 `data_theta_idx` 특정 인덱스에 해당하는지 확인
- `data_strain_sum`/`data_strain_cnt` 누산 로직 점검

#### 샘플 수가 불균형한 경우

`arduino/esp32_controller/esp32_controller.ino`의 stride 계산 로직을 확인한다.
- `strain_stride = max(1, cnt_val / STRAIN_BUF_SIZE)` 검토

### 4. 결과 보고

수정이 끝나면 다음 형식으로 보고한다:

```
## 스캔 분석 결과

**수집 데이터**: <링 수>링 × 18 포인트

**발견된 문제**:
- [문제 유형]: <구체적 설명>
- ...

**적용한 수정**:
- [파일명 L줄번호]: <변경 내용>
- ...

**다음 스캔에서 확인할 포인트**:
- ...
```

---

## 주의사항

- 하드웨어가 연결되지 않은 경우 `headless_scan.py`가 포트 오류를 반환한다.
  이때는 오류 메시지를 그대로 보고하고 이미지 분석 단계는 건너뛴다.
- 코드 수정 시 기존 동작을 깨지 않는 최소한의 변경만 적용한다.
- Arduino 펌웨어를 수정한 경우 재업로드가 필요하다는 사실을 사용자에게 알린다.
