# -*- coding: utf-8 -*-
"""fundamentals.py 단위 테스트 (네트워크 없이 돈다).

    python screener/test_fundamentals.py

requests를 가짜로 끼워 넣어 실제 호출을 막고, 순수 로직만 검사한다.
4분기 역산은 Phase 0에서 DART로 받은 실제 삼성전자 2025 숫자로 검산한다.
"""
import sys, types
req = types.ModuleType("requests"); req.RequestException = Exception
req.get = lambda *a, **k: (_ for _ in ()).throw(AssertionError("네트워크 호출 금지"))
sys.modules["requests"] = req
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import fundamentals as F

ok = lambda m: print("  OK  " + m)

# 1) pick_periods — 기간·서식 필터와 정정공시 처리
rows = [
    {"start":"2026-03-29","end":"2026-06-28","val":100,"form":"10-Q","filed":"2026-08-01"},
    {"start":"2026-03-29","end":"2026-06-28","val":111,"form":"10-Q","filed":"2026-09-15"},  # 정정 → 이게 채택
    {"start":"2025-06-29","end":"2026-06-28","val":400,"form":"10-K","filed":"2026-11-01"},  # 연간
    {"start":"2025-09-29","end":"2026-03-29","val":200,"form":"10-Q","filed":"2026-05-01"},  # 반기
    {"start":"2026-03-29","end":"2026-06-28","val":999,"form":"8-K","filed":"2026-07-01"},   # 서식 제외
    {"end":"2026-06-28","val":500,"form":"10-Q","filed":"2026-08-01"},                        # 시점
]
q = F.pick_periods(rows, 80, 100)
assert len(q) == 1 and q[0]["val"] == 111, q
ok("pick_periods: 분기만 1건, 정정공시(최신 filed) 채택")
a = F.pick_periods(rows, 350, 380)
assert len(a) == 1 and a[0]["val"] == 400
ok("pick_periods: 연간 구간 분리")
inst = F.pick_instants(rows)
assert len(inst) == 1 and inst[0]["val"] == 500
ok("pick_instants: start 없는 시점 항목만")

# 2) derive_q4 — VLO처럼 4분기가 없을 때
quarters = [
    {"start":"2025-07-01","end":"2025-09-30","val":30,"form":"10-Q","fy":2025,"fp":"Q3"},
    {"start":"2025-04-01","end":"2025-06-30","val":25,"form":"10-Q","fy":2025,"fp":"Q2"},
    {"start":"2025-01-01","end":"2025-03-31","val":20,"form":"10-Q","fy":2025,"fp":"Q1"},
]
annuals = [{"start":"2025-01-01","end":"2025-12-31","val":100,"form":"10-K","fy":2025}]
out = F.derive_q4(quarters, annuals)
q4 = [x for x in out if x.get("derived")]
assert len(q4) == 1 and q4[0]["val"] == 25 and q4[0]["end"] == "2025-12-31", q4
assert q4[0]["start"] == "2025-09-30"
assert "fy" not in q4[0] and "fp" not in q4[0]  # 오해 소지 있는 SEC 라벨은 저장하지 않는다
ok(f"derive_q4: 100 - (20+25+30) = {q4[0]['val']} (4분기 역산)")
out4 = F.derive_q4(quarters + [{"start":"2025-10-01","end":"2025-12-31","val":25,"form":"10-K","fy":2025,"fp":"Q4"}], annuals)
assert not any(x.get("derived") for x in out4)
ok("derive_q4: 4분기가 이미 있으면 역산하지 않음")

# 3) kr_amount
cases = {"79,140,503,000,000": 79140503000000.0, "-": None, "": None,
         "(1,234)": -1234.0, " 500 ": 500.0, None: None, "abc": None}
for raw, want in cases.items():
    got = F.kr_amount(raw)
    assert got == want, (raw, got, want)
ok("kr_amount: 콤마·괄호음수·빈값·'-' 처리")

# 4) kr_pick — 괄호 계정명, 연결 우선, 중복 행
rows = [
    {"fs_div":"OFS","account_nm":"당기순이익(손실)","thstrm_amount":"100"},
    {"fs_div":"CFS","account_nm":"당기순이익(손실)","thstrm_amount":"12,225,747,000,000"},
    {"fs_div":"CFS","account_nm":"당기순이익(손실)","thstrm_amount":"12,225,747,000,000"},
    {"fs_div":"CFS","account_nm":"영업이익","thstrm_amount":"12,166,062,000,000",
     "thstrm_add_amount":"23,527,391,000,000"},
]
assert F.kr_pick(rows, F.KR_ACCOUNTS["net_income"]) == 12225747000000.0
ok("kr_pick: '당기순이익(손실)' 접두 일치 + 연결(CFS) 우선 + 중복 무해")
assert F.kr_pick(rows, F.KR_ACCOUNTS["operating_income"], "thstrm_add_amount") == 23527391000000.0
ok("kr_pick: 누적 필드도 지정해 읽을 수 있음")
assert F.kr_pick(rows, ("없는계정",)) is None
ok("kr_pick: 없는 계정은 None")
assert F.kr_pick([{"fs_div":"OFS","account_nm":"매출액","thstrm_amount":"7"}],
                 F.KR_ACCOUNTS["revenue"]) == 7.0
ok("kr_pick: 연결이 없으면 별도(OFS)로 폴백")

# 5) fetch_kr — Phase 0에서 받은 실제 삼성전자 2025 숫자로 4분기 역산 검산
REAL = {  # (reprt_code) -> (매출액, 영업이익)
    "11013": (79140503000000, 6685272000000),
    "11012": (74566317000000, 4676057000000),
    "11014": (86061747000000, 12166062000000),
    "11011": (333605938000000, 43601051000000),
}
def fake_accounts(key, corp, year, reprt):
    if year != 2025:
        return []
    rev, op = REAL[reprt]
    return [
        {"fs_div":"CFS","sj_div":"IS","account_nm":"매출액","thstrm_amount":f"{rev:,}"},
        {"fs_div":"CFS","sj_div":"IS","account_nm":"영업이익","thstrm_amount":f"{op:,}"},
        {"fs_div":"CFS","sj_div":"IS","account_nm":"당기순이익(손실)","thstrm_amount":f"{int(op*0.9):,}"},
        {"fs_div":"CFS","sj_div":"BS","account_nm":"자본총계","thstrm_amount":"400,000,000,000,000"},
    ]
F.dart_accounts = fake_accounts
doc = F.fetch_kr("005930.KS", "00126380", "k", [2025])
qs = {q["label"]: q for q in doc["quarters"]}
print("     분기 라벨:", sorted(qs))
assert set(qs) == {"2025Q1","2025Q2","2025Q3","2025Q4"}, sorted(qs)
q4 = qs["2025Q4"]
exp_rev = 333605938000000 - (79140503000000 + 74566317000000 + 86061747000000)
exp_op  = 43601051000000 - (6685272000000 + 4676057000000 + 12166062000000)
assert q4["revenue"] == exp_rev, (q4["revenue"], exp_rev)
assert q4["operating_income"] == exp_op, (q4["operating_income"], exp_op)
assert sorted(q4["derived"]) == ["net_income","operating_income","revenue"]
ok(f"fetch_kr 4분기 역산: 매출 {exp_rev/1e12:.1f}조 / 영업이익 {exp_op/1e12:.2f}조 (derived 표기됨)")
assert doc["annuals"][0]["equity"] == 400000000000000.0
ok("fetch_kr: 연간 보고서에서 자본총계 수집")
assert doc["quarters"][0]["label"] == "2025Q4"  # 최신순
ok("fetch_kr: 최신 분기부터 정렬")

# 6) out_path 라우팅
assert F.out_path("AAPL").parent.name == "fundamentals"
assert F.out_path("005930.KS").parent.name == "fundamentals_kr"
assert F.out_path("247540.KQ").parent.name == "fundamentals_kr"
ok("out_path: 시장별 디렉터리 분리")


# 7) 누적 교차검증 — 당해값 그대로 쓰는 경우 (Phase 0의 삼성전자 2025)
def rows_for(rev, op, ni, rev_c=None, op_c=None, ni_c=None):
    def row(nm, a, c):
        r = {"fs_div": "CFS", "sj_div": "IS", "account_nm": nm,
             "thstrm_amount": f"{int(a):,}"}
        if c is not None:
            r["thstrm_add_amount"] = f"{int(c):,}"
        return r
    return [row("매출액", rev, rev_c), row("영업이익", op, op_c),
            row("당기순이익(손실)", ni, ni_c)]

T = 10**12
# 당해 3개월 + 누적이 정확히 맞는 정상 케이스
STD = {1: (79.14, 6.69, 8.22), 2: (74.57, 4.68, 5.12), 3: (86.06, 12.17, 12.23)}
def fake_std(key, corp, year, reprt):
    if year != 2025:
        return []
    if reprt == F.KR_ANNUAL:
        return rows_for(333.61*T, 43.60*T, 40.0*T)
    qno = F.KR_REPORTS[reprt]
    rev, op, ni = STD[qno]
    cum = tuple(sum(STD[q][i] for q in range(1, qno+1)) for i in range(3))
    return rows_for(rev*T, op*T, ni*T, cum[0]*T, cum[1]*T, cum[2]*T)
F.dart_accounts = fake_std
doc = F.fetch_kr("005930.KS", "c", "k", [2025])
qs = {q["label"]: q for q in doc["quarters"]}
assert abs(qs["2025Q2"]["revenue"] - 74.57*T) < 1e6, qs["2025Q2"]
assert doc["quarter_basis"]["2025"] == "당해(3개월) 값 그대로"
ok("누적 교차검증: 당해값이 맞으면 그대로 쓴다")

# 누적만 들어오는(= thstrm_amount가 누적인) 비정상 케이스 → 차분 복원
def fake_cum(key, corp, year, reprt):
    if year != 2025:
        return []
    if reprt == F.KR_ANNUAL:
        return rows_for(333.61*T, 43.60*T, 40.0*T)
    qno = F.KR_REPORTS[reprt]
    cum = tuple(sum(STD[q][i] for q in range(1, qno+1)) for i in range(3))
    return rows_for(cum[0]*T, cum[1]*T, cum[2]*T, cum[0]*T, cum[1]*T, cum[2]*T)
F.dart_accounts = fake_cum
doc = F.fetch_kr("005930.KS", "c", "k", [2025])
qs = {q["label"]: q for q in doc["quarters"]}
assert doc["quarter_basis"]["2025"] == "누적 차분으로 복원", doc["quarter_basis"]
for lbl, want in (("2025Q1", 79.14), ("2025Q2", 74.57), ("2025Q3", 86.06)):
    got = qs[lbl]["revenue"] / T
    assert abs(got - want) < 0.01, (lbl, got, want)
    assert qs[lbl]["basis"] == "cumulative_diff"
ok("누적 교차검증: 누적이 들어오면 차분으로 분기값 복원")
q4 = qs["2025Q4"]
assert abs(q4["revenue"]/T - (333.61 - 239.77)) < 0.02, q4["revenue"]/T
ok(f"복원 후에도 4분기 역산 정상: {q4['revenue']/T:.1f}조")
print("\n전체 통과")
