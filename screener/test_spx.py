# -*- coding: utf-8 -*-
"""spx_validate.py 단위 테스트 (네트워크 없음).

    python screener/test_spx.py

위키백과 표 해석, 시점별 구성종목 되돌리기, 같은 보유 기간 SPY 대비 초과수익,
슬롯 포트폴리오를 손으로 검산한 값과 맞춘다.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spx_validate as S

ok = lambda m: print("  OK  " + m)

# 1) 위키백과 표: 구성종목 표 + 2단 머리글 변경 이력 표
HTML = """
<table><tr><th>Symbol</th><th>Security</th></tr>
<tr><td>AAA</td><td>A</td></tr><tr><td>BRK.B</td><td>B</td></tr>
<tr><td>NEW</td><td>N</td></tr></table>
<table>
<tr><th rowspan="2">Effective Date</th><th colspan="2">Added</th>
<th colspan="2">Removed</th><th rowspan="2">Reason</th></tr>
<tr><th>Ticker</th><th>Security</th><th>Ticker</th><th>Security</th></tr>
<tr><td>March 18, 2024</td><td>NEW</td><td>New</td><td>OLD</td><td>Old</td><td>x</td></tr>
<tr><td>June 22, 2020</td><td>OLD</td><td>Old</td><td>GONE</td><td>Gone</td><td>x</td></tr>
<tr><td>January 5, 2021</td><td></td><td></td><td>BF.B</td><td>bf</td><td>x</td></tr>
<tr><td>not a date</td><td>ZZZ</td><td>z</td><td></td><td></td><td>x</td></tr>
</table>"""
cur, ch = S.parse_wiki(HTML)
assert cur == ["AAA", "BRK-B", "NEW"]
assert ch == [{"date": "2020-06-22", "added": "OLD", "removed": "GONE"},
              {"date": "2021-01-05", "added": None, "removed": "BF-B"},
              {"date": "2024-03-18", "added": "NEW", "removed": "OLD"}]
# 둘째 머리글 줄이 데이터로 읽히는 모양 (thead 에 첫 줄만 있음)
HTML2 = """
<table><thead><tr><th>Symbol</th><th>Security</th></tr></thead>
<tbody><tr><td>AAA</td><td>A</td></tr></tbody></table>
<table><thead><tr><th>Effective date</th><th colspan="2">Added</th>
<th colspan="2">Removed</th><th>Reason</th></tr></thead><tbody>
<tr><th></th><th>Ticker</th><th>Security</th><th>Ticker</th><th>Security</th><th></th></tr>
<tr><td>March 18, 2024</td><td>AAA</td><td>A</td><td>OLD</td><td>Old</td><td>x</td></tr>
</tbody></table>"""
cur2, ch2 = S.parse_wiki(HTML2)
assert cur2 == ["AAA"]
assert ch2 == [{"date": "2024-03-18", "added": "AAA", "removed": "OLD"}], ch2
try:
    S.parse_wiki("<table><tr><th>Foo</th></tr><tr><td>1</td></tr></table>")
    raise AssertionError("표가 없으면 실패해야 한다")
except ValueError as e:
    assert "Foo" in str(e)                                 # 진단용으로 열 이름을 보여준다
ok("parse_wiki: 2단 머리글 · 한 줄 머리글(둘째 줄이 데이터) · 날짜 정렬 · "
   "'.'→'-' · 빈 칸과 날짜 아닌 행 제외 · 실패 시 열 이름 출력")

# 2) 시점별 구성종목: 변경일에는 이미 바뀐 상태
assert S.members_at(cur, ch, "2024-03-18") == {"AAA", "BRK-B", "NEW"}
assert S.members_at(cur, ch, "2024-03-17") == {"AAA", "BRK-B", "OLD"}
assert S.members_at(cur, ch, "2021-01-04") == {"AAA", "BRK-B", "OLD", "BF-B"}
assert S.members_at(cur, ch, "2020-06-21") == {"AAA", "BRK-B", "GONE", "BF-B"}
assert S.all_members({"current": cur, "changes": ch}, "2020-01-01") == \
    {"AAA", "BRK-B", "NEW", "OLD", "GONE", "BF-B"}
assert S.all_members({"current": cur, "changes": ch}, "2022-01-01") == \
    {"AAA", "BRK-B", "NEW", "OLD"}
ok("members_at: 변경일 이후 변경을 거꾸로 되돌림 · all_members: 기간 중 한 번이라도")

# 3) SPY 대비 초과수익 — 시가 체결 청산이면 SPY 도 시가, 아니면 종가
days = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
spy = S.Spy(pd.DataFrame({"Open": [100.0, 101.0, 102.0, 103.0],
                          "Close": [100.5, 101.5, 102.5, 104.0]},
                         index=pd.to_datetime(days)))
assert spy.at("2024-01-03", "open") == 101.0
assert spy.at("2024-01-06", "open") == 104.0          # 휴장일 → 직전 종가
tr = S.add_excess([
    {"ticker": "X", "entry_date": "2024-01-02", "exit_date": "2024-01-04",
     "ret": 0.10, "reason": "고가 ≥ 익절선", "cohort": "2024-01-01"},
    {"ticker": "Y", "entry_date": "2024-01-03", "exit_date": "2024-01-05",
     "ret": -0.08, "reason": "갭하락 시가 체결", "cohort": "2024-01-01"},
    {"ticker": "Z", "status": "측정 불가"}], spy)
assert len(tr) == 2
assert abs(tr[0]["spy_ret"] - 0.025) < 1e-12               # 100 시가 → 102.5 종가
assert abs(tr[1]["spy_ret"] - (103 / 101 - 1)) < 1e-12     # 101 시가 → 103 시가
net = 1.10 * (1 - S.COST) / (1 + S.COST) - 1
assert abs(tr[0]["excess"] - (net - 0.025)) < 1e-12
ok("add_excess: 같은 보유 기간 SPY · 시가/종가 청산 구분 · 비용 편도 5bp")

# 4) 포트폴리오: 칸 2개, 손으로 계산
rows = lambda cl: [{"date": d, "open": c, "high": c, "low": c, "close": c}
                   for d, c in zip(days, cl)]
ohlc = {"A": rows([10, 10, 12, 12]), "B": rows([20, 20, 20, 20]),
        "C": rows([5, 5, 5, 5])}
index = {k: days for k in ohlc}
T = lambda t, e, x, xd, st="익절": {"ticker": t, "entry_date": e, "entry": ohlc[t][0]["open"],
                                    "exit": x, "exit_date": xd, "status": st,
                                    "cohort": "2024-01-01"}
trades = [T("A", "2024-01-02", 12.0, "2024-01-04"),
          T("B", "2024-01-02", 20.0, "2024-01-05", "보유"),
          T("C", "2024-01-03", 5.0, "2024-01-05")]          # 칸이 차서 건너뜀
p = S.portfolio(trades, ohlc, index, spy, "2024-01-02", "2024-01-05", slots=2)
assert p["taken"] == 2 and p["skipped"] == 1
ca = 0.5 / (10 * (1 + S.COST))                             # A 주식 수
cb = 0.5 / (20 * (1 + S.COST))
end = ca * 12 * (1 - S.COST) + cb * 20                     # A 청산 + B 평가
assert abs(p["total"] - (end - 1)) < 1e-12
assert abs(p["spy_total"] - (104 / 100 - 1)) < 1e-12
assert p["mdd"] <= 0 and p["spy_mdd"] == 0.0
# 같은 날 청산된 칸은 그날 진입에 쓸 수 있다
trades2 = [T("A", "2024-01-02", 12.0, "2024-01-04"),
           T("B", "2024-01-02", 20.0, "2024-01-05", "보유"),
           dict(T("C", "2024-01-04", 5.0, "2024-01-05"), entry=5.0)]
p2 = S.portfolio(trades2, ohlc, index, spy, "2024-01-02", "2024-01-05", slots=2)
assert p2["taken"] == 3 and p2["skipped"] == 0
assert S.portfolio(trades, ohlc, index, spy, "2024-01-02", "2024-01-02") == {}
ok("portfolio: 칸 크기·건너뛰기·청산 후 재사용·보유 평가·SPY 보유 비교")

# 5) 판정
def X(tkr, e, cohort):
    return {"ticker": tkr, "excess": e, "spy_ret": 0.01, "cohort": cohort}


good = [X(f"G{i}", 0.02 + (i % 3) * 0.001, f"{2019 + i % 4}-03-01") for i in range(40)]
port = {"cagr": 0.2, "spy_cagr": 0.1}
ev = S.evaluate(good, port, 0.99)
assert ev["passed"] and ev["years_pos"] == 4 and ev["win_vs_spy"] == 1.0
ev = S.evaluate(good, {"cagr": 0.05, "spy_cagr": 0.1}, 0.99)
assert ev["checks"]["portfolio"] is False and not ev["passed"]
bad = [X(f"B{i}", -0.01, "2019-03-01") for i in range(30)] + [X("BIG", 3.0, "2020-03-01")]
ev = S.evaluate(bad, port, 0.99)
assert ev["checks"]["positive"] and not ev["checks"]["survives_top2"]
assert ev["top2_dropped"] == ["B0", "BIG"]                 # 동점은 티커 순
assert S.evaluate([], {}, 0.99)["passed"] is False
ok("evaluate: 5개 기준 · 한 종목 의존 · 포트폴리오가 SPY 에 지면 탈락")

print("\n  전체 통과")
