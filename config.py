"""설정 파일과 API 키, 로깅을 담당하는 모듈.

이 모듈이 하는 일은 세 가지다.
  1) config.json 을 읽어 파이썬 딕셔너리로 돌려준다
  2) .env 에서 API 키를 읽고, 화면에 찍을 때는 가려서(masking) 보여준다
  3) logging 모듈을 초기화한다 (콘솔 + 파일 동시 기록)

키를 코드에 직접 적지 않는 이유:
  코드를 GitHub에 올리는 순간 키도 함께 공개된다. 남이 내 키로 API를 쓰면
  요금과 사용량이 내 계정에서 빠져나간다. 그래서 키는 .env 에 두고
  .env 는 .gitignore 에 등록해 올라가지 않게 한다.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
ENV_PATH = BASE_DIR / ".env"


class ConfigError(Exception):
    """설정을 읽지 못했을 때 발생시키는 오류."""


# ---------------------------------------------------------------- 설정 파일

def load_config(path: Path | None = None) -> dict:
    """config.json 을 읽어 딕셔너리로 돌려준다."""
    path = path or CONFIG_PATH
    if not path.exists():
        raise ConfigError(
            f"설정 파일을 찾을 수 없습니다: {path}\n"
            "  → bapsang-trend 폴더 안에서 실행하고 있는지 확인하세요."
        )
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(
            f"설정 파일의 형식이 잘못되었습니다: {path}\n"
            f"  → {e.lineno}번째 줄 부근을 확인하세요. ({e.msg})"
        ) from e


def resolve_path(config: dict, key: str) -> Path:
    """config['paths'] 의 상대 경로를 프로젝트 기준 절대 경로로 바꾼다."""
    rel = config.get("paths", {}).get(key)
    if not rel:
        raise ConfigError(f"config.json 의 paths 에 '{key}' 가 없습니다.")
    full = BASE_DIR / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    return full


# ------------------------------------------------------------------ API 키

def get_api_key(required: bool = True) -> str | None:
    """.env 또는 환경 변수에서 Gemini API 키를 읽는다."""
    load_dotenv(ENV_PATH)
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key or key.startswith("여기에"):
        if required:
            raise ConfigError(
                "GEMINI_API_KEY 가 설정되지 않았습니다.\n"
                "  1) .env.example 을 복사해 .env 를 만드세요\n"
                "  2) .env 안의 GEMINI_API_KEY= 뒤에 발급받은 키를 붙여넣으세요"
            )
        return None
    return key


def mask(secret: str | None) -> str:
    """키를 로그에 남길 때 앞 4글자만 남기고 가린다."""
    if not secret:
        return "(없음)"
    if len(secret) <= 8:
        return secret[:2] + "*" * (len(secret) - 2)
    return secret[:4] + "*" * 8


# ------------------------------------------------------------------- 로깅

def setup_logging(config: dict | None = None, level: str = "INFO") -> logging.Logger:
    """콘솔과 파일에 동시에 남기는 로거를 준비한다.

    콘솔  : 사람이 지금 보는 진행 상황
    파일  : 나중에 "그때 왜 실패했지?" 를 되짚기 위한 기록
    """
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        # 윈도우 터미널(cp949)에서 한글·기호가 깨지는 것을 막는다
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    log_file = BASE_DIR / "logs" / "app.log"
    if config:
        try:
            log_file = resolve_path(config, "log_file")
        except ConfigError:
            pass
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("bapsang")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(console)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logger.addHandler(file_handler)

    return logger


def get_logger() -> logging.Logger:
    """이미 준비된 로거를 가져온다."""
    return logging.getLogger("bapsang")
