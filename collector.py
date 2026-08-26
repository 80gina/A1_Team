"""뉴스를 실제로 가져오는 모듈.

수집 방법은 두 가지다. (요건 2)
  방법 1  RSS  — 언론사가 스스로 내주는 목록 파일을 읽는다
  방법 2  크롤링 — 사람이 보는 기사 페이지를 긁어 본문을 얻는다  (커밋 4에서 추가)

두 방법의 차이
  RSS   : 형식이 정해져 있어 깨질 일이 적고 서버 부담도 작다.
          대신 제공하는 항목(제목·요약·링크)만 얻을 수 있고 본문은 없다.
  크롤링 : 본문까지 얻을 수 있다.
          대신 사이트 디자인이 바뀌면 코드가 바로 깨지고, 요청 예절을 지켜야 한다.

오류를 다루는 원칙 (A1-02에서 얻은 결론을 그대로 가져왔다)
  4xx = 우리 잘못  → 다시 해도 결과가 같다. 즉시 포기.
  5xx = 서버 사정  → 잠시 뒤면 풀릴 수 있다. 기다렸다 다시.
  타임아웃         → 5xx 와 같게 취급.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET

import requests

import storage

# RSS 안에서 dc:date, dc:creator 를 찾을 때 쓰는 이름표
DC = "{http://purl.org/dc/elements/1.1/}"


class FetchError(Exception):
    """수집을 포기해야 할 때 발생시키는 오류."""


def http_get(url: str, cfg: dict, log) -> requests.Response:
    """타임아웃과 재시도를 갖춘 GET 요청. (요건 2)"""
    req = cfg.get("request", {})
    timeout = req.get("timeout_sec", 10)
    max_retry = req.get("max_retry", 3)
    headers = {"User-Agent": req.get("user_agent", "BapsangTrendBot/1.0")}

    for attempt in range(1, max_retry + 1):
        try:
            res = requests.get(url, headers=headers, timeout=timeout)
        except requests.Timeout:
            wait = 2 ** attempt
            log.warning("타임아웃 (%d/%d). %d초 뒤 재시도합니다.", attempt, max_retry, wait)
            time.sleep(wait)
            continue
        except requests.ConnectionError as e:
            wait = 2 ** attempt
            log.warning("연결 실패 (%d/%d): %s. %d초 뒤 재시도합니다.",
                        attempt, max_retry, e.__class__.__name__, wait)
            time.sleep(wait)
            continue

        if res.status_code == 200:
            return res

        if 400 <= res.status_code < 500:
            # 주소가 틀렸거나 권한이 없다. 100번 해도 같다.
            raise FetchError(f"요청이 거부되었습니다 (HTTP {res.status_code}) — 재시도하지 않습니다: {url}")

        # 5xx — 서버가 잠시 붐비는 것뿐일 수 있다
        wait = 2 ** attempt
        log.warning("서버 오류 HTTP %d (%d/%d). %d초 뒤 재시도합니다.",
                    res.status_code, attempt, max_retry, wait)
        time.sleep(wait)

    raise FetchError(f"{max_retry}회 시도했지만 가져오지 못했습니다: {url}")


def parse_rss(xml_text: str, source: dict) -> list[dict]:
    """RSS 문자열을 기사 딕셔너리 목록으로 바꾼다.

    날짜 태그가 피드마다 다르다는 점에 주의.
    경향신문 피드는 <pubDate> 가 아니라 <dc:date> 를 쓴다.
    여기서는 '원본 그대로' 담아두고, 형식 통일은 정제(clean) 단계에서 한다.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise FetchError(f"RSS 형식을 해석하지 못했습니다: {e}") from e

    items: list[dict] = []
    for node in root.iter("item"):
        link = (node.findtext("link") or "").strip()
        if not link:
            continue   # 주소가 없으면 저장할 수 없다

        published = node.findtext("pubDate") or node.findtext(DC + "date") or ""
        categories = [c.text for c in node.findall("category") if c.text]

        items.append({
            "source_name": source["name"],
            "method": "rss",
            "publisher": source.get("publisher"),
            "url": link,
            "guid": (node.findtext("guid") or "").strip() or None,
            "title": (node.findtext("title") or "").strip(),
            "description": (node.findtext("description") or "").strip(),
            "content": None,                       # 본문은 크롤링 단계에서 채운다
            "published_raw": published.strip(),
            "collected_at": storage.now_iso(),
            "raw_payload": {
                "categories": categories,
                "creator": node.findtext(DC + "creator"),
                "source_url": source["url"],
            },
        })
    return items


def collect_rss(source: dict, cfg: dict, log, limit: int | None = None) -> list[dict]:
    """소스 하나에서 RSS를 받아 기사 목록을 돌려준다."""
    log.info("[RSS] %s 수집 중... (%s)", source["name"], source["url"])
    res = http_get(source["url"], cfg, log)
    res.encoding = res.apparent_encoding or "utf-8"
    items = parse_rss(res.text, source)
    if limit:
        items = items[:limit]
    log.info("[RSS] %s — %d건 확보", source["name"], len(items))
    return items


def select_sources(cfg: dict, name: str | None, method: str) -> list[dict]:
    """config.json 의 소스 중 이번에 쓸 것만 고른다."""
    chosen = []
    for src in cfg.get("sources", []):
        if not src.get("enabled", True):
            continue
        if name and src["name"] != name:
            continue
        if method != "all" and src.get("method") != method:
            continue
        chosen.append(src)
    return chosen


def polite_sleep(cfg: dict) -> None:
    """요청 사이에 쉬어 간다. 상대 서버에 대한 예의이자 차단 방지."""
    time.sleep(cfg.get("request", {}).get("delay_sec", 1.0))
