# 팔도 밥상 트렌드 (bapsang-trend)

향토음식·미식 여행 뉴스를 자동 수집하고, AI로 요약·분석해
"지금 어느 지역의 어떤 음식이 뜨는가"를 리포트로 뽑아내는 CLI 프로그램.

> 2026년 AI 활용 학습 Term Project [Project B] 제출물 · 작성자 김진아

## 무엇을 하는가

```
수집(fetch) → 정제(clean) → AI 요약(summarize) → AI 분석(analyze) → 리포트(report) → 내보내기(export)
```

## 설치

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install requests beautifulsoup4 matplotlib openpyxl python-dotenv
Copy-Item .env.example .env   # 그리고 .env 안에 Gemini API 키를 채운다
```

## 사용법

```powershell
python main.py --help
```

(각 명령의 자세한 사용법은 구현이 끝나는 대로 채웁니다.)

## 폴더 구조

| 경로 | 역할 |
|---|---|
| `main.py` | CLI 진입점 — 명령을 받아 알맞은 모듈에 넘긴다 |
| `config.py` | config.json·.env 읽기, 키 마스킹, 로깅 설정 |
| `config.json` | 뉴스 소스 URL, 카테고리 분류 규칙, 중복 정책 |
| `.env` | Gemini API 키 (git에 올라가지 않음) |
| `data/` | SQLite 저장소 (raw / clean) |
| `output/` | 차트 PNG, 리포트, 내보낸 파일 |
| `logs/` | 실행 로그 |

## 이전 과제와의 연결

| 과제 | 이어받은 것 |
|---|---|
| A1-01 레시피 프롬프트 관리자 | 카테고리 분류 개념, CLI 메뉴 설계 |
| A1-02 travel-recommender | API 키 마스킹, 4xx/5xx 구분 재시도, JSON 스키마 강제 호출 |
