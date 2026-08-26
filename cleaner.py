"""raw 데이터를 정제해 clean 저장소에 넣는 모듈. (요건 3)

정제 규칙 네 가지
  1) 필수 필드 검증   — url·제목·날짜가 없으면 버린다
  2) 텍스트 정규화     — HTML 태그, &nbsp; 같은 기호, 중복 공백을 없앤다
  3) 날짜 형식 통일   — 어떤 형식으로 왔든 YYYY-MM-DD 로 맞춘다
  4) 결측값 처리       — 본문이 없으면 요약문으로 대신한다

여기에 이 프로젝트만의 규칙이 하나 더 붙는다.
  5) 주제 분류 — config.json 의 키워드로 5개 카테고리 중 하나를 정한다.
                 어디에도 안 걸리면 '음식·여행 기사가 아니다'로 보고 제외한다.

왜 raw 를 고치지 않고 새로 저장하는가
  정제 규칙은 계속 바뀐다. 키워드를 하나 더 넣고 싶어질 때,
  raw 가 원본 그대로 남아 있으면 뉴스 사이트에 다시 요청하지 않고
  clean 만 다시 만들면 된다.
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from email.utils import parsedate_to_datetime

TAG_RE = re.compile(r"<[^>]+>")


# --- 2) 텍스트 정규화 ------------------------------------------------

def normalize_text(value: str | None) -> str:
    """HTML 태그와 기호를 걷어내고 공백을 정리한다."""
    if not value:
        return ""
    text = TAG_RE.sub(" ", value)      # <b>제목</b> → 제목
    text = html.unescape(text)          # &amp; → &,  &nbsp; → 공백
    text = text.replace("\xa0", " ")
    return " ".join(text.split())       # 줄바꿈·연속 공백을 한 칸으로


# --- 3) 날짜 형식 통일 ------------------------------------------------

def normalize_date(raw: str | None) -> tuple[str | None, str | None]:
    """어떤 형식으로 오든 (YYYY-MM-DD, 전체시각ISO) 로 바꾼다.

    실제로 마주친 두 가지
        2026-08-26T16:50:00+09:00        ← dc:date  (ISO 8601)
        Tue, 26 Aug 2026 16:07:00 +0900  ← pubDate  (RFC 2822)
    """
    if not raw:
        return None, None
    raw = raw.strip()

    # ISO 8601 먼저
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d"), dt.isoformat()
    except ValueError:
        pass

    # RFC 2822 (메일 헤더에서 쓰는 형식. RSS 의 pubDate 가 이걸 쓴다)
    try:
        dt = parsedate_to_datetime(raw)
        return dt.strftime("%Y-%m-%d"), dt.isoformat()
    except (TypeError, ValueError):
        pass

    # 마지막 수단 — 문자열 안에서 날짜처럼 보이는 부분을 찾는다
    m = re.search(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", raw)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        try:
            dt = datetime(y, mo, d)
            return dt.strftime("%Y-%m-%d"), dt.isoformat()
        except ValueError:
            pass

    return None, None


# --- 5) 주제 분류 ------------------------------------------------------

def classify(text: str, categories: list[dict]) -> tuple[str | None, list[str]]:
    """키워드로 카테고리를 정한다. (카테고리명, 걸린 키워드들)

    조건이 세 가지다.
      keywords     — 이 중 하나라도 있으면 후보  (필수)
      require_any  — 후보가 되려면 이 중 하나도 함께 있어야 함  (선택)
      exclude      — 이 중 하나라도 있으면 탈락  (선택)

    require_any 를 넣은 이유
      '축제' 하나만 보면 영화제·음악축제까지 미식축제로 들어온다.
      그렇다고 '음식축제' 같은 긴 말만 찾으면 대부분을 놓친다.
      그래서 '축제' 와 '음식·먹거리·맛' 중 하나가 **함께** 있을 때만 인정한다.

    exclude 를 넣은 이유
      실제로 '번개장터'(중고거래) 기사가 '장터' 때문에 미식축제로 분류됐다.
      규칙을 좁히는 것만으로는 부족해서, 확실히 아닌 말을 따로 적어둔다.
    """
    lowered = text.lower()
    for cat in categories:
        if any(bad.lower() in lowered for bad in cat.get("exclude", [])):
            continue
        hits = [kw for kw in cat.get("keywords", []) if kw.lower() in lowered]
        if not hits:
            continue
        need = cat.get("require_any")
        if need:
            extra = [kw for kw in need if kw.lower() in lowered]
            if not extra:
                continue
            hits = hits + extra[:2]
        return cat["name"], hits
    return None, []


# --- 1)+4) 검증과 결측값 처리 -----------------------------------------

def clean_row(row, categories: list[dict]) -> tuple[dict | None, str]:
    """raw 한 건을 정제한다. 통과하면 (정제결과, "ok"), 아니면 (None, 사유)."""
    url = (row["url"] or "").strip()
    if not url:
        return None, "url 없음"

    title = normalize_text(row["title"])
    if not title:
        return None, "제목 없음"

    published_date, published_at = normalize_date(row["published_raw"])
    if not published_date:
        return None, "날짜 해석 실패"

    description = normalize_text(row["description"])
    content = normalize_text(row["content"])

    # 결측값 처리 — 본문이 없으면 요약문으로 대신한다
    body = content or description
    body_source = "content" if content else ("description" if description else "none")
    if not body:
        return None, "본문·요약 모두 없음"

    category, hits = classify(f"{title} {description} {content}", categories)
    if not category:
        return None, "주제 밖(음식·여행 아님)"

    return {
        "raw_id": row["id"],
        "url": url,
        "title": title,
        "description": description,
        "content": body,
        "body_source": body_source,
        "category": category,
        "matched_keywords": ", ".join(hits[:5]),
        "published_date": published_date,
        "published_at": published_at,
        "publisher": row["publisher"],
        "source_name": row["source_name"],
        "method": row["method"],
    }, "ok"
