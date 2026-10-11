# -*- coding: utf-8 -*-
"""펀더멘털 수집 (미국 SEC EDGAR / 한국 DART).

Phase 0에서 실제 응답으로 확인한 사실을 그대로 반영한다:
- SEC는 연락처가 담긴 User-Agent만 통과시킨다 (SEC_USER_AGENT 환경변수)
- 매출 태그는 기업마다 달라 우선순위 목록을 순서대로 시도한다
- 404(NoSuchKey)는 '그 태그가 없다'는 뜻이므로 재시도하지 않는다
- 4분기는 양 시장 모두 연간 - (1~3분기 합)으로 역산해야 하는 경우가 있다
- DART 주요계정의 thstrm_amount는 당해 3개월치다 (누적은 thstrm_add_amount)
- DART 계정명은 '당기순이익(손실)'처럼 괄호가 붙고, 같은 계정이 중복 행으로 온다

사용법:
    python screener/fundamentals.py AAPL
    python screener/fundamentals.py 005930.KS 000660.KQ
    python screener/fundamentals.py --refresh-maps AAPL   # 매핑 캐시 다시 받기

출력: docs/data/fundamentals/<TICKER>.json (한국은 fundamentals_kr/)
"""
import io
import json
import os
import sys
import time
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

import requests

DATA_DIR = Path(__file__).resolve().parent.parent / "docs" / "data"

SEC_UA = os.environ.get("SEC_USER_AGENT", "").strip()
SEC_HEADERS = {
    "User-Agent": SEC_UA,
    "Accept": "application/json,text/plain,*/*",
    "Accept-Encoding": "gzip, deflate",
}
SEC_SLEEP = 0.12          # SEC 권장 초당 10회 → 여유를 둔다
DART_SLEEP = 0.05         # 일 20,000회라 여유롭다

# 연결이 죽은 호스트를 오래 기다리지 않는다. 연결은 5초, 응답은 30초.
# (DART가 연결을 받지 않을 때 30초 x 3회를 호출마다 기다려 실행이 1시간으로 늘었다)
TIMEOUT = (5, 30)
# 한 호스트가 연속으로 이만큼 실패하면 그 호스트 호출을 그만둔다.
# 죽은 서버를 400종목 내내 두드리는 것을 막는 차단기다.
FAIL_LIMIT = 5
_fails: dict[str, int] = {}
_tripped: set[str] = set()

QUARTERS_KEEP = 12        # 저장할 분기 수 (성장률 계산에 최소 8개 필요)
YEARS_KEEP = 5            # 저장할 회계연도 수

# 기업마다 쓰는 매출 태그가 다르다 (VLO는 Including, AAPL은 Excluding)
REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
]
US_TAGS = {
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss"],
    "eps_diluted": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
}
US_INSTANT_TAGS = {"equity": ["StockholdersEquity",
                              "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]}

# DART 주요계정 — 공백 제거 후 접두 비교한다 ('당기순이익(손실)' 때문)
KR_ACCOUNTS = {
    "revenue": ("매출액", "수익(매출액)", "영업수익"),
    "operating_income": ("영업이익",),
    "net_income": ("당기순이익", "분기순이익", "반기순이익"),
}
KR_EQUITY_ACCOUNTS = ("자본총계",)
# 보고서 코드 → 분기 번호 (thstrm_amount가 해당 3개월치)
KR_REPORTS = {"11013": 1, "11012": 2, "11014": 3}
KR_ANNUAL = "11011"


# ------------------------------------------------------------------- 공통 유틸
def _d(s: str) -> date:
    y, m, dd = s.split("-")
    return date(int(y), int(m), int(dd))


def host_of(url: str) -> str:
    return url.split("/")[2] if "//" in url else url


def host_down(url: str) -> bool:
    """차단기가 열린 호스트인지."""
    return host_of(url) in _tripped


def http_get(url: str, headers: dict | None = None, params: dict | None = None,
             quiet_404: bool = False, retries: int = 2):
    """재시도 포함 GET.

    - 404는 '없다'는 뜻이므로 재시도하지 않는다 (Phase 0 확인)
    - 연결 자체가 안 되는 호스트는 FAIL_LIMIT번 연속 실패 후 포기한다.
      안 그러면 호출마다 타임아웃을 기다려 실행이 몇 시간으로 늘어난다
    """
    host = host_of(url)
    if host in _tripped:
        return None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=headers, params=params,
                             timeout=TIMEOUT)
            if r.status_code == 200:
                _fails[host] = 0
                return r
            if r.status_code == 404:
                _fails[host] = 0        # 서버는 살아 있다
                if not quiet_404:
                    print(f"    404 {url[:90]}", flush=True)
                return None
            print(f"    HTTP {r.status_code} ({attempt}/{retries}) {url[:70]}",
                  flush=True)
        except requests.RequestException as e:
            print(f"    {type(e).__name__} ({attempt}/{retries}) {host}: "
                  f"{str(e)[:120]}", flush=True)
        if attempt < retries:
            time.sleep(2 ** attempt)
    _fails[host] = _fails.get(host, 0) + 1
    if _fails[host] >= FAIL_LIMIT:
        _tripped.add(host)
        print(f"  !! {host} 연속 {FAIL_LIMIT}회 실패 → 이 실행에서는 "
              f"더 호출하지 않는다", flush=True)
    return None


# -------------------------------------------------------------------- 매핑 캐시
def load_cik_map(refresh: bool = False) -> dict[str, str]:
    """티커 → 10자리 CIK. docs/data/cik_map.json에 캐싱한다."""
    path = DATA_DIR / "cik_map.json"
    if path.exists() and not refresh:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    r = http_get("https://www.sec.gov/files/company_tickers.json",
                 headers=SEC_HEADERS)
    if not r:
        raise RuntimeError("SEC 티커 목록을 받지 못했다 "
                           "(SEC_USER_AGENT에 연락처가 있는지 확인)")
    out = {v["ticker"].upper(): str(v["cik_str"]).zfill(10)
           for v in r.json().values()}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"  cik_map.json 갱신: {len(out)}개 티커", flush=True)
    return out


def load_corp_map(key: str, refresh: bool = False) -> dict[str, str]:
    """종목코드(6자리) → DART corp_code. docs/data/corp_code_kr.json에 캐싱."""
    path = DATA_DIR / "corp_code_kr.json"
    if path.exists() and not refresh:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    r = http_get("https://opendart.fss.or.kr/api/corpCode.xml",
                 params={"crtfc_key": key})
    if not r:
        raise RuntimeError("DART corpCode.xml을 받지 못했다")
    try:
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        root = ElementTree.fromstring(zf.read(zf.namelist()[0]))
    except (zipfile.BadZipFile, ElementTree.ParseError) as e:
        raise RuntimeError(f"corpCode 응답이 zip/xml이 아니다: {r.text[:120]}") from e
    out = {}
    for item in root.iter("list"):
        stock = (item.findtext("stock_code") or "").strip()
        if stock:
            out[stock] = (item.findtext("corp_code") or "").strip()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"  corp_code_kr.json 갱신: {len(out)}개 상장사", flush=True)
    return out


# ------------------------------------------------------- 미국: 기간 항목 골라내기
def pick_periods(rows: list[dict], lo: int, hi: int) -> list[dict]:
    """기간 길이가 lo~hi일인 10-Q/10-K 항목만. 같은 종료일은 최신 공시분만."""
    cand = []
    for row in rows:
        start, end = row.get("start"), row.get("end")
        if not start or not end or row.get("form") not in ("10-Q", "10-K"):
            continue
        if lo <= (_d(end) - _d(start)).days <= hi:
            cand.append(row)
    # 정정공시가 있으면 filed가 늦은 것을 택한다
    best: dict[str, dict] = {}
    for row in cand:
        cur = best.get(row["end"])
        if cur is None or str(row.get("filed", "")) > str(cur.get("filed", "")):
            best[row["end"]] = row
    return sorted(best.values(), key=lambda r: r["end"], reverse=True)


def pick_instants(rows: list[dict]) -> list[dict]:
    """시점 항목(재무상태표). start가 없다 — Phase 0에서 확인한 차이."""
    best: dict[str, dict] = {}
    for row in rows:
        end = row.get("end")
        if not end or row.get("start") or row.get("form") not in ("10-Q", "10-K"):
            continue
        cur = best.get(end)
        if cur is None or str(row.get("filed", "")) > str(cur.get("filed", "")):
            best[end] = row
    return sorted(best.values(), key=lambda r: r["end"], reverse=True)


def derive_q4(quarters: list[dict], annuals: list[dict]) -> list[dict]:
    """연간 구간에 분기가 3개만 있으면 4분기를 역산한다.

    VLO처럼 10-K가 4분기를 따로 내보내지 않는 기업이 있다 (Phase 0 확인).
    """
    out = list(quarters)
    for ann in annuals:
        a_start, a_end = _d(ann["start"]), _d(ann["end"])
        inside = [q for q in out
                  if a_start <= _d(q["start"]) and _d(q["end"]) <= a_end]
        if len(inside) != 3:
            continue
        last_end = max(_d(q["end"]) for q in inside)
        val = ann["val"] - sum(q["val"] for q in inside)
        out.append({
            "start": last_end.isoformat(),
            "end": ann["end"],
            # EPS는 소수라 뺄셈에 부동소수 잡음이 남는다 (1.8399999999999999)
            "val": round(val, 4) if isinstance(val, float) else val,
            "form": ann.get("form", "10-K"),
            "derived": True,
        })
    return sorted(out, key=lambda r: r["end"], reverse=True)


def round_floats(doc: dict) -> dict:
    """소수 값만 4자리로 맞춘다 (금액은 정수라 영향 없음)."""
    for group in ("quarters", "annuals"):
        for row in doc.get(group, []):
            for k, v in row.items():
                if isinstance(v, float):
                    row[k] = round(v, 4)
    return doc


def sec_concept(cik: str, tag: str) -> tuple[list[dict], str | None]:
    """companyconcept 한 태그 조회 → (행들, 단위). 없으면 ([], None)."""
    url = (f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}"
           f"/us-gaap/{tag}.json")
    time.sleep(SEC_SLEEP)
    r = http_get(url, headers=SEC_HEADERS, quiet_404=True)
    if not r:
        return [], None
    units = r.json().get("units", {})
    if not units:
        return [], None
    unit = next(iter(units))
    return units[unit], unit


def fetch_us(ticker: str, cik: str) -> dict:
    """미국 한 종목의 분기/연간 원시치."""
    series: dict[str, list[dict]] = {}
    revenue_tag = None
    for tag in REVENUE_TAGS:
        rows, _ = sec_concept(cik, tag)
        if rows:
            revenue_tag, series["revenue"] = tag, rows
            break
    for field, tags in US_TAGS.items():
        for tag in tags:
            rows, _ = sec_concept(cik, tag)
            if rows:
                series[field] = rows
                break
    for field, tags in US_INSTANT_TAGS.items():
        for tag in tags:
            rows, _ = sec_concept(cik, tag)
            if rows:
                series[field] = rows
                break

    # 분기/연간 정리: 매출 기준으로 기간을 잡고 나머지를 같은 종료일에 맞춘다
    quarters: dict[str, dict] = {}
    annuals: dict[str, dict] = {}
    for field in ("revenue", "operating_income", "net_income", "eps_diluted"):
        rows = series.get(field)
        if not rows:
            continue
        q = pick_periods(rows, 80, 100)
        a = pick_periods(rows, 350, 380)
        q = derive_q4(q, a)
        for item in q:
            slot = quarters.setdefault(item["end"], {
                "end": item["end"], "start": item["start"]})
            slot[field] = item["val"]
            if item.get("derived"):
                slot.setdefault("derived", []).append(field)
        for item in a:
            slot = annuals.setdefault(item["end"], {
                "end": item["end"], "start": item["start"]})
            slot[field] = item["val"]

    equity = [{"end": r["end"], "value": r["val"]}
              for r in pick_instants(series.get("equity", []))][:QUARTERS_KEEP]

    return {
        "ticker": ticker,
        "market": "us",
        "source": "SEC EDGAR companyconcept",
        "revenue_tag": revenue_tag,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "quarters": sorted(quarters.values(), key=lambda x: x["end"],
                           reverse=True)[:QUARTERS_KEEP],
        "annuals": sorted(annuals.values(), key=lambda x: x["end"],
                          reverse=True)[:YEARS_KEEP],
        "equity": equity,
    }


# ----------------------------------------------------------------- 한국: DART
def kr_amount(raw: str | None) -> float | None:
    """'79,140,503,000,000' → float. '-'·빈값·음수 표기도 처리."""
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "").replace(" ", "")
    if s in ("", "-", "--"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def kr_pick(rows: list[dict], names: tuple[str, ...],
            field: str = "thstrm_amount") -> float | None:
    """주요계정에서 계정명 접두가 맞는 첫 값. 연결(CFS) 우선, 중복 행은 첫 값만.

    DART 계정명은 '당기순이익(손실)'처럼 괄호가 붙어 정확 일치가 안 된다.
    """
    for want_cfs in (True, False):
        for row in rows:
            if (row.get("fs_div") == "CFS") != want_cfs:
                continue
            nm = str(row.get("account_nm", "")).replace(" ", "")
            if any(nm.startswith(n) for n in names):
                val = kr_amount(row.get(field))
                if val is not None:
                    return val
    return None


def dart_accounts(key: str, corp: str, year: int, reprt: str) -> list[dict]:
    time.sleep(DART_SLEEP)
    r = http_get("https://opendart.fss.or.kr/api/fnlttSinglAcnt.json",
                 params={"crtfc_key": key, "corp_code": corp,
                         "bsns_year": str(year), "reprt_code": reprt})
    if not r:
        return []
    body = r.json()
    if body.get("status") != "000":
        # 013 = 조회된 데이터 없음 (아직 공시 전) → 조용히 넘긴다
        if body.get("status") != "013":
            print(f"    DART {year}/{reprt}: {body.get('status')} "
                  f"{body.get('message')}", flush=True)
        return []
    return body.get("list", [])


KR_FIELDS = ("revenue", "operating_income", "net_income")


def cumulative_ok(std: dict[int, dict], cum: dict[int, dict],
                  field: str) -> bool | None:
    """당해(3개월) 값들의 합이 누적 값과 맞는지. 판단 불가면 None.

    Phase 0에서 삼성전자 2025로 thstrm_amount가 3개월치임을 확인했지만,
    종목·연도마다 다를 수 있으므로 매번 데이터로 확인한다. 1~3분기 중
    가장 늦은 분기의 누적값과 그 이전 당해값들의 합을 비교한다.
    """
    qs = sorted(q for q in std if q in cum)
    if len(qs) < 2:
        return None
    last = qs[-1]
    total = cum[last].get(field)
    parts = [std[q].get(field) for q in qs if q <= last]
    if total is None or any(p is None for p in parts):
        return None
    s = sum(parts)
    if total == 0:
        return s == 0
    return abs(s - total) / abs(total) < 0.005


def fetch_kr(ticker: str, corp: str, key: str, years: list[int]) -> dict:
    """한국 한 종목.

    thstrm_amount(당해 3개월)를 기본으로 쓰되, 누적 필드와 합이 맞는지
    해마다 검증한다. 어긋나면 누적의 차분으로 분기값을 복원한다 —
    누적을 분기로 착각하면 성장률이 전부 틀어진다.
    """
    quarters, annuals, basis_notes = [], [], {}
    for year in years:
        if host_down("https://opendart.fss.or.kr/"):
            print(f"  [{ticker}] DART 호출 중단 — 남은 연도 건너뜀", flush=True)
            break
        print(f"  [{ticker}] {year} 조회...", flush=True)
        year_q: dict[int, dict] = {}
        year_cum: dict[int, dict] = {}
        for reprt, qno in KR_REPORTS.items():
            rows = dart_accounts(key, corp, year, reprt)
            if not rows:
                continue
            year_q[qno] = {
                "label": f"{year}Q{qno}",
                "year": year, "quarter": qno,
                "revenue": kr_pick(rows, KR_ACCOUNTS["revenue"]),
                "operating_income": kr_pick(rows, KR_ACCOUNTS["operating_income"]),
                "net_income": kr_pick(rows, KR_ACCOUNTS["net_income"]),
            }
            year_cum[qno] = {
                f: kr_pick(rows, KR_ACCOUNTS[f], "thstrm_add_amount")
                for f in KR_FIELDS
            }

        # 당해값 합 == 누적값 인지 확인. 어긋나면 누적 차분으로 복원한다.
        checks = {f: cumulative_ok(year_q, year_cum, f) for f in KR_FIELDS}
        if year_q and any(v is False for v in checks.values()):
            basis_notes[str(year)] = "누적 차분으로 복원"
            print(f"    {year}: 당해값 합이 누적과 어긋남 → 누적 차분으로 복원 "
                  f"{ {k: v for k, v in checks.items()} }", flush=True)
            for qno in sorted(year_q):
                for f in KR_FIELDS:
                    cur = year_cum.get(qno, {}).get(f)
                    prev = year_cum.get(qno - 1, {}).get(f)
                    if cur is None:
                        continue
                    year_q[qno][f] = cur if qno == 1 or prev is None else cur - prev
                year_q[qno]["basis"] = "cumulative_diff"
        elif year_q:
            basis_notes[str(year)] = "당해(3개월) 값 그대로"
        rows = dart_accounts(key, corp, year, KR_ANNUAL)
        ann = None
        if rows:
            ann = {
                "label": f"{year}FY", "year": year,
                "revenue": kr_pick(rows, KR_ACCOUNTS["revenue"]),
                "operating_income": kr_pick(rows, KR_ACCOUNTS["operating_income"]),
                "net_income": kr_pick(rows, KR_ACCOUNTS["net_income"]),
                "equity": kr_pick(rows, KR_EQUITY_ACCOUNTS),
            }
            annuals.append(ann)

        # 4분기 역산: 연간 - (1~3분기 합). 미국과 같은 규칙 (Phase 0 확인)
        if ann and len(year_q) == 3:
            q4 = {"label": f"{year}Q4", "year": year, "quarter": 4,
                  "derived": []}
            for field in ("revenue", "operating_income", "net_income"):
                parts = [year_q[q].get(field) for q in sorted(year_q)]
                if ann.get(field) is None or any(p is None for p in parts):
                    q4[field] = None
                    continue
                q4[field] = ann[field] - sum(parts)
                q4["derived"].append(field)
            year_q[4] = q4
        quarters.extend(year_q[q] for q in sorted(year_q))

    quarters.sort(key=lambda x: (x["year"], x["quarter"]), reverse=True)
    annuals.sort(key=lambda x: x["year"], reverse=True)
    return {
        "ticker": ticker,
        "market": "kr",
        "source": "DART fnlttSinglAcnt (연결 우선)",
        "note": "EPS는 주요계정에 없어 저장하지 않는다 (순이익으로 대체)",
        "quarter_basis": basis_notes,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "quarters": quarters[:QUARTERS_KEEP],
        "annuals": annuals[:YEARS_KEEP],
    }


# -------------------------------------------------------------------- 저장/CLI
def out_path(ticker: str) -> Path:
    is_kr = ticker.endswith((".KS", ".KQ"))
    d = DATA_DIR / ("fundamentals_kr" if is_kr else "fundamentals")
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{ticker}.json"


def save(doc: dict) -> Path:
    round_floats(doc)
    path = out_path(doc["ticker"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    return path


def main(tickers: list[str], refresh: bool) -> int:
    us = [t for t in tickers if not t.endswith((".KS", ".KQ"))]
    kr = [t for t in tickers if t.endswith((".KS", ".KQ"))]
    failed = 0

    if us:
        if not SEC_UA:
            sys.exit("SEC_USER_AGENT 환경변수가 필요하다 "
                     "(연락처가 없으면 SEC가 403으로 거부한다)")
        cmap = load_cik_map(refresh)
        for t in us:
            cik = cmap.get(t.upper())
            if not cik:
                print(f"[{t}] CIK 매핑에 없음 — 건너뜀", flush=True)
                failed += 1
                continue
            doc = fetch_us(t.upper(), cik)
            if not doc["quarters"]:
                print(f"[{t}] 분기 데이터를 못 받았다", flush=True)
                failed += 1
                continue
            path = save(doc)
            q = doc["quarters"][0]
            print(f"[{t}] 분기 {len(doc['quarters'])} / 연간 "
                  f"{len(doc['annuals'])} | 최근 {q['end']} "
                  f"매출 {q.get('revenue'):,.0f} | 태그 {doc['revenue_tag']} "
                  f"| 역산분기 {sum(1 for x in doc['quarters'] if x.get('derived'))}개 "
                  f"-> {path.name}", flush=True)

    if kr:
        key = os.environ.get("DART_API_KEY", "").strip()
        if not key:
            sys.exit("DART_API_KEY 환경변수가 필요하다")
        cmap = load_corp_map(key, refresh)
        this_year = date.today().year
        years = list(range(this_year, this_year - YEARS_KEEP, -1))
        for t in kr:
            corp = cmap.get(t.split(".")[0])
            if not corp:
                print(f"[{t}] corp_code 매핑에 없음 — 건너뜀", flush=True)
                failed += 1
                continue
            doc = fetch_kr(t, corp, key, years)
            if not doc["quarters"]:
                print(f"[{t}] 분기 데이터를 못 받았다", flush=True)
                failed += 1
                continue
            path = save(doc)
            q = doc["quarters"][0]
            print(f"[{t}] 분기 {len(doc['quarters'])} / 연간 "
                  f"{len(doc['annuals'])} | 최근 {q['label']} "
                  f"매출 {q.get('revenue'):,.0f} -> {path.name}", flush=True)

    return 1 if failed and failed == len(tickers) else 0


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    args = [a for a in sys.argv[1:] if a != "--refresh-maps"]
    if not args:
        sys.exit(__doc__)
    sys.exit(main(args, refresh="--refresh-maps" in sys.argv))
