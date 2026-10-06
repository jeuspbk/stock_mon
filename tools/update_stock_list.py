"""KRX 상장법인 목록(KIND)으로 data/krx_stocks.csv 를 갱신.

야후 폴백 시 한글 종목명 표시·오프라인 종목 검색용. 신규 상장 반영이 필요할 때 실행:
    python tools/update_stock_list.py
"""

from __future__ import annotations

import csv
import re
import urllib.request
from pathlib import Path

URL = "https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13"
OUT = Path(__file__).resolve().parent.parent / "data" / "krx_stocks.csv"
MARKETS = {"유가": "코스피", "코스닥": "코스닥"}   # 코넥스 제외


def main() -> None:
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("euc-kr", "replace")
    rows = []
    for tr in re.findall(r"<tr>(.*?)</tr>", html, re.S):
        cells = [re.sub(r"<[^>]+>|\s+", " ", c).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) >= 3 and cells[1] in MARKETS and re.fullmatch(r"\w{6}", cells[2]):
            rows.append((cells[2], cells[0], MARKETS[cells[1]]))
    rows.sort()
    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["code", "name", "market"])
        w.writerows(rows)
    print(f"{len(rows)} 종목 → {OUT}")


if __name__ == "__main__":
    main()
