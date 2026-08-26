"""영구 저장소(SQLite)를 담당하는 모듈.

왜 SQLite인가
  - 파일 하나가 곧 데이터베이스다. 서버를 설치할 필요가 없다.
  - 파이썬에 기본 내장(sqlite3)이라 따로 설치할 것이 없다.
  - 프로그램을 껐다 켜도 데이터가 남는다. (요건 10)

왜 raw 와 clean 을 나누는가
  raw   = 받은 그대로. 손대지 않은 원본.
  clean = 검증·정규화를 거친 정제본.

  정제 규칙이 잘못된 걸 나중에 알았을 때, raw 가 남아 있으면
  뉴스 사이트에 다시 요청하지 않고 정제만 다시 돌리면 된다.
  raw 를 안 남기면 기사가 내려간 뒤에는 영영 되돌릴 수 없다.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import config

# raw 저장소 스키마 --------------------------------------------------------
# collected_at(수집 시각) / source_name(소스 정보) / method(수집 방법) 를
# 반드시 함께 남긴다. (요건 2)
SCHEMA_RAW = """
CREATE TABLE IF NOT EXISTS raw_news (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name   TEXT NOT NULL,          -- 어느 소스에서 왔는가
    method        TEXT NOT NULL,          -- rss / crawl
    publisher     TEXT,                   -- 언론사
    url           TEXT NOT NULL UNIQUE,   -- 기사 주소 (중복 판별의 기준)
    guid          TEXT,
    title         TEXT,
    description   TEXT,
    content       TEXT,                   -- 크롤링으로 채우는 본문
    published_raw TEXT,                   -- 원본 날짜 문자열 (형식 통일 전)
    raw_payload   TEXT,                   -- 원본 항목 전체를 JSON 문자열로
    collected_at  TEXT NOT NULL           -- 수집 시각
);
"""

INDEX_RAW = """
CREATE INDEX IF NOT EXISTS idx_raw_source ON raw_news(source_name);
"""


def connect(cfg: dict) -> sqlite3.Connection:
    """DB 파일에 연결한다. 파일이 없으면 새로 만든다."""
    db_path: Path = config.resolve_path(cfg, "db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row   # 결과를 딕셔너리처럼 꺼내 쓸 수 있게
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """필요한 테이블을 만든다. 이미 있으면 아무 일도 하지 않는다."""
    conn.executescript(SCHEMA_RAW)
    conn.executescript(INDEX_RAW)
    conn.commit()


def now_iso() -> str:
    """수집 시각을 문자열로. (초 단위까지)"""
    return datetime.now().isoformat(timespec="seconds")


def insert_raw(conn: sqlite3.Connection, item: dict) -> bool:
    """raw 저장소에 한 건 넣는다.

    같은 url 이 이미 있으면 넣지 않고 False 를 돌려준다.
    (UNIQUE 제약 덕분에 파이썬에서 일일이 비교하지 않아도 된다)
    """
    sql = """
        INSERT OR IGNORE INTO raw_news
            (source_name, method, publisher, url, guid, title,
             description, content, published_raw, raw_payload, collected_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    cur = conn.execute(
        sql,
        (
            item.get("source_name", ""),
            item.get("method", ""),
            item.get("publisher"),
            item.get("url", ""),
            item.get("guid"),
            item.get("title"),
            item.get("description"),
            item.get("content"),
            item.get("published_raw"),
            json.dumps(item.get("raw_payload", {}), ensure_ascii=False),
            item.get("collected_at") or now_iso(),
        ),
    )
    return cur.rowcount > 0    # 0 이면 중복이라 무시된 것


def count_raw(conn: sqlite3.Connection) -> int:
    """raw 저장소에 몇 건 있는지."""
    return conn.execute("SELECT COUNT(*) FROM raw_news").fetchone()[0]


def count_raw_by_source(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """소스별 건수. 수집이 골고루 됐는지 확인용."""
    return conn.execute(
        "SELECT source_name, method, COUNT(*) AS n "
        "FROM raw_news GROUP BY source_name, method ORDER BY n DESC"
    ).fetchall()


def select_raw(conn: sqlite3.Connection, limit: int | None = None) -> list[sqlite3.Row]:
    """raw 데이터를 꺼낸다. 정제 단계에서 쓴다."""
    sql = "SELECT * FROM raw_news ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


# --- 크롤링으로 본문을 채우기 위해 나중에 추가된 컬럼 ------------------
# 이미 만들어진 테이블에 컬럼을 덧붙이는 것을 '마이그레이션'이라고 한다.
# 기존 데이터를 지우지 않고 구조만 넓힌다.
CONTENT_COLUMNS = {
    "content_source": "TEXT",        # 본문을 무엇으로 얻었는가 (crawl)
    "content_collected_at": "TEXT",  # 본문을 언제 얻었는가
}


def migrate(conn: sqlite3.Connection) -> None:
    """없는 컬럼만 골라서 추가한다. 이미 있으면 건너뛴다."""
    existing = {r[1] for r in conn.execute("PRAGMA table_info(raw_news)")}
    for name, coltype in CONTENT_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE raw_news ADD COLUMN {name} {coltype}")
    conn.commit()


def select_uncrawled(conn: sqlite3.Connection, limit: int | None = None,
                     only_clean: bool = False) -> list[sqlite3.Row]:
    """아직 본문이 없는 기사만 고른다. 같은 기사를 두 번 긁지 않기 위해.

    only_clean=True 이면 정제를 통과한 기사(= 음식·여행 기사)만 긁는다.
    주제와 상관없는 정치·증시 기사까지 방문할 이유가 없다.
    남의 서버에 보내는 요청을 줄이는 것이 곧 크롤링 예절이다.
    """
    where = "(content IS NULL OR TRIM(content) = '')"
    if only_clean:
        where += " AND id IN (SELECT raw_id FROM clean_news)"
    sql = f"SELECT id, url, title FROM raw_news WHERE {where} ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


def update_content(conn: sqlite3.Connection, row_id: int, content: str) -> None:
    """크롤링으로 얻은 본문을 해당 기사에 채운다."""
    conn.execute(
        "UPDATE raw_news SET content = ?, content_source = 'crawl', "
        "content_collected_at = ? WHERE id = ?",
        (content, now_iso(), row_id),
    )


def count_with_content(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM raw_news WHERE content IS NOT NULL AND TRIM(content) <> ''"
    ).fetchone()[0]


# ====================================================================
#  clean 저장소 (요건 3)
#  raw 와 같은 파일 안의 '다른 테이블' 이다. 서랍장 하나에 서랍 두 칸.
# ====================================================================
SCHEMA_CLEAN = """
CREATE TABLE IF NOT EXISTS clean_news (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_id           INTEGER,
    url              TEXT NOT NULL UNIQUE,
    title            TEXT NOT NULL,
    description      TEXT,
    content          TEXT,
    body_source      TEXT,      -- 본문을 content 에서 얻었나 description 에서 얻었나
    category         TEXT,      -- 향토음식 / 맛집·외식 / 식재료·제철 / 미식축제 / 여행·관광
    matched_keywords TEXT,      -- 어떤 단어 때문에 그 카테고리가 됐는가
    published_date   TEXT,      -- YYYY-MM-DD 로 통일된 날짜
    published_at     TEXT,
    publisher        TEXT,
    source_name      TEXT,
    method           TEXT,
    cleaned_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_clean_cat  ON clean_news(category);
CREATE INDEX IF NOT EXISTS idx_clean_date ON clean_news(published_date);
"""

CLEAN_FIELDS = ["raw_id", "url", "title", "description", "content", "body_source",
                "category", "matched_keywords", "published_date", "published_at",
                "publisher", "source_name", "method"]


def init_clean(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_CLEAN)
    conn.commit()


def upsert_clean(conn: sqlite3.Connection, item: dict, policy: str = "skip") -> str:
    """정제된 한 건을 clean 저장소에 넣는다.

    policy="skip"   이미 있으면 그대로 둔다 (기본)
    policy="upsert" 이미 있으면 새 내용으로 덮어쓴다

    왜 두 가지가 필요한가
      skip   — 같은 기사를 다시 처리하지 않아 빠르다. 평소 운영용.
      upsert — 정제 규칙(키워드 등)을 고친 뒤 전체를 다시 반영할 때 쓴다.
    """
    values = [item.get(f) for f in CLEAN_FIELDS] + [now_iso()]
    placeholders = ", ".join("?" * (len(CLEAN_FIELDS) + 1))
    columns = ", ".join(CLEAN_FIELDS + ["cleaned_at"])

    if policy == "upsert":
        updates = ", ".join(f"{f}=excluded.{f}" for f in CLEAN_FIELDS if f != "url")
        sql = (f"INSERT INTO clean_news ({columns}) VALUES ({placeholders}) "
               f"ON CONFLICT(url) DO UPDATE SET {updates}, cleaned_at=excluded.cleaned_at")
        exists = conn.execute(
            "SELECT 1 FROM clean_news WHERE url = ?", (item["url"],)
        ).fetchone()
        conn.execute(sql, values)
        return "updated" if exists else "saved"

    sql = f"INSERT OR IGNORE INTO clean_news ({columns}) VALUES ({placeholders})"
    cur = conn.execute(sql, values)
    return "saved" if cur.rowcount > 0 else "skipped"


def count_clean(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM clean_news").fetchone()[0]


def count_clean_by_category(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT category, COUNT(*) AS n FROM clean_news "
        "GROUP BY category ORDER BY n DESC"
    ).fetchall()


# --- 요약 결과를 담을 컬럼 (요건 4) -----------------------------------
SUMMARY_COLUMNS = {
    "summary": "TEXT",
    "summary_model": "TEXT",
    "summarized_at": "TEXT",
    # 보너스 — 감성 분석
    "sentiment": "TEXT",
    "sentiment_reason": "TEXT",
    "sentiment_at": "TEXT",
}


def migrate_clean(conn: sqlite3.Connection) -> None:
    existing = {r[1] for r in conn.execute("PRAGMA table_info(clean_news)")}
    for name, coltype in SUMMARY_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE clean_news ADD COLUMN {name} {coltype}")
    conn.commit()


def select_for_summary(conn: sqlite3.Connection, mode: str,
                       news_id: int | None = None,
                       limit: int | None = None) -> list[sqlite3.Row]:
    """요약할 기사를 고른다.

    mode="unsummarized"  아직 요약이 없는 것만 (기본)
    mode="all"           전체 (다시 요약)
    mode="id"            특정 한 건
    """
    base = "SELECT id, title, content, category FROM clean_news"
    if mode == "id":
        return conn.execute(base + " WHERE id = ?", (news_id,)).fetchall()
    if mode == "all":
        sql = base + " ORDER BY id"
    else:
        sql = base + " WHERE summary IS NULL OR TRIM(summary) = '' ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


def save_summary(conn: sqlite3.Connection, news_id: int, summary: str, model: str) -> None:
    conn.execute(
        "UPDATE clean_news SET summary = ?, summary_model = ?, summarized_at = ? "
        "WHERE id = ?",
        (summary, model, now_iso(), news_id),
    )


def count_summarized(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM clean_news WHERE summary IS NOT NULL AND TRIM(summary) <> ''"
    ).fetchone()[0]


def prune_clean(conn: sqlite3.Connection, keep_urls: list[str]) -> int:
    """지금 규칙으로 통과하지 못하는 기사를 clean 에서 지운다.

    raw 는 건드리지 않는다. 원본이 남아 있으므로 규칙을 되돌리면
    언제든 다시 만들어낼 수 있다 — 이것이 raw/clean 을 나눈 이유다.
    """
    if not keep_urls:
        return 0
    marks = ", ".join("?" * len(keep_urls))
    cur = conn.execute(f"DELETE FROM clean_news WHERE url NOT IN ({marks})", keep_urls)
    return cur.rowcount


# ====================================================================
#  분석 결과 저장소 (요건 5 — "분석 결과는 별도 저장하여 리포트에 활용")
# ====================================================================
SCHEMA_ANALYSIS = """
CREATE TABLE IF NOT EXISTS analyses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date_from     TEXT,
    date_to       TEXT,
    category      TEXT,
    article_count INTEGER NOT NULL,
    result_json   TEXT NOT NULL,   -- 분석 결과 전체를 JSON 문자열로
    model         TEXT,
    created_at    TEXT NOT NULL
);
"""


def init_analysis(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_ANALYSIS)
    conn.commit()


def save_analysis(conn: sqlite3.Connection, date_from, date_to, category,
                  count: int, result: dict, model: str) -> int:
    cur = conn.execute(
        "INSERT INTO analyses (date_from, date_to, category, article_count, "
        "result_json, model, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (date_from, date_to, category, count,
         json.dumps(result, ensure_ascii=False), model, now_iso()),
    )
    return cur.lastrowid


def latest_analysis(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM analyses ORDER BY id DESC LIMIT 1"
    ).fetchone()


def select_for_analysis(conn: sqlite3.Connection, date_from=None, date_to=None,
                        category=None) -> list[sqlite3.Row]:
    """조건에 맞는 clean 기사를 고른다. (요건 5 — 기간·카테고리별)"""
    sql = ("SELECT id, title, summary, content, category, published_date "
           "FROM clean_news WHERE 1=1")
    params: list = []
    if date_from:
        sql += " AND published_date >= ?"; params.append(date_from)
    if date_to:
        sql += " AND published_date <= ?"; params.append(date_to)
    if category:
        sql += " AND category = ?"; params.append(category)
    sql += " ORDER BY published_date, id"
    return conn.execute(sql, params).fetchall()


def count_clean_by_date(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """일자별 기사 수. 추이 차트의 재료."""
    return conn.execute(
        "SELECT published_date, COUNT(*) AS n FROM clean_news "
        "WHERE published_date IS NOT NULL GROUP BY published_date "
        "ORDER BY published_date"
    ).fetchall()


def quality_metrics(conn: sqlite3.Connection) -> dict:
    """리포트에 넣을 품질 지표. (요건 7 — 2개 이상)

    "몇 건 모았다"만으로는 파이프라인이 잘 도는지 알 수 없다.
    각 단계에서 얼마나 살아남았는지를 비율로 봐야 어디가 병목인지 보인다.
    """
    raw = count_raw(conn)
    clean = count_clean(conn)
    summarized = count_summarized(conn)
    with_body = conn.execute(
        "SELECT COUNT(*) FROM clean_news WHERE body_source = 'content'"
    ).fetchone()[0]
    crawled = count_with_content(conn)

    def pct(a: int, b: int) -> float:
        return round(a / b * 100, 1) if b else 0.0

    return {
        "raw": raw,
        "clean": clean,
        "summarized": summarized,
        "with_body": with_body,
        "crawled": crawled,
        "clean_rate": pct(clean, raw),          # 지표1 정제 통과율
        "summary_rate": pct(summarized, clean),  # 지표2 요약 완료율
        "body_rate": pct(with_body, clean),      # 지표3 본문 확보율
        "crawl_rate": pct(crawled, raw),         # 지표4 크롤링 도달률
    }


def top_keywords_from_analysis(conn: sqlite3.Connection, n: int = 5) -> list[str]:
    row = latest_analysis(conn)
    if not row:
        return []
    return json.loads(row["result_json"]).get("keywords", [])[:n]


EXPORT_FIELDS = ["id", "category", "published_date", "title", "summary",
                 "sentiment", "publisher", "source_name", "method",
                 "body_source", "matched_keywords", "url"]


def select_for_export(conn: sqlite3.Connection, status: str = "all",
                      category: str | None = None) -> list[sqlite3.Row]:
    """내보낼 기사를 고른다. (요건 8 — 필터링 옵션)"""
    sql = f"SELECT {', '.join(EXPORT_FIELDS)} FROM clean_news WHERE 1=1"
    params: list = []
    if status == "summarized":
        sql += " AND summary IS NOT NULL AND TRIM(summary) <> ''"
    elif status == "unsummarized":
        sql += " AND (summary IS NULL OR TRIM(summary) = '')"
    if category:
        sql += " AND category = ?"
        params.append(category)
    sql += " ORDER BY published_date DESC, id"
    return conn.execute(sql, params).fetchall()


# ====================================================================
#  조회 (보너스 과제 — list / show)
# ====================================================================

def _filter_sql(category=None, date_from=None, date_to=None,
                keyword=None, status="all") -> tuple[str, list]:
    """목록 조회와 건수 세기가 같은 조건을 쓰도록 한 곳에서 만든다.

    조건을 두 군데에 적으면 한쪽만 고쳤을 때 페이지 수가 어긋난다.
    """
    sql = " WHERE 1=1"
    params: list = []
    if category:
        sql += " AND category = ?"; params.append(category)
    if date_from:
        sql += " AND published_date >= ?"; params.append(date_from)
    if date_to:
        sql += " AND published_date <= ?"; params.append(date_to)
    if keyword:
        sql += " AND (title LIKE ? OR summary LIKE ? OR content LIKE ?)"
        like = f"%{keyword}%"
        params += [like, like, like]
    if status == "summarized":
        sql += " AND summary IS NOT NULL AND TRIM(summary) <> ''"
    elif status == "unsummarized":
        sql += " AND (summary IS NULL OR TRIM(summary) = '')"
    return sql, params


def count_filtered(conn: sqlite3.Connection, **kw) -> int:
    where, params = _filter_sql(**kw)
    return conn.execute("SELECT COUNT(*) FROM clean_news" + where, params).fetchone()[0]


def list_news(conn: sqlite3.Connection, page: int = 1, size: int = 10, **kw) -> list[sqlite3.Row]:
    """페이지 단위로 목록을 꺼낸다. (보너스 — 페이지네이션)"""
    where, params = _filter_sql(**kw)
    sql = ("SELECT id, category, published_date, title, publisher, "
           "CASE WHEN summary IS NULL OR TRIM(summary)='' THEN 0 ELSE 1 END AS has_summary "
           "FROM clean_news" + where +
           " ORDER BY published_date DESC, id DESC LIMIT ? OFFSET ?")
    return conn.execute(sql, params + [size, (page - 1) * size]).fetchall()


def get_news(conn: sqlite3.Connection, news_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM clean_news WHERE id = ?", (news_id,)).fetchone()


def select_for_sentiment(conn: sqlite3.Connection, redo: bool = False,
                         limit: int | None = None) -> list[sqlite3.Row]:
    """[보너스] 감성 판정이 필요한 기사를 고른다."""
    sql = "SELECT id, title, summary, content, category FROM clean_news"
    if not redo:
        sql += " WHERE sentiment IS NULL OR TRIM(sentiment) = ''"
    sql += " ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


def save_sentiment(conn: sqlite3.Connection, news_id: int,
                   label: str, reason: str) -> None:
    conn.execute(
        "UPDATE clean_news SET sentiment = ?, sentiment_reason = ?, "
        "sentiment_at = ? WHERE id = ?",
        (label, reason, now_iso(), news_id),
    )


def count_by_sentiment(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """[보너스] 감성별 건수. 긍정 → 중립 → 부정 순서로 고정한다."""
    return conn.execute(
        "SELECT sentiment, COUNT(*) AS n FROM clean_news "
        "WHERE sentiment IS NOT NULL AND TRIM(sentiment) <> '' "
        "GROUP BY sentiment "
        "ORDER BY CASE sentiment WHEN '긍정' THEN 1 WHEN '중립' THEN 2 ELSE 3 END"
    ).fetchall()
