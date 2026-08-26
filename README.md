# 팔도 밥상 트렌드 (bapsang-trend)

향토음식·미식 여행 뉴스를 자동 수집해 AI로 요약·분석하고,
**"지금 어느 지역의 어떤 음식이 뜨는가"** 를 리포트로 뽑아내는 CLI 프로그램.

> 2026년 AI 활용 학습 Term Project [Project B] · 작성자 김진아

```
수집(fetch) → 정제(clean) → AI 요약(summarize) → AI 분석(analyze) → 리포트(report) → 내보내기(export)
```

---

## 1. 빠른 시작

```powershell
# 1) 가상환경
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2) 패키지
python -m pip install requests beautifulsoup4 matplotlib openpyxl python-dotenv

# 3) API 키 (코드에 직접 적지 않는다)
Copy-Item .env.example .env
notepad .env          # GEMINI_API_KEY= 뒤에 발급받은 키를 붙여넣는다

# 4) 전체 흐름 한 번 돌려보기
python main.py fetch --limit 50
python main.py fetch --method crawl --target clean --limit 30
python main.py clean
python main.py summarize --unsummarized --limit 20
python main.py analyze
python main.py report --save --format md
python main.py export --format excel
```

---

## 2. 명령어

### 필수 서브커맨드

| 명령 | 하는 일 | 주요 옵션 |
|---|---|---|
| `fetch` | 뉴스 수집 → raw 저장 | `--method rss/crawl/all` `--target all/clean` `--source` `--limit` |
| `clean` | 정제 → clean 저장 | `--policy skip/upsert` `--rebuild` `--limit` |
| `summarize` | AI 요약 | `--all` `--id N` `--unsummarized` `--limit` |
| `analyze` | AI 인사이트 분석 | `--date-from` `--date-to` `--category` `--show` |
| `report` | 차트 + 리포트 | `--save` `--format md/txt` `--top N` |
| `export` | 파일로 내보내기 | `--format csv/jsonl/excel` `--status` `--category` |

### 보너스 서브커맨드

| 명령 | 하는 일 | 주요 옵션 |
|---|---|---|
| `list` | 기사 목록 조회 | `--category` `--date-from` `--date-to` `--keyword` `--status` `--page` `--size` |
| `show` | 기사 상세 조회 | `show <ID>` |
| `sentiment` | 감성(긍정/중립/부정) 판정 | `--all` `--limit` |

공통 옵션: `--log-level DEBUG|INFO|WARNING|ERROR`

---

## 3. 폴더 구조

| 경로 | 역할 |
|---|---|
| `main.py` | CLI 진입점 — 명령을 받아 알맞은 모듈에 넘긴다 |
| `config.py` | config.json·.env 읽기, API 키 마스킹, 로깅 설정 |
| `collector.py` | RSS 수집 + 크롤링, 타임아웃·재시도, 요청 간 지연 |
| `cleaner.py` | 정제 5규칙 (검증·정규화·날짜통일·결측처리·주제분류) |
| `storage.py` | SQLite raw/clean/analyses 테이블, skip·upsert |
| `ai_client.py` | Gemini 호출 — 요약·인사이트·감성 |
| `reporter.py` | matplotlib 차트, 리포트, CSV/JSONL/Excel |
| `config.json` | 뉴스 소스 URL, 카테고리 규칙, 중복 정책, 모델명 |
| `.env` | GEMINI_API_KEY (git에 올라가지 않음) |
| `data/` | SQLite 저장소 |
| `output/` | 차트 PNG, 리포트, 내보낸 파일 |
| `logs/` | 실행 로그 |

---

## 4. 설계 메모

### raw 와 clean 을 나눈 이유

`raw` 는 받은 그대로, `clean` 은 정제본이다.
정제 규칙(카테고리 키워드)이 잘못된 것을 나중에 알았을 때,
raw 가 남아 있으면 **뉴스 사이트에 다시 요청하지 않고** 정제만 다시 돌리면 된다.

실제로 이 프로젝트에서 '번개장터'(중고거래) 기사가 `장터` 키워드 때문에
미식축제로 분류된 일이 있었다. 규칙을 고치고 `clean --rebuild` 만 실행해
6건을 바로잡았다. 재수집은 한 번도 하지 않았다.

### RSS 와 크롤링의 역할 분담

| | RSS | 크롤링 |
|---|---|---|
| 얻는 것 | 제목·요약(약 200자)·링크 | 본문 전체 (600~4400자) |
| 장점 | 형식이 정해져 깨질 일이 적고 서버 부담이 작다 | 본문을 얻을 수 있다 |
| 단점 | 본문이 없다 | 사이트 디자인이 바뀌면 코드가 깨진다 |

AI 요약의 재료는 본문이어야 한다. RSS 요약문 203자를 다시 요약해봐야
의미가 없다. 그래서 RSS로 목록을 받고, 그 주소를 크롤링해 본문을 채운다.

크롤링의 본문 찾기는 2단계다.
1. `config.json` 의 선택자를 순서대로 시도 (`div.art_body` 등)
2. 전부 빗나가면 `<p>` 글자수가 가장 많은 영역을 본문으로 추정

2단계가 있으면 사이트 디자인이 바뀌어도 프로그램이 멈추지 않는다.
어느 경로로 찾았는지 로그에 남기므로, 1단계가 계속 실패하면 선택자를 고쳐야 한다는 신호가 된다.

### 오류를 종류별로 다르게 다룬다

| 상황 | 판단 | 동작 |
|---|---|---|
| 4xx | 내 잘못 (주소·키가 틀림) | 즉시 포기. 재시도해도 같다 |
| 5xx / 429 | 저쪽 사정 | 2초 → 4초 → 8초 지수 백오프 |
| 타임아웃 | 저쪽 사정과 동일 취급 | 재시도 |
| AI 404 | 그 계정에서 못 쓰는 모델 | 다음 모델로 자동 전환 |

한 소스가 실패해도 다음 소스로 넘어가고, 기사 하나가 실패해도 다음 기사로 넘어간다.

### AI 호출을 세 가지로 나눈 이유

| | 받는 형태 | 왜 |
|---|---|---|
| 요약 | 자유 형식 문장 | 사람이 읽을 글. 틀을 씌우면 문장이 딱딱해진다 |
| 인사이트 | JSON 스키마 | 키워드·트렌드를 프로그램이 꺼내 리포트에 써야 한다 |
| 감성 | JSON + `enum` | 긍정/중립/부정 셋 중 하나여야 집계가 된다 |

---

## 5. 크롤링 정책 준수

- 대상 사이트의 `robots.txt` 를 사전에 확인했다 (경향신문은 `User-agent: *` 에 대해 허용)
- 요청 사이에 `config.json` 의 `request.delay_sec`(기본 1초)만큼 쉰다
- 신원을 밝히는 User-Agent 를 보낸다
- `--limit` 으로 건수를 제한한다
- `--target clean` 으로 **정제를 통과한 기사만** 크롤링해, 쓰지 않을 기사에는 요청을 보내지 않는다

---

## 6. 정기 실행 스케줄링 (보너스)

매일 아침 자동으로 뉴스를 모으고 리포트까지 만들려면 아래처럼 등록한다.

### 6-1. 실행용 스크립트

프로젝트 폴더의 `daily.ps1` 이 전체 파이프라인을 한 번에 돌린다.

```powershell
powershell -ExecutionPolicy Bypass -File .\daily.ps1
```

이 스크립트에서 두 가지를 지켰다.

| 지킨 것 | 이유 |
|---|---|
| 경로를 적지 않고 `$PSScriptRoot` 사용 | 폴더 경로에 한글(`코디세이`)이 들어 있어, 경로를 직접 적으면 인코딩에 따라 깨진다 |
| `python` 대신 `.venv\Scripts\python.exe` | 스케줄러는 가상환경이 켜지지 않은 상태로 실행한다 |
| 파일을 **UTF-8 BOM** 으로 저장 | 윈도우 PowerShell 5.1 은 BOM 없는 `.ps1` 을 cp949 로 읽어 한글 주석·경로를 깨뜨린다 |

### 6-2. 윈도우 — 작업 스케줄러

1. 시작 메뉴에서 **작업 스케줄러** 실행
2. 오른쪽 **작업 만들기** 클릭
3. **일반** 탭 — 이름 `팔도 밥상 트렌드 일일 수집`, "사용자가 로그온했는지 여부에 관계없이 실행" 선택
4. **트리거** 탭 — 새로 만들기 → 매일, 오전 7:00
5. **동작** 탭 — 새로 만들기
   - 프로그램/스크립트: `powershell.exe`
   - 인수 추가: `-ExecutionPolicy Bypass -File "C:\Users\yello\코디세이\bapsang-trend\daily.ps1"`
   - 시작 위치: `C:\Users\yello\코디세이\bapsang-trend`
6. **조건** 탭 — "컴퓨터의 AC 전원이 켜져 있는 경우에만 시작" 해제(노트북이면)
7. 확인 → 목록에서 오른쪽 클릭 → **실행** 으로 한 번 시험해 본다

명령줄로 등록하려면 (관리자 PowerShell):

```powershell
schtasks /create /tn "BapsangTrendDaily" /tr "powershell.exe -ExecutionPolicy Bypass -File \"C:\Users\yello\코디세이\bapsang-trend\daily.ps1\"" /sc daily /st 07:00
```

### 6-3. 리눅스·맥 — cron

```bash
crontab -e
```

```
# 매일 오전 7시에 수집·요약·리포트
0 7 * * * cd /home/user/bapsang-trend && ./.venv/bin/python main.py fetch --limit 50 && ./.venv/bin/python main.py clean && ./.venv/bin/python main.py summarize --unsummarized --limit 20 && ./.venv/bin/python main.py report --save >> logs/cron.log 2>&1
```

cron 다섯 자리는 `분 시 일 월 요일` 이다. `0 7 * * *` 는 매일 7시 0분.

### 6-4. 스케줄 실행 시 주의

| 주의 | 이유 |
|---|---|
| 절대 경로를 쓴다 | 스케줄러는 프로젝트 폴더에서 시작하지 않는다 |
| `.venv` 의 python 을 직접 부른다 | 가상환경이 자동으로 켜지지 않는다 |
| 로그를 파일로 남긴다 | 실패해도 화면이 없어 눈에 띄지 않는다 (`logs/app.log` 에 이미 기록됨) |
| API 사용량을 확인한다 | 매일 20건씩 요약하면 한 달에 600건이다 |

---

## 7. 알려진 한계

- **키워드 기반 분류의 정밀도** — `박람회`, `맛집` 같은 낱말이 본문 어딘가에 있으면
  주제와 무관한 기사도 들어온다. 실측 16건 중 2건이 오분류였다(약 87%).
  `require_any`·`exclude` 조건으로 한 차례 좁혔으나 완전하지 않다.
  다음 단계는 분류 자체를 AI에 맡기는 것이다.
- **단일 소스** — 현재 경향신문 RSS 3종만 사용한다. `config.json` 의 `sources` 에
  항목을 추가하면 소스를 늘릴 수 있다.
- **본문 선택자 의존** — 다른 언론사를 추가하면 `crawl.selectors` 에
  그 사이트의 선택자를 추가해야 1단계로 잡힌다.

---

## 8. 이전 과제와의 연결

| 과제 | 이어받은 것 |
|---|---|
| 「순자 할머니 전라도 레시피북」 | 향토음식이라는 주제 |
| A1-01 레시피 프롬프트 관리자 | 카테고리 분류 개념, CLI 설계 |
| A1-02 travel-recommender | API 키 마스킹, 4xx/5xx 구분 재시도, JSON 스키마 강제 호출 |

그리고 AI 인사이트의 `레시피 소재 제안` 항목이 이 과제의 결과를
다시 레시피 작업으로 되돌려 보낸다.
