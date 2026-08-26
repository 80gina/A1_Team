"""차트·리포트·내보내기를 담당하는 모듈. (요건 6·7·8)

한글 폰트 이야기
    matplotlib 의 기본 폰트에는 한글 글자가 없다. 그대로 그리면
    제목과 축 이름이 전부 네모(□□□)로 나온다.
    그래서 이 컴퓨터에 깔린 한글 폰트를 찾아 먼저 지정한다.

차트를 그릴 때 지킨 것
    - 막대는 '크기'를 보여준다. 카테고리마다 다른 색을 칠하지 않는다.
      색이 다르면 '색깔별로 다른 뜻이 있나' 하고 읽게 되는데, 여기서는
      길이 하나만 읽으면 되기 때문이다.
    - 격자선은 옅게, 축 테두리는 최소한으로. 눈이 데이터로 먼저 가게 한다.
    - 값은 막대 끝에 직접 적는다. 축 눈금을 되짚어 읽지 않아도 되도록.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")           # 화면 없이 파일로만 저장한다
import matplotlib.pyplot as plt
from matplotlib import font_manager

import config

# 한글 폰트 후보 — 위에서부터 있는 것을 쓴다
KOREAN_FONTS = [
    "Malgun Gothic",      # 윈도우 기본
    "AppleGothic",        # 맥
    "NanumGothic",        # 나눔고딕
    "NanumBarunGothic",
    "Noto Sans CJK KR",   # 리눅스
    "Noto Sans CJK JP",   # 같은 글꼴 묶음이라 한글도 들어 있다
    "Noto Sans KR",
    "Gulim",
]

# 색은 최소한으로 — 하나의 값을 읽는 차트이므로 한 가지 색만 쓴다
INK        = "#1F2933"   # 제목·값
MUTED      = "#6B7280"   # 축 이름
GRID       = "#E5E7EB"   # 격자
BAR        = "#3D6FB4"   # 막대
LINE       = "#3D6FB4"   # 선
SURFACE    = "#FFFFFF"


def setup_korean_font(log) -> str:
    """이 컴퓨터에 있는 한글 폰트를 찾아 matplotlib 에 지정한다."""
    installed = {f.name for f in font_manager.fontManager.ttflist}
    for name in KOREAN_FONTS:
        if name in installed:
            plt.rcParams["font.family"] = name
            plt.rcParams["axes.unicode_minus"] = False   # 마이너스 기호 깨짐 방지
            log.info("한글 폰트 적용: %s", name)
            return name

    log.warning("한글 폰트를 찾지 못했습니다. 차트의 한글이 □ 로 보일 수 있습니다.")
    log.warning("  → 후보: %s", ", ".join(KOREAN_FONTS))
    plt.rcParams["axes.unicode_minus"] = False
    return ""


def _style(ax) -> None:
    """축을 옅게 정리한다. 공통 스타일."""
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, length=0)
    ax.title.set_color(INK)


def chart_category(rows, out_dir: Path, log) -> Path:
    """차트 1 — 카테고리별 뉴스 수 (가로 막대)

    가로로 눕힌 이유: 카테고리 이름이 '식재료·제철' 처럼 길어서
    세로 막대로 그리면 글자가 겹치거나 기울어진다.
    """
    labels = [r["category"] for r in rows][::-1]     # 큰 값이 위로 오도록 뒤집는다
    values = [r["n"] for r in rows][::-1]

    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)
    bars = ax.barh(labels, values, color=BAR, height=0.6)
    _style(ax)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_xlabel("기사 수 (건)", color=MUTED, fontsize=10)
    ax.set_title("카테고리별 뉴스 분포", fontsize=14, pad=14, loc="left")

    # 값을 막대 끝에 직접 적는다
    for bar, v in zip(bars, values):
        ax.text(bar.get_width() + max(values) * 0.02,
                bar.get_y() + bar.get_height() / 2,
                str(v), va="center", color=INK, fontsize=10)
    ax.set_xlim(0, max(values) * 1.15)

    path = out_dir / "chart_category.png"
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    log.info("차트 저장: %s", path.name)
    return path


def chart_daily(rows, out_dir: Path, log) -> Path:
    """차트 2 — 일자별 수집 추이 (꺾은선)"""
    dates = [r["published_date"] for r in rows]
    values = [r["n"] for r in rows]

    fig, ax = plt.subplots(figsize=(9, 4.5), dpi=150)
    ax.plot(dates, values, color=LINE, linewidth=2,
            marker="o", markersize=6, markerfacecolor=LINE,
            markeredgecolor=SURFACE, markeredgewidth=1.5)
    ax.fill_between(range(len(dates)), values, color=LINE, alpha=0.08)
    _style(ax)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_ylabel("기사 수 (건)", color=MUTED, fontsize=10)
    ax.set_title("일자별 기사 수집 추이", fontsize=14, pad=14, loc="left")
    ax.set_ylim(0, max(values) * 1.25)
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right", fontsize=9)

    # 가장 많은 날에만 값을 적는다 (모든 점에 숫자를 붙이면 지저분해진다)
    peak = values.index(max(values))
    ax.annotate(f"{max(values)}건", (peak, max(values)),
                textcoords="offset points", xytext=(0, 10),
                ha="center", color=INK, fontsize=10, fontweight="bold")

    path = out_dir / "chart_daily.png"
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    log.info("차트 저장: %s", path.name)
    return path


def output_dir(cfg: dict) -> Path:
    d = config.BASE_DIR / cfg.get("paths", {}).get("output_dir", "output")
    d.mkdir(parents=True, exist_ok=True)
    return d
