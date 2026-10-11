# -*- coding: utf-8 -*-
"""backtest.py 단위 테스트 (네트워크·git 접근 없음).

    python screener/test_backtest.py

합성 스냅샷으로 청산 규칙 두 가지, 재진입 처리, 생존 편향, 집계를 검산한다.
수치는 손으로 계산할 수 있는 값만 쓴다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backtest as B

ok = lambda m: print("  OK  " + m)


def snap(d, rows):
    """rows: {ticker: (rs, vcp, price)}"""
    return {"date": d, "rows": {t: {"name": t, "rs": r, "vcp": v, "price": p}
                                for t, (r, v, p) in rows.items()}}


# 4일치 창. AAA는 끝까지, BBB는 2일차에 빠지고 4일차에 복귀(재진입),
# CCC는 3일차에 빠져 안 돌아온다, DDD는 진입 다음 날 바로 빠진다.
SNAPS = [
    snap("2026-09-01", {"AAA": (99, 60, 100.0), "BBB": (98, 40, 50.0),
                        "CCC": (97, 20, 10.0), "DDD": (96, 0, 20.0)}),
    snap("2026-09-02", {"AAA": (99, 60, 110.0), "CCC": (97, 20, 11.0)}),
    snap("2026-09-03", {"AAA": (99, 60, 120.0)}),
    snap("2026-09-04", {"AAA": (99, 60, 130.0), "BBB": (98, 40, 80.0)}),
]

# 1) parse_snapshot — 필요한 필드만 뽑고 vcp 누락을 견딘다
p = B.parse_snapshot({"data_date": "2026-09-01", "results": [
    {"ticker": "AAA", "name": "A사", "rs_rank": 99, "price": 10.0,
     "vcp": {"score": 55}},
    {"ticker": "BBB", "rs_rank": 80, "price": 20.0},       # vcp 없음
    {"name": "이름만"},                                     # ticker 없음 → 버린다
]})
assert p["date"] == "2026-09-01"
assert set(p["rows"]) == {"AAA", "BBB"}
assert p["rows"]["AAA"] == {"name": "A사", "rs": 99, "vcp": 55, "price": 10.0}
assert p["rows"]["BBB"]["vcp"] is None and p["rows"]["BBB"]["name"] == "BBB"
assert B.parse_snapshot({"results": []}) is None            # data_date 없음
assert B.parse_snapshot({"data_date": "2026-09-01"}) is None
ok("parse_snapshot: ticker 없는 행 제외, vcp·name 누락 허용")

# 2) 재진입 카운트
dates = [s["date"] for s in SNAPS]
assert B.reentries(dates, {"2026-09-01", "2026-09-02", "2026-09-03",
                           "2026-09-04"}) == 0
assert B.reentries(dates, {"2026-09-01", "2026-09-04"}) == 1   # 빠졌다 복귀
assert B.reentries(dates, {"2026-09-01"}) == 0
ok("reentries: 빠졌다 다시 들어온 횟수")

# 3) gap 규칙 — 처음 빠진 날 매도. BBB는 복귀를 몰라야 한다(사후 정보 금지).
g = {p["ticker"]: p for p in B.positions(SNAPS, "gap")}
assert g["AAA"]["exit_date"] == "2026-09-04" and g["AAA"]["held"] is True
assert g["BBB"]["exit_date"] == "2026-09-02" and g["BBB"]["held"] is False
assert g["CCC"]["exit_date"] == "2026-09-03"
assert g["DDD"]["exit_date"] == "2026-09-02" and g["DDD"]["days"] == 1
assert g["BBB"]["survivor"] is True      # 마지막 스냅샷엔 있다(편향 분모용)
assert g["CCC"]["survivor"] is False
assert g["BBB"]["reentries"] == 1
ok("gap 규칙: 처음 빠진 날 매도 — 뒤의 복귀를 쓰지 않는다")

# 4) last 규칙 — 마지막 등장일 매도. BBB가 복귀일까지 늘어난다.
l = {p["ticker"]: p for p in B.positions(SNAPS, "last")}
assert l["BBB"]["exit_date"] == "2026-09-04" and l["BBB"]["held"] is True
assert l["CCC"]["exit_date"] == "2026-09-02"      # 마지막 등장은 2일차
assert l["DDD"]["exit_date"] == "2026-09-01"      # 진입일 = 유일한 등장일
assert l["DDD"]["days"] == 0
ok("last 규칙: 마지막 등장일 매도 — 두 규칙이 갈리는 지점 확인")

# 5) 정렬이 웹앱과 같다 (RS 내림 → VCP 내림)
assert [p["ticker"] for p in B.positions(SNAPS, "gap")] == \
    ["AAA", "BBB", "CCC", "DDD"]
assert B.rank_key({"rs": 98, "vcp": 70, "ticker": "X"}) < \
    B.rank_key({"rs": 98, "vcp": 40, "ticker": "A"})
ok("rank_key: RS 내림 후 VCP 내림 (screener.py 정렬과 동일)")

# 6) close_at — 그 날이 없으면 직전, 그보다 앞서면 None
s = {"2026-09-01": 10.0, "2026-09-03": 12.0}
assert B.close_at(s, "2026-09-01") == 10.0
assert B.close_at(s, "2026-09-02") == 10.0        # 휴장 → 직전
assert B.close_at(s, "2026-09-09") == 12.0
assert B.close_at(s, "2026-08-31") is None        # 이전 자료 없음
assert B.close_at({}, "2026-09-01") is None
ok("close_at: 휴장·거래정지는 직전 종가, 앞선 자료 없으면 측정 불가")

# 7) 스냅샷 가격 + last 규칙으로 수익률 검산
#    AAA 100→130 = +30%, BBB 50→80 = +60%, CCC 10→11 = +10%, DDD 0%
pos = B.positions(SNAPS, "last")
B.apply_prices(pos, B.snapshot_prices(SNAPS))
r = {p["ticker"]: p["ret"] for p in pos}
assert abs(r["AAA"] - 0.30) < 1e-9, r["AAA"]
assert abs(r["BBB"] - 0.60) < 1e-9
assert abs(r["CCC"] - 0.10) < 1e-9
assert abs(r["DDD"] - 0.00) < 1e-9
ok("apply_prices: 스냅샷 종가로 진입·청산 수익률")

# 8) 한쪽 가격만 있으면 측정 불가 — 미수정가와 수정종가를 섞지 않는다
half = B.positions(SNAPS, "last")
B.apply_prices(half, {"AAA": {"2026-09-01": 100.0}})   # 청산일 가격 없음
assert B.close_at({"2026-09-01": 100.0}, "2026-09-04") == 100.0  # 직전으로 메움
assert {p["ticker"] for p in half if p["ret"] is None} == \
    {"BBB", "CCC", "DDD"}                              # 아예 자료 없는 종목
ok("apply_prices: 자료 없는 종목은 ret=None (0%로 세지 않는다)")

# 9) 집계 — 평균/중앙값/승률, 벤치마크 초과
st = B.stats(pos, bench=0.10)
assert st["n"] == 4 and st["measured"] == 4 and st["unmeasured"] == 0
assert abs(st["mean"] - 0.25) < 1e-9, st["mean"]        # (30+60+10+0)/4
assert abs(st["median"] - 0.20) < 1e-9                 # (10+30)/2
assert abs(st["win"] - 0.75) < 1e-9                    # DDD는 0%라 승 아님
assert abs(st["excess"] - 0.15) < 1e-9
assert B.stats([])["measured"] == 0 and "mean" not in B.stats([])
ok("stats: 평균·중앙값·승률·초과수익, 빈 집합 안전")

# 10) 측정 불가는 평균에서 빠지고 개수로 남는다
mixed = B.stats([{"ret": 0.10, "days": 1}, {"ret": None, "days": 1}])
assert mixed["n"] == 2 and mixed["measured"] == 1 and mixed["unmeasured"] == 1
assert abs(mixed["mean"] - 0.10) < 1e-9
ok("stats: 측정 불가 종목은 분모에서 제외하고 따로 보고")

# 11) 생존 편향 = 남은 종목 평균 − 빠진 종목 평균
#     last 규칙에서 survivor는 AAA(+30%) BBB(+60%), 탈락은 CCC(+10%) DDD(0%)
bi = B.bias(pos)
assert abs(bi["survivors"]["mean"] - 0.45) < 1e-9
assert abs(bi["dropouts"]["mean"] - 0.05) < 1e-9
assert abs(bi["gap"] - 0.40) < 1e-9
ok("bias: 생존 편향을 남은 종목과 빠진 종목의 평균 차로 측정")

# 12) 한쪽이 비면 편향은 None (0으로 보고하지 않는다)
allsv = [p for p in pos if p["survivor"]]
assert B.bias(allsv)["gap"] is None
ok("bias: 비교군이 없으면 gap=None")

# 13) 부분집합 — 전체 + 상위N(종목 수보다 작을 때만) + VCP 컷
subs = dict((k, v) for k, v in B.subsets(pos))
assert "전체 4종목" in subs and len(subs["전체 4종목"]) == 4
assert not any(k.startswith("상위") for k in subs)     # 4종목 < 20
assert f"VCP ≥ {B.VCP_CUT} (1종목)" in subs            # AAA만 60점
many = B.positions(
    [snap("2026-09-01", {f"T{i:03d}": (99 - i % 9, i % 100, 10.0 + i)
                         for i in range(60)}),
     snap("2026-09-02", {"T000": (99, 0, 11.0)})], "last")
labels = [k for k, _ in B.subsets(many)]
assert any(k.startswith("상위 20") for k in labels)
assert any(k.startswith("상위 50") for k in labels)
ok("subsets: 전체·상위20·상위50·VCP컷, 표본보다 큰 상위N은 생략")

# 14) coverage_skew — 측정률이 생존 쪽에 쏠리면 경고용 수치가 나온다
sk = B.positions(SNAPS, "last")
B.apply_prices(sk, B.snapshot_prices(SNAPS))
assert B.coverage_skew(sk) == 0.0                   # 전부 측정됨 → 쏠림 없음
part = B.positions(SNAPS, "last")
B.apply_prices(part, {"AAA": {"2026-09-01": 1.0, "2026-09-04": 2.0}})
# survivor AAA·BBB 중 AAA만 측정(0.5), 탈락 CCC·DDD는 0건(0.0)
assert abs(B.coverage_skew(part) - 0.5) < 1e-9, B.coverage_skew(part)
assert B.coverage_skew([p for p in sk if p["survivor"]]) is None
ok("coverage_skew: 가격을 구한 종목이 생존 쪽에 쏠린 정도를 수치로 낸다")

# 15) closes_from — yfinance 열 구조 2단/단층 모두 받는다 (pandas 있을 때만)
try:
    import pandas as pd
except ImportError:
    print("  SKIP  closes_from (pandas 없음)")
else:
    idx = pd.to_datetime(["2026-09-01", "2026-09-02"])
    two = pd.DataFrame(
        {("AAA", "Close"): [1.0, 2.0], ("AAA", "Volume"): [10, 20],
         ("BBB", "Close"): [3.0, None]},
        index=idx)
    two.columns = pd.MultiIndex.from_tuples(two.columns)
    assert B.closes_from(two, "AAA") == {"2026-09-01": 1.0, "2026-09-02": 2.0}
    assert B.closes_from(two, "BBB") == {"2026-09-01": 3.0}   # NaN 제거
    assert B.closes_from(two, "ZZZ") == {}
    flat = pd.DataFrame({"Close": [5.0, 6.0], "Volume": [1, 2]}, index=idx)
    assert B.closes_from(flat, "AAA") == {"2026-09-01": 5.0, "2026-09-02": 6.0}
    assert B.closes_from(pd.DataFrame(), "AAA") == {}
    ok("closes_from: (종목,항목) 2단 열과 단층 열 모두 처리, NaN 제거")

# 16) wpad — 한글 두 칸을 세어 표시 너비로 맞춘다
assert B.wpad("abc", 6) == "abc   "
assert B.wpad("가나", 6) == "가나  "          # 2+2=4칸 → 2칸 채움
assert B.wpad("가나다", 4) == "가나"           # 넘치면 글자 단위로 자른다
assert B.wpad("a가", 4) == "a가 "
assert len(B.wpad("", 3)) == 3
ok("wpad: 한글 폭 2칸 기준으로 자르고 채운다 (표 정렬)")

# 17) 포맷터
assert B.pct(None).strip() == "-"
assert B.pct(0.1234).strip() == "+12.34%"
assert B.pct(-0.05).strip() == "-5.00%"
ok("pct: 측정 불가는 '-', 부호 유지")

print("\n전체 통과")
