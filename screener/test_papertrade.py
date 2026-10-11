# -*- coding: utf-8 -*-
"""papertrade.py 단위 테스트 (네트워크 없음).

    python screener/test_papertrade.py

손절·익절 판정은 손으로 계산할 수 있는 일봉만 쓴다. 진입가 100, 손절 -5%(95),
익절 +10%(110) 기준.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import papertrade as P

ok = lambda m: print("  OK  " + m)


def bar(d, o, h, l, c):
    return {"date": d, "open": o, "high": h, "low": l, "close": c}


ENTRY = bar("2026-09-11", 99, 101, 98, 100.0)     # 진입일: 종가 100에 매수


def sim(*bars):
    return P.simulate([ENTRY, *bars], "2026-09-11", -5.0, 10.0)


# 1) 진입일 당일은 판정하지 않는다 (종가에 샀으므로)
s = sim(bar("2026-09-14", 100, 101, 99, 100.0))
assert s["status"] == "보유" and s["entry"] == 100.0
assert abs(s["ret"]) < 1e-9
ok("진입일 종가 매수 — 당일 고가·저가로는 체결 판정하지 않는다")

# 2) 장중 저가가 손절선을 건드리면 손절선 가격에 체결
s = sim(bar("2026-09-14", 99, 100, 94.0, 99.0))   # 종가는 -1%지만 저가 -6%
assert s["status"] == "손절" and abs(s["exit"] - 95.0) < 1e-9
assert abs(s["ret"] + 0.05) < 1e-9
assert "저가" in s["reason"]
ok("장중 저가 이탈 — 종가만 보면 놓치는 손절을 잡는다")

# 3) 장중 고가가 익절선을 건드리면 익절선 가격에 체결
s = sim(bar("2026-09-14", 101, 112.0, 100, 105.0))
assert s["status"] == "익절" and abs(s["exit"] - 110.0) < 1e-9
assert abs(s["ret"] - 0.10) < 1e-9
ok("장중 고가 도달 — 익절선 가격에 체결")

# 4) 갭하락: 시가가 이미 손절선 아래면 시가에 체결 (-5%보다 더 잃는다)
s = sim(bar("2026-09-14", 90.0, 92, 88, 91.0))
assert s["status"] == "손절" and abs(s["exit"] - 90.0) < 1e-9
assert abs(s["ret"] + 0.10) < 1e-9, s["ret"]
assert "갭하락" in s["reason"]
ok("갭하락 — 손절선을 건너뛰고 시가 체결 (손실이 -5%를 넘는다)")

# 5) 갭상승: 시가가 이미 익절선 위면 시가에 체결
s = sim(bar("2026-09-14", 120.0, 125, 119, 124.0))
assert s["status"] == "익절" and abs(s["exit"] - 120.0) < 1e-9
assert abs(s["ret"] - 0.20) < 1e-9
ok("갭상승 — 익절선을 넘겨 시가 체결")

# 6) 같은 날 양쪽을 건드리면 손절 우선 + 표시
s = sim(bar("2026-09-14", 100, 115.0, 90.0, 105.0))
assert s["status"] == "손절" and s["both_hit"] is True
ok("같은 날 손절선·익절선 모두 건드림 — 손절 우선, both_hit 표시")

# 6b) 갭 체결에는 both_hit를 붙이지 않는다 — 시가에서 이미 끝났으므로
#     그 날 반대쪽 선을 건드렸는지는 체결 뒤의 일이다
s = sim(bar("2026-09-14", 120.0, 125, 90.0, 100.0))   # 갭상승 익절 + 저가는 손절선
assert s["status"] == "익절" and s["both_hit"] is False, s
s = sim(bar("2026-09-14", 90.0, 115.0, 88, 100.0))    # 갭하락 손절 + 고가는 익절선
assert s["status"] == "손절" and s["both_hit"] is False, s
ok("갭 체결 — 순서 모호 표시를 붙이지 않는다 (시가에서 체결 완료)")

# 6c) 가격 표기 — 원화 수천원대가 지수표기로 깨지지 않는다
assert P.price(13330.0) == "13,330"
assert P.price(10700.4) == "10,700"
assert P.price(68.59) == "68.59"
assert P.price(0.5) == "0.50"
ok("price: 1000 이상은 천단위 정수, 그 아래는 소수 둘째 자리")

# 7) 먼저 온 날이 이긴다 (둘째 날 익절선이라도 첫날 손절이면 손절)
s = sim(bar("2026-09-14", 99, 100, 94, 96.0),
        bar("2026-09-15", 100, 120.0, 99, 118.0))
assert s["status"] == "손절" and s["exit_date"] == "2026-09-14"
ok("시간 순서대로 판정 — 첫 체결에서 멈춘다")

# 8) 미도달이면 보유 + 마지막 종가로 평가
s = sim(bar("2026-09-14", 100, 104, 97, 103.0),
        bar("2026-09-15", 103, 108, 96, 107.0))
assert s["status"] == "보유" and s["exit_date"] == "2026-09-15"
assert abs(s["ret"] - 0.07) < 1e-9 and s["days"] == 4
ok("손절·익절 미도달 — 마지막 종가로 평가수익률")

# 9) 진입일에 거래가 없으면 그 뒤 첫 거래일 종가로 산다
s = P.simulate([bar("2026-09-14", 49, 51, 48, 50.0),       # 진입일로 대체
                bar("2026-09-15", 51, 56.0, 50, 54.0)],    # 고가 56 ≥ 55
               "2026-09-11", -5.0, 10.0)
assert s["entry"] == 50.0 and s["entry_date"] == "2026-09-14"
assert s["status"] == "익절" and abs(s["exit"] - 55.0) < 1e-9   # 50 × 1.10
ok("진입일 휴장·거래정지 — 이후 첫 거래일 종가로 진입")

# 10) 가격이 없으면 측정 불가 (0%로 세지 않는다)
assert P.simulate([], "2026-09-11", -5.0, 10.0)["status"] == "측정 불가"
assert P.simulate([bar("2026-09-01", 1, 1, 1, 1.0)], "2026-09-11",
                  -5.0, 10.0)["status"] == "측정 불가"   # 진입일 이후가 없다
ok("가격 없음 — 측정 불가로 분리")

# 11) at_or_before — 휴장일 환율은 직전 영업일
fx = {"2026-09-11": 1300.0, "2026-09-15": 1320.0}
assert P.at_or_before(fx, "2026-09-11") == 1300.0
assert P.at_or_before(fx, "2026-09-13") == 1300.0     # 주말
assert P.at_or_before(fx, "2026-09-20") == 1320.0
assert P.at_or_before(fx, "2026-09-01") is None
assert P.at_or_before({}, "2026-09-11") is None
ok("at_or_before: 휴장일은 직전 영업일 값, 앞선 자료 없으면 None")

# 12) 원화 환산 — 환율이 오르면 현지 수익률보다 원화 수익률이 높다
pos = {"market": "us", "sim": {"status": "보유", "entry_date": "2026-09-11",
                               "exit_date": "2026-09-15", "entry": 100.0,
                               "exit": 100.0, "ret": 0.0}}
P.value(pos, 1_000_000.0, fx)
assert abs(pos["qty"] - 1_000_000 / 130_000) < 1e-9       # 100달러 × 1300원
assert abs(pos["krw_ret"] - (1320 / 1300 - 1)) < 1e-9     # 주가 그대로, 환율만
ok("원화 환산: 수량은 진입 환율로, 평가는 청산 환율로 (환차익 분리)")

# 13) 한국 종목은 환율을 적용하지 않는다
kr = {"market": "kr", "sim": {"status": "보유", "entry_date": "2026-09-11",
                              "exit_date": "2026-09-15", "entry": 1000.0,
                              "exit": 1100.0, "ret": 0.1}}
P.value(kr, 1_000_000.0, {})
assert abs(kr["qty"] - 1000.0) < 1e-9
assert abs(kr["krw_ret"] - 0.1) < 1e-9
ok("한국 종목: 환율 미적용, 현지 수익률 = 원화 수익률")

# 14) 환율을 못 받으면 미국 종목은 원화 환산을 비운다 (가짜 숫자를 내지 않는다)
nofx = {"market": "us", "sim": {"status": "보유", "entry_date": "2026-09-11",
                                "exit_date": "2026-09-15", "entry": 100.0,
                                "exit": 110.0, "ret": 0.1}}
P.value(nofx, 1_000_000.0, {})
assert nofx["krw_ret"] is None and nofx["qty"] is None
ok("환율 없음: 원화 환산을 None으로 (1:1로 가정하지 않는다)")

# 15) 측정 불가 포지션도 value가 깨지지 않는다
bad = {"market": "us", "sim": {"status": "측정 불가", "reason": "x"}}
P.value(bad, 1_000_000.0, fx)
assert bad["krw_out"] is None and bad["krw_in"] == 1_000_000.0
ok("측정 불가 포지션에도 value가 안전하게 동작")

print("\n전체 통과")
