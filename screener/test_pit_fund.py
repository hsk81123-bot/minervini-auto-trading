# -*- coding: utf-8 -*-
"""pit_fund.py 단위 테스트 (네트워크 없음).

    python screener/test_pit_fund.py

신호일 뒤에 공시된 숫자가 섞이지 않는지, 비교 기간 중복을 빼도 결과가 같은지,
같은 날 공시와 오래된 분기를 쓰지 않는지 확인한다.
"""
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fundamentals as F
import pit_fund as T

ok = lambda m: print("  OK  " + m)


def qrow(end, val, filed, form="10-Q", days=91):
    e = date.fromisoformat(end)
    return {"start": (e - timedelta(days=days)).isoformat(), "end": end,
            "val": val, "filed": filed, "form": form}


def company(rev, eps, extra_gaap=None):
    gaap = {"Revenues": {"units": {"USD": rev}},
            "EarningsPerShareDiluted": {"units": {"USD/shares": eps}}}
    gaap.update(extra_gaap or {})
    return {"facts": {"us-gaap": gaap}}


# 분기 매출: 2022 년 100씩, 2023 년 130씩. 각 10-Q 는 전년 동기를 비교 기간으로 다시 싣는다.
ends22 = ["2022-03-31", "2022-06-30", "2022-09-30"]
ends23 = ["2023-03-31", "2023-06-30", "2023-09-30"]
filed22 = ["2022-05-05", "2022-08-04", "2022-11-03"]
filed23 = ["2023-05-04", "2023-08-03", "2023-11-02"]
rev, eps = [], []
for e, f in zip(ends22, filed22):
    rev.append(qrow(e, 100, f))
    eps.append(qrow(e, 1.0, f))
for e22, e, f in zip(ends22, ends23, filed23):
    rev += [qrow(e, 130, f), qrow(e22, 100, f)]           # 비교 기간 (같은 값)
    eps += [qrow(e, 1.5, f), qrow(e22, 1.0, f)]
    if e != ends23[0]:                                    # 누계(YTD) 6·9개월 — 버린다
        rev.append({"start": "2023-01-01", "end": e, "val": 999, "filed": f,
                    "form": "10-Q"})
eps[-1] = qrow(ends22[2], 0.9, filed23[2])                # 비교 기간 정정 (값이 바뀜)
rev.append(qrow("2023-09-30", 131, "2024-01-10", "10-Q/A"))  # 정정 공시 양식 — 안 쓴다
c = T.compact(company(rev, eps))
assert set(c) == {"revenue", "eps_diluted"}
rows = c["revenue"]["Revenues"]
assert all(r[2] != 999 for r in rows) and all(r[4] in T.FORMS for r in rows)
assert len(rows) == 6                                     # 비교 기간 중복 3개 제거
assert [r[3] for r in rows] == sorted(r[3] for r in rows)
assert len(c["eps_diluted"]["EarningsPerShareDiluted"]) == 7   # 정정치는 남는다
ok("compact: 누계·정정 양식 제외 · 같은 값 비교 기간 제거 · 바뀐 값은 남김")

# 시점 기준: 2023-08-03 공시 전에는 2023 2분기를 모른다
prep = T.prepare(c)
d = T.doc_asof(prep, "2023-08-02")
assert [q["end"] for q in d["quarters"]][:2] == ["2023-03-31", "2022-09-30"]
d = T.doc_asof(prep, "2023-08-03")
assert d["quarters"][0]["end"] == "2023-06-30"
d = T.doc_asof(prep, "2023-11-01")
assert {q["end"]: q["eps_diluted"] for q in d["quarters"]}["2022-09-30"] == 1.0
d = T.doc_asof(prep, "2023-11-02")
assert {q["end"]: q["eps_diluted"] for q in d["quarters"]}["2022-09-30"] == 0.9
ok("doc_asof: 공시일 이후에만 새 분기·정정치가 보인다")

# 타임라인과 조회: 공시일 < 신호일 (같은 날은 아직 모른다) · 오래된 분기는 없는 셈
tl = T.ticker_timeline(c)
assert [x[0] for x in tl] == filed22 + filed23
assert T.lookup(tl, "2023-08-03")["as_of"] == "2023-03-31"     # 같은 날 공시 미사용
m = T.lookup(tl, "2023-08-04")
assert m["as_of"] == "2023-06-30" and abs(m["eps_growth"] - 0.5) < 1e-9
assert abs(m["revenue_growth"] - 0.3) < 1e-9
m = T.lookup(tl, "2023-11-03")
assert abs(m["eps_growth"] - (1.5 / 0.9 - 1)) < 1e-4           # 정정된 전년 값 기준
assert m["eps_accel"] is False                                  # 0.67 > 0.5 = 0.5 — 1번만 상승
assert T.lookup(tl, "2022-05-05") is None                      # 첫 공시 당일
assert T.lookup(tl, "2024-03-30")["as_of"] == "2023-09-30"
assert T.lookup(tl, "2024-04-18") is None                      # 분기 종료 200일 초과
assert T.lookup([], "2024-01-01") is None and T.lookup(None, "2024-01-01") is None
ok("timeline/lookup: 공시일 < 신호일 · 정정 반영 · 200일 넘으면 None")

# 태그 선택도 그 시점 기준: 나중에 쓰기 시작한 태그를 미리 고르지 않는다
late = [qrow(e, 7, f) for e, f in zip(ends23, filed23)] + \
       [qrow(e, 7, "2024-02-01") for e in ("2023-12-31",)]
c2 = T.compact(company(rev, eps, {
    "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": late}}}))
p2 = T.prepare(c2)
assert T.doc_asof(p2, "2023-11-02")["quarters"][0]["revenue"] == 130
assert T.doc_asof(p2, "2024-02-01")["quarters"][0]["revenue"] == 7
ok("doc_asof: 태그 선택도 그 시점에 보이던 행으로")

# 중복 제거가 결과를 바꾸지 않는다 — 원본 행으로 같은 계산을 한 것과 비교
rng = random.Random(7)


def ref_doc(raw: dict, cutoff: str) -> dict:
    series = {}
    for field, tags in T.FIELDS.items():
        best = (None, ("", -1))
        for tag in tags:
            units = raw["facts"]["us-gaap"].get(tag, {}).get("units")
            if not units:
                continue
            rows = [r for r in units[next(iter(units))]
                    if r["filed"] <= cutoff and r.get("start")]
            if not rows:
                continue
            rank = F.tag_rank(rows)
            if rank > best[1]:
                best = (rows, rank)
        if best[0]:
            series[field] = best[0]
    q, a = F.assemble(series)
    return {"market": "us", "quarters": q[:F.QUARTERS_KEEP],
            "annuals": a[:F.YEARS_KEEP]}


for trial in range(30):
    raw_rev, raw_eps = [], []
    base = date(2016, 3, 31)
    for k in range(28):
        e = (base + timedelta(days=round(91.3 * k))).isoformat()
        f0 = date.fromisoformat(e) + timedelta(days=rng.randint(25, 45))
        for raw, v in ((raw_rev, rng.choice([90, 100, 110])),
                       (raw_eps, rng.choice([0.8, 1.0, 1.2]))):
            raw.append(qrow(e, v, f0.isoformat()))
            # 다음 해 비교 기간: 대개 같은 값, 가끔 정정
            f1 = (f0 + timedelta(days=365)).isoformat()
            raw.append(qrow(e, v if rng.random() < 0.8 else v * 1.1, f1))
            if k % 4 == 3:                                   # 연간 (10-K)
                raw.append(qrow(e, v * 4, f0.isoformat(), "10-K", 364))
                raw.append(qrow(e, v * 4, f1, "10-K", 364))
            raw.append({"start": e[:4] + "-01-01", "end": e, "val": v * 2,
                        "filed": f0.isoformat(), "form": "10-Q"})
    rng.shuffle(raw_rev)
    raw = company(raw_rev, raw_eps)
    prep = T.prepare(T.compact(raw))
    for cutoff in ("2017-06-01", "2019-02-14", "2021-08-30", "2023-12-31"):
        assert T.doc_asof(prep, cutoff) == ref_doc(raw, cutoff), (trial, cutoff)
ok("중복 제거·공시일 정렬 후에도 원본 행으로 계산한 문서와 같다 (30회 × 4시점)")

# 전체 이력(자르지 않음): 20개 분기가 모두 나오고 오름차순
rows_eps, rows_rev = [], []
for k in range(20):
    e = date(2015, 3, 31) + timedelta(days=round(91.3 * k))
    f = (e + timedelta(days=35)).isoformat()
    rows_eps.append(qrow(e.isoformat(), 1.0 + k * 0.1, f))
    rows_rev.append(qrow(e.isoformat(), 100 + k, f))
fq = T.full_quarters(T.compact(company(rows_rev, rows_eps)))
assert len(fq) == 20 and [q["end"] for q in fq] == sorted(q["end"] for q in fq)
assert abs(fq[0]["eps_diluted"] - 1.0) < 1e-9 and abs(fq[-1]["eps_diluted"] - 2.9) < 1e-9
assert len(T.doc_asof(T.prepare(T.compact(company(rows_rev, rows_eps))), "9999-12-31")["quarters"]) == 12
ok("full_quarters: 전체 이력(20분기) · doc_asof 는 12분기로 자름")

# 발행주식수: 종류가 여럿이면 합 · 같은 행 중복은 한 번 · 정정 양식 포함 · 공시일 < 신호일
def dei(rows):
    return {"facts": {"dei": {"EntityCommonStockSharesOutstanding":
                              {"units": {"shares": rows}}}}}


R = lambda accn, end, val, filed, form="10-Q": {"accn": accn, "end": end, "val": val,
                                                 "filed": filed, "form": form}
sh = T.compact_shares(dei([
    R("a1", "2023-04-20", 5_000, "2023-05-02"), R("a1", "2023-04-20", 800, "2023-05-02"),
    R("a1", "2023-04-20", 5_000, "2023-05-02"),                 # 같은 행 중복
    R("a2", "2023-07-20", 5_100, "2023-08-02"), R("a2", "2023-07-20", 800, "2023-08-02"),
    R("a3", "2023-10-20", 0, "2023-11-02"),                     # 0 은 버린다
    R("a4", "2023-10-20", 7, "2023-11-02", "8-K")]))             # 다른 양식
assert sh == [["2023-05-02", "2023-04-20", 5_800], ["2023-08-02", "2023-07-20", 5_900]]
assert T.shares_at(sh, "2023-05-02") is None                    # 같은 날 공시는 아직 모른다
assert T.shares_at(sh, "2023-05-03") == (5_800, "2023-04-20")
assert T.shares_at(sh, "2023-09-01") == (5_900, "2023-07-20")
assert T.shares_at(sh, "2024-12-01") is None                    # 기준일이 400일 넘게 전
assert T.shares_at(None, "2023-09-01") is None and T.compact_shares({}) == []
ok("compact_shares / shares_at: 종류별 합산 · 중복 · 공시일 < 신호일 · 오래된 값 제외")

print("\n  전체 통과")
