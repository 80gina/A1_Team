"""팔도 밥상 트렌드 — 향토음식·미식 여행 뉴스 분석 CLI.

이 파일은 '명령을 받아서 알맞은 모듈에 넘기는' 역할만 한다.
실제 일은 collector / storage / cleaner / ai_client / reporter 가 한다.

사용 예:
    python main.py --help
    python main.py fetch --limit 20
    python main.py clean
    python main.py summarize --unsummarized --limit 10
    python main.py analyze --date-from 2026-08-01 --category 향토음식
    python main.py report --save
    python main.py export --format csv
"""

from __future__ import annotations

import argparse
import sys

import config


def build_parser() -> argparse.ArgumentParser:
    """서브커맨드 6개를 가진 CLI를 만든다."""
    parser = argparse.ArgumentParser(
        prog="python main.py",
        description="팔도 밥상 트렌드 — 향토음식·미식 여행 뉴스 수집·분석 CLI",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="로그 상세 수준 (기본: INFO)",
    )

    sub = parser.add_subparsers(dest="command", metavar="<명령>")

    # 1) fetch — 뉴스 수집
    p_fetch = sub.add_parser("fetch", help="뉴스를 수집해 raw 저장소에 넣는다")
    p_fetch.add_argument("--source", help="config.json 의 소스 이름 (생략하면 전체)")
    p_fetch.add_argument("--method", choices=["rss", "crawl", "all"], default="rss",
                         help="수집 방법 (기본: rss)")
    p_fetch.add_argument("--limit", type=int, default=20, help="소스당 최대 건수 (기본: 20)")

    # 2) clean — 정제
    p_clean = sub.add_parser("clean", help="raw 데이터를 정제해 clean 저장소에 넣는다")
    p_clean.add_argument("--policy", choices=["skip", "upsert"],
                         help="중복 처리 정책 (생략하면 config.json 값)")
    p_clean.add_argument("--limit", type=int, help="처리할 최대 건수")

    # 3) summarize — AI 요약
    p_sum = sub.add_parser("summarize", help="AI로 기사를 요약한다")
    group = p_sum.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="전체 기사를 다시 요약")
    group.add_argument("--id", type=int, help="특정 기사 하나만 요약")
    group.add_argument("--unsummarized", action="store_true",
                       help="아직 요약되지 않은 기사만 (기본)")
    p_sum.add_argument("--limit", type=int, default=10, help="최대 건수 (기본: 10)")

    # 4) analyze — AI 인사이트 분석
    p_ana = sub.add_parser("analyze", help="기간·카테고리별 종합 인사이트를 분석한다")
    p_ana.add_argument("--date-from", help="시작일 (YYYY-MM-DD)")
    p_ana.add_argument("--date-to", help="종료일 (YYYY-MM-DD)")
    p_ana.add_argument("--category", help="카테고리 이름")
    p_ana.add_argument("--show", action="store_true", help="저장된 최근 분석 결과를 보여준다")

    # 5) report — 차트·리포트
    p_rep = sub.add_parser("report", help="차트를 그리고 리포트를 만든다")
    p_rep.add_argument("--save", action="store_true", help="파일로도 저장한다")
    p_rep.add_argument("--format", choices=["txt", "md"], default="md",
                       help="저장 형식 (기본: md)")
    p_rep.add_argument("--top", type=int, help="TOP N 개수 (생략하면 config.json 값)")

    # 6) export — 내보내기
    p_exp = sub.add_parser("export", help="데이터를 CSV/JSONL/Excel 로 내보낸다")
    p_exp.add_argument("--format", choices=["csv", "jsonl", "excel"], default="csv",
                       help="내보낼 형식 (기본: csv)")
    p_exp.add_argument("--status", choices=["all", "summarized", "unsummarized"],
                       default="all", help="내보낼 대상 (기본: all)")
    p_exp.add_argument("--category", help="특정 카테고리만")

    return parser


def not_ready(name: str, stage: str) -> int:
    """아직 만들지 않은 기능을 안내한다."""
    print(f"[INFO] '{name}' 명령은 아직 준비 중입니다. ({stage} 에서 추가됩니다)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    try:
        cfg = config.load_config()
    except config.ConfigError as e:
        print(f"[ERROR] {e}")
        return 2

    log = config.setup_logging(cfg, args.log_level)
    log.debug("설정 로드 완료: %s", cfg.get("project_name"))

    handlers = {
        "fetch": ("구간 B", "뉴스 수집"),
        "clean": ("구간 C", "데이터 정제"),
        "summarize": ("구간 D", "AI 요약"),
        "analyze": ("구간 D", "AI 인사이트 분석"),
        "report": ("구간 E", "차트·리포트"),
        "export": ("구간 E", "데이터 내보내기"),
    }
    stage, label = handlers[args.command]
    log.info("%s — %s", cfg.get("project_name"), label)
    return not_ready(args.command, stage)


if __name__ == "__main__":
    sys.exit(main())
