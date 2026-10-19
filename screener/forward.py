# -*- coding: utf-8 -*-
"""M9 실시간 모의 — PLAN.md §15. 사양을 동결하고, 매일 관측한 값으로 추가만 하는 기록을 남긴다.

    python screener/forward.py init      # 시작일(T0) 확정: forward/spec.json + 첫 행 (한 번만)
    python screener/forward.py update    # 새 거래일 행을 추가하고 SUMMARY.md 갱신 (매일, 멱등)
    python screener/forward.py verify    # 기록한 신호를 최신 자료로 다시 계산해 비교
    python screener/forward.py power     # 과거 이력으로 본 검정력(추적오차·최소 검출 차이)
    python screener/forward.py dry-run   # 임시 폴더에서 init → update 를 돌려 본다 (커밋 없음)

규칙은 연구와 똑같다: 신호는 t 일 종가로 내고(매크로는 t−1 값까지), 체결은 t+1 종가, 수익은 t+2 부터,
편도 5bp, 현금은 ^IRX 단기금리. 과거 성과는 소급해서 쓰지 않는다 — T0 종가에서 NAV=100 으로 시작한다.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, time, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import timing as T

ROOT = Path(__file__).resolve().parent.parent
FDIR = Path(os.environ.get("FORWARD_DIR") or ROOT / "forward")
COST_BP = 5.0
SPEC = {"M9": {"rule": "macro", "params": {"mode": "m9", "sma_n": 200, "cs_n": 200, "theta": 0.0, "h": 1.0}},
        "B1": {"rule": "sma", "params": {"n": 200, "off": 0.0}},
        "B0": {"rule": "hold", "params": {}}}
NAMES = ("M9", "B1", "B0")
LOG_COLS = ["date", "spy_close", "spy_adj", "r3m", "r10", "cs", "G", "CSon", "RTon", "sig_M9", "sig_B1",
            "w_M9", "w_B1", "w_B0", "ret_M9", "ret_B1", "ret_B0", "nav_M9", "nav_B1", "nav_B0",
            "hy_adj", "tsy_adj", "code_commit", "recorded_at"]
NAV0 = 100.0
CHECKPOINT_MONTHS = (12, 24, 36)
OPS_MIN_COVERAGE, OPS_MAX_MISMATCH = 0.98, 0.01


class SpecDrift(RuntimeError):
    pass


# ---------------------------------------------------------------- 계산
def golden_df(n: int = 900) -> pd.DataFrame:
    """사양 동결 확인용 고정 합성 자료 (난수 시드 고정)."""
    rng = np.random.default_rng(12345)
    idx = pd.bdate_range("2019-01-01", periods=n)
    tr = 100.0 * np.cumprod(1 + rng.normal(0.0004, 0.012, n))
    c = tr * np.linspace(1.0, 0.93, n)
    return pd.DataFrame({"O": c, "C": c, "TR": tr, "RF": 0.0001,
                         "CS": np.cumsum(rng.normal(0, 0.01, n)) - 1.0,
                         "R3M": 2.0 + np.abs(np.cumsum(rng.normal(0, 0.05, n))),
                         "R10": 3.5 + np.cumsum(rng.normal(0, 0.03, n))}, index=idx)


def compute(df: pd.DataFrame) -> dict[str, tuple[pd.Series, pd.Series, pd.Series]]:
    """이름 → (신호, 적용 비중, 일 수익률). 연구 코드(timing.run, 종가 체결·5bp)를 그대로 쓴다."""
    out = {}
    for name in ("M9", "B1"):
        spec = SPEC[name]
        sig = T.signal(df, spec["rule"], **spec["params"])
        r, w = T.run(df, sig, COST_BP, "close")
        out[name] = (sig, w, r)
    ret = df["TR"].pct_change()
    out["B0"] = (pd.Series(1.0, index=df.index), pd.Series(1.0, index=df.index), ret)
    return out


def golden_sha256() -> str:
    res = compute(golden_df())
    h = hashlib.sha256()
    for name in NAMES:
        for s in res[name]:
            h.update(np.round(s.fillna(-9.0).to_numpy(), 10).tobytes())
    return h.hexdigest()


def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, timeout=10).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


# ---------------------------------------------------------------- 기록
def spec_path(fdir: Path) -> Path:
    return fdir / "spec.json"


def log_path(fdir: Path) -> Path:
    return fdir / "log.csv"


def read_log(fdir: Path) -> pd.DataFrame:
    return pd.read_csv(log_path(fdir), parse_dates=["date"]) if log_path(fdir).exists() else pd.DataFrame(columns=LOG_COLS)


def load_spec(fdir: Path) -> dict:
    if not spec_path(fdir).exists():
        raise FileNotFoundError("forward/spec.json 이 없다 — 먼저 init 을 실행한다")
    return json.loads(spec_path(fdir).read_text(encoding="utf-8"))


def check_spec(spec: dict) -> None:
    if spec["spec"] != SPEC or spec["cost_bp"] != COST_BP:
        raise SpecDrift("규칙·비용 정의가 spec.json 과 다르다 — 사양을 바꾸려면 새 모의(새 T0)를 시작한다")
    if spec["golden_sha256"] != golden_sha256():
        raise SpecDrift("신호·체결 코드의 출력이 동결 시점과 다르다 — 사양을 바꾸려면 새 모의(새 T0)를 시작한다")


def _row(df: pd.DataFrame, res: dict, d: pd.Timestamp, nav_prev: dict | None, extra: dict, commit: str, now: str) -> dict:
    flags = T.macro_flags(df, **{k: v for k, v in SPEC["M9"]["params"].items() if k != "mode"})
    row = {"date": d, "spy_close": df["C"].loc[d], "spy_adj": df["TR"].loc[d], "r3m": df["R3M"].loc[d],
           "r10": df["R10"].loc[d], "cs": df["CS"].loc[d], "G": flags["G"].loc[d], "CSon": flags["CSon"].loc[d],
           "RTon": flags["RTon"].loc[d], "hy_adj": extra.get("hy_adj", np.nan), "tsy_adj": extra.get("tsy_adj", np.nan),
           "code_commit": commit, "recorded_at": now}
    for n in NAMES:
        sig, w, r = res[n]
        if n != "B0":
            row[f"sig_{n}"] = sig.loc[d]
        row[f"w_{n}"] = w.loc[d]
        if nav_prev is None:
            row[f"ret_{n}"], row[f"nav_{n}"] = np.nan, NAV0
        else:
            if np.isnan(r.loc[d]):
                raise ValueError(f"{d.date()} 수익률이 정의되지 않는다 ({n})")
            row[f"ret_{n}"] = r.loc[d]
            row[f"nav_{n}"] = nav_prev[n] * (1.0 + r.loc[d])
    return row


def credit_extra(d: pd.Timestamp) -> dict:
    p = T.CACHE / "macro_credit.csv"
    if not p.exists():
        return {}
    c = pd.read_csv(p, index_col=0, parse_dates=True)
    if d not in c.index:
        return {}
    return {"hy_adj": float(c.loc[d, T.CREDIT_TICKERS[0]]), "tsy_adj": float(c.loc[d, T.CREDIT_TICKERS[1]])}


def init(df: pd.DataFrame, fdir: Path, commit: str = "unknown", now: str | None = None) -> pd.Timestamp:
    """T0 = 자료의 마지막 거래일. 이미 시작했으면 거부한다 (재시작은 폴더를 보관하고 새로 시작)."""
    if spec_path(fdir).exists():
        raise FileExistsError("이미 시작했다 (spec.json 존재)")
    fdir.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    d0 = df.index[-1]
    res = compute(df)
    spec_path(fdir).write_text(json.dumps({
        "start_date": str(d0.date()), "spec": SPEC, "cost_bp": COST_BP, "golden_sha256": golden_sha256(),
        "code_commit": commit, "created_at": now,
        "note": "T0 종가에서 NAV=100. 과거 성과는 소급하지 않는다. 사양·코드가 바뀌면 이 모의는 무효(새 T0 로 재시작)."},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    pd.DataFrame([_row(df, res, d0, None, credit_extra(d0), commit, now)], columns=LOG_COLS).to_csv(log_path(fdir), index=False)
    return d0


def update(df: pd.DataFrame, fdir: Path, commit: str = "unknown", now: str | None = None) -> list[pd.Timestamp]:
    """마지막 기록일 뒤의 거래일을 추가한다. 이미 기록한 행은 건드리지 않는다 (멱등)."""
    spec = load_spec(fdir)
    check_spec(spec)
    log = read_log(fdir)
    last = log["date"].max()
    new = df.index[df.index > last]
    if len(new) == 0:
        return []
    now = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    res = compute(df)
    nav = {n: float(log[f"nav_{n}"].iloc[-1]) for n in NAMES}
    rows = []
    for d in new:
        row = _row(df, res, d, nav, credit_extra(d), commit, now)
        nav = {n: row[f"nav_{n}"] for n in NAMES}
        rows.append(row)
    pd.concat([log, pd.DataFrame(rows, columns=LOG_COLS)], ignore_index=True).to_csv(log_path(fdir), index=False)
    return list(new)


def verify(df: pd.DataFrame, fdir: Path) -> dict:
    """기록한 신호(그날 관측한 자료로 계산)를 지금의 전체 자료로 다시 계산해 비교한다."""
    log = read_log(fdir)
    res = compute(df)
    mism = []
    for n in ("M9", "B1"):
        s = res[n][0].reindex(log["date"]).to_numpy()
        diff = np.abs(s - log[f"sig_{n}"].to_numpy())
        bad = log["date"][~(diff <= 1e-9)]
        mism += [(str(d.date()), n) for d in bad]
    days = len(log)
    bad_days = len({d for d, _ in mism})
    return {"days": days, "mismatch_days": bad_days, "mismatch_rate": bad_days / days if days else 0.0, "detail": mism[:20]}


# ---------------------------------------------------------------- 요약
def nav_stats(log: pd.DataFrame, name: str) -> dict:
    nav = log[f"nav_{name}"].to_numpy(dtype=float)
    pk = np.maximum.accumulate(nav)
    yrs = (log["date"].iloc[-1] - log["date"].iloc[0]).days / 365.25
    cum = nav[-1] / nav[0] - 1.0
    w = log[f"w_{name}"].astype(float)
    return {"cum": cum, "cagr": (nav[-1] / nav[0]) ** (1 / yrs) - 1.0 if yrs >= 1 else None,
            "mdd": float((nav / pk - 1.0).min()), "exposure": float(w.iloc[1:].mean()) if len(w) > 1 else float(w.iloc[0]),
            "trades": int((w.diff().abs() > 1e-9).sum())}


def months_elapsed(log: pd.DataFrame) -> float:
    return (log["date"].iloc[-1] - log["date"].iloc[0]).days / 30.4375


def checkpoint_stats(log: pd.DataFrame) -> dict | None:
    """12개월 이상이면 M9−B1, M9−B0 의 연복리 차이 95% 구간(정상 블록 부트스트랩, 평균 블록 21일)."""
    if len(log) < 253:
        return None
    import timing_eval as TE
    out = {}
    idx = pd.DatetimeIndex(log["date"].iloc[1:])
    r = {n: pd.Series(log[f"ret_{n}"].iloc[1:].to_numpy(dtype=float), index=idx) for n in NAMES}
    for ref in ("B1", "B0"):
        bt = TE.bootstrap_diff(r["M9"], r[ref], B=1000, mean_block=21)
        out[ref] = {"cagr_diff_ci": bt["cagr_diff_ci"], "mdd_diff_ci": bt["mdd_diff_ci"]}
    return out


def spy_drawdown_episode(log: pd.DataFrame, thr: float = 0.10) -> bool:
    px = log["spy_close"].to_numpy(dtype=float)
    return bool((px / np.maximum.accumulate(px) - 1.0).min() <= -thr)


def build_summary(fdir: Path, ver: dict | None, trading_days_since_start: int | None = None) -> str:
    spec, log = load_spec(fdir), read_log(fdir)
    d = log.iloc[-1]
    st = {n: nav_stats(log, n) for n in NAMES}
    m = months_elapsed(log)
    cov = (len(log) - 1) / trading_days_since_start if trading_days_since_start else None
    pc = lambda x: "-" if x is None else f"{x * 100:+.2f}%"
    L = ["# M9 실시간 모의 — 요약", "",
         f"자동 생성 (PLAN.md §15). 시작(T0) {spec['start_date']} · 마지막 기록 {d['date'].date()} · 경과 {m:.1f}개월 · "
         f"기록 {len(log) - 1}거래일 · 사양 동결 코드 {spec['code_commit']} · 최근 기록 코드 {d['code_commit']}", "",
         "※ 연구용 모의 기록이며 투자 권고가 아니다. 세금·환율·계좌는 평가하지 않는다 (사용자 결정).", "",
         "## 오늘의 신호", "",
         f"- 오늘({d['date'].date()}) 종가 신호: M9 **{d['sig_M9']:.2f}** (G {d['G']:.0f} · 신용 {d['CSon']:.0f} · 금리인상회피 {d['RTon']:.0f}) · "
         f"B1(200일선) {d['sig_B1']:.0f}",
         f"- 내일 수익에 반영되는 보유 비중(이미 확정): M9 {d['w_M9']:.2f} · B1 {d['w_B1']:.0f} · B0 1",
         f"- 모의 체결 규칙: **다음 거래일 종가**에 SPY 비중을 M9 {d['sig_M9']:.2f} (현금 {1 - d['sig_M9']:.2f}) 로 맞추면 그 다음 날부터 수익에 반영", "",
         "## 성과 (T0 종가 NAV=100, 편도 5bp)", "",
         "| | NAV | 누적 | 연복리(1년↑) | MDD | 평균 SPY 비중 | 비중 변경 |", "|---|---|---|---|---|---|---|"]
    for n in NAMES:
        L.append(f"| {n} | {d[f'nav_{n}']:.2f} | {pc(st[n]['cum'])} | {pc(st[n]['cagr'])} | {pc(st[n]['mdd'])} | "
                 f"{st[n]['exposure']:.2f} | {st[n]['trades']} |")
    L += ["", f"M9 − B1 누적 {pc(st['M9']['cum'] - st['B1']['cum'])} · M9 − B0 누적 {pc(st['M9']['cum'] - st['B0']['cum'])} · "
          f"기간 중 SPY −10% 이상 낙폭 {'있음' if spy_drawdown_episode(log) else '아직 없음'}", "",
          "## 운영 점검 (§15)", ""]
    if ver is not None:
        L.append(f"- 신호 재현성: 기록 {ver['days']}일 중 재계산과 다른 날 {ver['mismatch_days']}일 ({ver['mismatch_rate'] * 100:.2f}%, 허용 {OPS_MAX_MISMATCH * 100:.0f}% 이하)"
                 + (f" — {ver['detail'][:5]}" if ver['detail'] else ""))
    if cov is not None:
        L.append(f"- 기록 완결성: {cov * 100:.1f}% (허용 {OPS_MIN_COVERAGE * 100:.0f}% 이상)")
    L += ["", "## 체크포인트", ""]
    ck = checkpoint_stats(log)
    for months in CHECKPOINT_MONTHS:
        L.append(f"- {months}개월: {'도달' if m >= months else f'{max(0.0, months - m):.1f}개월 남음'}")
    if ck:
        for ref in ("B1", "B0"):
            lo, hi = ck[ref]["cagr_diff_ci"]
            L.append(f"- M9 − {ref} 연복리 차이 95% 구간 [{lo * 100:+.2f}%, {hi * 100:+.2f}%] (정상 블록 부트스트랩) — 36개월 전에는 판정에 쓰지 않는다")
    else:
        L.append("- 12개월 미만 — 통계 구간 미산출")
    L += ["", "판정 규칙은 PLAN.md §15 에 사전 등록되어 있다. 이 요약은 규칙을 바꾸지 않는다.", ""]
    return "\n".join(L)


# ---------------------------------------------------------------- 자료와 명령
def load_df() -> pd.DataFrame:
    """Actions 에서만 받을 수 있다: SPY·^IRX·재무부·펀드를 받아 SPY 거래일에 맞춘 프레임."""
    T.fetch(with_french=False)
    T.fetch_macro()
    spy = T.load_spy()
    df = spy.join(T.load_macro(spy.index))
    try:
        from zoneinfo import ZoneInfo
        now_et = datetime.now(ZoneInfo("America/New_York"))
        if df.index[-1].date() == now_et.date() and now_et.time() < time(16, 30):
            df = df.iloc[:-1]                              # 장중 미완성 봉은 쓰지 않는다
    except Exception:
        pass
    return df


def trading_days_after(df: pd.DataFrame, start: pd.Timestamp) -> int:
    return int((df.index > start).sum())


def power(df: pd.DataFrame) -> None:
    """과거 이력(평가 1994-05-02~)으로 본 M9−B1, M9−B0 추적오차와 최소 검출 차이 (95%, 단측 아님)."""
    res = compute(df)
    lo = pd.Timestamp(T.EVAL_START["spy"])
    r = {n: res[n][2].loc[lo:] for n in NAMES}
    print(f"\n  [검정력 · 과거 {lo.date()}~{df.index[-1].date()} 일수익 기준]")
    for ref in ("B1", "B0"):
        d = r["M9"] - r[ref]
        te = float(d.std(ddof=1) * np.sqrt(252))
        print(f"  M9 − {ref}: 연 추적오차 {te * 100:.2f}% · 과거 연복리 차이 {(T.cagr(r['M9']) - T.cagr(r[ref])) * 100:+.2f}%p")
        print("    기간(년)  최소 검출 연복리 차이(≈1.96×TE/√N)")
        for yrs in (1, 3, 5, 10, 20):
            print(f"    {yrs:>5}    ±{1.96 * te / np.sqrt(yrs) * 100:5.2f}%p")


def cmd(argv: list[str]) -> int:
    c = argv[0] if argv else ""
    fdir = Path(argv[argv.index("--dir") + 1]) if "--dir" in argv else FDIR
    if c == "power":
        power(load_df())
        return 0
    if c in ("init", "update", "verify"):
        df = load_df()
        commit = git_commit()
        if c == "init":
            d0 = init(df, fdir, commit)
            print(f"  시작(T0) {d0.date()} · 사양 동결 · forward/log.csv 첫 행")
        elif not spec_path(fdir).exists():
            print("  아직 시작하지 않았다 (init 필요) — 아무것도 하지 않는다")
            return 0
        if c in ("update", "init"):
            added = update(df, fdir, commit) if c == "update" else []
            print(f"  추가한 거래일 {len(added)}개: {[str(d.date()) for d in added][:5]}")
        ver = verify(df, fdir)
        print(f"  재현성: {ver['mismatch_days']}/{ver['days']}일 불일치 {ver['detail'][:5]}")
        (fdir / "SUMMARY.md").write_text(build_summary(fdir, ver, trading_days_after(df, pd.Timestamp(load_spec(fdir)['start_date']))),
                                         encoding="utf-8")
        print((fdir / "SUMMARY.md").read_text(encoding="utf-8"))
        return 0
    if c == "dry-run":
        df = load_df()
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            d0 = init(df.iloc[:-3], td, git_commit())
            added = update(df, td, git_commit())
            ver = verify(df, td)
            print(f"  dry-run: T0 {d0.date()} → 추가 {[str(d.date()) for d in added]} · 불일치 {ver['mismatch_days']}/{ver['days']}")
            print(build_summary(td, ver, trading_days_after(df, d0)))
        return 0
    sys.exit(__doc__)


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(cmd(sys.argv[1:]))
