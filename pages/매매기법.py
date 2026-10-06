"""유명 투자자 매매 기법 — 종목별 현재 신호와 일봉 백테스트."""

from __future__ import annotations

import pandas as pd
import streamlit as st

import strategies as stg

st.set_page_config(page_title="매매 기법", page_icon="📚", layout="wide")

LEVEL_STYLE = {   # 국내 관례: 빨강 = 매수, 파랑 = 매도
    "buy": ("#d33", "🔴"), "hold": ("#e0823d", "🟠"),
    "sell": ("#06c", "🔵"), "wait": ("#888", "⚪"),
}
PRESETS = {
    "삼성전자 (005930)": "005930",
    "SK하이닉스 (000660)": "000660",
    "KOSPI 지수": "KOSPI",
    "KOSDAQ 지수": "KOSDAQ",
}


@st.cache_data(ttl=600, show_spinner="기법별 신호·백테스트 계산 중...")
def load(symbol: str, years: int, fee: float, k: float):
    return stg.run_all(symbol, years=years, fee=fee, vb_k=k)


@st.cache_data(ttl=3600, show_spinner=False)
def search(q: str):
    return stg.search_stocks(q)


def pct(v, signed=True):
    if v is None:
        return "—"
    return f"{v:+.1f}%" if signed else f"{v:.1f}%"


# ---------------------------------------------------------------------------
# 사이드바 — 종목 · 백테스트 설정
# ---------------------------------------------------------------------------

st.sidebar.page_link("app.py", label="🏠 홈 (시황 대시보드)")
st.sidebar.header("🔎 종목 선택")
q = st.sidebar.text_input("종목명/코드 검색", placeholder="예: 카카오, 035720")
options = dict(PRESETS)
if q.strip():
    try:
        found = search(q.strip())
    except Exception:
        found = []
        st.sidebar.warning("검색 실패 — 네트워크를 확인하세요.")
    if found:
        options = {f"{n} ({c}) · {m}": c for c, n, m in found}
    elif q.strip().isdigit() and len(q.strip()) == 6:
        options = {f"종목코드 {q.strip()}": q.strip()}
    else:
        st.sidebar.caption("검색 결과가 없어 기본 종목을 보여줍니다.")
label = st.sidebar.selectbox("종목", list(options))
symbol = options[label]

st.sidebar.header("⚙️ 백테스트")
years = st.sidebar.select_slider("기간 (년)", options=[1, 2, 3, 5], value=3)
fee = st.sidebar.number_input("왕복 거래비용 (%)", 0.0, 2.0, 0.25, 0.05) / 100
vb_k = st.sidebar.slider("변동성 돌파 k", 0.2, 1.0, 0.5, 0.1)


# ---------------------------------------------------------------------------
# 본문
# ---------------------------------------------------------------------------

st.page_link("app.py", label="← 홈으로", icon="🏠")
st.title("📚 매매 기법")
st.caption(
    "성공한 투자자들의 매매 기법을 **적합도 순**으로 정리했습니다. "
    "규칙화가 가능한 기법은 선택 종목에 적용해 현재 신호와 백테스트를 보여주고, "
    "재무·정성 판단이 필요한 기법은 하단에 설명만 나열합니다."
)
with st.expander("ⓘ 적합도 순위 기준"):
    st.markdown(
        "1. **검증된 실적** — 창시자의 실전 기록 또는 장기 백테스트 근거\n"
        "2. **규칙의 명확성** — 재량 없이 일봉 가격·거래량만으로 재현 가능한지\n"
        "3. **위험 관리** — 손절·추세 필터 등 하락 방어 장치 내장 여부\n"
        "4. **개인 실행 용이성** — 판단 빈도, 한국 시장(개별주) 적용성\n\n"
        "백테스트 순위가 아닙니다. 백테스트 성과는 종목·기간에 따라 크게 달라집니다."
    )

try:
    name, results, price, source = load(symbol, years, fee, vb_k)
except Exception as e:
    st.error(f"데이터를 불러올 수 없습니다: {e}")
    st.stop()

last = price.iloc[-1]
bh_total = (price["Close"].iloc[-1] / price["Close"].iloc[0] - 1) * 100
st.subheader(f"{name} · 현재가 {last['Close']:,.2f}")
st.caption(
    f"분석 기간 {price.index[0]:%Y.%m.%d} ~ {price.index[-1]:%Y.%m.%d} "
    f"({years}년) · 같은 기간 단순보유 {bh_total:+.1f}% · "
    f"왕복 비용 {fee * 100:.2f}% 반영 · 출처: {source}"
)
if symbol in ("KOSPI", "KOSDAQ"):
    st.info("지수를 선택하면 'KOSPI 대비 상대강도' 조건(미너비니·듀얼 모멘텀)은 "
            "자기 자신과 비교하게 되어 충족되지 않습니다.")

# --- 요약표 ---------------------------------------------------------------
rows = []
for i, (meta, r) in enumerate(zip(stg.STRATEGIES, results), 1):
    s = r.stats
    rows.append({
        "순위": i,
        "기법": f"{meta['name']} — {meta['author']}",
        "현재 신호": f"{LEVEL_STYLE[r.level][1]} {r.signal}",
        "총수익률": s.get("총수익률"),
        "CAGR": s.get("연환산(CAGR)"),
        "MDD": s.get("MDD"),
        "거래": s.get("거래횟수"),
        "승률": s.get("승률"),
        "보유비중": s.get("보유비중"),
    })
st.markdown("#### 기법별 현재 신호 · 백테스트 요약")
st.dataframe(
    pd.DataFrame(rows),
    hide_index=True,
    width="stretch",
    column_config={
        "총수익률": st.column_config.NumberColumn(format="%+.1f%%"),
        "CAGR": st.column_config.NumberColumn(format="%+.1f%%"),
        "MDD": st.column_config.NumberColumn(format="%.1f%%"),
        "승률": st.column_config.NumberColumn(format="%.0f%%"),
        "보유비중": st.column_config.NumberColumn(format="%.0f%%",
                                              help="기간 중 주식을 보유한 날의 비율"),
    },
)

# --- 기법별 상세 ----------------------------------------------------------
st.markdown("#### 기법 상세 (적합도 순)")
for i, (meta, r) in enumerate(zip(stg.STRATEGIES, results), 1):
    color, icon = LEVEL_STYLE[r.level]
    with st.expander(f"{i}. {meta['name']} — {meta['author']}　{icon} {r.signal}",
                     expanded=(i == 1)):
        if r.error:
            st.warning(f"계산 오류: {r.error}")
            continue
        left, right = st.columns([2, 3])
        with left:
            st.markdown(f"**실적** · {meta['record']}")
            st.markdown(f"**적합 이유** · {meta['why']}")
            st.markdown("**매매 규칙**\n" + "\n".join(f"- {x}" for x in meta["rules"]))
            st.markdown(
                f'<div style="padding:8px 12px;margin:8px 0;border-radius:8px;'
                f'background:{color}1a;border-left:4px solid {color}">'
                f"<b>현재 신호</b> &nbsp; {icon} {r.signal}</div>",
                unsafe_allow_html=True,
            )
            if r.checklist:
                st.markdown("\n".join(
                    f"- {'✅' if ok else '❌'} {text}" for text, ok in r.checklist))
            for d in r.details:
                st.caption(d)
        with right:
            s = r.stats
            m = st.columns(4)
            m[0].metric("총수익률", pct(s["총수익률"]),
                        delta=f"단순보유 대비 {s['총수익률'] - bh_total:+.1f}%p")
            m[1].metric("CAGR", pct(s["연환산(CAGR)"]))
            m[2].metric("MDD", pct(s["MDD"]))
            m[3].metric("승률", pct(s["승률"], signed=False),
                        help=f"거래 {s['거래횟수']}회")
            if not r.equity.empty:
                st.caption("누적 자산 추이 (시작 = 100) — 전략 vs 단순보유")
                st.line_chart(r.equity, height=240, color=["#5b8def", "#999999"])

# --- 개발이 어려운 기법 ----------------------------------------------------
st.divider()
st.markdown("#### 📖 기법만 소개 — 재무·정성 판단이 필요해 자동화하지 않은 기법 (적합도 순)")
st.caption("전 종목 재무제표 일괄 수집이나 사람의 판단이 필요해 무료 일봉 데이터로는 구현이 어렵습니다.")
for i, (author, title, desc, reason) in enumerate(stg.MANUAL_STRATEGIES, 1):
    st.markdown(f"**{i}. {title} — {author}**  \n{desc}  \n"
                f"<span style='color:#888'>↳ 미구현 사유: {reason}</span>",
                unsafe_allow_html=True)

st.divider()
st.caption(
    "ⓘ 백테스트는 일봉 종가 체결·슬리피지 미반영 등으로 단순화된 결과이며 미래 수익을 보장하지 않습니다. "
    "원저의 기법을 데이터로 구현 가능한 범위에서 근사한 것이므로 투자 참고용으로만 활용하세요."
)
