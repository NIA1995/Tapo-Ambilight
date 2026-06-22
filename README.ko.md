[English](README.md) | **한국어**

# Tapo Ambilight

![demo](docs/images/demo.gif)

PC 화면 색을 **TP-Link Tapo L930** RGBIC 스트립에 **존별로**(화면 가장자리를 따라 흐르는 색 그라디언트), **깜빡임 없이** 동기화합니다.

L930에는 공식 "화면 동기화"/엔터테인먼트 모드가 없습니다. 이 프로젝트는 로컬 API의 `set_segment_effect`를 통해 스트립을 제어하는데, 이 경로만이 **효과를 재시작하지 않고 세그먼트 색을 제자리에서 갱신**합니다(리버스 엔지니어링 기록은 [`docs/PROTOCOL.md`](docs/PROTOCOL.md) 참고).

> **현실적인 한계:** 이 전구의 로컬 프로토콜은 **초당 4~5회 갱신** 정도가 천장입니다(암호화 왕복당 약 200ms, 스트리밍 API 없음). 차분하고 천천히 바뀌는 앰비언트 조명에는 훌륭하지만, **빠른 게임이나 액션 영상에는 충분히 매끄럽지 않습니다.** 이건 버그가 아니라 하드웨어 한계입니다.

## 기능

- 존별 둘레 동기화 — 스트립의 물리적 루프를 화면 가장자리에 매핑
- 무점멸 (고정 효과 id로 `set_segment_effect` 사용)
- 데스크톱 GUI: 연결 설정, 조명 슬라이더, 실시간 fps/지연 표시
- 가이드형 **보정 마법사** — 화면 네 모서리 + 스트립 끝점 태그
- **시스템 트레이** 백그라운드 모드 + 선택적 **부팅 시 자동 실행**
- GUI 없이 돌리는 헤드리스 **CLI**
- 색 추출 시 유채색 픽셀만 반영(어둡거나 무채색 화면은 노이즈 대신 은은한 흰색 유지)

## 요구 사항

- Windows (트레이·자동실행은 Windows API 사용; 엔진/CLI는 크로스 플랫폼)
- Python 3.10 이상
- Tapo L930, 그리고 Tapo 앱에서 **나 → 제3자 서비스 → 타사 서비스 호환성** 활성화
- 스트립의 로컬 IP (공유기에서 고정 할당 권장 — IP가 바뀌지 않도록)

## 설치

```bash
pip install -r requirements.txt
```

`pystray`와 `pillow`는 트레이 아이콘에만 필요합니다. 없어도 앱은 동작하며, 이 경우 "트레이로 숨기기"는 일반 최소화로 대체됩니다.

## 설정

```bash
cp config.example.json config.json   # 이후 계정 정보 입력 (config.json은 gitignore됨)
```

`username`, `password`, `ip`를 채우세요. 나머지는 일단 기본값으로 둡니다.

## 실행

**GUI** (권장):

```bash
pythonw app.py        # Windows, 콘솔 창 없음  (또는 run.bat 더블클릭)
python  app.py        # 콘솔 로그와 함께 실행
```

1. **Connection** 탭 → 계정 정보 입력 → **Connect / Test**
2. **Calibration** 탭 → **Run Calibration Wizard**
   - 흰색 LED 하나가 켜집니다. **Next / Prev**로 루프를 따라 한 칸씩 옮깁니다.
   - LED가 화면 각 모서리에 올 때 **Top-Left / Top-Right / Bottom-Left /
     Bottom-Right**를 태그합니다. 스트립이 물리적으로 끝나는 지점에서 **Mark END**.
   - **Save & Close**.
3. **▶ Start**.

**CLI** (헤드리스):

```bash
python cli.py calibrate   # 터미널에서 대화형 보정
python cli.py             # 동기화 실행, 실효 fps + 전송 지연 출력
```

## 설정값

모든 값은 `config.json`에 있고 GUI에서 수정할 수 있습니다.

| 키 | 의미 | 추천 |
|---|---|---|
| `brightness` | 전체 밝기 | 70–90 |
| `saturation_boost` | 색을 더 진하게 | 1.5–2.0 |
| `num_bands` | 스트립에 보낼 존 수 | 16–25 |
| `target_fps` | 갱신 목표(실제 천장은 ~4–5) | 12–20 |
| `smoothing` | 전환 부드러움(높을수록 부드럽지만 느림) | 0.55–0.7 |
| `min_change` | 이 값 이상 바뀔 때만 전송 | 1–4 |
| `min_value` | 최소 밝기 바닥값 | 10 |
| `band_frac` | 가장자리에서 색을 따올 깊이 | 0.15 |
| `display_index` | 0 = 전체 모니터, 1/2 = 개별 | 0 |
| `corners`, `last_segment` | 보정 결과 — **직접 편집 금지** | 마법사로 생성 |

`corners` / `last_segment`는 보정에서 나옵니다. 스트립을 물리적으로 다시 감았을 때만 마법사를 재실행하세요.

## 프로젝트 구성

```
app.py                 GUI 데스크톱 앱 (트레이, 설정, 보정 마법사)
engine.py              동기화 엔진: 캡처 -> 추출 -> 매핑 -> set_segment_effect
cli.py                 헤드리스 명령줄 버전 (calibrate / run)
extras/solid_sync.py   단색 전체 동기화 (더 빠르고 부드러움, 존별 아님)
config.example.json    설정 템플릿 (config.json으로 복사)
docs/PROTOCOL.md       무점멸 존별 갱신을 찾아낸 과정 + fps 한계
run.bat                Windows용 콘솔 없는 런처
```

## 왜 느린가요 / 더 부드럽게 안 되나요?

안 됩니다. 갱신 한 번이 개별 암호화 KLAP 왕복(~200ms)이고, L930은 저지연 스트리밍 모드를 제공하지 않습니다. `num_bands`를 줄여도 소용없습니다 — 비용은 페이로드가 아니라 프로토콜 오버헤드입니다. 진짜 매끄러운 동기화가 필요하면 스트리밍 엔터테인먼트 프로토콜을 가진 하드웨어(예: Philips Hue Entertainment, Govee DreamView)가 필요합니다. 앰비언트 용도라면 `smoothing`을 올려서 즐기세요. [`docs/PROTOCOL.md`](docs/PROTOCOL.md) 참고.

## 라이선스

MIT — [LICENSE](LICENSE) 참고.

## 감사의 말

[`tapo`](https://github.com/mihai-dinculescu/tapo) 파이썬 라이브러리(MIT)와 화면
캡처용 [`mss`](https://github.com/BoboTiG/python-mss)(MIT) 위에서 만들어졌습니다.
두 라이브러리는 pip로 설치되며 이 저장소에 재배포되지 않습니다.

> TP-Link와 제휴하거나 승인받은 프로젝트가 아닙니다. "Tapo"는 해당 소유자의
> 상표입니다. 사용에 따른 책임은 사용자 본인에게 있습니다.
