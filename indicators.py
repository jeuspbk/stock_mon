"""주식 시황 모니터링 - 데이터 수집 및 지표 계산 모듈.

데이터 소스 (무료):
  - KOSPI/KOSDAQ 이격도, USD/KRW 환율: 네이버 금융 (야후보다 최신, 실패 시 야후 폴백)
  - 그 외 매크로·세계지수: Yahoo Finance (yfinance), 약 15~20분 지연 시세
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass, field

import pandas as pd
import yfinance as yf

# ---------------------------------------------------------------------------
# 지표 정의
# ---------------------------------------------------------------------------

# 단일 시세 지표: (표시이름, 야후 티커, 단위)
MACRO = [
    ("미국채 10년물", "^TNX", "%"),
    ("달러인덱스 (DXY)", "DX-Y.NYB", ""),
    ("USD/KRW 환율", "KRW=X", "원"),
    ("필라델피아 반도체 (SOX)", "^SOX", ""),
]

# 세계 주요 지수: 지역별 그룹
WORLD_INDICES = {
    "미국": [
        ("S&P 500", "^GSPC"),
        ("나스닥", "^IXIC"),
        ("다우존스", "^DJI"),
    ],
    "아시아": [
        ("일본 닛케이225", "^N225"),
        ("중국 상하이종합", "000001.SS"),
        ("홍콩 항셍", "^HSI"),
    ],
    "유럽": [
        ("독일 DAX", "^GDAXI"),
        ("영국 FTSE100", "^FTSE"),
        ("유로스톡스50", "^STOXX50E"),
    ],
}

# 이격도 대상 지수
DISPARITY_TARGETS = [
    ("KOSPI", "^KS11"),
    ("KOSDAQ", "^KQ11"),
]

# 이격도 지수는 네이버 금융을 1차 소스로 사용 (야후보다 하루 더 최신).
# {야후 티커: 네이버 지수 코드}
NAVER_INDEX_CODE = {
    "^KS11": "KOSPI",
    "^KQ11": "KOSDAQ",
}

# 환율도 네이버 금융을 1차 소스로 사용.
# {야후 티커: 네이버 환율 코드}
NAVER_FX_CODE = {
    "KRW=X": "FX_USDKRW",
}

# 해외지수(달러인덱스·SOX·세계지수)도 네이버를 1차 소스로 사용.
# 네이버 /chart/foreign/index/{reuters} 엔드포인트로 일별 종가 수집.
# 미국채 10년물(^TNX)은 네이버 미제공 → 야후 유지.
# {야후 티커: 네이버 reuters 코드}
NAVER_WORLD_CODE = {
    "DX-Y.NYB": ".DXY",
    "^SOX": ".SOX",
    "^GSPC": ".INX",
    "^IXIC": ".IXIC",
    "^DJI": ".DJI",
    "^N225": ".N225",
    "000001.SS": ".SSEC",
    "^HSI": ".HSI",
    "^GDAXI": ".GDAXI",
    "^FTSE": ".FTSE",
    "^STOXX50E": ".STOXX50E",
}

# 매크로 임계치 경고 (지정값 이상이면 경고). {야후 티커: (표시이름, 임계치)}
MACRO_ALERTS = {
    "^TNX": ("미국채 10년물", 4.5),
    "DX-Y.NYB": ("달러인덱스 (DXY)", 100.5),
}

# 이격도 이동평균 기간
MA_PERIODS = [20, 50]

# 이격도 경고 임계치 — 이동평균 기간별로 다르게 적용 (100 = 이동평균선과 일치)
#   overheat: 이상이면 과열 / oversold: 이하이면 과매도
DISPARITY_THRESHOLDS = {
    20: {"overheat": 113.0, "oversold": 87.0},
    50: {"overheat": 130.0, "oversold": 70.0},
}
# 정의되지 않은 기간에 대한 기본값
DISPARITY_DEFAULT = {"overheat": 105.0, "oversold": 95.0}


# ---------------------------------------------------------------------------
# 데이터 모델
# ---------------------------------------------------------------------------

@dataclass
class Quote:
    name: str
    ticker: str
    unit: str = ""
    price: float | None = None
    change: float | None = None       # 전일 대비 절대 변화
    change_pct: float | None = None   # 전일 대비 %
    error: str | None = None


@dataclass
class Disparity:
    name: str
    ticker: str
    price: float | None = None
    values: dict[int, float] = field(default_factory=dict)  # 기간 -> 이격도
    error: str | None = None


@dataclass
class IndexTrading:
    """지수 거래량·거래대금 현황 + 봉차트용 시계열."""
    name: str
    volume: float | None = None        # 최신 거래량 (백만주)
    volume_pct: float | None = None    # 거래량 전일 대비 %
    value: float | None = None         # 최신 거래대금 (조원)
    value_pct: float | None = None     # 거래대금 전일 대비 %
    df: pd.DataFrame = field(default_factory=pd.DataFrame)  # 컬럼: 거래량/거래대금
    error: str | None = None


# ---------------------------------------------------------------------------
# 수집 로직
# ---------------------------------------------------------------------------

def _extract_close(data, ticker: str, multi: bool) -> pd.Series:
    try:
        series = data[ticker]["Close"] if multi else data["Close"]
        return series.dropna()
    except (KeyError, TypeError):
        return pd.Series(dtype=float)


def _download(tickers: list[str], period: str = "6mo") -> dict[str, pd.Series]:
    """여러 티커의 일봉 종가를 받아 {티커: Series} 로 반환.

    yfinance 캐시 잠금 등으로 일부 티커가 비면 개별 재시도한다.
    """
    data = yf.download(
        tickers,
        period=period,
        interval="1d",
        auto_adjust=False,
        progress=False,
        group_by="ticker",
        threads=False,
    )
    multi = len(tickers) > 1
    closes = {t: _extract_close(data, t, multi) for t in tickers}

    # 빈 결과는 개별 재시도 (캐시 잠금/일시 오류 대비)
    for t in tickers:
        if closes[t].empty:
            try:
                single = yf.download(
                    t, period=period, interval="1d",
                    auto_adjust=False, progress=False, threads=False,
                )
                closes[t] = _extract_close(single, t, multi=False)
            except Exception:
                pass
    return closes


def _fetch_naver_index(code: str, rows: int = 300) -> pd.Series:
    """네이버 금융에서 지수 일봉 종가를 받아 Series(index=날짜, 오름차순)로 반환.

    code: 'KOSPI' / 'KOSDAQ' 등 네이버 지수 코드.
    """
    out: dict[pd.Timestamp, float] = {}
    page = 1
    while len(out) < rows and page <= 20:
        url = (
            f"https://m.stock.naver.com/api/index/{code}/price"
            f"?pageSize=50&page={page}"  # 네이버는 pageSize 최대 50
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not data:
            break
        for d in data:
            dt = pd.Timestamp(d["localTradedAt"])
            close = float(str(d["closePrice"]).replace(",", ""))
            out[dt] = close
        page += 1
    return pd.Series(out).sort_index()


def _fetch_naver_exchange(code: str, rows: int = 10) -> pd.Series:
    """네이버 금융에서 환율 일별 종가를 받아 Series(index=날짜, 오름차순)로 반환.

    code: 'FX_USDKRW' 등 네이버 환율 코드.
    """
    out: dict[pd.Timestamp, float] = {}
    page = 1
    while len(out) < rows and page <= 10:
        url = (
            f"https://api.stock.naver.com/marketindex/exchange/{code}/prices"
            f"?page={page}&pageSize=10"  # pageSize 최소 10
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not data:
            break
        for d in data:
            dt = pd.Timestamp(d["localTradedAt"])
            out[dt] = float(str(d["closePrice"]).replace(",", ""))
        page += 1
    return pd.Series(out).sort_index()


def _fetch_naver_world(code: str) -> pd.Series:
    """네이버 금융 해외지수 일별 종가를 Series(index=날짜, 오름차순)로 반환.

    code: '.DXY', '.SOX', '.INX' 등 reuters 코드.
    """
    url = (
        f"https://api.stock.naver.com/chart/foreign/index/{code}"
        "?periodType=dayCandle"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    out: dict[pd.Timestamp, float] = {}
    for d in data.get("priceInfos", []):
        dt = pd.Timestamp(str(d["localDate"]))  # "20260616"
        out[dt] = float(d["closePrice"])
    return pd.Series(out).sort_index()


def _world_close(ticker: str, period: str = "6mo") -> pd.Series:
    """해외지수 종가를 네이버에서 받고, 실패 시 야후로 폴백."""
    code = NAVER_WORLD_CODE.get(ticker)
    if code:
        try:
            s = _fetch_naver_world(code).dropna()
            if not s.empty:
                return s
        except Exception:
            pass
    s = _download([ticker], period=period).get(ticker, pd.Series(dtype=float))
    if isinstance(s, pd.DataFrame):
        s = s.squeeze("columns")
    return s.dropna()


def macro_alert(ticker: str, price: float | None) -> str | None:
    """매크로 임계치 초과 시 경고 메시지를 반환, 아니면 None."""
    cfg = MACRO_ALERTS.get(ticker)
    if cfg is None or price is None:
        return None
    name, threshold = cfg
    if price >= threshold:
        return f"{name} {price:.2f} ≥ {threshold} 경고"
    return None


def _disparity_closes() -> dict[str, pd.Series]:
    """KOSPI/KOSDAQ 종가를 네이버에서 수집. 실패한 종목만 야후로 폴백."""
    closes: dict[str, pd.Series] = {}
    failed: list[str] = []
    for _, ticker in DISPARITY_TARGETS:
        code = NAVER_INDEX_CODE.get(ticker)
        series = pd.Series(dtype=float)
        if code:
            try:
                series = _fetch_naver_index(code)
            except Exception:
                series = pd.Series(dtype=float)
        series = series.dropna()
        closes[ticker] = series
        if series.empty:
            failed.append(ticker)

    if failed:  # 네이버 실패분만 야후로 보강
        fb = _download(failed, period="1y")
        for t in failed:
            s = fb.get(t, pd.Series(dtype=float))
            if isinstance(s, pd.DataFrame):
                s = s.squeeze("columns")
            closes[t] = s.dropna()
    return closes


def _last_two(series: pd.Series) -> tuple[float | None, float | None]:
    s = series.dropna()
    if len(s) >= 2:
        return float(s.iloc[-1]), float(s.iloc[-2])
    if len(s) == 1:
        return float(s.iloc[-1]), None
    return None, None


def fetch_quotes() -> list[Quote]:
    """매크로 + 세계지수 단일 시세를 수집."""
    flat: list[tuple[str, str, str]] = list(MACRO)
    for region, items in WORLD_INDICES.items():
        for name, ticker in items:
            flat.append((f"{region} · {name}", ticker, ""))

    tickers = [t for _, t, _ in flat]
    closes = _download(tickers, period="1mo")

    # 환율은 네이버를 1차 소스로 덮어쓰기 (실패 시 야후 값 유지)
    for tkr, code in NAVER_FX_CODE.items():
        if tkr not in tickers:
            continue
        try:
            s = _fetch_naver_exchange(code).dropna()
            if not s.empty:
                closes[tkr] = s
        except Exception:
            pass

    # 해외지수(DXY·SOX·세계지수)도 네이버로 덮어쓰기 (실패 시 야후 유지)
    for tkr, code in NAVER_WORLD_CODE.items():
        if tkr not in tickers:
            continue
        try:
            s = _fetch_naver_world(code).dropna()
            if not s.empty:
                closes[tkr] = s
        except Exception:
            pass

    quotes: list[Quote] = []
    for name, ticker, unit in flat:
        series = closes.get(ticker, pd.Series(dtype=float))
        last, prev = _last_two(series)
        q = Quote(name=name, ticker=ticker, unit=unit)
        if last is None:
            q.error = "데이터 없음"
        else:
            q.price = last
            if prev is not None:
                q.change = last - prev
                q.change_pct = (last - prev) / prev * 100 if prev else None
        quotes.append(q)
    return quotes


def fetch_disparities() -> list[Disparity]:
    """KOSPI/KOSDAQ 이격도(표준 공식)를 계산.

    이격도 = (종가 / N일 단순이동평균) × 100
    """
    closes = _disparity_closes()

    results: list[Disparity] = []
    for name, ticker in DISPARITY_TARGETS:
        series = closes.get(ticker, pd.Series(dtype=float)).dropna()
        d = Disparity(name=name, ticker=ticker)
        if series.empty:
            d.error = "데이터 없음"
            results.append(d)
            continue
        price = float(series.iloc[-1])
        d.price = price
        for period in MA_PERIODS:
            if len(series) >= period:
                ma = float(series.tail(period).mean())
                d.values[period] = price / ma * 100 if ma else None
            else:
                d.values[period] = None
        results.append(d)
    return results


def fetch_disparity_series(lookback_days: int = 120) -> dict[str, pd.DataFrame]:
    """이격도 추이 차트용 시계열.

    {지수명: DataFrame(index=날짜, 컬럼='20일','50일')} 형태로 반환.
    """
    closes = _disparity_closes()

    out: dict[str, pd.DataFrame] = {}
    for name, ticker in DISPARITY_TARGETS:
        series = closes.get(ticker, pd.Series(dtype=float)).dropna()
        if series.empty:
            out[name] = pd.DataFrame()
            continue
        df = pd.DataFrame(index=series.index)
        for period in MA_PERIODS:
            ma = series.rolling(period).mean()
            df[f"{period}일"] = series / ma * 100
        out[name] = df.dropna().tail(lookback_days)
    return out


def disparity_thresholds(period: int) -> dict[str, float]:
    """해당 이동평균 기간의 과열/과매도 임계치를 반환."""
    return DISPARITY_THRESHOLDS.get(period, DISPARITY_DEFAULT)


def disparity_status(value: float | None, period: int) -> str:
    """이격도 값에 대한 상태 분류: overheat / oversold / normal / na.

    기간(period)별 임계치를 적용한다.
    """
    if value is None:
        return "na"
    th = disparity_thresholds(period)
    if value >= th["overheat"]:
        return "overheat"
    if value <= th["oversold"]:
        return "oversold"
    return "normal"


def fetch_world_series(lookback_days: int = 60) -> dict[str, pd.DataFrame]:
    """세계 주요 지수 추이 차트용 시계열.

    지역별로 각 지수를 시작일=100 으로 리베이스해 상대 추이를 비교할 수 있게 한다.
    {지역: DataFrame(index=날짜, 컬럼=지수명, 값=리베이스 지수)} 형태.
    """
    out: dict[str, pd.DataFrame] = {}
    for region, items in WORLD_INDICES.items():
        cols: dict[str, pd.Series] = {}
        for name, ticker in items:
            s = _world_close(ticker).tail(lookback_days)
            if not s.empty:
                cols[name] = s / s.iloc[0] * 100  # 시작일 = 100
        if cols:
            df = pd.DataFrame(cols).sort_index().ffill()
            out[region] = df
        else:
            out[region] = pd.DataFrame()
    return out


def _fetch_naver_index_trading(code: str, pages: int = 6) -> pd.DataFrame:
    """네이버 일별시세 테이블에서 지수 거래량·거래대금을 수집.

    구버전 페이지(EUC-KR)의 일별시세 표에는 차트 API에 없는 거래대금이 포함된다.
    표 한 행: 날짜 · 체결가 · 전일비 · 등락률 · 거래량(천주) · 거래대금(백만원).

    반환: DataFrame(index=날짜 오름차순, 컬럼=['거래량_천주', '거래대금_백만'])
    """
    rows: dict[pd.Timestamp, tuple[float, float]] = {}
    for page in range(1, pages + 1):
        url = (
            "https://finance.naver.com/sise/sise_index_day.naver"
            f"?code={code}&page={page}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("euc-kr", "replace")
        for m in re.finditer(r"(\d{4}\.\d{2}\.\d{2})</td>(.*?)</tr>", html, re.S):
            date = m.group(1)
            cells = re.findall(r"<td[^>]*>(.*?)</td>", m.group(2), re.S)
            nums = [re.sub(r"<[^>]+>|[\s,%+]", "", c) for c in cells]
            nums = [n for n in nums if re.match(r"^-?\d+\.?\d*$", n)]
            if len(nums) >= 5:  # 체결가·전일비·등락률·거래량·거래대금
                rows[pd.Timestamp(date.replace(".", "-"))] = (
                    float(nums[-2]), float(nums[-1])
                )
    df = pd.DataFrame.from_dict(
        rows, orient="index", columns=["거래량_천주", "거래대금_백만"]
    )
    return df.sort_index()


def fetch_index_trading(lookback_days: int = 30) -> list[IndexTrading]:
    """KOSPI/KOSDAQ 거래량·거래대금 현황과 봉차트용 시계열을 수집.

    거래량은 백만주, 거래대금은 조원 단위로 환산한다.
    """
    results: list[IndexTrading] = []
    for name, ticker in DISPARITY_TARGETS:
        code = NAVER_INDEX_CODE.get(ticker, name)
        t = IndexTrading(name=name)
        try:
            raw = _fetch_naver_index_trading(code)
        except Exception:
            raw = pd.DataFrame()
        if raw.empty:
            t.error = "데이터 없음"
            results.append(t)
            continue

        df = pd.DataFrame(index=raw.index)
        df["거래량(백만주)"] = raw["거래량_천주"] / 1_000        # 천주 → 백만주
        df["거래대금(조원)"] = raw["거래대금_백만"] / 1_000_000   # 백만원 → 조원
        t.df = df.tail(lookback_days)

        vol, vol_prev = _last_two(df["거래량(백만주)"])
        val, val_prev = _last_two(df["거래대금(조원)"])
        t.volume, t.value = vol, val
        if vol is not None and vol_prev:
            t.volume_pct = (vol - vol_prev) / vol_prev * 100
        if val is not None and val_prev:
            t.value_pct = (val - val_prev) / val_prev * 100
        results.append(t)
    return results


def fetch_all() -> dict:
    """대시보드용 전체 수집."""
    return {
        "quotes": fetch_quotes(),
        "disparities": fetch_disparities(),
        "trading": fetch_index_trading(),
    }
