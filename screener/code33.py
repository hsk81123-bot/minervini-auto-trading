# -*- coding: utf-8 -*-
"""펀더멘털 지표 계산 + 코드33 점수 (미너비니 SEPA).

입력: docs/data/fundamentals[_kr]/<TICKER>.json (fundamentals.py 산출물)
출력: docs/data/fundamentals[_kr].json  (종목별 지표 요약 — 웹앱이 읽는다)

설계 근거:
- 모든 성장률은 전년 동기 대비다. 계절성이 큰 업종에서 전분기 대비는 의미가 없고,
  미너비니의 기준 자체가 전년 동기 대비다.
- 미국은 분기를 종료일로, 한국은 (연도, 분기)로 식별한다. 전년 동기는 미국은
  종료일 -365일(±25일), 한국은 같은 분기의 전년도로 찾는다.
- 한국은 주요계정에 EPS가 없어 순이익 성장률로 대체한다 (Phase 0 확인).
  어느 쪽을 썼는지 eps_source에 남긴다.
- 분모가 0 이하면(적자) 비율이 의미를 잃는다. 점수는 0으로 두고 '적자→흑자'
  같은 플래그로 구분해 화면에서 따로 읽게 한다.
- 측정 불가 항목은 0점이 아니라 배점에서 제외하고, 남은 배점으로 환산한다.
  데이터가 없다는 것과 나쁘다는 것은 다르다.

사용법:
    python screener/code33.py            # 수집된 전 종목 → 요약 파일
    python screener/code33.py AAPL       # 한 종목만 계산해 출력
"""
import json
import sys
from datetime import date
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "docs" / "data"

# 코드33 배점 (합 100)
WEIGHTS = {
    "eps_growth": 30,       # 분기 EPS(또는 순이익) 전년동기비
    "revenue_growth": 25,   # 분기 매출 전년동기비
    "opm_change": 20,       # 영업이익률 확대
    "eps_accel": 15,        # EPS 성장 가속
    "annual_eps_cagr": 10,  # 연간 EPS 3년 CAGR
}
# 만점 기준 / 0점 기준
BANDS = {
    "eps_growth": (0.10, 0.25),
    "revenue_growth": (0.05, 0.20),
    "opm_change": (0.0, 0.02),
    "annual_eps_cagr": (0.0, 0.25),
}
# SEPA 기준선 (통과/미달 표시용)
THRESHOLDS = {"eps_growth": 0.25, "revenue_growth": 0.20,
              "opm_change": 0.0, "annual_eps_cagr": 0.25, "roe": 0.17}
MIN_COVERAGE = 0.55        # 이보다 적게 측정되면 점수를 내지 않는다


def _d(s: str) -> date:
    y, m, dd = s.split("-")
    return date(int(y), int(m), int(dd))


def band_score(value: float | None, key: str) -> float | None:
    """0점 기준~만점 기준 사이를 선형으로. 범위 밖은 0 또는 만점."""
    if value is None:
        return None
    lo, hi = BANDS[key]
    w = WEIGHTS[key]
    if value >= hi:
        return float(w)
    if value <= lo:
        return 0.0
    return w * (value - lo) / (hi - lo)


def ratio(cur: float | None, prev: float | None) -> tuple[float | None, str | None]:
    """전년동기비 증가율. 분모가 0 이하면 비율 대신 상태 문자열을 돌려준다."""
    if cur is None or prev is None:
        return None, None
    if prev <= 0:
        if cur > 0:
            return None, "적자→흑자"
        return None, "적자 지속"
    return cur / prev - 1, ("흑자→적자" if cur <= 0 else None)


def quarter_pairs(doc: dict) -> list[tuple[dict, dict | None]]:
    """[(분기, 전년 동기)] — 최신 분기부터. 시장별로 짝 찾는 방식이 다르다."""
    qs = doc.get("quarters", [])
    out = []
    if doc.get("market") == "kr":
        by_key = {(q.get("year"), q.get("quarter")): q for q in qs}
        for q in qs:
            prev = by_key.get((q.get("year", 0) - 1, q.get("quarter")))
            out.append((q, prev))
    else:
        for q in qs:
            if not q.get("end"):
                out.append((q, None))
                continue
            target = _d(q["end"]).toordinal() - 365
            prev = None
            for other in qs:
                if other is q or not other.get("end"):
                    continue
                if abs(_d(other["end"]).toordinal() - target) <= 25:
                    prev = other
                    break
            out.append((q, prev))
    return out


def opm(q: dict | None) -> float | None:
    if not q:
        return None
    rev, op = q.get("revenue"), q.get("operating_income")
    if rev is None or op is None or rev <= 0:
        return None
    return op / rev


def eps_field(doc: dict) -> str:
    """미국은 희석EPS, 한국은 EPS가 없어 순이익으로 대체한다."""
    return "net_income" if doc.get("market") == "kr" else "eps_diluted"


def accel(pairs: list[tuple[dict, dict | None]], field: str) -> tuple[bool | None, int, list]:
    """최근 3개 분기의 성장률이 계단식으로 올라가는지. (가속여부, 증가횟수, 성장률)"""
    growth = []
    for q, prev in pairs[:3]:
        g, _ = ratio(q.get(field), (prev or {}).get(field))
        growth.append(g)
    if any(g is None for g in growth):
        return None, 0, growth
    rises = sum(1 for i in range(len(growth) - 1) if growth[i] > growth[i + 1])
    return rises == 2, rises, growth


def annual_cagr(doc: dict, field: str) -> float | None:
    """최근 3개 회계연도 CAGR. 시작값이 0 이하면 계산하지 않는다."""
    anns = [a for a in doc.get("annuals", []) if a.get(field) is not None]
    if len(anns) < 2:
        return None
    anns = anns[:4]
    newest, oldest = anns[0][field], anns[-1][field]
    years = len(anns) - 1
    if oldest <= 0 or newest <= 0 or years < 1:
        return None
    return (newest / oldest) ** (1 / years) - 1


def latest_equity(doc: dict) -> float | None:
    if doc.get("market") == "kr":
        for a in doc.get("annuals", []):
            if a.get("equity"):
                return a["equity"]
        return None
    eq = doc.get("equity") or []
    return eq[0]["value"] if eq else None


def compute(doc: dict) -> dict:
    """한 종목의 지표와 코드33 점수."""
    field = eps_field(doc)
    pairs = quarter_pairs(doc)
    cur, prev = (pairs[0] if pairs else ({}, None))
    flags = list(doc.get("anomalies") or [])

    eps_g, eps_state = ratio(cur.get(field), (prev or {}).get(field))
    rev_g, rev_state = ratio(cur.get("revenue"), (prev or {}).get("revenue"))
    opm_cur, opm_prev = opm(cur), opm(prev)
    opm_chg = None if opm_cur is None or opm_prev is None else opm_cur - opm_prev
    is_accel, rises, growth3 = accel(pairs, field)
    cagr = annual_cagr(doc, field)

    # TTM 순이익 / 자기자본
    nis = [q.get("net_income") for q, _ in pairs[:4]]
    equity = latest_equity(doc)
    roe = (sum(nis) / equity
           if equity and equity > 0 and all(n is not None for n in nis)
           else None)

    for state in (eps_state, rev_state):
        if state and state not in flags:
            flags.append(state)

    comps = {
        "eps_growth": band_score(eps_g, "eps_growth"),
        "revenue_growth": band_score(rev_g, "revenue_growth"),
        "opm_change": band_score(opm_chg, "opm_change"),
        "eps_accel": (None if is_accel is None else
                      float(WEIGHTS["eps_accel"]) if is_accel else
                      WEIGHTS["eps_accel"] / 2 if rises == 1 else 0.0),
        "annual_eps_cagr": band_score(cagr, "annual_eps_cagr"),
    }
    # 적자로 비율을 못 낸 항목은 0점으로 둔다 (측정 불가와 구분)
    if eps_g is None and eps_state:
        comps["eps_growth"] = 0.0
    if rev_g is None and rev_state:
        comps["revenue_growth"] = 0.0

    available = sum(WEIGHTS[k] for k, v in comps.items() if v is not None)
    earned = sum(v for v in comps.values() if v is not None)
    coverage = available / sum(WEIGHTS.values())
    score = round(earned / available * 100) if coverage >= MIN_COVERAGE else None

    def passed(value, key):
        return None if value is None else bool(value >= THRESHOLDS[key])

    return {
        "ticker": doc.get("ticker"),
        "market": doc.get("market"),
        "as_of": cur.get("label") or cur.get("end"),
        "compared_to": (prev or {}).get("label") or (prev or {}).get("end"),
        "eps_source": field,
        "eps_growth": None if eps_g is None else round(eps_g, 4),
        "revenue_growth": None if rev_g is None else round(rev_g, 4),
        "opm": None if opm_cur is None else round(opm_cur, 4),
        "opm_change": None if opm_chg is None else round(opm_chg, 4),
        "eps_accel": is_accel,
        "eps_growth_3q": [None if g is None else round(g, 4) for g in growth3],
        "annual_eps_cagr": None if cagr is None else round(cagr, 4),
        "roe": None if roe is None else round(roe, 4),
        "checks": {
            "eps_25plus": passed(eps_g, "eps_growth"),
            "revenue_20plus": passed(rev_g, "revenue_growth"),
            "opm_expanding": passed(opm_chg, "opm_change"),
            "eps_accelerating": is_accel,
            "annual_eps_25plus": passed(cagr, "annual_eps_cagr"),
            "roe_17plus": passed(roe, "roe"),
        },
        "components": {k: (None if v is None else round(v, 1))
                       for k, v in comps.items()},
        "coverage": round(coverage, 2),
        "code33": score,
        "flags": flags,
        "quarter_basis": doc.get("quarter_basis"),
        "fetched_at": doc.get("fetched_at"),
    }


def load_all(market: str) -> list[dict]:
    d = DATA_DIR / ("fundamentals_kr" if market == "kr" else "fundamentals")
    out = []
    for path in sorted(d.glob("*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                out.append(json.load(f))
        except json.JSONDecodeError:
            print(f"  건너뜀(JSON 오류): {path.name}", flush=True)
    return out


def main(only: list[str]) -> int:
    for market in ("us", "kr"):
        docs = load_all(market)
        if only:
            docs = [d for d in docs if d.get("ticker") in only]
        if not docs:
            continue
        rows = {}
        for doc in docs:
            m = compute(doc)
            rows[m["ticker"]] = m
            print(f"[{m['ticker']}] {m['as_of']} vs {m['compared_to']} | "
                  f"코드33 {m['code33']} (측정 {m['coverage']:.0%}) | "
                  f"EPS {fmt_pct(m['eps_growth'])} 매출 {fmt_pct(m['revenue_growth'])} "
                  f"OPM {fmt_pct(m['opm'])}({fmt_pct(m['opm_change'])}) "
                  f"가속 {m['eps_accel']}"
                  + (f" | 플래그 {m['flags']}" if m["flags"] else ""), flush=True)
        if only:
            continue
        out = DATA_DIR / ("fundamentals_kr.json" if market == "kr"
                          else "fundamentals.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
        print(f"  {len(rows)}종목 -> {out.name}", flush=True)
    return 0


def fmt_pct(v: float | None) -> str:
    return "-" if v is None else f"{v * 100:+.1f}%"


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main([a.upper() for a in sys.argv[1:]]))
