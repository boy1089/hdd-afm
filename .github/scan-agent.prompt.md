---
mode: agent
description: HDD 스캔 실행 → 이미지 분석 → 코드 개선 자동화
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
  --arduino-port /dev/cu.usbserial-1120 \
  --esp32-port   /dev/cu.usbserial-0001 \
  --output       scans/scan_latest.png
```

- `SAVED: scans/scan_latest.png` 출력이 나올 때까지 대기한다.
- 오류가 발생하면 오류 내용을 그대로 보고하고 중단한다.

### 2. 이미지 분석

`view_image` 도구로 `scans/scan_latest.png`를 읽는다.

다음 항목을 각각 판단한다:

| 항목 | 판단 기준 |
|------|-----------|
| **Spike (이상치)** | 주변 색상과 현저히 다른 밝은 점(노랑/초록)의 비율 |
| **Dark band** | 특정 각도 범위에서 연속으로 어두운(파랑/검정) 섹터 |
| **Coverage** | 반경 방향으로 균등하게 데이터가 채워져 있는지 |
| **전반적 품질** | 색상 그라디언트가 부드러운지, 노이즈 수준 |

### 3. 코드 분석 및 수정

판단 결과에 따라 해당 파일을 읽고 수정한다.

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
