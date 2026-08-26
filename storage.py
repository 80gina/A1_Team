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
