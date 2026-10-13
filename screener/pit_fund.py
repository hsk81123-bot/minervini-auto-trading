# -*- coding: utf-8 -*-
"""시점 기준(point-in-time) 미국 펀더멘털 — 신호일에 이미 공시된 숫자만 쓴다.

과거 신호일에 '지금 받은' 재무 데이터를 붙이면 그 뒤에 나온 실적과 정정치가
섞인다. SEC XBRL 의 각 행에는 공시일(filed)이 있으므로, 신호일 d 에는
filed < d 인 행만으로 분기 표를 다시 만들고 지표를 낸다.

  1) download  SEC companyfacts.zip (전 종목 XBRL 일괄, 1GB 남짓)
  2) extract   유니버스 종목의 필요한 us-gaap 태그만 → .cache/us_facts.json.gz
  3) timeline  종목마다 공시일별 지표 → .cache/us_fund_pit.json.gz

지표는 화면용과 같은 함수(fundamentals.assemble → code33.compute)로 낸다.
'filed < d' (같은 날 공시는 쓰지 않는다): 신호는 장 마감 뒤에 나오지만 공시가
그 날 장 마감 뒤에 접수됐는지 일봉만으로는 알 수 없어 보수적으로 하루를 띄운다.

한계 — 결과를 읽을 때 같이 적는다:
  - 외국 기업(20-F, IFRS)은 us-gaap 태그가 없어 빠진다
  - 티커→CIK 는 지금의 매핑이다 (그 사이 티커가 바뀐 회사는 어긋날 수 있다)
  - 10-K/A·10-Q/A 정정 공시는 쓰지 않는다 (fundamentals.py 와 같다)

사용법:
    SEC_USER_AGENT="이름 연락처" python screener/pit_fund.py download
    python screener/pit_fund.py extract
    python screener/pit_fund.py timeline
"""
import gzip
import json
import os
import sys
import time
import zipfile
from bisect import bisect_left, bisect_right
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import code33 as Q
import fundamentals as F

ROOT = Path(__file__).resolve().parent.parent
# history.py 와 같은 규칙: HISTORY_CACHE 로 다른 캐시(긴 기간)를 쓴다
CACHE = Path(os.environ.get("HISTORY_CACHE") or ROOT / ".cache")
FACTS_ZIP = CACHE / "companyfacts.zip"
FACTS = CACHE / "us_facts.json.gz"
TIMELINE = CACHE / "us_fund_pit.json.gz"
SHARES = CACHE / "us_shares.json.gz"
UNIVERSE = ROOT / "docs" / "data" / "tickers_us.json"
SP500_PIT = CACHE / "sp500_pit.json"
URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"

FIELDS = {"revenue": F.REVENUE_TAGS, **F.US_TAGS}
FORMS = ("10-Q", "10-K")
EVENTS_FROM = "2017-01-01"    # 이 날 이후 공시만 타임라인으로 (첫 신호 2019-02)
STALE_DAYS = 200              # 최근 분기 종료일이 신호일보다 이만큼 넘게 지났으면 없는 셈


# ---------------------------------------------------------------- 1) 내려받기
def download() -> None:
    if not F.SEC_UA:
        raise SystemExit("SEC_USER_AGENT 가 비어 있다 — SEC 는 연락처 없는 요청을 막는다")
    import requests
    CACHE.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    tmp = FACTS_ZIP.with_suffix(".part")
    with requests.get(URL, headers=F.SEC_HEADERS, stream=True,
                      timeout=(10, 120)) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    tmp.replace(FACTS_ZIP)
    print(f"  companyfacts.zip {FACTS_ZIP.stat().st_size / 1e6:.0f}MB "
          f"({time.time() - t0:.0f}s)", flush=True)


# ---------------------------------------------------------------- 2) 추리기
def bucket(start: str, end: str) -> str | None:
    """fundamentals.pick_periods 가 쓰는 기간 길이: 분기 80~100일, 연간 350~380일."""
    n = (F._d(end) - F._d(start)).days
    return "q" if 80 <= n <= 100 else "a" if 350 <= n <= 380 else None


def compact(facts: dict) -> dict[str, dict[str, list]]:
    """companyfacts 한 회사 → {필드: {태그: [[start, end, val, filed, form], …]}}.

    기간 항목의 10-Q/10-K 중 분기·연간 길이만 남기고 공시일 순으로 정렬한다.
    단위는 fundamentals.sec_concept 처럼 태그의 첫 단위다 (EPS 는 USD/shares).

    같은 기간을 다음 공시가 같은 값으로 다시 싣는 행(비교 기간)은 뺀다:
    pick_periods 는 종료일마다 '보이는 것 중 가장 늦게 공시된' 행(같은 공시일이면
    목록에서 먼저 나온 행)을 고른다. 그래서 공시일 순으로 정렬하되 같은 공시일
    안의 원래 순서는 지키고, 한 공시에 그 종료일 행이 하나뿐이면서 직전에 고를
    행과 (시작일, 값)이 같을 때만 뺀다 — 어느 시점에 골라도 결과가 같다.
    값이 바뀐 정정은 남는다.
    """
    gaap = facts.get("facts", {}).get("us-gaap", {})
    out: dict[str, dict[str, list]] = {}
    for field, tags in FIELDS.items():
        for tag in tags:
            units = gaap.get(tag, {}).get("units") or {}
            if not units:
                continue
            rows = sorted(([r["start"], r["end"], r["val"], r["filed"], r["form"]]
                           for r in units[next(iter(units))]
                           if r.get("start") and r.get("end")
                           and r.get("form") in FORMS and r.get("filed")
                           and r.get("val") is not None),
                          key=lambda x: x[3])           # 안정 정렬
            groups: dict[tuple, list] = {}
            for r in rows:
                b = bucket(r[0], r[1])
                if b is not None:
                    groups.setdefault((r[3], b, r[1]), []).append(r)
            last: dict[tuple, tuple] = {}
            drop = set()
            for (fd, b, end), g in groups.items():      # 삽입 순서 = 공시일 순
                head = (g[0][0], g[0][2])
                if len(g) == 1 and last.get((b, end)) == head:
                    drop.add(id(g[0]))
                last[(b, end)] = head
            kept = [r for r in rows if bucket(r[0], r[1]) is not None
                    and id(r) not in drop]
            if kept:
                out.setdefault(field, {})[tag] = kept
    return out


def compact_shares(facts: dict) -> list[list]:
    """표지의 발행주식수 → [[공시일, 기준일, 총 주식수], …] 공시일 순.

    dei:EntityCommonStockSharesOutstanding 은 종류가 여럿인 회사(GOOGL 등)에서 같은
    공시에 종류별 값이 따로 온다. 같은 (공시번호, 기준일)의 서로 다른 값은 더해
    총 주식수로 쓴다. 같은 행이 여러 번 실린 경우(accn·val 이 같음)는 한 번만 센다.
    """
    units = (facts.get("facts", {}).get("dei", {})
             .get("EntityCommonStockSharesOutstanding", {}).get("units") or {})
    rows = units.get("shares") or next(iter(units.values()), [])
    seen, tot, meta = set(), {}, {}
    for r in rows:
        if r.get("form") not in FORMS + ("10-K/A", "10-Q/A") or not r.get("filed") \
                or r.get("val") is None or not r.get("end"):
            continue
        key = (r.get("accn"), r["end"], r["val"])
        if key in seen:
            continue
        seen.add(key)
        g = (r.get("accn") or r["filed"], r["end"])
        tot[g] = tot.get(g, 0) + r["val"]
        meta[g] = r["filed"]
    return sorted(([meta[g], g[1], v] for g, v in tot.items() if v > 0),
                  key=lambda x: (x[0], x[1]))


def shares_at(rows: list[list] | None, d: str, max_age_days: int = 400,
              backfill_days: int = 0) -> tuple[float, str] | None:
    """신호일 d 에 알 수 있던 가장 최근 발행주식수 (공시일 < d) → (주식수, 기준일).
    기준일이 d 보다 max_age_days 넘게 전이면 없는 셈.

    XBRL 표지 공시는 2009~2011년에 시작해 그 전 날짜에는 값이 없다. backfill_days > 0 이면
    d 이후 backfill_days 안에 첫 공시가 있을 때 그 값을 쓴다 — 미래 정보를 쓰는 셈이라
    '근사 비중' 용도로만(결과 판정이 아니라 가중치의 대략적인 크기) 허용하고, 호출하는 쪽이
    분할 배수로 d 기준으로 되돌린다 (marketcap.Caps)."""
    if not rows:
        return None
    i = bisect_left([x[0] for x in rows], d) - 1
    if i < 0:
        if backfill_days and rows[0][0] <= (date.fromisoformat(d)
                                            + timedelta(days=backfill_days)).isoformat():
            return rows[0][2], rows[0][1]
        return None
    # 같은 공시일에 여러 기준일이 있으면 가장 최근 기준일
    j = i
    while j > 0 and rows[j - 1][0] == rows[i][0]:
        j -= 1
    fd, end, v = max(rows[j:i + 1], key=lambda x: x[1])
    if end < (date.fromisoformat(d) - timedelta(days=max_age_days)).isoformat():
        return None
    return v, end


def universe_ciks() -> dict[str, str]:
    """유니버스 + (있으면) 기간 중 S&P 500 구성종목이던 종목 → CIK."""
    names = set(json.loads(UNIVERSE.read_text(encoding="utf-8")))
    if SP500_PIT.exists():                   # spx_validate.py members 산출물
        m = json.loads(SP500_PIT.read_text(encoding="utf-8"))
        names |= set(m["current"])
        names |= {x for ch in m["changes"] for x in (ch["added"], ch["removed"]) if x}
    cmap = F.load_cik_map()
    out = {}
    for t in sorted(names):
        cik = cmap.get(t) or cmap.get(t.replace("-", "."))
        if cik:
            out[t] = cik
    return out


def extract() -> None:
    ciks = universe_ciks()
    t0 = time.time()
    out, shares, missing = {}, {}, 0
    with zipfile.ZipFile(FACTS_ZIP) as z:
        have = set(z.namelist())
        for n, (t, cik) in enumerate(sorted(ciks.items()), 1):
            name = f"CIK{cik}.json"
            if name not in have:
                missing += 1
                continue
            raw = json.loads(z.read(name))
            c = compact(raw)
            if c:
                out[t] = c
            sh = compact_shares(raw)
            if sh:
                shares[t] = sh
            if n % 1000 == 0:
                print(f"  {n}/{len(ciks)} ({time.time() - t0:.0f}s)", flush=True)
    with gzip.open(FACTS, "wt", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    with gzip.open(SHARES, "wt", encoding="utf-8") as f:
        json.dump(shares, f, separators=(",", ":"))
    print(f"  CIK 매핑 {len(ciks)} · 파일 없음 {missing} · us-gaap 태그 있음 "
          f"{len(out)} → {FACTS} · 발행주식수 있음 {len(shares)} → {SHARES} "
          f"({time.time() - t0:.0f}s)", flush=True)


# ---------------------------------------------------------------- 3) 타임라인
def prepare(c: dict[str, dict[str, list]]) -> dict:
    """{필드: {태그: (공시일 목록, 행 dict 목록)}} — 공시일 순이라 앞부분만 자르면 된다."""
    return {field: {tag: ([r[3] for r in rows],
                          [{"start": a, "end": e, "val": v, "filed": fd, "form": fm}
                           for a, e, v, fd, fm in rows])
                    for tag, rows in tags.items()}
            for field, tags in c.items()}


def series_asof(prep: dict, cutoff: str) -> dict:
    """filed ≤ cutoff 인 행만으로 필드별 원시 행 (태그 선택도 그 시점에 보이던 행으로 —
    나중에 쓰기 시작한 태그를 미리 고르지 않게)."""
    series = {}
    for field, tags in prep.items():
        best = (None, ("", -1))
        for tag in FIELDS[field]:
            if tag not in tags:
                continue
            filed, rows = tags[tag]
            rows = rows[:bisect_right(filed, cutoff)]
            if not rows:
                continue
            rank = F.tag_rank(rows)
            if rank > best[1]:
                best = (rows, rank)
        if best[0]:
            series[field] = best[0]
    return series


def doc_asof(prep: dict, cutoff: str) -> dict:
    """filed ≤ cutoff 인 행만으로 fundamentals.fetch_us 와 같은 모양의 문서."""
    quarters, annuals = F.assemble(series_asof(prep, cutoff))
    return {"market": "us", "quarters": quarters[:F.QUARTERS_KEEP],
            "annuals": annuals[:F.YEARS_KEEP]}


def full_quarters(c: dict) -> list[dict]:
    """한 종목의 전체 분기 이력 (오름차순, 자르지 않음) — SUE 처럼 긴 이력이 필요할 때.
    값은 지금까지 공시된 최신본이다 (정정·분할 반영)."""
    quarters, _ = F.assemble(series_asof(prepare(c), "9999-12-31"))
    return sorted(quarters, key=lambda q: q["end"])


def metrics(doc: dict) -> list | None:
    """[최근 분기 종료일, EPS 성장, 매출 성장, 영업이익률 변화, EPS 가속]."""
    if not doc["quarters"]:
        return None
    m = Q.compute(doc)
    return [m["as_of"], m["eps_growth"], m["revenue_growth"], m["opm_change"],
            m["eps_accel"]]


def ticker_timeline(c: dict) -> list[list]:
    """[[공시일, 지표…], …] — 공시일 오름차순. 신호일 d 에는 공시일 < d 인 마지막 행."""
    filed = sorted({r[3] for tags in c.values() for rows in tags.values()
                    for r in rows if r[3] >= EVENTS_FROM})
    prep = prepare(c)
    out = []
    for fd in filed:
        m = metrics(doc_asof(prep, fd))
        if m is not None and (not out or out[-1][1:] != m):
            out.append([fd] + m)
    return out


def timeline() -> None:
    with gzip.open(FACTS, "rt", encoding="utf-8") as f:
        facts = json.load(f)
    t0 = time.time()
    out = {}
    for n, (t, c) in enumerate(sorted(facts.items()), 1):
        tl = ticker_timeline(c)
        if tl:
            out[t] = tl
        if n % 500 == 0:
            print(f"  {n}/{len(facts)} ({time.time() - t0:.0f}s)", flush=True)
    with gzip.open(TIMELINE, "wt", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"  타임라인 {len(out)}종목 · 공시 시점 {sum(map(len, out.values()))}개 "
          f"→ {TIMELINE} ({time.time() - t0:.0f}s)", flush=True)


# ---------------------------------------------------------------- 조회
def load_shares() -> dict[str, list[list]]:
    with gzip.open(SHARES, "rt", encoding="utf-8") as f:
        return json.load(f)


def load_timeline() -> dict[str, list[list]]:
    with gzip.open(TIMELINE, "rt", encoding="utf-8") as f:
        return json.load(f)


def lookup(tl: list[list] | None, d: str) -> dict | None:
    """신호일 d 에 알 수 있던 지표. 공시가 없거나 최근 분기가 너무 오래면 None."""
    if not tl:
        return None
    i = bisect_left([x[0] for x in tl], d) - 1      # 공시일 < d
    if i < 0:
        return None
    fd, as_of, eps, rev, opm, acc = tl[i]
    if not as_of or as_of < (date.fromisoformat(d)
                             - timedelta(days=STALE_DAYS)).isoformat():
        return None
    return {"filed": fd, "as_of": as_of, "eps_growth": eps,
            "revenue_growth": rev, "opm_change": opm, "eps_accel": acc}


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    steps = {"download": download, "extract": extract, "timeline": timeline}
    if cmd not in steps:
        raise SystemExit(f"사용법: pit_fund.py {'|'.join(steps)}")
    steps[cmd]()
