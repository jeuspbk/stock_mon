"""주식 시황 모니터링 대시보드 (Streamlit).

실행:  streamlit run app.py
"""

from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st

import indicators as ind

st.set_page_config(page_title="주식 시황 모니터링", page_icon="📈", layout="wide")

KST = ZoneInfo("Asia/Seoul")


# ---------------------------------------------------------------------------
# 포맷 헬퍼
# ---------------------------------------------------------------------------

def fmt_num(value: float | None, unit: str = "") -> str:
    if value is None:
        return "—"
    return f"{value:,.2f} {unit}".strip()


def change_html(change: float | None, pct: float | None) -> str:
    if change is None or pct is None:
        return '<span style="color:#888">—</span>'
    color = "#d33" if change > 0 else ("#06c" if change < 0 else "#888")
    arrow = "▲" if change > 0 else ("▼" if change < 0 else "—")
    return f'<span style="color:{color}">{arrow} {change:+,.2f} ({pct:+.2f}%)</span>'


def disparity_color(status: str) -> str:
    return {"overheat": "#d33", "oversold": "#06c",
            "normal": "#1a7f37", "na": "#888"}[status]


def disparity_label(status: str) -> str:
    return {"overheat": "과열", "oversold": "과매도",
            "normal": "중립", "na": "N/A"}[status]


# ---------------------------------------------------------------------------
# 데이터 로딩 (tick 으로 캐시 무효화 → 자동 갱신 시 새 데이터)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner="시세를 불러오는 중...")
def load_snapshot(tick: int):
    return ind.fetch_all()


@st.cache_data(ttl=300, show_spinner=False)
def load_series(tick: int):
    return ind.fetch_disparity_series()


@st.cache_data(ttl=300, show_spinner=False)
def load_world_series(tick: int):
    return ind.fetch_world_series()


@st.cache_data(ttl=300, show_spinner=False)
def load_trading(tick: int):
    return ind.fetch_index_trading()


@st.cache_data(ttl=300, show_spinner=False)
def load_deposit(tick: int):
    return ind.fetch_deposit()


# ---------------------------------------------------------------------------
# 사이드바 — 자동 갱신 설정
# ---------------------------------------------------------------------------

st.sidebar.header("⚙️ 설정")
auto = st.sidebar.toggle("자동 갱신", value=False)
interval = st.sidebar.select_slider(
    "갱신 주기(초)", options=[30, 60, 120, 300], value=60, disabled=not auto,
)
show_chart = st.sidebar.toggle("이격도 추이 차트", value=True)
show_trading = st.sidebar.toggle("거래량·거래대금 차트", value=True)
show_deposit_chart = st.sidebar.toggle("증시 자금 추이 차트", value=True)
show_world_chart = st.sidebar.toggle("세계지수 추이 차트", value=True)
if st.sidebar.button("🔄 지금 새로고침", use_container_width=True):
    st.cache_data.clear()
    st.rerun()
_th20 = ind.disparity_thresholds(20)
_th50 = ind.disparity_thresholds(50)
st.sidebar.caption(
    "자동 갱신 켜짐 시 설정한 주기마다 시세를 다시 불러옵니다.\n\n"
    f"20일선 과열 ≥ {_th20['overheat']:.0f} · 과매도 ≤ {_th20['oversold']:.0f}\n\n"
    f"50일선 과열 ≥ {_th50['overheat']:.0f} · 과매도 ≤ {_th50['oversold']:.0f}"
)


# ---------------------------------------------------------------------------
# 대시보드 본문 (fragment: auto 시 run_every 주기로 자동 재실행)
# ---------------------------------------------------------------------------

@st.fragment(run_every=interval if auto else None)
def dashboard():
    tick = int(time.time() // interval) if auto else 0
    data = load_snapshot(tick)
    quotes = data["quotes"]
    disparities = data["disparities"]

    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
    st.title("📈 주식 시황 모니터링")
    badge = "🟢 자동 갱신 중" if auto else "⏸ 수동"
    st.caption(
        f"기준 시각: {now} · {badge} · "
        "데이터: 미국채 10년물 = Yahoo, 그 외(지수·환율·DXY·SOX) = 네이버 금융"
    )

    # --- 경고 배너 -------------------------------------------------------
    alerts = []
    for q in quotes:                       # 매크로 임계치 경고
        msg = ind.macro_alert(q.ticker, q.price)
        if msg:
            alerts.append(f"🔺 **{msg}**")
    for d in disparities:                  # 이격도 과열/과매도 경고
        for period in ind.MA_PERIODS:
            val = d.values.get(period) if not d.error else None
            status = ind.disparity_status(val, period)
            if status == "overheat":
                alerts.append(f"🔴 **{d.name} {period}일선 과열** (이격도 {val:.1f})")
            elif status == "oversold":
                alerts.append(f"🔵 **{d.name} {period}일선 과매도** (이격도 {val:.1f})")
    if alerts:
        st.warning("　|　".join(alerts), icon="⚠️")

    # --- 매크로 지표 -----------------------------------------------------
    st.subheader("매크로 지표")
    macro_names = {n for n, _, _ in ind.MACRO}
    macro_quotes = [q for q in quotes if q.name in macro_names]
    for col, q in zip(st.columns(len(macro_quotes)), macro_quotes):
        with col:
            alerted = ind.macro_alert(q.ticker, q.price) is not None
            delta = (f"{q.change:+,.2f} ({q.change_pct:+.2f}%)"
                     if q.change_pct is not None else None)
            st.metric(
                label=("🔺 " if alerted else "") + q.name,
                value=fmt_num(q.price, q.unit) if q.error is None else "오류",
                delta=delta,
            )
            cfg = ind.MACRO_ALERTS.get(q.ticker)
            if cfg:
                mark = "🔴 경고" if alerted else "🟢 정상"
                st.caption(f"{mark} · 임계 ≥ {cfg[1]}")

    # --- 이격도 ----------------------------------------------------------
    st.subheader("KOSPI / KOSDAQ 이격도")
    st.caption(
        "이격도 = (종가 / N일 이동평균) × 100 ｜ "
        f"20일선 과열 ≥ {_th20['overheat']:.0f}·과매도 ≤ {_th20['oversold']:.0f} ｜ "
        f"50일선 과열 ≥ {_th50['overheat']:.0f}·과매도 ≤ {_th50['oversold']:.0f}"
    )
    series_data = load_series(tick) if show_chart else {}
    for col, d in zip(st.columns(len(disparities)), disparities):
        with col:
            st.markdown(f"#### {d.name}")
            if d.error:
                st.warning(d.error)
                continue
            st.markdown(f"현재가 **{d.price:,.2f}**")
            for period in ind.MA_PERIODS:
                val = d.values.get(period)
                status = ind.disparity_status(val, period)
                color = disparity_color(status)
                label = disparity_label(status)
                val_str = f"{val:.2f}" if val is not None else "—"
                st.markdown(
                    f'<div style="padding:8px 12px;margin:4px 0;border-radius:8px;'
                    f'background:{color}1a;border-left:4px solid {color}">'
                    f"<b>{period}일선 이격도</b> &nbsp; "
                    f'<span style="font-size:1.3em;color:{color};font-weight:700">'
                    f"{val_str}</span> &nbsp; "
                    f'<span style="color:{color}">({label})</span></div>',
                    unsafe_allow_html=True,
                )
            if show_chart:
                df = series_data.get(d.name)
                if df is not None and not df.empty:
                    st.line_chart(df, height=200)

    # --- 거래량 · 거래대금 ----------------------------------------------
    st.subheader("KOSPI / KOSDAQ 거래량 · 거래대금")
    st.caption(
        "거래량 = 백만주, 거래대금 = 조원 ｜ 봉차트는 최근 30거래일 ｜ "
        "증감 %는 전일 대비 (당일은 장중 누적이라 잠정치)"
    )
    trading = load_trading(tick)
    if not trading:
        st.info("거래량·거래대금 데이터를 불러올 수 없습니다.")
    for col, t in zip(st.columns(len(trading)) if trading else [], trading):
        with col:
            st.markdown(f"#### {t.name}")
            if t.error:
                st.warning(t.error)
                continue
            mcols = st.columns(2)
            mcols[0].metric(
                "거래량 (백만주)",
                f"{t.volume:,.0f}",
                delta=(f"{t.volume_pct:+.2f}%"
                       if t.volume_pct is not None else None),
            )
            mcols[1].metric(
                "거래대금 (조원)",
                f"{t.value:,.2f}",
                delta=(f"{t.value_pct:+.2f}%"
                       if t.value_pct is not None else None),
            )
            if show_trading and not t.df.empty:
                st.caption("거래량 (백만주)")
                st.bar_chart(t.df["거래량(백만주)"], height=180, color="#5b8def")
                st.caption("거래대금 (조원)")
                st.bar_chart(t.df["거래대금(조원)"], height=180, color="#e0823d")

    # --- 증시 자금 (고객예탁금) -----------------------------------------
    st.subheader("증시 자금 (고객예탁금 · 신용융자)")
    st.caption(
        "고객예탁금 = 증시 대기 자금, 신용융자 = 빚투 잔고 ｜ 단위 조원 ｜ "
        "증감 %는 전일 대비 ｜ 출처: 네이버 금융"
    )
    dep = load_deposit(tick)
    if dep.error:
        st.info("증시 자금 데이터를 불러올 수 없습니다.")
    else:
        st.caption(f"기준일: {dep.date}")
        dcols = st.columns(2)
        dcols[0].metric(
            "고객예탁금 (조원)",
            f"{dep.deposit:,.2f}" if dep.deposit is not None else "—",
            delta=(f"{dep.deposit_pct:+.2f}%"
                   if dep.deposit_pct is not None else None),
        )
        dcols[1].metric(
            "신용융자 잔고 (조원)",
            f"{dep.credit:,.2f}" if dep.credit is not None else "—",
            delta=(f"{dep.credit_pct:+.2f}%"
                   if dep.credit_pct is not None else None),
        )
        if show_deposit_chart and not dep.df.empty:
            chcols = st.columns(2)
            with chcols[0]:
                st.caption("고객예탁금 추이 (조원)")
                st.line_chart(dep.df["고객예탁금(조원)"], height=200,
                              color="#5b8def")
            with chcols[1]:
                st.caption("신용융자 잔고 추이 (조원)")
                st.line_chart(dep.df["신용잔고(조원)"], height=200,
                              color="#e0823d")

    # --- 세계 주요 지수 --------------------------------------------------
    st.subheader("세계 주요 지수")
    if show_world_chart:
        st.caption("추이 차트는 각 지수의 시작일을 100으로 맞춘 상대지수입니다 (지역 내 비교용).")
    world_series = load_world_series(tick) if show_world_chart else {}
    by_name = {q.name: q for q in quotes}
    for col, (region, items) in zip(
        st.columns(len(ind.WORLD_INDICES)), ind.WORLD_INDICES.items()
    ):
        with col:
            st.markdown(f"#### {region}")
            rows = []
            for name, _ in items:
                q = by_name.get(f"{region} · {name}")
                if q is None:
                    continue
                rows.append(
                    f'<tr><td style="padding:6px 8px">{name}</td>'
                    f'<td style="padding:6px 8px;text-align:right">'
                    f"{fmt_num(q.price)}</td>"
                    f'<td style="padding:6px 8px;text-align:right">'
                    f"{change_html(q.change, q.change_pct)}</td></tr>"
                )
            st.markdown(
                '<table style="width:100%;border-collapse:collapse">'
                + "".join(rows) + "</table>",
                unsafe_allow_html=True,
            )
            if show_world_chart:
                wdf = world_series.get(region)
                if wdf is not None and not wdf.empty:
                    st.line_chart(wdf, height=220)

    st.divider()
    st.caption(
        "ⓘ 빨강 = 상승/과열, 파랑 = 하락/과매도 (국내 증시 관례). "
        "본 정보는 투자 참고용이며 정확성을 보장하지 않습니다."
    )


dashboard()
