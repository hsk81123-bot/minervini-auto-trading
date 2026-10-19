# -*- coding: utf-8 -*-
"""SPY 타이밍 엔진 — PLAN.md §4~§6 의 정의를 그대로 구현한다 (정의는 v3 커밋에서 동결).

데이터(Actions 에서만 받을 수 있다):
  spy.csv     SPY 일봉 (무조정 시가·종가 + 수정종가)         → O, C, TR
  irx.csv     ^IRX 13주 국채 할인율(%)                       → RF (BEY 환산, 전일 값)
  french.csv  Ken French 일간 Mkt-RF, RF (%)                 → 보류용 TR, RF

규칙 신호 sig[t]: 종가 t 까지의 정보로 낸 SPY 목표 비중 (0~1). 나머지는 현금(RF).
체결: 종가 체결 w_eff[d] = sig[d-2] (신호 종가 d-2 → 체결 종가 d-1 → 수익 d 일부터),
      시가 체결 w[d]    = sig[d-1] 이 d 일 시가부터.
"""
import hashlib
import io
import os
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("TIMING_CACHE") or ROOT / ".cache-timing")
SPY_START = "1993-01-01"
IRX_START = "1960-01-01"
EVAL_START = {"spy": "1994-05-02", "french": "1927-11-01"}
FRENCH_URL = ("https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
              "F-F_Research_Data_Factors_daily_CSV.zip")
COST_BP = 5.0


# ---------------------------------------------------------------- 내려받기 (Actions)
def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _flat(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    return df


def fetch() -> dict[str, str]:
    """원자료를 받아 CSV 로 저장하고 SHA-256 을 돌려준다 (원본은 커밋하지 않는다)."""
    import yfinance as yf
    import requests

    CACHE.mkdir(parents=True, exist_ok=True)
    hashes = {}
    spy = _flat(yf.download("SPY", start=SPY_START, interval="1d", auto_adjust=False,
                            progress=False))
    spy = spy[["Open", "High", "Low", "Close", "Adj Close", "Volume"]].dropna(subset=["Close"])
    spy.to_csv(CACHE / "spy.csv")
    irx = _flat(yf.download("^IRX", start=IRX_START, interval="1d", auto_adjust=False,
                            progress=False))[["Close"]].dropna()
    irx.to_csv(CACHE / "irx.csv")
    for name in ("spy.csv", "irx.csv"):
        hashes[name] = sha256(CACHE / name)
    try:
        r = requests.get(FRENCH_URL, timeout=60)
        r.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(r.content))
        fr = parse_french(z.read(z.namelist()[0]).decode("latin-1"))
        fr.to_csv(CACHE / "french.csv")
        hashes["french.csv"] = sha256(CACHE / "french.csv")
    except Exception as e:                       # 보류는 참고로 격하 (PLAN §8 S0)
        print(f"  French 자료를 받지 못했다: {type(e).__name__}: {e}", flush=True)
    return hashes


def parse_french(text: str) -> pd.DataFrame:
    """French 일간 CSV 본문 → DataFrame(index=날짜, Mkt-RF, SMB, HML, RF) 단위 %."""
    rows = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 5 and parts[0].isdigit() and len(parts[0]) == 8:
            rows.append((pd.Timestamp(parts[0]), *map(float, parts[1:5])))
    df = pd.DataFrame(rows, columns=["date", "Mkt-RF", "SMB", "HML", "RF"]).set_index("date")
    return df.sort_index()


FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"
FRED_IDS = ("BAA10Y", "T10Y3M", "DGS10")      # 무디스 Baa−10년 국채 스프레드(%), 10년−3개월(%), 10년 금리(%)


def parse_fred(text: str) -> pd.Series:
    """FRED 그래프 CSV(첫 열 날짜, 둘째 열 값, 결측은 '.' 또는 빈칸) → 일별 시리즈."""
    df = pd.read_csv(io.StringIO(text), na_values=[".", ""])
    s = pd.Series(pd.to_numeric(df.iloc[:, 1], errors="coerce").to_numpy(),
                  index=pd.to_datetime(df.iloc[:, 0]), name=str(df.columns[1]))
    return s.sort_index()


def fetch_macro() -> dict[str, str]:
    """금리·스프레드 원자료를 받아 CSV 로 저장하고 SHA-256 을 돌려준다 (Actions 전용)."""
    import requests

    CACHE.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for sid in FRED_IDS:
        r = requests.get(FRED_URL.format(sid), timeout=60, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        s = parse_fred(r.text)
        s.to_csv(CACHE / f"fred_{sid}.csv", header=True)
        hashes[f"fred_{sid}.csv"] = sha256(CACHE / f"fred_{sid}.csv")
    return hashes


def macro_aligned(s: pd.Series, index: pd.DatetimeIndex, max_gap: int = 5, lag: int = 1) -> pd.Series:
    """거래일에 맞춘 매크로 값. 직전 값을 최대 max_gap 거래일 채우고, 발표 지연을 보수적으로
    보아 lag 거래일 늦춘다 (t 일 종가 신호는 t−lag 일자까지의 값만 쓴다)."""
    union = s.index.union(index)
    return s.reindex(union).ffill(limit=max_gap).reindex(index).shift(lag)


def load_macro(index: pd.DatetimeIndex, cache: Path | None = None) -> pd.DataFrame:
    cache = cache or CACHE
    cols = {}
    for sid in FRED_IDS:
        raw = pd.read_csv(cache / f"fred_{sid}.csv", index_col=0, parse_dates=True).iloc[:, 0]
        cols[sid] = macro_aligned(raw.astype("float64"), index)
    return pd.DataFrame(cols, index=index)


# ---------------------------------------------------------------- 적재
def bey(discount_pct: pd.Series | float):
    """13주 국채 할인율(%) → 채권수익률(BEY, 소수). 91일 기준 P = 100(1 − d·91/360)."""
    p = 100.0 * (1.0 - np.asarray(discount_pct, dtype="float64") / 100.0 * 91.0 / 360.0)
    return (100.0 / p - 1.0) * 365.0 / 91.0


def rf_series(irx_pct: pd.Series, index: pd.DatetimeIndex, max_gap: int = 5
              ) -> tuple[pd.Series, dict]:
    """거래일마다 일 무위험 수익률 — 전일 IRX 를 BEY 로 바꿔 달력일수만큼 복리.
    결측은 직전값(최대 max_gap 거래일), 음수 금리는 0. 반환: (RF, 점검 정보)."""
    union = irx_pct.index.union(index)
    y = irx_pct.reindex(union).ffill(limit=max_gap).reindex(index)
    if y.isna().any():
        raise ValueError(f"IRX 결측이 {max_gap}거래일을 넘는다: {y[y.isna()].index[:3].tolist()}")
    info = {"negative": int((y < 0).sum()), "zero": int((y == 0).sum()),
            "filled": int((~index.isin(irx_pct.index)).sum())}
    prev = y.clip(lower=0).shift(1)
    days = pd.Series(index, index=index).diff().dt.days
    rf = (1.0 + pd.Series(bey(prev), index=index)) ** (days / 365.0) - 1.0
    rf.iloc[0] = 0.0
    return rf, info


def load_spy(cache: Path | None = None) -> pd.DataFrame:
    cache = cache or CACHE
    spy = pd.read_csv(cache / "spy.csv", index_col=0, parse_dates=True).sort_index()
    irx = pd.read_csv(cache / "irx.csv", index_col=0, parse_dates=True)["Close"].sort_index()
    rf, info = rf_series(irx, spy.index)
    df = pd.DataFrame({"O": spy["Open"].astype("float64"), "C": spy["Close"].astype("float64"),
                       "TR": spy["Adj Close"].astype("float64"), "RF": rf})
    df.attrs["irx_info"] = info
    return df


def load_french(cache: Path | None = None) -> pd.DataFrame:
    """보류용: 시장 총수익 = Mkt-RF + RF (일간 %), 지수 TR=C 로 만든다 (시가 없음)."""
    cache = cache or CACHE
    fr = pd.read_csv(cache / "french.csv", index_col=0, parse_dates=True).sort_index()
    ret = (fr["Mkt-RF"] + fr["RF"]) / 100.0
    tr = 100.0 * (1.0 + ret).cumprod()
    return pd.DataFrame({"O": np.nan, "C": tr, "TR": tr, "RF": fr["RF"] / 100.0})


# ---------------------------------------------------------------- 지표
def month_end_flags(index: pd.DatetimeIndex) -> np.ndarray:
    """그 달의 마지막 거래일이면 True (데이터의 마지막 행 포함)."""
    s = pd.Series(index.to_period("M"), index=index)
    return (s != s.shift(-1)).to_numpy()


def vol_ann(tr: pd.Series, window: int = 21) -> pd.Series:
    """σ̂ = 일수익률 window 개(당일 포함) 표본표준편차(ddof=1) × √252."""
    return tr.pct_change().rolling(window).std(ddof=1) * np.sqrt(252.0)


def rsi2(c: pd.Series) -> pd.Series:
    """Wilder RSI(2): 평활 alpha=1/2, adjust=False. 평균 하락이 0 이면 100."""
    d = c.diff()
    ag = d.clip(lower=0.0).ewm(alpha=0.5, adjust=False).mean()
    al = (-d).clip(lower=0.0).ewm(alpha=0.5, adjust=False).mean()
    out = 100.0 - 100.0 / (1.0 + ag / al)
    out[al == 0.0] = 100.0
    out[d.isna()] = np.nan
    return out


def _monthly_to_daily(index: pd.DatetimeIndex, me: np.ndarray, vals: np.ndarray) -> pd.Series:
    s = pd.Series(vals, index=index[me])
    return s.reindex(index).ffill()


# ---------------------------------------------------------------- 신호 (PLAN §6)
def sig_sma(df: pd.DataFrame, n: int = 200, off: float = 0.0) -> pd.Series:
    """B1: 종가 > n일 SMA(엄격) → 1, 아니면 off. SMA 정의 전은 NaN."""
    sma = df["C"].rolling(n).mean()
    out = pd.Series(np.where(df["C"] > sma, 1.0, off), index=df.index)
    out[sma.isna()] = np.nan
    return out


def sig_faber(df: pd.DataFrame, months: int = 10, off: float = 0.0) -> pd.Series:
    """B3: 월말 종가 > 직전 months 개 월말 종가 평균(당월 포함) → 다음 달 1, 아니면 off."""
    me = month_end_flags(df.index)
    cm = df["C"][me]
    avg = cm.rolling(months).mean()
    vals = np.where(cm > avg, 1.0, off).astype("float64")
    vals[avg.isna().to_numpy()] = np.nan
    return _monthly_to_daily(df.index, me, vals)


def sig_absmom(df: pd.DataFrame, months: int = 12, off: float = 0.0) -> pd.Series:
    """A1: 월말 TR 의 months 개월 수익률 > 같은 기간 RF 복리 수익률 → 1, 아니면 off (동률은 off)."""
    me = month_end_flags(df.index)
    trm = df["TR"][me]
    tbm = (1.0 + df["RF"]).cumprod()[me]
    ret = trm / trm.shift(months) - 1.0
    tb = tbm / tbm.shift(months) - 1.0
    vals = np.where(ret > tb, 1.0, off).astype("float64")
    vals[ret.isna().to_numpy() | tb.isna().to_numpy()] = np.nan
    return _monthly_to_daily(df.index, me, vals)


def apply_band(tgt: pd.Series, band: float = 0.05) -> pd.Series:
    """직전 비중과 band 이상 차이나거나 (tgt==1 이고 직전≠1) 일 때만 비중을 tgt 로 바꾼다.
    처음 정의되는 날 직전 비중은 1. tgt 가 NaN 인 날은 NaN."""
    out = pd.Series(np.nan, index=tgt.index)
    prev = 1.0
    for i, v in enumerate(tgt.to_numpy()):
        if np.isnan(v):
            continue
        if abs(v - prev) >= band - 1e-12 or (v == 1.0 and prev != 1.0):
            prev = v
        out.iloc[i] = prev
    return out


def sig_voltarget(df: pd.DataFrame, window: int = 21, target: float = 0.15,
                  band: float = 0.05, floor: float = 0.01) -> pd.Series:
    """A2: tgt = min(1, target/max(σ̂,floor)) 를 apply_band 로 걸러 거래 횟수를 줄인다."""
    sigma = vol_ann(df["TR"], window)
    tgt = np.minimum(1.0, target / np.maximum(sigma, floor))
    tgt[sigma.isna()] = np.nan
    return apply_band(pd.Series(tgt, index=df.index), band)


def sig_trendvol(df: pd.DataFrame, sma_n: int = 200, vol_thr: float = 0.20,
                 off: float = 0.0, window: int = 21) -> pd.Series:
    """A3: (종가 < SMA 이고 σ̂ > vol_thr) 이면 off, 그 외 1. 둘 다 엄격 부등호."""
    sma = df["C"].rolling(sma_n).mean()
    sg = vol_ann(df["TR"], window)
    bad = (df["C"] < sma) & (sg > vol_thr)
    out = pd.Series(np.where(bad, off, 1.0), index=df.index)
    out[sma.isna() | sg.isna()] = np.nan
    return out


def sig_pullback(df: pd.DataFrame, sma_n: int = 200, rsi_thr: float = 10.0,
                 exit_n: int = 5, off: float = 0.0) -> pd.Series:
    """A4: 종가 > SMA → 1(눌림목 해제). 아니면 off 이되 RSI(2)<thr 에서 눌림목 진입(비중 1),
    종가 > exit_n 일 SMA 에서 청산."""
    c = df["C"]
    sma = c.rolling(sma_n).mean()
    ex = c.rolling(exit_n).mean()
    rsi = rsi2(c)
    out = pd.Series(np.nan, index=df.index)
    pb = False
    for i in range(len(c)):
        if np.isnan(sma.iloc[i]) or np.isnan(rsi.iloc[i]):
            continue
        if c.iloc[i] > sma.iloc[i]:
            out.iloc[i] = 1.0
            pb = False
            continue
        if not pb and rsi.iloc[i] < rsi_thr:
            pb = True
        elif pb and c.iloc[i] > ex.iloc[i]:
            pb = False
        out.iloc[i] = 1.0 if pb else off
    return out


def sig_ddfilter(df: pd.DataFrame, mode: str = "and", sma_n: int = 200, x: float = 0.10,
                 n: int = 63, off: float = 0.0) -> pd.Series:
    """PLAN §12 D1~D4. G = 종가 > SMA(sma_n), Q = 종가 > (1−x)·최근 n일(당일 포함) 최고 종가 (둘 다 엄격).
    mode: q → Q · and → G·Q · or → max(G,Q) · avg → (G+Q)/2. 꺼짐 비중은 off. 정의 전은 NaN."""
    c = df["C"]
    g = (c > c.rolling(sma_n).mean()).astype("float64")
    g[c.rolling(sma_n).mean().isna()] = np.nan
    hi = c.rolling(n).max()
    q = (c > (1.0 - x) * hi).astype("float64")
    q[hi.isna()] = np.nan
    on = {"q": q, "and": g * q, "or": pd.concat([g, q], axis=1).max(axis=1, skipna=False),
          "avg": (g + q) / 2.0}[mode]
    return off + (1.0 - off) * on


RULES = {"sma": sig_sma, "faber": sig_faber, "absmom": sig_absmom, "voltarget": sig_voltarget,
         "trendvol": sig_trendvol, "pullback": sig_pullback, "ddfilter": sig_ddfilter}


def signal(df: pd.DataFrame, rule: str, **params) -> pd.Series:
    if rule == "hold":
        return pd.Series(1.0, index=df.index)
    if rule == "mix":
        return pd.Series(float(params["w"]), index=df.index)
    return RULES[rule](df, **params)


# ---------------------------------------------------------------- 체결과 일 손익
def run(df: pd.DataFrame, sig: pd.Series, cost_bp: float = COST_BP, mode: str = "close"
        ) -> tuple[pd.Series, pd.Series]:
    """(일 수익률, 적용 비중). mode='close': w_eff[d]=sig[d-2] · 'open': sig[d-1] 이 d 시가부터.

    r[d] = w·ret + (1−w)·RF − cost·|Δw|  (시가 체결은 갭/장중으로 나눠 계산).
    """
    cost = cost_bp / 1e4
    ret = df["TR"].pct_change()
    if mode == "close":
        w = sig.shift(2)
        r = w * ret + (1.0 - w) * df["RF"] - cost * (w - w.shift(1)).abs()
        return r, w
    if mode == "open":
        if df["O"].isna().all():
            raise ValueError("시가 자료가 없다")
        w = sig.shift(1)
        wp = w.shift(1)
        o_tr = df["O"] * df["TR"] / df["C"]
        gap = o_tr / df["TR"].shift(1) - 1.0
        intra = df["TR"] / o_tr - 1.0
        stock = (1.0 + wp * gap) * (1.0 + w * intra) - 1.0
        r = stock + (1.0 - (wp + w) / 2.0) * df["RF"] - cost * (w - wp).abs()
        return r, w
    raise ValueError(f"mode 는 close|open (받은 값: {mode})")


def static_mix(df: pd.DataFrame, e: float, cost_bp: float = COST_BP) -> pd.Series:
    """B2/B4: 월말 종가에 SPY e : 현금 1−e 로 리밸런싱, 사이에는 방치(흘러감). 일 수익률."""
    cost = cost_bp / 1e4
    ret = df["TR"].pct_change().fillna(0.0).to_numpy()
    rf = df["RF"].fillna(0.0).to_numpy()
    me = month_end_flags(df.index)
    s, c = e, 1.0 - e
    out = np.zeros(len(df))
    for i in range(len(df)):
        s *= 1.0 + ret[i]
        c *= 1.0 + rf[i]
        tot = s + c
        out[i] = tot - 1.0                     # 이 날 시작 총자산 1 기준
        s, c = s / tot, c / tot                # 다음 날 시작을 1 로 정규화
        if me[i]:
            turn = abs(s - e)
            out[i] -= cost * turn
            s, c = e, 1.0 - e
    return pd.Series(out, index=df.index)


# ---------------------------------------------------------------- 지표
def years_between(idx: pd.DatetimeIndex) -> float:
    return (idx[-1] - idx[0]).days / 365.25


def wealth(r: pd.Series) -> pd.Series:
    return (1.0 + r).cumprod()


def cagr(r: pd.Series) -> float:
    return float(wealth(r).iloc[-1] ** (1.0 / years_between(r.index)) - 1.0)


def max_dd(r: pd.Series) -> float:
    w = np.concatenate([[1.0], wealth(r).to_numpy()])
    return float((w / np.maximum.accumulate(w) - 1.0).min())


def summarize(r: pd.Series, rf: pd.Series, w: pd.Series | None = None) -> dict:
    yrs = years_between(r.index)
    n_per_year = len(r) / yrs
    ex = r - rf.reindex(r.index)
    out = {"cagr": cagr(r), "mdd": max_dd(r), "vol": float(r.std(ddof=1) * np.sqrt(n_per_year)),
           "sharpe": float(ex.mean() / ex.std(ddof=1) * np.sqrt(n_per_year)),
           "years": yrs}
    out["calmar"] = out["cagr"] / abs(out["mdd"]) if out["mdd"] else float("inf")
    yr = (1.0 + r).groupby(r.index.year).prod() - 1.0
    out["worst_year"] = (int(yr.idxmin()), float(yr.min()))
    if w is not None:
        w = w.reindex(r.index)
        out["exposure"] = float(w.mean())
        out["trades_per_year"] = float(((w - w.shift(1)).abs() > 1e-9).sum() / yrs)
    return out
