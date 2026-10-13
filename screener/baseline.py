# -*- coding: utf-8 -*-
"""기준선 보고 (ROADMAP 단계 1) — 새 후보를 재기 전에 잣대가 맞는지부터 확인한다.

같은 표에 SPY · RSP · S&P 500 풀 동일가중 · 풀 시총가중 · 템플릿 동일가중 · 템플릿 시총가중을
놓고 evalkit 의 기준으로 잰다. 보는 것:

  1) 풀 시총가중이 SPY 와 닮았나 (상관·추적오차·시대별 연복리 차이) — 시총 데이터가 믿을
     만한지에 대한 검증. 닮지 않으면 시총 가중 후보(H3)는 이 데이터로 못 한다.
  2) 우리 동일가중 − RSP: 상장폐지 종목이 빠지고 매주 재조정하는 우리 계산의 부풀림 추정치
     → 후보 수익에서 빼는 '생존편향 보정치'.
  3) 템플릿(동일가중·시총가중)의 판정 — 설계 탈락이면 보류 기간은 계산하지 않는다.

비용: 편도 5bp (대형주). 중소형 교차 검증은 25bp 를 쓴다 (evalkit.COST_BP).

사용법 (spx_validate members/prices/rebuild, pit_fund extract 산출물 필요):
    python screener/baseline.py [--json baseline.json]
"""
import json
import sys
from pathlib import Path
from statistics import pstdev

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evalkit as E
import history as H
import marketcap as M
import pit_fund as PF
import spx_validate as S
import template_check as T

HERE = Path(__file__).resolve().parent
LEDGER = HERE / "ledger.json"
REFS = (("spy", "SPY"), ("rsp", "RSP (동일가중 ETF)"), ("pool", "풀 동일가중"),
        ("pool_cw", "풀 시총가중"), ("tmpl_ew", "템플릿 동일가중"),
        ("tmpl_cw", "템플릿 시총가중"))


BACKFILL_DAYS = 6 * 366       # 첫 XBRL 공시(2009~2011) 이전 날짜의 주식수 근사 허용 범위
# 기억에 의한 참고 종가 (실제 거래가, 분할·배당 조정 전). 1% 넘게 어긋나면 가격 복원을 의심한다.
REF_PRICES = (("AAPL", "2019-02-08", 170.18), ("MSFT", "2019-02-08", 105.67))


def selfcheck(caps: "M.Caps") -> list[dict]:
    out = []
    for t, d, ref in REF_PRICES:
        got = caps.price(t, d)
        out.append({"ticker": t, "date": d, "ref": ref, "got": got,
                    "diff": None if got is None else got / ref - 1})
    return out


def etf_returns(bench: S.Spy, d0: list[str], d1: list[str]) -> list[float]:
    return [bench.at(b, "open") / bench.at(a, "open") - 1 for a, b in zip(d0, d1)]


def basic(fr: E.Frame, name: str, lo: str, hi: str) -> dict:
    ix = fr.idx(lo, hi)
    if len(ix) < 2 * (E.NW_LAGS + 1):
        return {}
    rs = fr.pick(name, ix)
    reg = E.nw_alpha_beta(rs, fr.pick("spy", ix)) if name != "spy" else {}
    return {"cagr": E.cagr(rs, fr.days(ix)), "vol": pstdev(rs) * 52 ** 0.5,
            "mdd": E.max_dd(rs), "beta": reg.get("beta"),
            "alpha": reg.get("alpha_ann"), "t": reg.get("t_alpha")}


def tracking(fr: E.Frame, a: str, b: str, lo: str, hi: str) -> dict:
    ix = fr.idx(lo, hi)
    if len(ix) < 2 * (E.NW_LAGS + 1):
        return {}
    x, y = fr.pick(a, ix), fr.pick(b, ix)
    diff = [u - v for u, v in zip(x, y)]
    mx, my = sum(x) / len(x), sum(y) / len(y)
    cov = sum((u - mx) * (v - my) for u, v in zip(x, y)) / len(x)
    corr = cov / (pstdev(x) * pstdev(y)) if pstdev(x) and pstdev(y) else None
    return {"corr": corr, "te": pstdev(diff) * 52 ** 0.5,
            "cagr_diff": E.cagr(x, fr.days(ix)) - E.cagr(y, fr.days(ix))}


def build_frame(args=None) -> tuple[E.Frame, dict]:
    px, data, ex = S.all_prices()
    spy = S.Spy(ex["spy"])
    m = S.load_members()
    snaps, _ = S.load_snaps()
    shares = PF.load_shares() if PF.SHARES.exists() else {}
    mem = lambda d: S.members_at(m["current"], m["changes"], d)
    members = {s["date"]: mem(s["date"]) for s in snaps}
    need = set().union(*members.values()) | {t for s in snaps for t in s["rows"]}
    p = T.Prices(px, need, spy.dates)
    try:
        cik_of = PF.universe_ciks()
    except Exception as e:                   # cik_map 이 없으면 종류 합산 보정만 빠진다
        print(f"  CIK 매핑 실패: {e}", flush=True)
        cik_of = {}
    caps = M.Caps(px, shares, cik_of=cik_of, backfill_days=BACKFILL_DAYS)
    cap_cover, cw_pool, cw_tmpl = [], [], []
    for s in snaps:
        sig, mm = s["date"], members[s["date"]]
        c = caps.caps(mm, sig)
        have_px = [t for t in mm if t in p.cols]
        cap_cover.append(len([t for t in have_px if t in c]) / len(have_px)
                         if have_px else 0.0)
        cw_pool.append((sig, E.cap_weights(mm, c)))
        cw_tmpl.append((sig, E.cap_weights(set(s["rows"]), c)))
    scheds = {"pool": [(s["date"], E.ew(members[s["date"]])) for s in snaps],
              "pool_cw": cw_pool,
              "tmpl_ew": [(s["date"], E.ew(s["rows"])) for s in snaps],
              "tmpl_cw": cw_tmpl}
    cost = E.COST_BP["large"]
    runs = {k: E.run_weights(v, p, cost) for k, v in scheds.items()}
    base = runs["pool"]
    for k, r in runs.items():
        if r[:3] != base[:3]:
            raise RuntimeError(f"{k}: 주 달력이 풀 동일가중과 다르다")
    fr = E.Frame(base[0], base[1], base[2])
    fr.add("spy", etf_returns(spy, fr.d0, fr.d1))
    import diagnose as D
    rsp = D.fetch_rsp(data["start"])
    if rsp is not None:
        fr.add("rsp", etf_returns(rsp, fr.d0, fr.d1))
    for k, r in runs.items():
        fr.add(k, r[4])
    meta = {"cap_cover": sum(cap_cover) / len(cap_cover), "undone": caps.undone,
            "cover_by_sig": dict(zip([x[0] for x in cw_pool], cap_cover)),
            "backfilled": caps.backfilled, "selfcheck": selfcheck(caps),
            "adjusted_only": caps.adjusted_only, "shares_tickers": len(shares),
            "cost_bp": cost, "rsp": rsp is not None}
    return fr, meta


def report(fr: E.Frame, meta: dict) -> dict:
    doc: dict = {"meta": meta}
    names = [(k, lbl) for k, lbl in REFS if k in fr.r]
    led = E.ledger_summary(LEDGER)
    first, last = fr.signal[0], fr.signal[-1]
    print(f"\n{'=' * 104}")
    print(f"  기준선 보고 — S&P 500 시점별 구성종목 · {first} ~ {last} · {len(fr.signal)}주 · "
          f"편도 {meta['cost_bp']:.0f}bp")
    print(f"  시총: 발행주식수(SEC) {meta['shares_tickers']}종목 · 그 날 구성종목 중 시총 계산됨 "
          f"{meta['cap_cover'] * 100:.1f}% · 배당 조정 되돌림 {meta['undone']}종목"
          + (f" (조정가 그대로 {meta['adjusted_only']}종목)" if meta["adjusted_only"] else ""))
    print(f"  시도한 가설 {led['n']}개 → Bonferroni 임계 z {led['bonferroni_z']:.2f} "
          f"(이 도구의 알파 t 기준 {led['t_min']:.1f} 은 후보 수가 적을 때의 최소선 — "
          f"후보가 늘면 올린다)")
    print(f"  RSP {'있음' if meta['rsp'] else '없음 — 생존편향 보정 못 함'} · 첫 공시 이전 값을 "
          f"되돌려 쓴 조회 {meta['backfilled']}건")
    for c in meta["selfcheck"]:
        got = "없음" if c["got"] is None else f"{c['got']:.2f} ({c['diff'] * 100:+.2f}%)"
        print(f"  가격 복원 점검 {c['ticker']} {c['date']}: 기억한 종가 {c['ref']:.2f} vs "
              f"복원 {got}")
    print(f"{'=' * 104}")

    for title, (lo, hi) in (("전체 기간", ("0000", "9999")), ("설계 기간 2013~", E.DESIGN),
                            ("보류 기간 2005–12 (참고용 기준선만 — 후보는 설계 통과 때만)",
                             E.HOLDOUT)):
        print(f"\n  [{title}]")
        print(f"  {'':<22} {'연복리':>8} {'변동성':>7} {'MDD':>8} {'베타':>5} "
              f"{'알파(연)':>9} {'t':>5}")
        rows = {}
        for k, lbl in names:
            b = basic(fr, k, lo, hi)
            if not b:
                continue
            rows[k] = b
            print(f"  {lbl:<22} {E.pct(b['cagr'])} {b['vol'] * 100:6.1f}% {E.pct(b['mdd'])} "
                  f"{'-' if b['beta'] is None else f'{b['beta']:5.2f}':>5} "
                  f"{E.pct(b['alpha'], 9)} {E.fmt_t(b['t']):>5}")
        doc.setdefault("basic", {})[title] = rows

    print(f"\n  [시대별 연복리]")
    print(f"  {'':<22} " + " ".join(f"{lbl:>8}" for lbl, _, _ in E.ERAS))
    eras = {}
    for k, lbl in names:
        vals = []
        for el, lo, hi in E.ERAS:
            ix = fr.idx(lo, hi)
            vals.append(E.cagr(fr.pick(k, ix), fr.days(ix)) if len(ix) >= E.ERA_MIN_WEEKS
                        else None)
        eras[k] = vals
        print(f"  {lbl:<22} " + " ".join(E.pct(v) for v in vals))
    doc["eras"] = eras

    print(f"\n  [시총 가중 추적 검증] 풀 시총가중 vs SPY — 닮지 않으면 시총 데이터를 못 믿는다")
    print(f"  {'':<22} {'상관':>7} {'추적오차':>8} {'연복리 차이':>10} {'시총 계산 비율':>13}")
    trk = {}
    cov = meta["cover_by_sig"]
    for el, lo, hi in (("전체", "0000", "9999"),) + tuple(E.ERAS):
        t = tracking(fr, "pool_cw", "spy", lo, hi)
        if t:
            trk[el] = t
            ix = fr.idx(lo, hi)
            t["coverage"] = sum(cov[fr.signal[i]] for i in ix) / len(ix)
            corr = "-" if t["corr"] is None else f"{t['corr']:.4f}"
            print(f"  {el:<22} {corr:>7} {t['te'] * 100:7.2f}% "
                  f"{E.pct(t['cagr_diff'], 10)} {t['coverage'] * 100:12.1f}%")
    doc["tracking"] = trk
    if "rsp" in fr.r:
        print(f"\n  [생존편향 보정치] 후보 수익에서 빼는 연 %p = mean(max(풀 동일가중 − RSP, 0)) × 52")
        hc = E.survivorship_haircut(fr)
        bias = {}
        for el, lo, hi in E.ERAS:
            ix = fr.idx(lo, hi)
            if len(ix) >= E.ERA_MIN_WEEKS:
                bias[el] = hc[ix[0]] * 52
                print(f"    {el:<8} {E.pct(bias[el])}")
        doc["haircut"] = bias

    print(f"\n  [후보 판정] 설계 기간 → 통과 때만 보류 기간 (ROADMAP 성공 기준 6항목)")
    E.header()
    doc["verdict"] = {}
    for k, lbl in (("tmpl_ew", "템플릿 동일가중"), ("tmpl_cw", "템플릿 시총가중")):
        v = E.verdict(fr, k)
        doc["verdict"][k] = v
        E.row(lbl, v["design"])
        if "checks" in v["design"]:
            f = E.fails(v["design"])
            if f:
                print(f"      탈락 이유: {' / '.join(f)}")
            for cl, (c, s) in v["design"]["crisis"].items():
                print(f"      위기 {cl}: 후보 {E.pct(c).strip()} · SPY {E.pct(s).strip()}")
        if v["holdout"]:
            E.row(lbl + " (보류)", v["holdout"])
        else:
            print(f"      설계 기간 탈락 — 보류 기간은 계산하지 않는다")
    print(f"\n  ※ 생존편향 보정은 하한이다 (모멘텀류는 상장폐지 종목을 더 많이 담았을 수 있다).")
    return doc


def main(argv: list[str]) -> int:
    out_json = argv[argv.index("--json") + 1] if "--json" in argv else None
    fr, meta = build_frame()
    doc = report(fr, meta)
    if out_json:
        Path(out_json).write_text(json.dumps(doc, ensure_ascii=False, indent=1,
                                             default=str), encoding="utf-8")
        print(f"  → {out_json}")
    return 0


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
