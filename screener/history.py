# -*- coding: utf-8 -*-
"""3단계: 과거 추천 리스트 재구성 (미국) — 여러 해, 하락장 포함.

git 에 쌓인 스냅샷은 2026-08-30 부터라 6주·상승장 하나·고유 18종목뿐이다. 그
표본으로는 전략의 실력과 몇 종목의 운을 구분할 수 없었다(1단계 실패). 여기서는
과거 가격으로 '그 주에 스크리너를 돌렸다면 나왔을 리스트'를 다시 만든다.

설계 근거:
- 그 날까지의 데이터만 쓴다. 지표는 종목별 일봉에서 rolling/shift 로만 계산하고,
  각 신호일에는 그 날 행만 읽는다. VCP 는 그 날까지 2년 구간을 잘라 넘긴다.
- 스크리너와 같은 계산이어야 한다. 임계값은 screener.CFG 를, VCP 는 vcp.detect_vcp
  를 그대로 쓴다. 지표 식은 screener.trend_template / rs_raw_score 와 같은 값을
  내는지 테스트로 묶었다(test_history.py).
- 분할: yfinance 수정주가는 '그 뒤에 일어난 분할'까지 소급 반영한다. NVDA 의
  2021년 가격이 $200 이 아니라 $5 로 나와 $10 가격 필터에서 잘못 빠진다. 가격·
  거래량 필터에는 그 날 이후 분할을 되돌린 값을 쓴다. 이동평균·RS 같은 비율
  지표는 분할 수정 가격이 맞다. 배당 수정분(몇 %)은 되돌리지 않는다.
- 주 1회(그 주 마지막 거래일) 신호를 만든다. 진입은 다음 거래일 시가다.
- 충실도 점검: git 에 실제 스냅샷이 있는 날은 같은 날짜로도 재구성해, 통과 종목·
  RS·상위 5가 실제와 얼마나 같은지 잰다. 재구성이 실제와 다르면 과거 결과도
  믿을 수 없다.
- 생존 편향: 유니버스는 '지금' 상장된 종목(tickers_us.json)이다. 그 사이 상장
  폐지된 종목은 빠진다. 폐지 종목은 대개 망한 종목이라 결과가 좋게 기운다. 무료
  데이터로는 고칠 수 없어 보고서에 항상 적는다.

사용법:
    python screener/history.py download [--start 2018-01-01]   # → .cache/us_prices.pkl
    python screener/history.py rebuild                         # → .cache/us_snaps.json.gz
    python screener/history.py fidelity                        # 재구성 vs 실제 스냅샷
    python screener/history.py validate --stop -8 --no-target --trail -15 [--json f]
"""
import gzip
import json
import pickle
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from statistics import mean

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backtest as B
import papertrade as P
import robust as R
import validate as V

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache"
PRICES = CACHE / "us_prices.pkl"
SNAPS = CACHE / "us_snaps.json.gz"
FIDELITY = CACHE / "us_fidelity.json"
UNIVERSE = ROOT / "docs" / "data" / "tickers_us.json"

DATA_START = "2018-01-01"
SIGNAL_LAG_DAYS = 400     # 첫 신호는 데이터 시작 + 이만큼 뒤 (260거래일 이력 필요)
MIN_ROWS_2Y = 260         # screener.download_history(min_len=260), 2년 구간 기준
WINDOW_2Y = "730D"        # screener 는 period="2y" 로 받는다
TIE_N = 10                # VCP 를 계산할 범위: RS 상위 10번째 종목의 RS 이상
BATCH = 100
BENCH = "^GSPC"

# 결과를 보기 전에 정해 둔 3단계 판정 기준 (robust.CHECKS 5개에 더해)
FIDELITY_JACCARD = 0.90   # 실제 스냅샷과 통과 종목 겹침(자카드) 평균
FIDELITY_TOP5 = 4.0       # 상위 5 중 평균 4종목 이상 일치
HISTORY_CHECKS = (
    ("fidelity", f"재구성이 실제 스냅샷과 일치 (통과 종목 자카드 ≥ "
                 f"{FIDELITY_JACCARD:.2f}, 상위 5 평균 ≥ {FIDELITY_TOP5:.0f}종목)"),
    ("years_positive", "기대값이 플러스인 해가 절반 넘음"),
)


def screener_cfg() -> dict:
    """스크리너와 같은 임계값 — 손으로 옮겨 적지 않는다."""
    import screener as S
    return S.CFG["us"]


# ---------------------------------------------------------------- 수집
def download(start: str = DATA_START) -> None:
    """유니버스 전 종목 일봉(수정주가 + 분할 기록)을 받아 캐시에 저장한다."""
    import yfinance as yf

    names = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    tickers = sorted(names)
    prices: dict[str, pd.DataFrame] = {}
    with_splits = 0
    for i in range(0, len(tickers), BATCH):
        batch = tickers[i:i + BATCH]
        df = None
        for attempt in range(3):
            try:
                df = yf.download(batch, start=start, interval="1d",
                                 auto_adjust=True, actions=True,
                                 group_by="ticker", threads=True,
                                 progress=False)
                break
            except Exception as e:                       # 일시 오류 재시도
                print(f"  재시도 {attempt + 1}/3: {e}", flush=True)
                time.sleep(5 * (attempt + 1))
        if df is None:
            continue
        for t in batch:
            try:
                sub = df[t].dropna(subset=["Close"])
            except (KeyError, TypeError):
                continue
            if len(sub) < MIN_ROWS_2Y:
                continue        # 1년치도 없으면 어느 날에도 통과할 수 없다
            cols = ["Open", "High", "Low", "Close", "Volume"]
            frame = sub[cols].astype("float32")
            if "Stock Splits" in sub.columns:
                sp = sub["Stock Splits"].fillna(0.0).astype("float32")
                frame["Splits"] = sp
                with_splits += int((sp > 0).any())
            prices[t] = frame
        print(f"  가격 {min(i + BATCH, len(tickers))}/{len(tickers)} "
              f"(보관 {len(prices)})", flush=True)
        time.sleep(1.0)

    bench = yf.download(BENCH, start=start, interval="1d", auto_adjust=True,
                        progress=False)["Close"].dropna()
    if isinstance(bench, pd.DataFrame):
        bench = bench.iloc[:, 0]
    if with_splits == 0:
        print("  경고: 분할 기록을 한 종목도 받지 못했다 — 가격 필터가 미래 분할에 "
              "오염된다", flush=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    with open(PRICES, "wb") as f:
        pickle.dump({"start": start, "fetched": date.today().isoformat(),
                     "prices": prices, "bench": bench.astype("float64")}, f)
    print(f"  {len(prices)}종목 (분할 기록 있는 종목 {with_splits}) · 벤치마크 "
          f"{len(bench)}일 → {PRICES}", flush=True)


def load_prices() -> dict:
    # 이 워크플로가 직접 만든 캐시 파일만 읽는다 (외부 입력 아님).
    with open(PRICES, "rb") as f:
        return pickle.load(f)


# ---------------------------------------------------------------- 지표
def split_factor_after(splits: pd.Series | None, index: pd.Index) -> pd.Series:
    """그 날 '이후'에 일어난 분할 배수의 곱. 그 날의 실제 가격 = 수정주가 × 이 값.

    분할 당일 행은 이미 분할 뒤 가격이라 그 날의 분할은 넣지 않는다.
    """
    if splits is None:
        return pd.Series(1.0, index=index)
    r = splits.astype("float64").fillna(0.0)
    r = r.where(r > 0, 1.0)
    incl = r[::-1].cumprod()[::-1]           # 그 날 포함 이후의 곱
    return incl / r                          # 그 날 제외


def indicators(df: pd.DataFrame) -> pd.DataFrame:
    """한 종목의 일봉 → 날짜별 스크리너 지표 (그 날까지의 데이터만 쓴다).

    screener 는 '그 날까지 2년 구간'을 받아 iloc[-k] 로 읽는다. 종목 자기 행
    기준 shift/rolling 은 같은 값을 낸다(2년 구간 안에 260행 이상일 때).
    """
    c = df["Close"].astype("float64")
    h = df["High"].astype("float64")
    lo = df["Low"].astype("float64")
    v = df["Volume"].astype("float64")
    after = split_factor_after(df["Splits"] if "Splits" in df.columns
                               else None, df.index)
    q1, q2, q3, q4 = (c.shift(63), c.shift(126), c.shift(189), c.shift(252))
    raw = 2.0 * (c / q1) + (c / q2) + (c / q3) + (c / q4)
    raw = raw.where((q1 > 0) & (q2 > 0) & (q3 > 0) & (q4 > 0))
    ma200 = c.rolling(200).mean()
    return pd.DataFrame({
        "close": c,
        "price_now": c * after,                          # 그 날 실제 거래가
        "vol50_now": v.rolling(50).mean() / after,       # 그 날 기준 주식 수
        "n2y": c.rolling(WINDOW_2Y).count(),
        "raw": raw,
        "ma50": c.rolling(50).mean(),
        "ma150": c.rolling(150).mean(),
        "ma200": ma200,
        "ma200_22": ma200.shift(21),                     # screener: ma200.iloc[-22]
        "low52": lo.rolling(252, min_periods=1).min(),
        "high52": h.rolling(252, min_periods=1).max(),
    }, index=df.index)


def population(day: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """RS 백분위 모집단 — 스크리너와 같다: 유동성 필터 통과 + 2년 구간 260행
    이상 + 원점수 계산 가능."""
    return day[(day["price_now"] >= cfg["min_price"])
               & (day["vol50_now"] >= cfg["min_vol"])
               & (day["n2y"] >= MIN_ROWS_2Y)
               & day["raw"].notna()].copy()


def screen(day: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """한 신호일의 단면(종목별 지표) → RS 백분위 + 트렌드 템플릿 통과 종목."""
    elig = population(day, cfg)
    if elig.empty:
        return elig
    elig["rs"] = (elig["raw"].rank(pct=True) * 98 + 1).round(0)
    c, m50, m150, m200 = elig["close"], elig["ma50"], elig["ma150"], elig["ma200"]
    ok = ((c > m150) & (c > m200) & (m150 > m200)
          & (m200 > elig["ma200_22"]) & (m50 > m150) & (m50 > m200)
          & (c > m50) & (c >= elig["low52"] * 1.30)
          & (c >= elig["high52"] * 0.75) & (elig["rs"] >= 70))
    return elig[ok].sort_values(["rs", "raw"], ascending=False)


def weekly_dates(calendar: pd.DatetimeIndex) -> list[pd.Timestamp]:
    """각 주(ISO 주)의 마지막 거래일."""
    s = pd.Series(calendar, index=calendar)
    iso = calendar.isocalendar()
    return list(s.groupby([iso.year.values, iso.week.values]).max().sort_values())


# ---------------------------------------------------------------- 재구성
def rebuild(top_check: bool = True) -> None:
    from vcp import detect_vcp

    cfg = screener_cfg()
    data = load_prices()
    prices, bench = data["prices"], data["bench"]
    names = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    cal = pd.DatetimeIndex(bench.index)
    first = pd.Timestamp(data["start"]) + pd.Timedelta(days=SIGNAL_LAG_DAYS)
    weekly = [d for d in weekly_dates(cal) if d >= first]
    # 실제 스냅샷 날짜도 재구성해 충실도를 잰다
    actual = {s["date"] for s in B.snapshots("us", 3650)}
    extra = [pd.Timestamp(d) for d in sorted(actual)
             if pd.Timestamp(d) in cal]
    sig = pd.DatetimeIndex(sorted(set(weekly) | set(extra)))
    weekly_set = {d.strftime("%Y-%m-%d") for d in weekly}
    print(f"  신호일 {len(sig)}개 (주간 {len(weekly)} · 실제 스냅샷 대조용 "
          f"{len(extra)}) · {sig[0].date()} ~ {sig[-1].date()}", flush=True)

    t0 = time.time()
    parts = []
    for n, (t, df) in enumerate(prices.items(), 1):
        x = indicators(df)
        x = x[x.index.isin(sig)]
        if not x.empty:
            x = x.assign(ticker=t)
            parts.append(x)
        if n % 1000 == 0:
            print(f"  지표 {n}/{len(prices)} ({time.time() - t0:.0f}s)", flush=True)
    long = pd.concat(parts)

    extra_set = {d.strftime("%Y-%m-%d") for d in extra}
    snaps = []
    for d, day in long.groupby(level=0):
        cross = day.set_index("ticker")
        passed = screen(cross, cfg)
        ds = d.strftime("%Y-%m-%d")
        pop = None
        if ds in extra_set:
            # 충실도 진단용: RS 백분위 모집단 전체의 원점수 (실제 rs_dist.json 과 대조)
            el = population(cross, cfg)
            pop = {t: round(float(v), 6) for t, v in el["raw"].items()}
        rows = {}
        cut = (passed["rs"].iloc[min(TIE_N, len(passed)) - 1]
               if len(passed) else 100)
        lo_d = d - pd.Timedelta(WINDOW_2Y)
        for t, r in passed.iterrows():
            vcp = {}
            if r["rs"] >= cut:
                win = prices[t].loc[(prices[t].index > lo_d)
                                    & (prices[t].index <= d),
                                    ["Open", "High", "Low", "Close", "Volume"]]
                vcp = detect_vcp(win.astype("float64"))
            rows[t] = {"name": names.get(t, ""), "rs": int(r["rs"]),
                       "vcp": vcp.get("score"), "price": round(float(r["close"]), 2),
                       "pivot": vcp.get("pivot"), "vstop": vcp.get("stop"),
                       "raw": round(float(r["raw"]), 6)}
        snap = {"date": ds, "weekly": ds in weekly_set, "rows": rows}
        if pop is not None:
            snap["pop"] = pop
        snaps.append(snap)
    CACHE.mkdir(parents=True, exist_ok=True)
    with gzip.open(SNAPS, "wt", encoding="utf-8") as f:
        json.dump({"built": date.today().isoformat(), "start": data["start"],
                   "snaps": snaps}, f, ensure_ascii=False)
    n_pass = [len(s["rows"]) for s in snaps if s["weekly"]]
    print(f"  스냅샷 {len(snaps)}개 · 주간 통과 종목 수 평균 {mean(n_pass):.0f} "
          f"(최소 {min(n_pass)}, 최대 {max(n_pass)}) · {time.time() - t0:.0f}s "
          f"→ {SNAPS}", flush=True)


def load_snaps() -> list[dict]:
    with gzip.open(SNAPS, "rt", encoding="utf-8") as f:
        return json.load(f)["snaps"]


# ---------------------------------------------------------------- 충실도
def top_set(rows: dict, n: int = 5) -> set[str]:
    order = sorted(({"ticker": t, **v} for t, v in rows.items()),
                   key=B.rank_key)
    return {r["ticker"] for r in order[:n]}


def compare(actual: dict, rec: dict) -> dict:
    """한 날짜의 실제 스냅샷 rows vs 재구성 rows."""
    a, r = set(actual), set(rec)
    both = a & r
    union = a | r
    rs_eq = [actual[t]["rs"] == rec[t]["rs"] for t in both]
    rs_diff = [abs((actual[t]["rs"] or 0) - (rec[t]["rs"] or 0)) for t in both]
    return {"actual": len(a), "rebuilt": len(r), "both": len(both),
            "jaccard": len(both) / len(union) if union else 1.0,
            "rs_exact": mean(rs_eq) if rs_eq else None,
            "rs_absdiff": mean(rs_diff) if rs_diff else None,
            "top5": len(top_set(actual) & top_set(rec)),
            "only_actual": sorted(a - r), "only_rebuilt": sorted(r - a)}


def live_dists() -> dict[str, list[float]]:
    """data_date → 그 날 실제 스크리너가 커밋한 RS 원점수 분포(rs_dist.json)."""
    rel = B.CFG["us"]["rel"]
    out: dict[str, list[float]] = {}
    for sha in B.commit_shas(rel, date.today() - timedelta(days=3650)):
        try:
            d = json.loads(B.git("show", f"{sha}:{rel}"))["data_date"]
            if d in out:
                continue
            out[d] = json.loads(B.git("show", f"{sha}:docs/data/rs_dist.json"))
        except (RuntimeError, json.JSONDecodeError, KeyError):
            continue
    return out


def match_count(a: list[float], b: list[float], tol: float = 1e-4) -> int:
    """정렬된 두 값 목록에서 tol 안으로 짝지어지는 개수 (투 포인터)."""
    a, b = sorted(a), sorted(b)
    i = j = n = 0
    while i < len(a) and j < len(b):
        if abs(a[i] - b[j]) <= tol:
            n, i, j = n + 1, i + 1, j + 1
        elif a[i] < b[j]:
            i += 1
        else:
            j += 1
    return n


def stale_ratio(cur: list[float], prev: list[float] | None) -> float | None:
    """원점수가 전날과 똑같은 비율 — 높으면 그 날 실제 스크리너가 지연 데이터를 썼다."""
    if not prev or not cur:
        return None
    from collections import Counter
    return sum((Counter(cur) & Counter(prev)).values()) / len(cur)


def fidelity(verbose: bool = True) -> dict:
    snaps_rec = {s["date"]: s for s in load_snaps()}
    rec = {d: s["rows"] for d, s in snaps_rec.items()}
    dists = live_dists()
    live = {s["date"]: s for s in B.snapshots("us", 3650)}
    rows = []
    prev_dist = None
    for d in sorted(dists):
        cur = dists[d]
        st = stale_ratio(cur, prev_dist)
        prev_dist = cur
        if d not in rec:
            continue
        actual = live.get(d)
        if actual is None:
            continue
        pop = snaps_rec[d].get("pop") or {}
        rows.append({"date": d, **compare(actual["rows"], rec[d]),
                     "stale": st, "pop_live": len(cur), "pop_rec": len(pop),
                     "pop_match": match_count(cur, list(pop.values()))
                     if pop else None,
                     "rs_shift": (mean(rec[d][t]["rs"] - actual["rows"][t]["rs"]
                                       for t in set(rec[d]) & set(actual["rows"]))
                                  if set(rec[d]) & set(actual["rows"]) else None),
                     "only_actual_rs": {t: actual["rows"][t]["rs"]
                                        for t in sorted(set(actual["rows"])
                                                        - set(rec[d]))},
                     "only_rebuilt_rs": {t: rec[d][t]["rs"]
                                         for t in sorted(set(rec[d])
                                                         - set(actual["rows"]))}})
    if not rows:
        print("  대조할 날짜가 없다")
        return {"days": 0, "passed": False}
    jac = mean(r["jaccard"] for r in rows)
    top5 = mean(r["top5"] for r in rows)
    rs_vals = [r["rs_exact"] for r in rows if r["rs_exact"] is not None]
    rs_exact = mean(rs_vals) if rs_vals else 0.0     # 겹치는 종목이 없으면 0
    passed = jac >= FIDELITY_JACCARD and top5 >= FIDELITY_TOP5
    if verbose:
        print(f"\n  [충실도] 재구성 vs 실제 커밋 스냅샷 ({len(rows)}일)")
        print(f"  지연 = 실제 스크리너의 RS 원점수가 전날과 똑같은 비율 (높으면 그 날 "
              f"실제 쪽이 전날 데이터를 다시 썼다)")
        print(f"  모집단 = RS 백분위 모집단 크기 실제/재구성, 원점수 일치 = 실제 원점수 중 "
              f"재구성에 같은 값(±1e-4)이 있는 비율")
        print(f"  {'날짜':<10} {'실제':>5} {'재구성':>6} {'자카드':>6} "
              f"{'RS일치':>6} {'RS이동':>6} {'상위5':>5} {'지연':>6} "
              f"{'모집단':>11} {'원점수일치':>8}")
        for r in rows:
            st = "   -  " if r["stale"] is None else f"{r['stale'] * 100:5.1f}%"
            pm = ("     -  " if r["pop_match"] is None
                  else f"{r['pop_match'] / max(1, r['pop_live']) * 100:6.1f}%")
            sh = "   -  " if r["rs_shift"] is None else f"{r['rs_shift']:+6.2f}"
            print(f"  {r['date']:<10} {r['actual']:>5} {r['rebuilt']:>6} "
                  f"{r['jaccard']:>6.3f} {(r['rs_exact'] or 0) * 100:>5.0f}% "
                  f"{sh} {r['top5']:>4}/5 {st} "
                  f"{r['pop_live']:>5}/{r['pop_rec']:<5} {pm}")
        fresh = [r for r in rows if (r["stale"] or 0) < 0.05]
        if fresh:
            print(f"  (진단) 실제 쪽 데이터가 지연되지 않은 날만 ({len(fresh)}일): "
                  f"자카드 {mean(r['jaccard'] for r in fresh):.3f} · 상위 5 "
                  f"{mean(r['top5'] for r in fresh):.1f}/5 — 판정은 위 전체 기준 그대로")
        for r in rows:
            if r["jaccard"] < 0.95 and (r["stale"] or 0) < 0.05:
                oa = list(r["only_actual_rs"].items())[:10]
                orb = list(r["only_rebuilt_rs"].items())[:10]
                print(f"  {r['date']} 실제에만 {len(r['only_actual_rs'])}: {oa}")
                print(f"  {r['date']} 재구성에만 {len(r['only_rebuilt_rs'])}: {orb}")
        print(f"  평균: 자카드 {jac:.3f} · RS 정확히 일치 {rs_exact * 100:.0f}% · "
              f"상위 5 일치 {top5:.1f}/5 → "
              f"{'통과' if passed else '실패 — 재구성이 실제와 다르다'}")
        last = rows[-1]
        print(f"  최근일({last['date']}) 실제에만: {last['only_actual'][:12]}")
        print(f"  최근일({last['date']}) 재구성에만: {last['only_rebuilt'][:12]}")
    out = {"days": len(rows), "jaccard": jac, "top5": top5,
           "rs_exact": rs_exact, "passed": passed, "per_day": rows}
    CACHE.mkdir(parents=True, exist_ok=True)
    FIDELITY.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


# ---------------------------------------------------------------- 검증
def to_ohlc(prices: dict, tickers: set[str], since: str
            ) -> tuple[dict[str, list[dict]], dict[str, list[str]]]:
    """캐시 가격 → simulate() 가 읽는 일봉 dict 목록 + 날짜 색인."""
    ohlc, index = {}, {}
    since_ts = pd.Timestamp(since)
    for t in tickers:
        df = prices.get(t)
        if df is None:
            continue
        df = df[df.index >= since_ts]
        if df.empty:
            continue
        ds = [d.strftime("%Y-%m-%d") for d in df.index]
        o, h, l, c = (df[k].astype("float64").tolist()
                      for k in ("Open", "High", "Low", "Close"))
        ohlc[t] = [{"date": d, "open": a, "high": b, "low": e, "close": f}
                   for d, a, b, e, f in zip(ds, o, h, l, c)]
        index[t] = ds
    return ohlc, index


def by_year(trades: list[dict]) -> dict[str, list[float]]:
    g: dict[str, list[float]] = {}
    for t in V.marked(trades):
        g.setdefault(t["cohort"][:4], []).append(t["r_multiple"])
    return g


def regime_on(bench: pd.Series) -> dict[str, bool]:
    """신호일에 S&P 500 이 200일선 위인가 — 미너비니: 지수 조정기엔 신규 매수 중단."""
    ma = bench.rolling(200).mean()
    ok = (bench > ma) & ma.notna()
    return {d.strftime("%Y-%m-%d"): bool(v) for d, v in ok.items()}


def year_returns(bench: pd.Series) -> dict[str, float]:
    yr = bench.groupby(bench.index.year).agg(["first", "last"])
    return {str(y): float(r["last"] / r["first"] - 1) for y, r in yr.iterrows()}


def validate(argv: list[str]) -> int:
    o, _, out_json = P.parse_args(argv)
    o = dict(o, entry="next_open")
    data = load_prices()
    snaps = [s for s in load_snaps() if s["weekly"]]
    prep = R.prepare(snaps, o["top"])
    base = prep["base"]
    tie, other = R.needed_tickers(prep)
    fid = (json.loads(FIDELITY.read_text(encoding="utf-8"))
           if FIDELITY.exists() else fidelity(verbose=False))

    print(f"\n{'=' * 92}")
    print(f"  US 3단계 과거 재구성 검증  주간 코호트 {len(base)}개 "
          f"({base[0][0]} ~ {base[-1][0]}) · 코호트당 상위 {o['top']}")
    print(f"{'=' * 92}")
    print(f"  매도 규칙: {P.rules_line(o)}")
    print(f"  기준: 전량 청산 · 부트스트랩 {R.N_BOOT}회 · 무작위 {R.N_PERM}회 · "
          f"시드 {R.SEED}")
    print(f"  재구성 충실도: 자카드 {fid.get('jaccard', 0):.3f} · 상위 5 "
          f"{fid.get('top5', 0):.1f}/5 ({fid.get('days', 0)}일 대조)")
    print(f"  ※ 생존 편향: 유니버스는 지금 상장된 종목뿐 — 그 사이 상장폐지된 종목이 "
          f"빠져 결과가 좋게 기운다")
    print(f"  일봉 변환 {len(tie | other)}종목 …", flush=True)
    ohlc, index = to_ohlc(data["prices"], tie | other, base[0][0])

    doc = R.run_checks(snaps, ohlc, o, prep, index, stage="견고성 5개 기준")
    top_tr = doc.pop("_top_trades")
    rnd_tr = doc.pop("_rnd_trades")

    # ---- 연도별 -------------------------------------------------------------
    bench = data["bench"]
    yret = year_returns(bench)
    ty = by_year(top_tr)
    ry = [by_year(t) for t in rnd_tr]
    print(f"\n  [4] 연도별 — 시장 국면이 바뀌어도 버티나")
    print(f"  {'연도':<6} {'거래':>5} {'종목':>5} {'기대값':>8} {'승률':>6} "
          f"{'무작위 중앙값':>12} {'S&P 500':>9}")
    years = {}
    for y in sorted(ty):
        rs = ty[y]
        uniq = len({t["ticker"] for t in V.marked(top_tr)
                    if t["cohort"][:4] == y})
        rm = R.median_of([mean(r[y]) for r in ry if r.get(y)])
        years[y] = {"trades": len(rs), "tickers": uniq, "expectancy_r": mean(rs),
                    "win": sum(1 for x in rs if x > 0) / len(rs),
                    "random_median_r": rm, "spx": yret.get(y)}
        print(f"  {y:<6} {len(rs):>5} {uniq:>5} {P.rfmt(mean(rs))} "
              f"{years[y]['win'] * 100:5.1f}% {P.rfmt(rm):>12} "
              f"{B.pct(yret.get(y))}")
    pos_years = sum(1 for v in years.values() if v["expectancy_r"] > 0)
    print(f"    기대값 플러스인 해 {pos_years}/{len(years)}")

    # ---- 시장 국면 필터 -----------------------------------------------------
    reg = regime_on(bench)
    on_tr = [t for t in top_tr if reg.get(t["cohort"], False)]
    off_tr = [t for t in top_tr if not reg.get(t["cohort"], False)]
    e_all, e_on, e_off = (R.expectancy(x) for x in (top_tr, on_tr, off_tr))
    on_y = by_year(on_tr)
    worst_all = min((v["expectancy_r"] for v in years.values()), default=None)
    worst_on = min((mean(v) for v in on_y.values() if v), default=None)
    rnd_on = R.median_of([e for e in (R.expectancy(
        [t for t in x if reg.get(t["cohort"], False)]) for x in rnd_tr)
        if e is not None])
    n_on = sum(1 for d, _ in base if reg.get(d, False))
    print(f"\n  [5] 시장 국면 필터 — S&P 500 이 200일선 아래면 신규 매수 안 함")
    print(f"    코호트 {len(base)}개 중 매수 가능 {n_on}개")
    print(f"    필터 없음 {P.rfmt(e_all)} · 필터 적용(지수 200일선 위) "
          f"{P.rfmt(e_on)} · 걸러진 쪽(지수 200일선 아래) {P.rfmt(e_off)}")
    print(f"    최악의 해: 필터 없음 {P.rfmt(worst_all)} → 필터 적용 "
          f"{P.rfmt(worst_on)} · 필터 적용 시 무작위 중앙값 {P.rfmt(rnd_on)}")
    regime = {"cohorts_on": n_on, "expectancy_all": e_all, "expectancy_on": e_on,
              "expectancy_off": e_off, "worst_year_all": worst_all,
              "worst_year_on": worst_on, "random_median_on": rnd_on,
              "improves_expectancy": (e_on or -9) > (e_all or -9),
              "improves_worst_year": (worst_on or -9) > (worst_all or -9)}

    # ---- 3단계 판정 ---------------------------------------------------------
    hist = {"fidelity": bool(fid.get("passed")),
            "years_positive": pos_years > len(years) / 2}
    print(f"\n  [3단계 판정] 결과를 보기 전에 정한 기준")
    for key, desc in HISTORY_CHECKS:
        print(f"    {R.mark(hist[key])}  {desc}")
    for key, desc in R.CHECKS:
        print(f"    {R.mark(doc['checks'][key])}  {desc}")
    passed = all(hist.values()) and doc["passed"]
    print(f"    → {'3단계 통과' if passed else '3단계 실패'}"
          f" · 시장 국면 필터: 기대값 "
          f"{'개선' if regime['improves_expectancy'] else '개선 안 됨'}, 최악의 해 "
          f"{'개선' if regime['improves_worst_year'] else '개선 안 됨'}")
    print(f"    ※ 생존 편향 때문에 위 수치는 실제보다 좋게 나왔을 가능성이 크다")

    doc.update({"source": "history", "fidelity": {k: v for k, v in fid.items()
                                                  if k != "per_day"},
                "years": years, "regime": regime, "history_checks": hist,
                "passed_stage3": passed})
    if out_json:
        path = Path(out_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n  → {path}")
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        sys.exit(__doc__)
    cmd, rest = argv[0], argv[1:]
    if cmd == "download":
        start = DATA_START
        if rest[:1] == ["--start"] and len(rest) > 1:
            start = rest[1]
        download(start)
    elif cmd == "rebuild":
        rebuild()
    elif cmd == "fidelity":
        fidelity()
    elif cmd == "validate":
        return validate(rest)
    else:
        sys.exit(f"알 수 없는 명령: {cmd}\n{__doc__}")
    return 0


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
