# -*- coding: utf-8 -*-
"""forward.py 단위 테스트 (네트워크 없음).

    python screener/test_forward.py

실시간 모의의 약속을 확인한다: 연구와 같은 수익 계산, 추가만 하는 멱등 기록, 사양 동결(변경 시 거부),
관측값 기록과 재계산의 비교(재현성), 시작일 이전 성과 비소급.
"""
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import forward as F
import timing as T

ok = lambda m: print("  OK  " + m)
close = lambda a, b, tol=1e-9: abs(a - b) <= tol
ROOT = Path(__file__).resolve().parent.parent

# 0) PLAN §15 와 같은 사양
plan = (ROOT / "PLAN.md").read_text(encoding="utf-8")
assert F.SPEC["M9"]["params"] == {"mode": "m9", "sma_n": 200, "cs_n": 200, "theta": 0.0, "h": 1.0}
assert F.SPEC["B1"]["params"] == {"n": 200, "off": 0.0} and F.COST_BP == 5.0
assert "§15" in plan and "골든" in plan and "NAV=100" in plan
assert F.CHECKPOINT_MONTHS == (12, 24, 36) and "12·24·36개월" in plan and "36개월" in plan
assert (F.OPS_MIN_COVERAGE, F.OPS_MAX_MISMATCH) == (0.98, 0.01)
ok("사양·체크포인트·운영 기준이 PLAN §15 와 같다")

# 1) 사양 동결 해시 (신호·체결 코드의 출력이 바뀌면 이 값이 바뀐다)
GOLDEN = "bcbb9810b2baa79d89884ec2448437eeb1ebad56c200fa249e90a53922fea6a5"
assert F.golden_sha256() == F.golden_sha256()
if GOLDEN != "FILL_ME":
    assert F.golden_sha256() == GOLDEN, "신호·체결 코드의 출력이 바뀌었다 — 모의를 새로 시작해야 한다"
ok("golden_sha256: 같은 코드는 같은 값")

df = F.golden_df(900)
T0_ROW = 500

with tempfile.TemporaryDirectory() as td:
    fdir = Path(td)
    # 2) 업데이트가 로그를 쌓고, 수익이 연구 계산과 같다 (소급 없음)
    d0 = F.init(df.iloc[:T0_ROW], fdir, "abc1234", "2026-01-01T00:00:00+00:00")
    assert d0 == df.index[T0_ROW - 1]
    log = F.read_log(fdir)
    assert len(log) == 1 and log["nav_M9"].iloc[0] == 100.0 and np.isnan(log["ret_M9"].iloc[0])
    try:
        F.init(df.iloc[:T0_ROW], fdir)
        assert False
    except FileExistsError:
        pass
    added = F.update(df.iloc[:T0_ROW + 5], fdir, "abc1234")
    assert len(added) == 5 and added[0] == df.index[T0_ROW]
    assert F.update(df.iloc[:T0_ROW + 5], fdir, "abc1234") == []                   # 멱등
    added2 = F.update(df, fdir, "abc1234")
    assert len(added2) == 900 - T0_ROW - 5
    log = F.read_log(fdir)
    assert len(log) == 900 - T0_ROW + 1 and log["date"].is_monotonic_increasing and log["date"].is_unique
    res = F.compute(df)
    for n in F.NAMES:
        r = res[n][2].loc[df.index[T0_ROW]:]
        nav = 100.0 * (1.0 + r).cumprod()
        assert np.allclose(log[f"nav_{n}"].iloc[1:].to_numpy(), nav.to_numpy(), rtol=1e-12), n
    assert (log["w_B0"] == 1.0).all()
    ok("init/update: NAV=100 시작, 연구 계산과 일치, 멱등, 중복·역순 없음")

    # 3) 이미 쓴 행은 건드리지 않는다 (기록 시점 값 보존)
    before = F.read_log(fdir).iloc[:50].copy()
    df_rev = df.copy()
    df_rev.loc[df_rev.index[T0_ROW:T0_ROW + 50], "CS"] += 5.0                      # 지난 자료가 사후에 수정된 상황
    F.update(df_rev, fdir, "abc1234")
    after = F.read_log(fdir).iloc[:50]
    assert before.equals(after)
    ok("append-only: 이미 기록한 행은 자료가 수정돼도 그대로")

    # 4) 재현성 점검: 같은 자료면 불일치 0, 과거 자료가 바뀌면 불일치를 센다
    v0 = F.verify(df, fdir)
    assert v0["mismatch_days"] == 0 and v0["days"] == len(log)
    df_bad = df.copy()
    df_bad["C"] = df_bad["C"] * np.where(np.arange(len(df_bad)) % 9 == 0, 0.70, 1.0)         # 과거 종가가 사후에 바뀐 상황
    v1 = F.verify(df_bad, fdir)
    assert v1["mismatch_days"] > 0 and v1["mismatch_rate"] > F.OPS_MAX_MISMATCH and v1["detail"]
    ok("verify: 동일 자료 불일치 0 · 사후 수정은 불일치로 드러남")

    # 5) 사양 동결: 정의나 코드가 바뀌면 거부
    sp = json.loads(F.spec_path(fdir).read_text(encoding="utf-8"))
    sp["golden_sha256"] = "0" * 64
    F.spec_path(fdir).write_text(json.dumps(sp), encoding="utf-8")
    try:
        F.update(df, fdir)
        assert False
    except F.SpecDrift:
        pass
    sp["golden_sha256"] = F.golden_sha256()
    sp["cost_bp"] = 7.0
    F.spec_path(fdir).write_text(json.dumps(sp), encoding="utf-8")
    try:
        F.update(df, fdir)
        assert False
    except F.SpecDrift:
        pass
    sp["cost_bp"] = 5.0
    F.spec_path(fdir).write_text(json.dumps(sp), encoding="utf-8")
    old = dict(F.SPEC["M9"]["params"])
    F.SPEC["M9"]["params"]["h"] = 1.5
    try:
        F.update(df, fdir)
        assert False
    except F.SpecDrift:
        pass
    finally:
        F.SPEC["M9"]["params"].update(old)
    ok("spec 동결: 해시·비용·파라미터가 바뀌면 SpecDrift")

    # 6) 요약
    text = F.build_summary(fdir, v0, 900 - T0_ROW)
    for needle in ("M9 실시간 모의", "오늘의 신호", "다음 거래일 종가", "신호 재현성", "기록 완결성", "체크포인트", "M9 − B1 연복리 차이"):
        assert needle in text, needle
    ck = F.checkpoint_stats(F.read_log(fdir))
    assert ck is not None and set(ck) == {"B1", "B0"} and ck["B1"]["cagr_diff_ci"][0] <= ck["B1"]["cagr_diff_ci"][1]
    ok("build_summary / checkpoint_stats(12개월↑ 부트스트랩)")

with tempfile.TemporaryDirectory() as td:
    fdir = Path(td)
    F.init(df.iloc[:T0_ROW], fdir)
    F.update(df.iloc[:T0_ROW + 10], fdir)
    assert F.checkpoint_stats(F.read_log(fdir)) is None                              # 12개월 미만이면 구간을 내지 않는다
    assert "12개월 미만" in F.build_summary(fdir, None, 10)
    st = F.nav_stats(F.read_log(fdir), "B0")
    assert st["cagr"] is None and close(st["cum"], F.read_log(fdir)["nav_B0"].iloc[-1] / 100.0 - 1.0)
ok("12개월 미만: 통계 구간 미산출, 누적만 표시")

# 7) 검정력 출력이 돈다 (연구 평가 구간을 합성 자료에 맞춰서)
saved = dict(T.EVAL_START)
T.EVAL_START["spy"] = "2020-06-01"
try:
    F.power(df)
finally:
    T.EVAL_START.clear(); T.EVAL_START.update(saved)
ok("power: 추적오차와 최소 검출 차이 출력")

print("\n모두 통과")
