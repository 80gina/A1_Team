"""Gemini API 를 호출하는 모듈. (요건 4·5)

AI 를 두 번, 서로 다른 방식으로 부른다.
    요약(summarize) — 사람이 읽을 문장 한 덩이   → 자유 형식으로 받는다
    분석(analyze)   — 프로그램이 꺼내 쓸 값들     → JSON 스키마로 묶어서 받는다

"무엇을 시키느냐" 보다 "어떤 형태로 받느냐" 가 중요하다.
자유 형식으로 받은 글에서는 '핵심 키워드 5개'를 프로그램이 꺼낼 수 없다.
그래서 분석 쪽은 응답 형식을 스키마로 강제한다. (A1-02 에서 쓴 방식 그대로)

오류 처리 원칙은 collector 와 같다.
    4xx → 즉시 포기 (키가 틀렸으면 100번 해도 틀리다)
    5xx / 429 → 지수 백오프로 재시도
"""

from __future__ import annotations

import json
import time

import requests

API_BASE = "https://generativelanguage.googleapis.com/v1beta"


class AIError(Exception):
    """AI 호출을 포기해야 할 때."""


def _endpoint(model: str) -> str:
    return f"{API_BASE}/models/{model}:generateContent"


def model_chain(cfg: dict) -> list[str]:
    """쓸 모델을 우선순위대로 나열한다.

    앞의 모델이 404(그 계정에서 못 쓰는 모델)로 막히면 다음 모델로 넘어간다.
    모델 이름을 config.json 에 둔 덕분에 소스코드는 건드릴 필요가 없다.
    """
    ai = cfg.get("ai", {})
    chain = [ai.get("model", "gemini-3.5-flash-lite")]
    chain += [m for m in ai.get("fallback_models", []) if m not in chain]
    return chain


def generate(prompt: str, cfg: dict, log, api_key: str,
             schema: dict | None = None) -> str:
    """Gemini 에 프롬프트를 보내고 응답 텍스트를 돌려준다."""
    chain = model_chain(cfg)
    last_error: Exception | None = None
    for idx, model in enumerate(chain):
        try:
            return _generate_with(model, prompt, cfg, log, api_key, schema)
        except AIError as e:
            if "404" not in str(e) or idx == len(chain) - 1:
                raise
            log.warning("모델 '%s' 을(를) 쓸 수 없습니다. '%s' 로 전환합니다.",
                        model, chain[idx + 1])
            last_error = e
    raise last_error or AIError("사용 가능한 모델이 없습니다.")


def _generate_with(model: str, prompt: str, cfg: dict, log, api_key: str,
                   schema: dict | None = None) -> str:
    """모델 하나로 실제 호출한다."""
    ai = cfg.get("ai", {})
    req = cfg.get("request", {})
    timeout = req.get("timeout_sec", 10) * 3      # 생성은 수집보다 오래 걸린다
    max_retry = req.get("max_retry", 3)

    payload: dict = {"contents": [{"parts": [{"text": prompt}]}]}
    if schema:
        payload["generationConfig"] = {
            "responseMimeType": "application/json",
            "responseSchema": schema,
        }

    for attempt in range(1, max_retry + 1):
        try:
            res = requests.post(
                _endpoint(model),
                params={"key": api_key},
                json=payload,
                timeout=timeout,
            )
        except (requests.Timeout, requests.ConnectionError) as e:
            wait = 2 ** attempt
            log.warning("AI 연결 실패 (%d/%d): %s. %d초 뒤 재시도합니다.",
                        attempt, max_retry, e.__class__.__name__, wait)
            time.sleep(wait)
            continue

        if res.status_code == 200:
            return _extract_text(res.json())

        if res.status_code == 404:
            raise AIError(
                f"모델 '{model}' 을(를) 찾을 수 없습니다 (HTTP 404).\n"
                "  → config.json 의 ai.model 값을 확인하세요."
            )

        if res.status_code == 429:
            wait = 2 ** attempt * 2
            log.warning("사용량 제한 (429). %d초 뒤 재시도합니다. (%d/%d)",
                        wait, attempt, max_retry)
            time.sleep(wait)
            continue

        if 400 <= res.status_code < 500:
            # 서버가 준 설명을 버리지 않는다. 원인 진단이 빨라진다.
            raise AIError(f"AI 요청이 거부되었습니다 (HTTP {res.status_code}): "
                          f"{res.text[:300]}")

        wait = 2 ** attempt
        log.warning("AI 서버 오류 HTTP %d (%d/%d). %d초 뒤 재시도합니다.",
                    res.status_code, attempt, max_retry, wait)
        time.sleep(wait)

    raise AIError(f"{max_retry}회 시도했지만 응답을 받지 못했습니다.")


def _extract_text(data: dict) -> str:
    """Gemini 응답 구조에서 실제 글자만 꺼낸다."""
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError) as e:
        blocked = data.get("promptFeedback", {}).get("blockReason")
        if blocked:
            raise AIError(f"AI 가 응답을 거부했습니다 (사유: {blocked})") from e
        raise AIError(f"응답 구조를 해석하지 못했습니다: {json.dumps(data)[:200]}") from e


# ------------------------------------------------------------ 요약 (요건 4)

SUMMARY_PROMPT = """다음은 음식·여행 분야 뉴스 기사입니다.
핵심 내용을 한국어 {max_chars}자 이내로 요약하세요.

규칙:
- 지역명, 음식명, 행사명 같은 고유명사는 반드시 남길 것
- 기자 이름, 사진 설명, 구독 안내 같은 군더더기는 빼기
- 요약문만 출력하고 "요약:" 같은 머리말은 붙이지 말 것

[제목] {title}
[본문] {body}
"""


def summarize(title: str, body: str, cfg: dict, log, api_key: str) -> str:
    """기사 하나를 요약한다."""
    ai = cfg.get("ai", {})
    prompt = SUMMARY_PROMPT.format(
        max_chars=ai.get("summary_max_chars", 200),
        title=title,
        body=body[: ai.get("max_input_chars", 4000)],
    )
    return generate(prompt, cfg, log, api_key)
