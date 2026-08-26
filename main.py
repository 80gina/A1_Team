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
import json
import sys

import ai_client
import cleaner
import collector
import config
import reporter
import storage


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
    p_fetch.add_argument("--target", choices=["all", "clean"], default="all",
                         help="크롤링 대상: all=전체, clean=정제를 통과한 음식·여행 기사만")

    # 2) clean — 정제
    p_clean = sub.add_parser("clean", help="raw 데이터를 정제해 clean 저장소에 넣는다")
    p_clean.add_argument("--policy", choices=["skip", "upsert"],
                         help="중복 처리 정책 (생략하면 config.json 값)")
    p_clean.add_argument("--limit", type=int, help="처리할 최대 건수")
    p_clean.add_argument("--rebuild", action="store_true",
                         help="분류 규칙을 고친 뒤 clean 저장소를 raw 기준으로 다시 맞춘다")

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


def _fetch_rss(args, cfg, log, conn) -> tuple[int, int, int]:
    """방법 1 — RSS 로 기사 목록을 모은다."""
    sources = collector.select_sources(cfg, args.source, "rss")
    if not sources:
        log.warning("RSS 소스가 없습니다. (--source=%s)", args.source)
        return 0, 0, 0

    log.info("[방법 1/RSS] 소스 %d개, 소스당 최대 %d건", len(sources), args.limit)
    saved = skipped = failed = 0
    for i, src in enumerate(sources, 1):
        try:
            items = collector.collect_rss(src, cfg, log, args.limit)
        except collector.FetchError as e:
            log.error("[%d/%d] %s 수집 실패: %s", i, len(sources), src["name"], e)
            failed += 1
            continue
        for item in items:
            if storage.insert_raw(conn, item):
                saved += 1
            else:
                skipped += 1
        conn.commit()
        if i < len(sources):
            collector.polite_sleep(cfg)

    log.info("[방법 1/RSS] %d건 저장, %d건 중복 건너뜀, %d개 소스 실패",
             saved, skipped, failed)
    return saved, skipped, failed


def _fetch_crawl(args, cfg, log, conn) -> tuple[int, int]:
    """방법 2 — 저장된 기사 주소를 방문해 본문을 긁어온다."""
    only_clean = args.target == "clean"
    if only_clean:
        storage.init_clean(conn)
        log.info("[방법 2/크롤링] 정제를 통과한 기사만 대상으로 합니다.")
    targets = storage.select_uncrawled(conn, args.limit, only_clean)
    if not targets:
        log.info("[방법 2/크롤링] 본문이 없는 기사가 없습니다. 할 일 없음.")
        return 0, 0

    log.info("[방법 2/크롤링] 대상 %d건 (요청 간격 %.1f초)",
             len(targets), cfg.get("request", {}).get("delay_sec", 1.0))
    ok = fail = 0
    for i, row in enumerate(targets, 1):
        try:
            content, how = collector.crawl_article(row["url"], cfg, log)
        except collector.FetchError as e:
            log.warning("[%d/%d] ID=%d 본문 실패: %s", i, len(targets), row["id"], e)
            fail += 1
            collector.polite_sleep(cfg)
            continue

        if content:
            storage.update_content(conn, row["id"], content)
            conn.commit()
            log.info("[%d/%d] ID=%d 본문 %d자 확보 — %s",
                     i, len(targets), row["id"], len(content), how)
            ok += 1
        else:
            log.warning("[%d/%d] ID=%d 본문을 찾지 못했습니다 (%s)",
                        i, len(targets), row["id"], row["title"][:20])
            fail += 1

        collector.polite_sleep(cfg)

    log.info("[방법 2/크롤링] %d건 성공, %d건 실패", ok, fail)
    return ok, fail


def cmd_fetch(args, cfg: dict, log) -> int:
    """뉴스를 수집해 raw 저장소에 넣는다. (요건 2)"""
    conn = storage.connect(cfg)
    storage.init_db(conn)
    storage.migrate(conn)

    failed = 0
    if args.method in ("rss", "all"):
        _, _, failed = _fetch_rss(args, cfg, log, conn)
    if args.method in ("crawl", "all"):
        _fetch_crawl(args, cfg, log, conn)

    total = storage.count_raw(conn)
    with_body = storage.count_with_content(conn)
    log.info("raw 저장소 총 %d건 (본문 확보 %d건)", total, with_body)
    for r in storage.count_raw_by_source(conn):
        log.info("  - %s (%s): %d건", r["source_name"], r["method"], r["n"])

    conn.close()
    return 0 if failed == 0 else 3


def cmd_clean(args, cfg: dict, log) -> int:
    """raw 를 정제해 clean 저장소에 넣는다. (요건 3)"""
    conn = storage.connect(cfg)
    storage.init_db(conn)
    storage.migrate(conn)
    storage.init_clean(conn)

    policy = args.policy or cfg.get("duplicate_policy", "skip")
    if args.rebuild:
        policy = "upsert"   # 다시 맞추려면 덮어써야 한다
        log.info("재구축 모드: 규칙에 더는 맞지 않는 기사는 clean 에서 제거됩니다.")
    rows = storage.select_raw(conn, args.limit)
    if not rows:
        log.warning("raw 저장소가 비어 있습니다. 먼저 fetch 를 실행하세요.")
        conn.close()
        return 1

    log.info("정제 시작: 대상 %d건, 중복 정책 = %s", len(rows), policy)

    stats = {"saved": 0, "updated": 0, "skipped": 0}
    dropped: dict[str, int] = {}
    valid_urls: list[str] = []

    for row in rows:
        item, reason = cleaner.clean_row(row, cfg.get("categories", []))
        if item is None:
            dropped[reason] = dropped.get(reason, 0) + 1
            log.debug("제외 (ID=%d, %s): %s", row["id"], reason, (row["title"] or "")[:30])
            continue
        result = storage.upsert_clean(conn, item, policy)
        valid_urls.append(item["url"])
        stats[result] += 1
    conn.commit()

    if args.rebuild:
        removed = storage.prune_clean(conn, valid_urls)
        conn.commit()
        log.info("재구축: 규칙에 맞지 않게 된 %d건을 clean 에서 제거했습니다.", removed)

    total_dropped = sum(dropped.values())
    log.info("정제 완료: %d건 저장, %d건 갱신, %d건 건너뜀, %d건 제외",
             stats["saved"], stats["updated"], stats["skipped"], total_dropped)
    for reason, n in sorted(dropped.items(), key=lambda kv: -kv[1]):
        log.info("  제외 사유 - %s: %d건", reason, n)

    log.info("clean 저장소 총 %d건", storage.count_clean(conn))
    for r in storage.count_clean_by_category(conn):
        log.info("  - %s: %d건", r["category"], r["n"])

    conn.close()
    return 0


def cmd_summarize(args, cfg: dict, log) -> int:
    """AI로 기사를 요약한다. (요건 4)"""
    try:
        api_key = config.get_api_key()
    except config.ConfigError as e:
        log.error("%s", e)
        return 2
    log.info("Gemini 키 확인: %s / 모델: %s",
             config.mask(api_key), cfg.get("ai", {}).get("model"))

    conn = storage.connect(cfg)
    storage.init_clean(conn)
    storage.migrate_clean(conn)

    mode = "unsummarized"
    if args.all:
        mode = "all"
    elif args.id:
        mode = "id"

    rows = storage.select_for_summary(conn, mode, args.id, args.limit)
    if not rows:
        log.info("요약할 기사가 없습니다. (이미 전부 요약되었거나 clean 저장소가 비어 있음)")
        conn.close()
        return 0

    log.info("요약 대상: %d건 (모드=%s)", len(rows), mode)
    model = cfg.get("ai", {}).get("model", "")
    ok = fail = 0

    for i, row in enumerate(rows, 1):
        body = row["content"] or ""
        try:
            summary = ai_client.summarize(row["title"], body, cfg, log, api_key)
        except ai_client.AIError as e:
            # 실패해도 멈추지 않는다. 기록만 남기고 다음 기사로. (요건 4)
            log.error("[%d/%d] ID=%d 요약 실패 — 건너뜁니다: %s", i, len(rows), row["id"], e)
            fail += 1
            continue

        storage.save_summary(conn, row["id"], summary, model)
        conn.commit()
        log.info("[%d/%d] ID=%d 요약 완료 (%d자 → %d자) [%s]",
                 i, len(rows), row["id"], len(body), len(summary), row["category"])
        ok += 1

    log.info("요약 완료: %d건 성공, %d건 실패", ok, fail)
    log.info("clean 저장소 %d건 중 %d건 요약됨",
             storage.count_clean(conn), storage.count_summarized(conn))
    conn.close()
    return 0


def _print_analysis(result: dict, header: str) -> None:
    """분석 결과를 사람이 읽기 좋게 출력한다."""
    print()
    print("=" * 62)
    print(f"  AI 인사이트 분석 결과 — {header}")
    print("=" * 62)

    print("\n[주요 트렌드]")
    for t in result.get("trends", []):
        print(f"  - {t}")

    print("\n[핵심 키워드]")
    print("  " + ", ".join(result.get("keywords", [])))

    print("\n[공통점과 차이점]")
    print(f"  {result.get('comparison', '')}")

    print("\n[시사점]")
    print(f"  {result.get('implications', '')}")

    print("\n[레시피 소재 제안]")
    for r in result.get("recipe_ideas", []):
        print(f"  - {r}")
    print()


def cmd_analyze(args, cfg: dict, log) -> int:
    """기간·카테고리별 뉴스를 종합해 인사이트를 뽑는다. (요건 5)"""
    conn = storage.connect(cfg)
    storage.init_clean(conn)
    storage.init_analysis(conn)

    # --show 는 저장된 결과를 다시 보여주기만 한다 (API 호출 없음)
    if args.show:
        row = storage.latest_analysis(conn)
        if not row:
            log.warning("저장된 분석 결과가 없습니다. 먼저 analyze 를 실행하세요.")
            conn.close()
            return 1
        log.info("저장된 분석 #%d (%s, 기사 %d건, 모델 %s)",
                 row["id"], row["created_at"], row["article_count"], row["model"])
        _print_analysis(json.loads(row["result_json"]),
                        f"저장본 #{row['id']}")
        conn.close()
        return 0

    rows = storage.select_for_analysis(conn, args.date_from, args.date_to, args.category)
    if not rows:
        log.warning("조건에 맞는 기사가 없습니다. (기간=%s~%s, 카테고리=%s)",
                    args.date_from, args.date_to, args.category)
        conn.close()
        return 1

    try:
        api_key = config.get_api_key()
    except config.ConfigError as e:
        log.error("%s", e)
        conn.close()
        return 2

    log.info("분석 대상: %d건", len(rows))
    lines = []
    for i, r in enumerate(rows, 1):
        body = (r["summary"] or r["content"] or "")[:400]
        lines.append(f"{i}. [{r['category']}] ({r['published_date']}) {r['title']}\n   {body}")
    articles_text = "\n".join(lines)

    period = f"{args.date_from or rows[0]['published_date']} ~ {args.date_to or rows[-1]['published_date']}"
    log.info("AI 분석 요청 중... (기간 %s%s)",
             period, f", 카테고리 {args.category}" if args.category else "")

    try:
        result = ai_client.analyze(articles_text, period, len(rows), cfg, log, api_key)
    except ai_client.AIError as e:
        log.error("분석 실패: %s", e)
        conn.close()
        return 3

    model = cfg.get("ai", {}).get("model", "")
    analysis_id = storage.save_analysis(conn, args.date_from, args.date_to,
                                        args.category, len(rows), result, model)
    conn.commit()
    log.info("분석 완료 — 결과를 저장했습니다 (분석 #%d)", analysis_id)

    header = period + (f" / {args.category}" if args.category else " / 전체")
    _print_analysis(result, header)
    conn.close()
    return 0


def cmd_report(args, cfg: dict, log) -> int:
    """차트를 그리고 리포트를 만든다. (요건 6·7)"""
    conn = storage.connect(cfg)
    storage.init_clean(conn)
    storage.init_analysis(conn)

    if storage.count_clean(conn) == 0:
        log.error("clean 저장소가 비어 있습니다. fetch → clean 을 먼저 실행하세요.")
        conn.close()
        return 1

    reporter.setup_korean_font(log)
    out = reporter.output_dir(cfg)

    by_cat = storage.count_clean_by_category(conn)
    by_date = storage.count_clean_by_date(conn)
    log.info("차트 생성: 카테고리 %d종, 수집일 %d일", len(by_cat), len(by_date))

    charts = [
        reporter.chart_category(by_cat, out, log),
        reporter.chart_daily(by_date, out, log),
    ]

    if args.top:
        cfg.setdefault("report", {})["top_n"] = args.top

    metrics = storage.quality_metrics(conn)
    analysis_row = storage.latest_analysis(conn)
    if analysis_row is None:
        log.warning("저장된 AI 분석이 없습니다. 리포트의 인사이트 절이 비어 있게 됩니다.")

    text = reporter.build_report(cfg, metrics, by_cat, by_date, analysis_row, charts)

    print()
    print(text)

    if args.save:
        reporter.save_report(text, out, args.format, log)
    else:
        log.info("파일로 남기려면 --save 를 붙이세요. (예: report --save --format md)")

    conn.close()
    return 0


def cmd_export(args, cfg: dict, log) -> int:
    """데이터를 CSV / JSONL / Excel 로 내보낸다. (요건 8)"""
    conn = storage.connect(cfg)
    storage.init_clean(conn)
    storage.migrate_clean(conn)

    rows = storage.select_for_export(conn, args.status, args.category)
    if not rows:
        log.warning("조건에 맞는 기사가 없습니다. (status=%s, category=%s)",
                    args.status, args.category)
        conn.close()
        return 1

    log.info("내보내기 대상: %d건 (status=%s%s)", len(rows), args.status,
             f", category={args.category}" if args.category else "")

    out = reporter.output_dir(cfg)
    fields = storage.EXPORT_FIELDS
    writers = {"csv": reporter.export_csv,
               "jsonl": reporter.export_jsonl,
               "excel": reporter.export_excel}
    path = writers[args.format](rows, fields, out, log)

    log.info("완료: %s", path)
    conn.close()
    return 0


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

    if args.command == "fetch":
        return cmd_fetch(args, cfg, log)
    if args.command == "clean":
        return cmd_clean(args, cfg, log)
    if args.command == "summarize":
        return cmd_summarize(args, cfg, log)
    if args.command == "analyze":
        return cmd_analyze(args, cfg, log)
    if args.command == "report":
        return cmd_report(args, cfg, log)
    if args.command == "export":
        return cmd_export(args, cfg, log)

    return not_ready(args.command, stage)


if __name__ == "__main__":
    sys.exit(main())
