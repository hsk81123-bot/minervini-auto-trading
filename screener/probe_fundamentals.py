# -*- coding: utf-8 -*-
"""Phase 0 — 펀더멘털 데이터 소스 필드 검증 (일회성 프로브).

본 구현(fundamentals.py)을 짜기 전에, 실제 응답에서 어떤 필드가 어떤 이름으로
몇 개 나오는지 확인하는 용도다. 개발 환경에서는 금융 API가 네트워크 정책으로
막혀 있어 GitHub Actions 안에서만 실제 응답을 볼 수 있다.

사용법:
    python screener/probe_fundamentals.py          # 미국 + 한국(키 있으면)
    python screener/probe_fundamentals.py us
    python screener/probe_fundamentals.py kr

한국은 DART_API_KEY 환경변수가 필요하다. 없으면 그 사실만 알리고 건너뛴다.
"""
import io
import os
import sys
import time
import zipfile
from xml.etree import ElementTree

import requests

# SEC는 요청자를 식별할 수 있는 User-Agent를 요구한다. 공식 안내는 연락처(이메일)를
# 넣으라고 하므로, 넣고 싶으면 SEC_USER_AGENT 환경변수로 주입한다.
SEC_UA = os.environ.get("SEC_USER_AGENT", "").strip() or (
    "minervini-auto-trading/1.0 "
    "(+https://github.com/hsk81123-bot/minervini-auto-trading)")
UA = {"User-Agent": SEC_UA,
      "Accept": "application/json,text/plain,*/*",
      "Accept-Encoding": "gzip, deflate"}
SEC_RATE_SLEEP = 0.15          # 초당 10회 권장 → 여유를 두고 간격을 준다

# 티커 매핑을 못 받아도 미국 검증을 계속할 수 있게 샘플 CIK를 박아둔다.
# (응답의 entityName을 함께 출력하므로 CIK가 틀리면 바로 드러난다)
FALLBACK_CIK = {"AAPL": "0000320193", "NVDA": "0001045810",
                "VLO": "0001035002"}

US_SAMPLES = ["AAPL", "NVDA", "VLO"]   # 9월 결산 / 1월 결산 / 통과 종목
KR_SAMPLES = ["005930", "000660"]      # 삼성전자, SK하이닉스

# 기업마다 쓰는 매출 태그가 달라서 우선순위를 두고 위에서부터 찾는다.
REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
]
OTHER_TAGS = {
    "영업이익": ["OperatingIncomeLoss"],
    "순이익": ["NetIncomeLoss"],
    "희석EPS": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
    "자기자본": ["StockholdersEquity"],
}


def ua_hint(ua: str) -> str:
    """로그용 UA 표기 — 이메일 아이디를 가린다 (공개 저장소는 로그도 공개)."""
    import re
    return re.sub(r"[\w.+-]+@", "***@", ua)


def why(text: str) -> str:
    """오류 응답에서 사람이 읽을 부분만. SEC는 거부 이유를 <title>과 본문에 적는다."""
    flat = " ".join((text or "").split())
    import re
    title = re.search(r"<title[^>]*>(.*?)</title>", flat, re.I)
    if title:
        msg = title.group(1).strip()
        # 제목 뒤 본문에서 설명 문장도 한 줄 건진다
        body = re.sub(r"<[^>]+>", " ", flat)
        body = " ".join(body.split())
        hint = body[:220]
        return f"{msg} | {hint}" if hint else msg
    return flat[:220] or "(본문 없음)"


def get(url: str, quiet_404: bool = False, **kw):
    """재시도 포함 GET. 실패하면 None.

    404는 '그 태그가 없다'는 뜻이므로 재시도하지 않는다 (Phase 0 확인 결과).
    재시도는 403/429/5xx 같은 일시적·정책성 실패에만 의미가 있다.
    """
    for attempt in range(1, 4):
        try:
            r = requests.get(url, headers=UA, timeout=30, **kw)
            if r.status_code == 200:
                return r
            if r.status_code == 404:
                if not quiet_404:
                    print(f"    HTTP 404 (없음) {url[:80]}")
                return None
            print(f"    HTTP {r.status_code} ({attempt}/3) {url[:80]}")
            print(f"      이유: {why(r.text)}")
        except requests.RequestException as e:
            print(f"    {type(e).__name__} ({attempt}/3): {e}")
        time.sleep(2 ** attempt)
    return None


# ------------------------------------------------------------------ 미국 (SEC)
def cik_map() -> dict[str, str]:
    r = get("https://www.sec.gov/files/company_tickers.json")
    if not r:
        return {}
    data = r.json()
    return {v["ticker"].upper(): str(v["cik_str"]).zfill(10)
            for v in data.values()}


def quarterly_entries(unit_rows: list[dict]) -> list[dict]:
    """10-Q/10-K에서 기간이 약 1분기(80~100일)인 항목만 최신순으로."""
    out = []
    for row in unit_rows:
        start, end = row.get("start"), row.get("end")
        if not start or not end:
            continue  # 시점 항목(자기자본 등)은 기간이 없다
        days = (_date(end) - _date(start)).days
        if 80 <= days <= 100 and row.get("form") in ("10-Q", "10-K"):
            out.append(row)
    out.sort(key=lambda r: r["end"], reverse=True)
    # 같은 분기가 수정 공시로 중복되면 최신 것만
    seen, uniq = set(), []
    for row in out:
        if row["end"] in seen:
            continue
        seen.add(row["end"])
        uniq.append(row)
    return uniq


def _date(s: str):
    from datetime import date
    y, m, d = s.split("-")
    return date(int(y), int(m), int(d))


def probe_concept(cik: str, tag: str) -> tuple[list[dict], str | None]:
    """companyconcept 조회 → (분기 항목들, 단위 이름). 없으면 ([], None)."""
    url = (f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}"
           f"/us-gaap/{tag}.json")
    time.sleep(SEC_RATE_SLEEP)
    r = get(url, quiet_404=True)
    if not r:
        return [], None
    body = r.json()
    units = body.get("units", {})
    if not units:
        return [], None
    unit_name = next(iter(units))
    rows = quarterly_entries(units[unit_name])
    if rows:
        rows[0] = dict(rows[0], _entity=body.get("entityName", ""))
    return rows, unit_name


def probe_access() -> None:
    """어느 호스트·헤더 조합이 통과하는지 먼저 가린다 (403의 원인 좁히기)."""
    print("[접근 점검] 호스트·User-Agent 조합별 응답 코드")
    variants = {
        "기본(저장소 URL)": SEC_UA,
        "이메일 포함 형식": "minervini-auto-trading admin@example.com",
        "브라우저 위장": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/120.0 Safari/537.36"),
    }
    targets = {
        "www.sec.gov/files/company_tickers.json":
            "https://www.sec.gov/files/company_tickers.json",
        "data.sec.gov/submissions/AAPL":
            "https://data.sec.gov/submissions/CIK0000320193.json",
        "data.sec.gov/companyconcept/AAPL OperatingIncomeLoss":
            "https://data.sec.gov/api/xbrl/companyconcept/CIK0000320193"
            "/us-gaap/OperatingIncomeLoss.json",
    }
    for vname, ua in variants.items():
        for tname, url in targets.items():
            try:
                r = requests.get(url, timeout=25, headers={
                    "User-Agent": ua,
                    "Accept": "application/json,text/plain,*/*",
                    "Accept-Encoding": "gzip, deflate"})
                code, size = r.status_code, len(r.content)
            except requests.RequestException as e:
                code, size = type(e).__name__, 0
            print(f"  {vname:<16} {tname:<48} {code} ({size:,}B)")
            if code != 200 and size:
                print(f"      이유: {why(r.text)}")
            time.sleep(0.3)
    print()


def probe_us() -> None:
    print("=" * 70)
    print("미국 — SEC EDGAR XBRL companyconcept")
    print(f"User-Agent: {ua_hint(SEC_UA)}")
    print("=" * 70)
    probe_access()
    cmap = cik_map()
    print(f"company_tickers.json: {len(cmap)}개 티커 매핑 로드")
    if not cmap:
        print("  → 티커 매핑 실패. 샘플 CIK를 하드코딩해 계속 진행한다.")
        cmap = dict(FALLBACK_CIK)

    for ticker in US_SAMPLES:
        cik = cmap.get(ticker)
        print(f"\n[{ticker}] CIK={cik}")
        if not cik:
            print("  매핑에 없음 — 티커 표기 확인 필요")
            continue

        # 매출: 어느 태그가 실제로 나오는지
        hit_tag = None
        for tag in REVENUE_TAGS:
            rows, unit = probe_concept(cik, tag)
            mark = f"{len(rows)}개 분기" if rows else "없음"
            print(f"  매출 태그 {tag[:52]:<52} {mark}")
            if rows and not hit_tag:
                hit_tag, hit_rows, hit_unit = tag, rows, unit
        if hit_tag:
            print(f"  → 채택: {hit_tag} ({hit_unit}) "
                  f"| 회사명 {hit_rows[0].get('_entity', '?')}")
            for row in hit_rows[:8]:
                print(f"     {row['start']}~{row['end']} "
                      f"FY{row.get('fy')}{row.get('fp')} {row.get('form'):<5} "
                      f"{row['val']:>18,}")
            ends = {r["end"][5:] for r in hit_rows[:8]}
            print(f"  분기 말일 패턴: {sorted(ends)}")
        else:
            print("  → 매출 태그를 하나도 못 찾음 (본 구현에서 폴백 필요)")

        for label, tags in OTHER_TAGS.items():
            for tag in tags:
                rows, unit = probe_concept(cik, tag)
                if rows:
                    latest = rows[0]
                    print(f"  {label:<7} {tag[:34]:<34} {len(rows)}개 분기 "
                          f"| 최근 {latest['end']} {latest['val']:,} ({unit})")
                    break
            else:
                print(f"  {label:<7} {'/'.join(tags)[:34]:<34} 없음")

    print("\n[확인 포인트] 분기 말일 패턴에 4분기(회계연도 말)가 빠져 있으면,")
    print("10-K는 연간 수치만 담고 4분기를 따로 내보내지 않는다는 뜻이다.")
    print("그 경우 본 구현에서 4분기 = 연간 - 1~3분기 합으로 계산해야 한다.")


# ----------------------------------------------------------------- 한국 (DART)
def probe_kr() -> None:
    print("\n" + "=" * 70)
    print("한국 — DART OpenAPI")
    print("=" * 70)
    key = os.environ.get("DART_API_KEY", "").strip()
    if not key:
        print("DART_API_KEY 가 없다 → 한국 검증 건너뜀.")
        print("발급: https://opendart.fss.or.kr → 인증키 신청,")
        print("저장소 Settings → Secrets → Actions 에 DART_API_KEY 로 등록.")
        return

    r = get("https://opendart.fss.or.kr/api/corpCode.xml",
            params={"crtfc_key": key})
    if not r:
        print("corpCode.xml 조회 실패 — 키가 유효한지 확인 필요")
        return
    try:
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        root = ElementTree.fromstring(zf.read(zf.namelist()[0]))
    except (zipfile.BadZipFile, ElementTree.ParseError):
        print(f"corpCode 응답이 zip/xml 이 아니다: {r.text[:200]}")
        return
    code_map = {}
    for item in root.iter("list"):
        stock = (item.findtext("stock_code") or "").strip()
        if stock:
            code_map[stock] = item.findtext("corp_code").strip()
    print(f"corpCode.xml: 상장사 {len(code_map)}개 매핑 로드")

    # 11013=1분기, 11012=반기, 11014=3분기, 11011=사업보고서
    reports = {"11013": "1분기", "11012": "반기",
               "11014": "3분기", "11011": "사업(연간)"}
    for stock in KR_SAMPLES:
        corp = code_map.get(stock)
        print(f"\n[{stock}] corp_code={corp}")
        if not corp:
            print("  매핑에 없음")
            continue
        for rc, label in reports.items():
            r = get("https://opendart.fss.or.kr/api/fnlttSinglAcnt.json",
                    params={"crtfc_key": key, "corp_code": corp,
                            "bsns_year": "2025", "reprt_code": rc})
            if not r:
                continue
            body = r.json()
            if body.get("status") != "000":
                print(f"  {label:<9} status={body.get('status')} "
                      f"{body.get('message')}")
                continue
            rows = body.get("list", [])
            cfs = [x for x in rows if x.get("fs_div") == "CFS"]
            names = [x["account_nm"] for x in (cfs or rows)]
            print(f"  {label:<9} 항목 {len(rows)}개 (연결 {len(cfs)}개)")
            for want in ("매출액", "영업이익", "당기순이익"):
                found = next((x for x in (cfs or rows)
                              if x["account_nm"].replace(" ", "") == want), None)
                val = found.get("thstrm_amount") if found else "없음"
                print(f"     {want:<7} {val}")
            if not any("주당" in n for n in names):
                print("     주당이익(EPS) 항목 없음 → 순이익÷주식수로 계산 필요")

    print("\n[확인 포인트] 주요계정(fnlttSinglAcnt)에 EPS가 없으면,")
    print("전체 재무제표(fnlttSinglAcntAll)를 쓰거나 순이익으로 대체해야 한다.")


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    which = sys.argv[1].lower() if len(sys.argv) > 1 else "all"
    if which == "access":        # 접근 점검만 (로그가 짧아 원인 보기 쉽다)
        print(f"User-Agent: {ua_hint(SEC_UA)}\n")
        probe_access()
        sys.exit(0)
    if which in ("all", "us"):
        probe_us()
    if which in ("all", "kr"):
        probe_kr()
