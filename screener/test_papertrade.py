# -*- coding: utf-8 -*-
"""papertrade.py 단위 테스트 (네트워크 없음).

    python screener/test_papertrade.py

손으로 계산할 수 있는 일봉만 쓴다. 기본 기준: 진입가 100, 손절 -8%(92),
익절 +25%(125) → 1R = 8.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import papertrade as P

ok = lambda m: print("  OK  " + m)


def bar(d, o, h, l, c):
    return {"date": d, "open": o, "high": h, "low": l, "close": c}


ENTRY = bar("2026-09-11", 99, 101, 98, 100.0)     # 진입일: 종가 100에 매수
D = ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17"]


def sim(*bars, **kw):
    # 1~16번은 '신호 당일 종가 진입' 기준으로 쓴 판정 테스트다.
    # 다음날 시가 진입은 아래 별도 블록에서 확인한다.
    kw.setdefault("stop", -8.0)
    kw.setdefault("target", 25.0)
    kw.setdefault("entry_mode", "close")
    return P.simulate([ENTRY, *bars], "2026-09-11", **kw)


# ---------------------------------------------------------------- 기본 체결
# 1) 진입일 당일은 판정하지 않는다 (종가에 샀으므로 그 날 고저는 매수 전)
s = sim(bar(D[0], 100, 101, 99, 100.0))
assert s["status"] == "보유" and s["entry"] == 100.0 and abs(s["ret"]) < 1e-9
assert abs(s["risk"] - 8.0) < 1e-9, s["risk"]
ok("진입일 종가 매수 — 당일 고가·저가로는 체결 판정하지 않는다")

# 2) 장중 저가가 손절선을 건드리면 손절선 가격에 체결 → 정확히 -1R
s = sim(bar(D[0], 99, 100, 91.0, 99.0))
assert s["status"] == "손절" and abs(s["exit"] - 92.0) < 1e-9
assert abs(s["r_multiple"] + 1.0) < 1e-9, s["r_multiple"]
ok("장중 손절 — 손절선 체결은 정확히 -1R")

# 3) 익절선 도달 → 설계 손익비가 그대로 R로 나온다 (25/8 = 3.125R)
s = sim(bar(D[0], 101, 130.0, 100, 128.0))
assert s["status"] == "익절" and abs(s["exit"] - 125.0) < 1e-9
assert abs(s["r_multiple"] - 3.125) < 1e-9, s["r_multiple"]
ok("익절 — 설계 손익비 3.1:1 이 실측 +3.125R 로 확인된다")

# 4) 갭하락은 손절선을 건너뛴다 — 손실이 -1R보다 커진다
s = sim(bar(D[0], 85.0, 88, 84, 86.0))
assert s["status"] == "손절" and abs(s["exit"] - 85.0) < 1e-9
assert abs(s["r_multiple"] + 1.875) < 1e-9, s["r_multiple"]
assert "갭하락" in s["reason"]
ok("갭하락 — 실제 손실이 -1R을 넘는다 (손절선은 바닥이 아니다)")

# 5) 갭상승도 익절선을 건너뛴다
s = sim(bar(D[0], 140.0, 145, 139, 144.0))
assert s["status"] == "익절" and abs(s["exit"] - 140.0) < 1e-9
assert abs(s["r_multiple"] - 5.0) < 1e-9
ok("갭상승 — 익절선을 넘겨 시가 체결")

# 6) 같은 날 양쪽을 건드리면 손절 우선 + 표시
s = sim(bar(D[0], 100, 130.0, 90.0, 105.0))
assert s["status"] == "손절" and s["both_hit"] is True
ok("같은 날 손절선·익절선 모두 건드림 — 손절 우선, both_hit 표시")

# 7) 갭 체결에는 both_hit를 붙이지 않는다 (시가에서 이미 끝났다)
s = sim(bar(D[0], 140.0, 145, 90.0, 100.0))
assert s["status"] == "익절" and s["both_hit"] is False
ok("갭 체결 — 순서 모호 표시를 붙이지 않는다")

# 8) 시간 순서대로 — 첫 체결에서 멈춘다
s = sim(bar(D[0], 99, 100, 91, 95.0), bar(D[1], 100, 140.0, 99, 138.0))
assert s["status"] == "손절" and s["exit_date"] == D[0]
ok("시간 순서대로 판정 — 첫 체결에서 멈춘다")

# ---------------------------------------------------------------- 추격손절
# 9) 추격손절은 전일까지의 고가로만 올린다 (당일 고가를 쓰면 룩어헤드)
#    1일차 고가 120 → 2일차 손절선 = 120 × 0.85 = 102
s = sim(bar(D[0], 101, 120.0, 100, 118.0),
        bar(D[1], 117, 119, 101.0, 103.0),
        stop=-8.0, target=None, trail=-15.0)
assert s["status"] == "추격손절" and abs(s["exit"] - 102.0) < 1e-9, s
assert s["exit_date"] == D[1]
assert abs(s["r_multiple"] - 0.25) < 1e-9, s["r_multiple"]   # (102-100)/8
ok("추격손절 — 전일까지의 고가 기준으로만 선을 올린다")

# 10) 1일차에는 추격선이 아직 진입가 기준이라 최초 손절선이 유효하다
s = sim(bar(D[0], 99, 101, 91.0, 99.0), stop=-8.0, target=None, trail=-15.0)
assert s["status"] == "손절" and abs(s["exit"] - 92.0) < 1e-9
ok("추격손절 첫날 — 고가 갱신 전이므로 최초 손절선이 걸린다")

# 11) 고정 익절이 없으면 상승을 자르지 않는다
s = sim(bar(D[0], 101, 130, 100, 128.0),
        bar(D[1], 129, 160, 128, 158.0),
        stop=-8.0, target=None, trail=-15.0)
assert s["status"] == "보유" and abs(s["exit"] - 158.0) < 1e-9
assert abs(s["r_multiple"] - 7.25) < 1e-9, s["r_multiple"]
ok("고정 익절 없음 — +7.25R까지 그대로 따라간다 (익절선이 승자를 자르지 않는다)")

# 12) 본전손절 — +10% 도달 뒤에는 진입가 아래로 내려가면 청산
s = sim(bar(D[0], 101, 112.0, 100, 110.0),
        bar(D[1], 109, 110, 99.0, 99.5),
        stop=-8.0, target=None, breakeven=10.0)
assert s["status"] == "추격손절" and abs(s["exit"] - 100.0) < 1e-9, s
assert abs(s["r_multiple"]) < 1e-9          # 정확히 0R
ok("본전손절 — 목표 도달 후 손절선을 진입가로 올려 0R로 끝낸다")

# 13) 본전손절 기준 미달이면 최초 손절선 그대로
s = sim(bar(D[0], 101, 105.0, 100, 104.0),
        bar(D[1], 103, 104, 99.0, 99.5),
        stop=-8.0, target=None, breakeven=10.0)
assert s["status"] == "보유", s          # 92까지 안 내려왔다
ok("본전손절 기준 미달 — 손절선을 올리지 않는다")

# 14) 시간청산 — N일 경과 시 종가 청산
s = sim(bar(D[0], 100, 101, 99, 100.5), bar(D[1], 100, 102, 99, 101.0),
        stop=-8.0, target=None, time_stop=2)
assert s["status"] == "시간청산" and abs(s["exit"] - 101.0) < 1e-9
assert s["exit_date"] == D[1]
ok("시간청산 — 무반응 종목을 N일에 끊어 자금을 돌린다")

# 15) 손절·익절이 시간청산보다 먼저다
s = sim(bar(D[0], 99, 100, 91, 95.0), bar(D[1], 95, 96, 94, 95.0),
        stop=-8.0, target=None, time_stop=2)
assert s["status"] == "손절"
ok("시간청산보다 손절이 먼저 — 순서가 뒤바뀌지 않는다")

# ---------------------------------------------------------------- MAE / MFE
# 16) 보유 중 최대 역행·순행을 남긴다
s = sim(bar(D[0], 100, 115.0, 94.0, 100.0), bar(D[1], 100, 108, 97, 105.0),
        stop=-8.0, target=None)
assert abs(s["mfe"] - 0.15) < 1e-9 and abs(s["mae"] + 0.06) < 1e-9, s
ok("MAE/MFE — 손절선이 노이즈에 걸리는지 판단할 근거를 남긴다")

# ---------------------------------------------------------------- 결측
# 17) 가격이 없으면 측정 불가 (0%로 세지 않는다)
assert P.simulate([], "2026-09-11", -8.0)["status"] == "측정 불가"
assert P.simulate([bar("2026-09-01", 1, 1, 1, 1.0)], "2026-09-11",
                  -8.0)["status"] == "측정 불가"
ok("가격 없음 — 측정 불가로 분리")

# 18) 진입일 휴장이면 이후 첫 거래일 종가로 진입
s = P.simulate([bar(D[0], 49, 51, 48, 50.0), bar(D[1], 51, 63.0, 50, 62.0)],
               "2026-09-11", -8.0, 25.0, entry_mode="close")
assert s["entry"] == 50.0 and s["entry_date"] == D[0]
assert s["status"] == "익절" and abs(s["exit"] - 62.5) < 1e-9
ok("진입일 휴장·거래정지 — 이후 첫 거래일 종가로 진입")

# ---------------------------------------------------------------- 다음날 시가 진입
# 스크리너는 장 마감 뒤에 돈다. 신호를 만든 종가에는 살 수 없다.
# 19) 기본값은 신호 다음 거래일 시가 진입
s = P.simulate([ENTRY, bar(D[0], 104.0, 106, 103, 105.0)], "2026-09-11", -8.0)
assert s["entry"] == 104.0 and s["entry_date"] == D[0], s
assert s["status"] == "보유" and abs(s["exit"] - 105.0) < 1e-9
assert abs(s["risk"] - 104.0 * 0.08) < 1e-9
ok("기본 진입 = 신호 다음날 시가 (신호 당일 종가는 실제로 살 수 없다)")

# 20) 산 날 장중에도 손절이 걸린다 (시가에 샀으므로 그 날 저가는 매수 뒤)
s = P.simulate([ENTRY, bar(D[0], 100.0, 101, 91.0, 95.0)], "2026-09-11", -8.0)
assert s["status"] == "손절" and abs(s["exit"] - 92.0) < 1e-9, s
assert s["exit_date"] == D[0] and s["days"] == 0
ok("다음날 시가 진입 — 산 날 장중 저가로 바로 손절될 수 있다")

# 21) 밤사이 갭상승은 진입가에 그대로 반영된다 — 종가 진입과 차이가 여기서 난다
gap = [ENTRY, bar(D[0], 112.0, 115, 110, 114.0), bar(D[1], 114, 116, 113, 115.0)]
a = P.simulate(gap, "2026-09-11", -8.0, entry_mode="close")
b = P.simulate(gap, "2026-09-11", -8.0)
assert a["entry"] == 100.0 and b["entry"] == 112.0
assert a["ret"] > b["ret"], (a["ret"], b["ret"])
ok("갭상승 — 종가 진입은 밤사이 상승분을 공짜로 먹는다 (다음날 시가 진입은 아니다)")

# 22) 신호 다음 거래일이 없으면 측정 불가 (신호 당일 가격으로 메우지 않는다)
s = P.simulate([ENTRY], "2026-09-11", -8.0)
assert s["status"] == "측정 불가", s
ok("신호 다음 거래일 가격 없음 — 측정 불가")

# 23) 알 수 없는 진입 방식은 거부
try:
    P.simulate([ENTRY], "2026-09-11", -8.0, entry_mode="vwap")
    raise AssertionError("거부해야 한다")
except ValueError:
    pass
o, _, _ = P.parse_args(["--entry", "close"])
assert o["entry"] == "close" and P.DEFAULTS["entry"] == "next_open"
assert "다음날 시가" in P.rules_line(dict(P.DEFAULTS))
ok("entry_mode: next_open|close 만 허용, 규칙 설명 줄에 진입 방식이 찍힌다")

# 24) 날짜 색인(이분 탐색)을 주어도 결과가 선형 탐색과 같다
rows = [ENTRY, bar(D[0], 104.0, 106, 103, 105.0), bar(D[1], 105, 107, 90.0, 95.0),
        bar(D[2], 95, 96, 94, 95.0)]
idx = [r["date"] for r in rows]
for mode in ("next_open", "close"):
    for d in ("2026-09-10", "2026-09-11", "2026-09-12", D[1], D[2], "2026-12-01"):
        a = P.simulate(rows, d, -8.0, None, -15.0, entry_mode=mode)
        b = P.simulate(rows, d, -8.0, None, -15.0, entry_mode=mode, dates=idx)
        assert a == b, (mode, d, a, b)
ok("dates 색인: 이분 탐색과 선형 탐색이 모든 진입일·진입 방식에서 같은 결과")

# ---------------------------------------------------------------- 성과 지표
# 19) 손익비·기대값 — 손으로 검산
m = P.perf([{"r_multiple": 3.0, "mae": -0.05, "mfe": 0.3},
            {"r_multiple": -1.0, "mae": -0.09, "mfe": 0.01},
            {"r_multiple": -1.0, "mae": -0.09, "mfe": 0.02},
            {"r_multiple": -1.0, "mae": -0.09, "mfe": 0.03}])
assert abs(m["win_rate"] - 0.25) < 1e-9
assert abs(m["expectancy_r"] - 0.0) < 1e-9        # (3-1-1-1)/4 = 0
assert abs(m["payoff"] - 3.0) < 1e-9              # 3.0 / |-1.0|
ok("perf: 승률 25%·손익비 3:1 이면 기대값 0R (승률만 보면 거꾸로 읽는다)")

m = P.perf([{"r_multiple": 4.0, "mae": 0.0, "mfe": 0.0},
            {"r_multiple": -1.0, "mae": 0.0, "mfe": 0.0}])
assert abs(m["expectancy_r"] - 1.5) < 1e-9 and abs(m["payoff"] - 4.0) < 1e-9
assert m["best_r"] == 4.0 and m["worst_r"] == -1.0
ok("perf: 기대값이 양수여야 굴릴 수 있는 규칙이다")

# 20) 한쪽만 있으면 손익비는 정의하지 않는다 (무한대로 보고하지 않는다)
assert P.perf([{"r_multiple": 2.0, "mae": 0.0, "mfe": 0.0}])["payoff"] is None
assert P.perf([])["measured"] == 0 and "payoff" not in P.perf([])
ok("perf: 승리만·패배만인 표본에서 손익비를 만들어내지 않는다")

# ---------------------------------------------------------------- 비용·환산
# 21) 거래비용 — 한국은 매도 시 거래세가 더 붙는다
bk, sk = P.cost_mult("kr")
bu, su = P.cost_mult("us")
assert abs(bk - 1.0005) < 1e-12 and abs(bu - 1.0005) < 1e-12
assert abs(sk - (1 - 0.0023)) < 1e-12, sk        # 5bp + 18bp
assert abs(su - (1 - 0.0005)) < 1e-12
ok("cost_mult: 편도 수수료 + 한국 매도 거래세 0.18%")

# 22) 수익률 0%인 거래도 비용 때문에 손실이 된다
flat = {"market": "kr", "sim": {"status": "보유", "entry_date": "2026-09-11",
                                "exit_date": "2026-09-15", "entry": 1000.0,
                                "exit": 1000.0, "ret": 0.0}}
P.value(flat, 1_000_000.0, {})
assert flat["krw_ret"] < 0, flat["krw_ret"]
assert abs(flat["krw_ret"] + 0.0028) < 1e-4      # 약 -0.28%
ok("value: 비용을 반영하면 본전 거래도 마이너스다 (회전율이 공짜가 아니다)")

# 23) 환율 — 주가가 그대로여도 환율이 오르면 원화 수익이 난다
fx = {"2026-09-11": 1300.0, "2026-09-15": 1320.0}
assert P.at_or_before(fx, "2026-09-13") == 1300.0     # 주말은 직전 영업일
assert P.at_or_before(fx, "2026-09-01") is None
us = {"market": "us", "sim": {"status": "보유", "entry_date": "2026-09-11",
                              "exit_date": "2026-09-15", "entry": 100.0,
                              "exit": 100.0, "ret": 0.0}}
P.value(us, 1_000_000.0, fx)
assert us["krw_ret"] > 0.014 and us["krw_ret"] < 0.0155, us["krw_ret"]
ok("value: 환차익을 분리해 원화 수익률로 낸다")

# 24) 환율을 못 받으면 미국 종목 원화 환산을 비운다 (1:1로 가정하지 않는다)
nofx = {"market": "us", "sim": {"status": "보유", "entry_date": "2026-09-11",
                                "exit_date": "2026-09-15", "entry": 100.0,
                                "exit": 110.0, "ret": 0.1}}
P.value(nofx, 1_000_000.0, {})
assert nofx["krw_ret"] is None and nofx["qty"] is None
ok("value: 환율 없음 — 원화 환산을 None으로")

# 25) 측정 불가 포지션에도 value가 안전하게 동작
bad = {"market": "us", "sim": {"status": "측정 불가", "reason": "x"}}
P.value(bad, 1_000_000.0, fx)
assert bad["krw_out"] is None and bad["krw_in"] == 1_000_000.0
ok("value: 측정 불가 포지션에서도 깨지지 않는다")

# ---------------------------------------------------------------- 인자
# 26) 인자 파싱 — 잘못된 부호를 거른다
o, mk, j = P.parse_args(["--stop", "-8", "--trail", "-15", "--no-target"])
assert o["stop"] == -8.0 and o["trail"] == -15.0 and o["target"] is None
assert mk == ["us", "kr"] and j is None
o, mk, _ = P.parse_args(["--market", "us", "--time-stop", "20"])
assert o["time-stop"] == 20 and mk == ["us"]
o, _, _ = P.parse_args(["--trail", "none"])
assert o["trail"] is None
ok("parse_args: --no-target / none / 시장 지정")

# 27) 가격·R 표기
assert P.price(13330.0) == "13,330" and P.price(68.59) == "68.59"
assert P.rfmt(None).strip() == "-" and P.rfmt(-1.0).strip() == "-1.00R"
assert P.rfmt(3.126).strip() == "+3.13R"
ok("price/rfmt: 원화 천단위, R은 부호 유지")

# 28) 규칙 설명 줄에 설계 손익비가 찍힌다
line = P.rules_line({"stop": -8.0, "target": 25.0, "trail": None,
                     "breakeven": None, "time-stop": None})
assert "3.1:1" in line, line
line = P.rules_line({"stop": -8.0, "target": None, "trail": -15.0,
                     "breakeven": 10.0, "time-stop": 20})
assert "고정 익절 없음" in line and "추격손절" in line and "본전손절" in line
ok("rules_line: 설계 손익비와 적용 규칙을 한 줄로 남긴다")

print("\n전체 통과")
