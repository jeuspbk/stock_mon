"""유명 투자자 매매 기법 - 신호 계산 및 일봉 백테스트 모듈.

데이터 소스: 네이버 금융 차트 API (종목·지수 일봉 OHLCV, 무료)

백테스트 공통 가정:
  - 신호는 당일 종가까지의 데이터로 판단하고, 손익은 다음 거래일부터 반영 (미래 참조 방지)
  - 손절/익절·청산은 종가 체결로 단순화 (변동성 돌파만 목표가 매수·익일 시가 매도)
  - 거래비용은 왕복 fee(기본 0.25%, 수수료+매도세)를 진입·청산 시 절반씩 차감
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

import pandas as pd

TRADING_DAYS = 252


# ---------------------------------------------------------------------------
# 데이터 수집
# ---------------------------------------------------------------------------

def fetch_ohlcv(symbol: str, count: int = 1000) -> tuple[str, pd.DataFrame]:
    """네이버 차트 API에서 일봉 OHLCV 수집.

    symbol: 종목코드('005930') 또는 지수('KOSPI'/'KOSDAQ').
    반환: (종목명, DataFrame[Open, High, Low, Close, Volume], index=날짜 오름차순)
    """
    url = (
        "https://fchart.stock.naver.com/sise.nhn"
        f"?symbol={symbol}&timeframe=day&count={count}&requestType=0"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        xml = resp.read().decode("euc-kr", "replace")
    m = re.search(r'<chartdata[^>]*name="([^"]*)"', xml)
    name = m.group(1) if m else symbol
    rows = []
    for item in re.findall(r'<item data="([^"]+)"', xml):
        d, o, h, lo, c, v = item.split("|")
        rows.append((pd.Timestamp(d), float(o), float(h), float(lo),
                     float(c), float(v)))
    df = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    df = df.set_index("Date").sort_index()
    # 거래정지 등으로 시가 0 이 들어오는 행 보정
    df["Open"] = df["Open"].where(df["Open"] > 0, df["Close"])
    return name, df


def search_stocks(query: str, limit: int = 10) -> list[tuple[str, str, str]]:
    """네이버 자동완성으로 국내 종목 검색. 반환: [(코드, 이름, 시장)]"""
    url = ("https://ac.stock.naver.com/ac?target=stock&q="
           + urllib.parse.quote(query))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    out = []
    for it in data.get("items", []):
        if it.get("nationCode") == "KOR" and it.get("category") == "stock":
            out.append((it["code"], it["name"], it.get("typeName", "")))
    return out[:limit]


# ---------------------------------------------------------------------------
# 지표 · 백테스트 헬퍼
# ---------------------------------------------------------------------------

def _rsi(close: pd.Series, n: int) -> pd.Series:
    diff = close.diff()
    up = diff.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    down = (-diff.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / down.replace(0, 1e-12))


def _atr(df: pd.DataFrame, n: int = 20) -> pd.Series:
    prev = df["Close"].shift(1)
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - prev).abs(),
                    (df["Low"] - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def _stateful(entry: pd.Series, exit_: pd.Series) -> pd.Series:
    """진입/청산 조건으로 보유 여부(0/1) 시리즈 생성 (청산 우선)."""
    pos, held = [], 0
    for e, x in zip(entry.fillna(False), exit_.fillna(False)):
        if held and x:
            held = 0
        elif not held and e:
            held = 1
        pos.append(held)
    return pd.Series(pos, index=entry.index, dtype=float)


def _stats(ret: pd.Series, trades: list[float]) -> dict:
    eq = (1 + ret).cumprod()
    years = max(len(ret) / TRADING_DAYS, 1e-9)
    total = float(eq.iloc[-1] - 1) if len(eq) else 0.0
    return {
        "총수익률": total * 100,
        "연환산(CAGR)": ((1 + total) ** (1 / years) - 1) * 100 if total > -1 else -100.0,
        "MDD": float((eq / eq.cummax() - 1).min() * 100) if len(eq) else 0.0,
        "거래횟수": len(trades),
        "승률": (sum(t > 0 for t in trades) / len(trades) * 100) if trades else None,
        "보유비중": None,
    }


def _backtest_position(df: pd.DataFrame, pos: pd.Series, fee: float, start):
    """보유 시리즈(당일 종가 기준 판단) → 일별 전략수익률, 거래별 수익률."""
    daily = df["Close"].pct_change().fillna(0)
    held = pos.shift(1).fillna(0)           # 다음 날부터 손익 반영
    change = held.diff().abs().fillna(held)
    ret = held * daily - change * fee / 2
    ret, held = ret.loc[start:], held.loc[start:]
    trades, cur = [], None
    for r, h in zip(ret, held):
        if h:
            cur = (1 + (cur or 0)) * (1 + r) - 1
        elif cur is not None:
            trades.append(cur - fee / 2)    # 청산일 비용 반영
            cur = None
    if cur is not None:
        trades.append(cur)
    st = _stats(ret, trades)
    st["보유비중"] = float(held.mean() * 100) if len(held) else 0.0
    return ret, st


@dataclass
class StrategyResult:
    key: str
    signal: str                      # 현재 신호 텍스트
    level: str                       # buy / hold / sell / wait
    details: list[str] = field(default_factory=list)
    checklist: list[tuple[str, bool]] = field(default_factory=list)
    equity: pd.DataFrame = field(default_factory=pd.DataFrame)
    stats: dict = field(default_factory=dict)
    error: str | None = None


def _finish(key, df, ret, stats, bh_ret, signal, level, details=None,
            checklist=None) -> StrategyResult:
    equity = pd.concat([(1 + ret).cumprod().rename("전략"),
                        (1 + bh_ret).cumprod().rename("단순보유")], axis=1) * 100
    return StrategyResult(key=key, signal=signal, level=level,
                          details=details or [], checklist=checklist or [],
                          equity=equity, stats=stats)


def _pos_signal(pos: pd.Series, entry_today: bool) -> tuple[str, str]:
    if pos.iloc[-1] and entry_today:
        return "매수 신호 (신규 진입)", "buy"
    if pos.iloc[-1]:
        return "보유 유지", "hold"
    if len(pos) > 1 and pos.iloc[-2]:
        return "매도 신호 (청산)", "sell"
    return "관망", "wait"


# ---------------------------------------------------------------------------
# 개별 기법
# ---------------------------------------------------------------------------

def minervini(df, mkt, fee, start):
    """마크 미너비니 트렌드 템플릿 (SEPA) — 8개 조건 모두 충족 시 매수, 50일선 이탈 시 청산."""
    c = df["Close"]
    ma50, ma150, ma200 = (c.rolling(n).mean() for n in (50, 150, 200))
    hi52, lo52 = df["High"].rolling(250).max(), df["Low"].rolling(250).min()
    rs = c.pct_change(250) > mkt.pct_change(250)
    conds = {
        "종가 > 150일선 & 200일선": (c > ma150) & (c > ma200),
        "150일선 > 200일선": ma150 > ma200,
        "200일선 1개월 이상 상승 추세": ma200 > ma200.shift(20),
        "50일선 > 150일선 > 200일선": (ma50 > ma150) & (ma150 > ma200),
        "종가 > 50일선": c > ma50,
        "52주 최저가 대비 +30% 이상": c >= lo52 * 1.30,
        "52주 최고가 대비 -25% 이내": c >= hi52 * 0.75,
        "상대강도: 1년 수익률 > KOSPI": rs,
    }
    allok = pd.concat(conds.values(), axis=1).all(axis=1)
    pos = _stateful(allok, c < ma50)
    ret, stats = _backtest_position(df, pos, fee, start)
    bh = c.pct_change().fillna(0).loc[start:]
    checklist = [(k, bool(v.iloc[-1])) for k, v in conds.items()]
    n_ok = sum(ok for _, ok in checklist)
    sig, lvl = _pos_signal(pos, bool(allok.iloc[-1] and not pos.iloc[-2]))
    return _finish("minervini", df, ret, stats, bh, sig, lvl,
                   [f"트렌드 템플릿 충족 {n_ok}/8",
                    f"50일선 {ma50.iloc[-1]:,.0f} (청산 기준)"], checklist)


def oneil(df, mkt, fee, start, stop=0.08, target=0.25):
    """윌리엄 오닐 CAN SLIM 기술적 요소 — 신고가 돌파 + 거래량 급증 + 시장 상승 추세.

    청산: -8% 손절, +25% 익절, 또는 50일선 이탈.
    """
    c, v = df["Close"], df["Volume"]
    hi = df["High"].rolling(250).max().shift(1)
    vol_avg = v.rolling(50).mean().shift(1)
    mkt_up = (mkt > mkt.rolling(50).mean())
    ma50 = c.rolling(50).mean()
    entry = (c > hi) & (v >= vol_avg * 1.5) & mkt_up
    pos, held, entry_px = [], 0, 0.0
    for dt in df.index:
        px = c[dt]
        if held and (px <= entry_px * (1 - stop) or px >= entry_px * (1 + target)
                     or px < ma50[dt]):
            held = 0
        elif not held and bool(entry[dt]):
            held, entry_px = 1, px
        pos.append(held)
    pos = pd.Series(pos, index=df.index, dtype=float)
    ret, stats = _backtest_position(df, pos, fee, start)
    bh = c.pct_change().fillna(0).loc[start:]
    checklist = [
        ("N: 52주 신고가 돌파", bool(c.iloc[-1] > hi.iloc[-1])),
        ("S: 거래량 50일 평균 1.5배 이상", bool(v.iloc[-1] >= vol_avg.iloc[-1] * 1.5)),
        ("M: KOSPI 50일선 위 (상승장)", bool(mkt_up.iloc[-1])),
    ]
    sig, lvl = _pos_signal(pos, bool(entry.iloc[-1] and not pos.iloc[-2]))
    return _finish("oneil", df, ret, stats, bh, sig, lvl,
                   [f"52주 최고가 {hi.iloc[-1]:,.0f} · 현재가 {c.iloc[-1]:,.0f}",
                    f"손절 -{stop:.0%} · 익절 +{target:.0%} · 50일선 이탈 청산"],
                   checklist)


def turtle(df, mkt, fee, start):
    """터틀 트레이딩 (리처드 데니스) 시스템1 — 20일 고가 돌파 매수, 10일 저가 이탈 또는 2N 손절."""
    c = df["Close"]
    hi20 = df["High"].rolling(20).max().shift(1)
    lo10 = df["Low"].rolling(10).min().shift(1)
    n = _atr(df, 20)
    pos, held, stop_px = [], 0, 0.0
    for dt in df.index:
        px = c[dt]
        if held and (px < lo10[dt] or px < stop_px):
            held = 0
        elif not held and px > hi20[dt]:
            held, stop_px = 1, px - 2 * n[dt]
        pos.append(held)
    pos = pd.Series(pos, index=df.index, dtype=float)
    ret, stats = _backtest_position(df, pos, fee, start)
    bh = c.pct_change().fillna(0).loc[start:]
    sig, lvl = _pos_signal(pos, bool(c.iloc[-1] > hi20.iloc[-1] and not pos.iloc[-2]))
    return _finish("turtle", df, ret, stats, bh, sig, lvl,
                   [f"20일 고가(진입선) {hi20.iloc[-1]:,.0f}",
                    f"10일 저가(청산선) {lo10.iloc[-1]:,.0f}",
                    f"N(ATR20) {n.iloc[-1]:,.1f} → 손절폭 2N = {2 * n.iloc[-1]:,.0f}"])


def dual_momentum(df, mkt, fee, start):
    """게리 안토나치 듀얼 모멘텀 — 12개월 절대·상대(vs KOSPI) 모멘텀 모두 양(+)일 때만 보유, 월 1회 리밸런싱."""
    c = df["Close"]
    r12, m12 = c.pct_change(250), mkt.pct_change(250)
    raw = (r12 > 0) & (r12 > m12)
    month_end = c.index.to_period("M") != c.index.to_series().shift(-1).dt.to_period("M")
    pos = raw.astype(float).where(month_end).ffill().fillna(0)
    ret, stats = _backtest_position(df, pos, fee, start)
    bh = c.pct_change().fillna(0).loc[start:]
    now_ok = bool(raw.iloc[-1])
    sig, lvl = (("보유 유지" if pos.iloc[-1] else "매수 신호 (다음 리밸런싱)"), "hold" if pos.iloc[-1] else "buy") \
        if now_ok else (("매도 신호 (다음 리밸런싱)", "sell") if pos.iloc[-1] else ("관망 (현금)", "wait"))
    checklist = [
        (f"절대 모멘텀: 12개월 수익률 {r12.iloc[-1]:+.1%} > 0", bool(r12.iloc[-1] > 0)),
        (f"상대 모멘텀: KOSPI {m12.iloc[-1]:+.1%} 보다 우위", bool(r12.iloc[-1] > m12.iloc[-1])),
    ]
    return _finish("dual", df, ret, stats, bh, sig, lvl,
                   ["월말 종가 기준 판단, 다음 달 보유"], checklist)


def volatility_breakout(df, mkt, fee, start, k=0.5, ma_filter=True):
    """래리 윌리엄스 변동성 돌파 — 당일 시가 + k×전일 변동폭 돌파 시 매수, 다음날 시가 매도."""
    o, h, c = df["Open"], df["High"], df["Close"]
    rng = (h - df["Low"]).shift(1)
    tgt = o + k * rng
    ok = h >= tgt
    if ma_filter:
        ok &= c.shift(1) > c.rolling(5).mean().shift(1)
    next_open = o.shift(-1)
    trade = (next_open / tgt - 1 - fee).where(ok & next_open.notna(), 0.0)
    ret = trade.loc[start:]
    trades = [t for t, f in zip(ret, ok.loc[start:]) if f]
    stats = _stats(ret, trades)
    stats["보유비중"] = float(ok.loc[start:].mean() * 100)
    bh = c.pct_change().fillna(0).loc[start:]
    hit = bool(h.iloc[-1] >= tgt.iloc[-1])
    filt = (not ma_filter) or bool(c.iloc[-2] > c.rolling(5).mean().iloc[-2])
    if hit and filt:
        sig, lvl = "매수 신호 (오늘 목표가 돌파)", "buy"
    elif not filt:
        sig, lvl = "관망 (이평 필터 미충족)", "wait"
    else:
        sig, lvl = "대기 (목표가 미돌파)", "wait"
    return _finish("vb", df, ret, stats, bh, sig, lvl,
                   [f"오늘 매수 목표가 {tgt.iloc[-1]:,.0f} (시가 {o.iloc[-1]:,.0f} + {k}×{rng.iloc[-1]:,.0f})",
                    f"오늘 고가 {h.iloc[-1]:,.0f} · 현재가 {c.iloc[-1]:,.0f}",
                    "필터: 전일 종가 > 5일선" if ma_filter else "필터 없음"])


def darvas(df, mkt, fee, start):
    """니콜라스 다바스 박스 — 52주 신고가 부근 박스 상단 돌파+거래량 매수, 박스 하단 이탈 청산."""
    c, v = df["Close"], df["Volume"]
    top = df["High"].rolling(20).max().shift(1)
    bottom = df["Low"].rolling(20).min().shift(1)
    hi52 = df["High"].rolling(250).max().shift(1)
    vol_ok = v > v.rolling(20).mean().shift(1)
    entry = (c > top) & (top >= hi52 * 0.9) & vol_ok
    pos, held, stop_px = [], 0, 0.0
    for dt in df.index:
        px = c[dt]
        if held:
            stop_px = max(stop_px, bottom[dt])   # 박스가 올라가면 손절선도 상향
            if px < stop_px:
                held = 0
        elif bool(entry[dt]):
            held, stop_px = 1, bottom[dt]
        pos.append(held)
    pos = pd.Series(pos, index=df.index, dtype=float)
    ret, stats = _backtest_position(df, pos, fee, start)
    bh = c.pct_change().fillna(0).loc[start:]
    sig, lvl = _pos_signal(pos, bool(entry.iloc[-1] and not pos.iloc[-2]))
    return _finish("darvas", df, ret, stats, bh, sig, lvl,
                   [f"박스 상단 {top.iloc[-1]:,.0f} · 하단 {bottom.iloc[-1]:,.0f} (20일)",
                    f"52주 최고가 {hi52.iloc[-1]:,.0f} 의 90% 이상에서만 진입"],
                   [("박스 상단 돌파", bool(c.iloc[-1] > top.iloc[-1])),
                    ("신고가 부근 (52주 고가 90% 이상)", bool(top.iloc[-1] >= hi52.iloc[-1] * 0.9)),
                    ("거래량 20일 평균 이상", bool(vol_ok.iloc[-1]))])


def connors_rsi2(df, mkt, fee, start):
    """래리 코너스 RSI(2) — 200일선 위에서 RSI(2)<10 눌림목 매수, 종가 5일선 회복 시 청산."""
    c = df["Close"]
    rsi2 = _rsi(c, 2)
    ma200, ma5 = c.rolling(200).mean(), c.rolling(5).mean()
    entry = (c > ma200) & (rsi2 < 10)
    pos = _stateful(entry, c > ma5)
    ret, stats = _backtest_position(df, pos, fee, start)
    bh = c.pct_change().fillna(0).loc[start:]
    sig, lvl = _pos_signal(pos, bool(entry.iloc[-1] and not pos.iloc[-2]))
    return _finish("rsi2", df, ret, stats, bh, sig, lvl,
                   [f"RSI(2) {rsi2.iloc[-1]:.1f} (10 미만 매수)",
                    f"200일선 {ma200.iloc[-1]:,.0f} · 5일선 {ma5.iloc[-1]:,.0f}"],
                   [("종가 > 200일선 (장기 상승)", bool(c.iloc[-1] > ma200.iloc[-1])),
                    ("RSI(2) < 10 (단기 과매도)", bool(rsi2.iloc[-1] < 10))])


def bollinger(df, mkt, fee, start):
    """존 볼린저 밴드 역추세 — 하단밴드(20, 2σ) 이탈 매수, 중심선 회복 시 청산."""
    c = df["Close"]
    mid = c.rolling(20).mean()
    sd = c.rolling(20).std()
    upper, lower = mid + 2 * sd, mid - 2 * sd
    pos = _stateful(c < lower, c > mid)
    ret, stats = _backtest_position(df, pos, fee, start)
    bh = c.pct_change().fillna(0).loc[start:]
    pb = (c.iloc[-1] - lower.iloc[-1]) / (upper.iloc[-1] - lower.iloc[-1])
    sig, lvl = _pos_signal(pos, bool(c.iloc[-1] < lower.iloc[-1] and not pos.iloc[-2]))
    return _finish("bb", df, ret, stats, bh, sig, lvl,
                   [f"상단 {upper.iloc[-1]:,.0f} · 중심 {mid.iloc[-1]:,.0f} · 하단 {lower.iloc[-1]:,.0f}",
                    f"%B = {pb:.2f} (0 이하 매수 구간, 1 이상 과열)"])


def granville(df, mkt, fee, start):
    """그랜빌 이동평균 — 20일선이 60일선 상향 돌파(골든크로스) 매수, 하향 돌파(데드크로스) 매도."""
    c = df["Close"]
    ma20, ma60 = c.rolling(20).mean(), c.rolling(60).mean()
    pos = (ma20 > ma60).astype(float)
    ret, stats = _backtest_position(df, pos, fee, start)
    bh = c.pct_change().fillna(0).loc[start:]
    sig, lvl = _pos_signal(pos, bool(pos.iloc[-1] and not pos.iloc[-2]))
    return _finish("granville", df, ret, stats, bh, sig, lvl,
                   [f"20일선 {ma20.iloc[-1]:,.0f} · 60일선 {ma60.iloc[-1]:,.0f}",
                    "골든크로스 매수 / 데드크로스 매도"])


# ---------------------------------------------------------------------------
# 기법 카탈로그 (적합도 순)
# ---------------------------------------------------------------------------

STRATEGIES = [
    {
        "key": "minervini", "fn": minervini,
        "name": "트렌드 템플릿 (SEPA)", "author": "마크 미너비니",
        "record": "1997 미국 투자대회(USIC) 연 155% 우승, 2021 대회 334%",
        "why": "가격·이평선만으로 완전히 규칙화되어 재현성이 높고, 강한 상승 추세 종목만 거르는 1차 필터로 탁월",
        "rules": [
            "종가가 150일·200일선 위, 150일선 > 200일선",
            "200일선이 최소 1개월 상승 중, 50일선 > 150일선 > 200일선",
            "52주 최저가 대비 +30% 이상, 52주 최고가 대비 -25% 이내",
            "상대강도(RS) 상위 — 여기선 1년 수익률이 KOSPI 보다 높은지로 대체",
            "청산: 종가 50일선 이탈 (원저는 -7~8% 손절 병행)",
        ],
    },
    {
        "key": "oneil", "fn": oneil,
        "name": "CAN SLIM (기술적 요소)", "author": "윌리엄 오닐",
        "record": "IBD 창립자, AAII 백테스트상 장기 최상위권 성장주 전략",
        "why": "신고가 돌파·거래량·시장 방향(N·S·M)은 일봉으로 구현 가능, 손절 원칙(-7~8%)이 명확",
        "rules": [
            "N: 52주 신고가 돌파 (원저는 컵앤핸들 등 베이스 돌파)",
            "S: 돌파일 거래량이 50일 평균의 1.5배 이상",
            "M: 시장(KOSPI)이 50일선 위 상승 추세일 때만",
            "청산: -8% 손절, +25% 익절, 또는 50일선 이탈",
            "C·A(실적 성장)·I(기관 수급)는 재무 데이터 필요 → 미반영",
        ],
    },
    {
        "key": "turtle", "fn": turtle,
        "name": "터틀 트레이딩", "author": "리처드 데니스 · 윌리엄 에크하르트",
        "record": "초보 '터틀' 교육생들이 4년간 1억 달러 이상 수익 (1984~1988)",
        "why": "완전 기계적 추세추종, ATR 기반 손절로 위험 관리가 체계적",
        "rules": [
            "진입: 종가가 직전 20일 최고가 돌파 (시스템1)",
            "청산: 종가가 직전 10일 최저가 이탈",
            "손절: 진입가 - 2N (N = 20일 ATR)",
            "원저는 1N마다 피라미딩, 1% 위험 기반 포지션 사이징 → 여기선 단순 1회 진입",
        ],
    },
    {
        "key": "dual", "fn": dual_momentum,
        "name": "듀얼 모멘텀", "author": "게리 안토나치",
        "record": "1974~2013 백테스트에서 S&P500 대비 높은 수익·절반 수준 MDD",
        "why": "월 1회만 판단하는 저빈도 전략이라 직장인도 실행 쉽고, 하락장 회피 효과가 큼",
        "rules": [
            "절대 모멘텀: 12개월 수익률 > 0 (원저는 > 무위험 수익률)",
            "상대 모멘텀: 12개월 수익률이 KOSPI 보다 높음",
            "둘 다 충족 시 보유, 아니면 현금 — 월말 리밸런싱",
            "원저는 주식(미국/해외)·채권 간 자산배분 → 여기선 단일 종목 vs 시장으로 적용",
        ],
    },
    {
        "key": "vb", "fn": volatility_breakout,
        "name": "변동성 돌파", "author": "래리 윌리엄스",
        "record": "1987 로빈스컵 선물대회 1년 11,376% 수익 우승",
        "why": "하루 보유 단기 전략이라 오버나이트 리스크 적고 한국 개인투자자 자동매매에 널리 쓰임",
        "rules": [
            "목표가 = 당일 시가 + k × (전일 고가 - 전일 저가), k=0.5",
            "장중 목표가 돌파 시 매수, 다음 날 시가 매도",
            "필터: 전일 종가가 5일선 위일 때만 (상승 추세 확인)",
            "일봉 백테스트라 체결 슬리피지 미반영 → 실제보다 낙관적일 수 있음",
        ],
    },
    {
        "key": "darvas", "fn": darvas,
        "name": "박스 이론", "author": "니콜라스 다바스",
        "record": "무용수 출신, 18개월간 3만 달러를 225만 달러로 (1950년대)",
        "why": "신고가 부근 박스 돌파 + 추적 손절 구조가 단순하고 직관적",
        "rules": [
            "박스 = 최근 20일 고가/저가 범위 (원저는 3일 확인 규칙)",
            "52주 신고가 부근(90% 이상)에서 박스 상단을 거래량 동반 돌파 시 매수",
            "손절선 = 박스 하단, 박스가 올라가면 손절선도 따라 상향",
        ],
    },
    {
        "key": "rsi2", "fn": connors_rsi2,
        "name": "RSI(2) 눌림목", "author": "래리 코너스",
        "record": "『Short Term Trading Strategies That Work』, 지수 ETF 대상 높은 승률",
        "why": "승률이 높은 단기 역추세, 추세 필터(200일선)로 하락장 매수 회피 — 개별주는 지수보다 성과 편차 큼",
        "rules": [
            "조건: 종가 > 200일선 (장기 상승 중)",
            "진입: RSI(2) < 10 (단기 급락)",
            "청산: 종가가 5일선 위로 회복",
        ],
    },
    {
        "key": "bb", "fn": bollinger,
        "name": "볼린저 밴드 역추세", "author": "존 볼린저",
        "record": "볼린저 밴드 창시자 (1980년대), 가장 널리 쓰이는 변동성 지표",
        "why": "박스권 장에서 유효하지만 추세장에서는 손실이 커 단독 사용엔 한계",
        "rules": [
            "밴드 = 20일 이동평균 ± 2 표준편차",
            "진입: 종가가 하단 밴드 아래",
            "청산: 종가가 중심선(20일선) 회복",
        ],
    },
    {
        "key": "granville", "fn": granville,
        "name": "골든/데드 크로스", "author": "조셉 그랜빌",
        "record": "이동평균 8법칙, OBV 지표 창시자",
        "why": "가장 단순·대중적이나 신호가 늦고 횡보장 휩소가 잦아 보조 지표로 적합",
        "rules": [
            "20일선이 60일선 상향 돌파(골든크로스) 매수",
            "20일선이 60일선 하향 돌파(데드크로스) 매도",
        ],
    },
]


# 개발이 어려워 기법만 소개 (적합도 순)
MANUAL_STRATEGIES = [
    ("조엘 그린블라트", "마법 공식",
     "ROC(자본수익률) 상위 + 이익수익률(EBIT/EV) 상위 종목 20~30개를 1년 보유·교체",
     "전 종목 재무제표(EBIT·EV·투하자본) 일괄 수집 필요 — 무료 API로는 어려움"),
    ("벤저민 그레이엄", "안전마진 · NCAV",
     "순유동자산(유동자산-총부채)의 2/3 이하 가격에 매수, 저PER·저PBR·꾸준한 배당 분산투자",
     "재무제표 기반 전 종목 스크리닝 필요"),
    ("피터 린치", "PEG · 생활 속 발견",
     "PEG(PER/이익성장률) 1 이하 성장주, 내가 잘 아는 제품·서비스 기업, 6가지 기업 유형 분류",
     "이익성장률 추정·정성 판단 필요"),
    ("워런 버핏", "경제적 해자 가치투자",
     "지속적 경쟁우위(해자)·높은 ROE·정직한 경영진 기업을 내재가치보다 싸게 사서 장기 보유",
     "해자·경영진 평가는 정성적 판단"),
    ("필립 피셔", "성장주 15가지 포인트",
     "매출 성장 잠재력·R&D·영업이익률·경영진 등 15개 항목 '사실 수집(scuttlebutt)' 후 장기 보유",
     "현장 조사 기반의 정성 분석"),
    ("레이 달리오", "올웨더 포트폴리오",
     "주식 30%·장기채 40%·중기채 15%·금 7.5%·원자재 7.5%로 경기 국면과 무관한 위험 균형",
     "여러 자산 ETF 시세 필요 — 종목 신호가 아닌 자산배분이라 별도 화면이 적합"),
    ("존 템플턴", "최대 비관 시점 역발상",
     "'강세장은 비관 속에 태어난다' — 공포가 극에 달한 시장·종목을 분산 매수",
     "심리·뉴스 판단 필요 (대시보드의 이격도 과매도·신용잔고 감소를 참고 지표로 활용 가능)"),
    ("앙드레 코스톨라니", "달걀 모형",
     "금리 사이클 6국면(금리 고점→하락→저점→상승)에 따라 주식·채권·현금 비중 전환",
     "국면 판단이 해석적 — 대시보드의 미국채 10년물 추이를 참고"),
    ("제시 리버모어", "피벗 포인트 · 피라미딩",
     "핵심 가격(피벗) 돌파 확인 후 진입, 수익 날 때만 단계적 추가매수, 손실은 즉시 정리",
     "피벗 판단이 재량적 (터틀·다바스가 이를 규칙화한 형태)"),
    ("조지 소로스", "재귀성 이론",
     "시장 참여자의 인식이 펀더멘털을 바꾸는 자기강화 버블/붕괴 국면을 포착해 큰 베팅",
     "매크로 내러티브 판단 — 정량화 불가"),
]


def run_all(symbol: str, years: int = 3, fee: float = 0.0025,
            vb_k: float = 0.5) -> tuple[str, list[StrategyResult], pd.DataFrame]:
    """선택 종목에 모든 기법을 적용. 반환: (종목명, 결과 목록, OHLCV)."""
    count = years * TRADING_DAYS + 300          # 200·250일 지표 워밍업
    name, df = fetch_ohlcv(symbol, count)
    if len(df) < 300:
        raise ValueError(f"데이터 부족 ({len(df)}일) — 상장 1년 이상 종목만 분석 가능")
    _, mkt_df = fetch_ohlcv("KOSPI", count + 50)
    mkt = mkt_df["Close"].reindex(df.index).ffill()
    start = df.index[max(len(df) - years * TRADING_DAYS, 260)]
    results = []
    for s in STRATEGIES:
        try:
            kw = {"k": vb_k} if s["key"] == "vb" else {}
            results.append(s["fn"](df, mkt, fee, start, **kw))
        except Exception as e:  # 개별 기법 오류가 페이지 전체를 막지 않도록
            results.append(StrategyResult(key=s["key"], signal="오류",
                                          level="wait", error=str(e)))
    return name, results, df.loc[start:]
