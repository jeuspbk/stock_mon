# 📈 주식 시황 모니터링

매크로 지표와 KOSPI/KOSDAQ 이격도를 한눈에 보는 웹 대시보드입니다.
무료 데이터를 사용하며(KOSPI/KOSDAQ·USD/KRW는 네이버 금융, 그 외는 Yahoo Finance),
새로고침 시점 기준의 스냅샷을 보여줍니다.

## 모니터링 지표

| 구분 | 항목 |
|------|------|
| 매크로 | 미국채 10년물, 달러인덱스(DXY), USD/KRW, 필라델피아 반도체(SOX) |
| 세계지수 | 미국(S&P500·나스닥·다우), 아시아(닛케이·상하이·항셍), 유럽(DAX·FTSE100·유로스톡스50) |
| 이격도 | KOSPI / KOSDAQ 의 **20일선·50일선 이격도** |

**이격도 = (종가 / N일 이동평균) × 100**
- 과열: 105 이상 (🔴 빨강)
- 과매도: 95 이하 (🔵 파랑)
- 중립: 그 사이 (🟢 녹색)

> 임계치·기간·지수 구성은 `indicators.py` 상단 상수에서 조정할 수 있습니다.

## 설치

```bash
pip install -r requirements.txt
```

## 실행

```bash
streamlit run app.py
```

브라우저에서 자동으로 `http://localhost:8501` 이 열립니다.

### 기능
- **수동/자동 갱신** — 사이드바에서 자동 갱신을 켜고 주기(30·60·120·300초)를 선택하거나, **🔄 지금 새로고침**으로 즉시 갱신
- **경고 배너** — 이격도가 과열(≥105)·과매도(≤95) 임계치를 넘으면 화면 상단에 배너로 표시
- **이격도 추이 차트** — KOSPI/KOSDAQ 20일·50일 이격도의 최근 120거래일 추이를 라인 차트로

## 구성

- `indicators.py` — 데이터 수집(yfinance) 및 이격도 계산
- `app.py` — Streamlit 대시보드 UI
- `requirements.txt` — 의존성

## 모바일/외부 접속 — Streamlit Community Cloud 배포

GitHub 저장소에 올린 뒤 무료 클라우드에 배포하면 안드로이드 폰 등 어디서나 접속할 수 있습니다.

1. **GitHub에 저장소 생성** 후 이 코드를 push (아래 "배포 절차" 참고)
2. <https://share.streamlit.io> 접속 → GitHub 계정으로 로그인
3. **New app** → 저장소·브랜치(main)·`app.py` 선택 → **Deploy**
4. 몇 분 뒤 `https://<앱이름>.streamlit.app` 공개 URL 발급 → 폰 브라우저에서 접속

### 배포 절차 (로컬 → GitHub)

```bash
# (이미 git init·commit 되어 있음)
git remote add origin https://github.com/<사용자명>/stock-mon.git
git branch -M main
git push -u origin main
```

> ⚠️ 참고: 클라우드 서버는 해외에 있어 **네이버 금융 API가 차단·실패할 수 있습니다.**
> 이 경우 코드가 자동으로 Yahoo Finance로 폴백하므로 앱은 정상 동작하지만,
> KOSPI/KOSDAQ·환율의 최신성이 야후 기준(약 1일 지연)으로 떨어질 수 있습니다.

## 참고

- KOSPI/KOSDAQ·환율·세계지수·DXY·SOX는 네이버 금융, 미국채 10년물은 Yahoo Finance 기준입니다.
- 결과는 5분간 캐시됩니다(`@st.cache_data(ttl=300)`).
- 본 정보는 투자 참고용이며 정확성을 보장하지 않습니다.
