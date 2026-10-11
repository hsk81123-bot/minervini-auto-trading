# Minervini Auto Trading

마크 미너비니(SEPA) 방법론 기반 주식 스크리닝 + 매매 관리 웹앱.

## 구조

```
├─ .github/workflows/screener.yml   # 평일 22:00 UTC(장 마감 후) 자동 스크리닝 → results.json 커밋
├─ screener/
│  ├─ screener.py                   # 트렌드 템플릿 8조건 + RS 랭킹 + VCP 후보 점수
│  ├─ vcp.py                        # VCP(변동성 수축) 패턴 탐지
│  ├─ fundamentals.py               # SEC EDGAR / DART 재무 수집
│  ├─ code33.py                     # 재무 지표 + 코드33 점수
│  ├─ ondemand.py                   # 미통과 종목 차트 온디맨드 생성
│  ├─ backtest.py                   # 로직 검증: git 스냅샷으로 과거 추천 재현
│  └─ requirements.txt
├─ docs/                            # GitHub Pages 정적 웹앱 (빌드 도구 없음)
│  ├─ index.html                    # SPA 진입점 (해시 라우팅)
│  ├─ js/app.js                     # 라우터 + 공통 유틸
│  ├─ js/filter.js                  # ① 종목 필터: 통과 종목 테이블, 정렬
│  ├─ js/analysis.js                # ② 심층 분석: TradingView 위젯 + 지표 + 포지션 사이징
│  ├─ js/journal.js                 # ③ 매매 기록: localStorage (추후 Firebase 예정)
│  ├─ js/stats.js                   # ④ 통계: 승률/손익비/기대값/R-multiple
│  └─ data/results.json             # 스크리너 출력 (웹앱 데이터 소스)
└─ prototype/                       # 초기 검증용 스크립트
```

## 스크리닝 로직

**트렌드 템플릿 (8조건 전부 통과해야 함):**
현재가 > 150·200일선 / 150 > 200일선 / 200일선 1개월+ 상승 / 50일선 > 150·200일선 /
현재가 > 50일선 / 52주 저점 +30% 이상 / 52주 고점 −25% 이내 / RS 순위 ≥ 70

**유니버스 (시장별, `python screener/screener.py us|kr`):**
- 미국: NASDAQ Trader 공식 심볼 디렉토리 (NASDAQ+NYSE/AMEX 보통주 ~5,100종목), 필터: $10+ / 50일 평균 거래량 10만 주+
- 한국: KRX 상장목록(FinanceDataReader) KOSPI·KOSDAQ 보통주 (~2,300종목, 우선주·스팩·리츠 제외), 필터: 1,000원+ / 5만 주+.
  가격은 yfinance(.KS/.KQ), 벤치마크는 KOSPI(^KS11). 출력: results_kr.json + history_kr/

**RS 순위:** IBD 방식 — 최근 3개월 수익률 40% + 이전 3개 분기 각 20% 가중 후 **해당 시장 유니버스 내** 백분위(1~99).

**차트 이력:** 통과 종목(정렬 상위 `TOP_EXPORT`=1000, 사실상 전체)의 5년 이력 JSON을 내보냄 — 이력이 없는 종목은 TradingView 위젯 폴백.
매 실행마다 이력 디렉터리를 정리하지만, 웹앱의 '자체 차트 생성'으로 만든 온디맨드 차트(`meta.ondemand` / `ondemand` 플래그)는 보존한다 — 생성 버튼은 미통과 종목에만 뜨므로 지우면 사용자가 만든 차트가 매 실행마다 사라진다.

**VCP 다단계 수축 감지 (0~100점, screener/vcp.py):**
ZigZag(ATR 적응형 임계값)로 스윙 고점/저점을 추출해 베이스 내 수축(고점→저점) 목록을 만들고,
'수축 폭이 단계적으로 감소'(다음 ≤ 직전×0.85)하는 트레일링 시퀀스를 VCP로 스코어링.
- 수축 구조 40점 (2회 24 / 3회 36 / 4회+ 40)
- 마지막 수축 타이트함 20점 (≤5% 만점)
- 수축별 거래량 감소 10점 + 드라이업 15점 (5일 < 50일평균×0.65)
- 피봇 근접도 15점 (피봇 5% 이내 만점, 이미 돌파 시 5점)

책 사례(NFLX 2009-10-23, 그림 7-5)로 검증: 수축 -14.4%→-8.1%→-6.6% 감지, 피봇이 실제 돌파 지점과 일치.
심층 분석 차트에 수축 지그재그·T라벨·피봇/손절 라인이 오버레이되고, 피봇 원클릭으로 포지션 계산기 입력.
자동 매수 신호가 아닌 후보 선별용 — 최종 판단은 차트로.

## 로컬 실행

```bash
pip install -r screener/requirements.txt
python screener/screener.py        # docs/data/results.json 생성
python -m http.server 8000 -d docs # http://localhost:8000
```

## 로직 검증 (백테스트)

워크플로가 매일 `results.json`을 커밋하므로, git 이력 자체가 '그 날의 추천
목록' 시계열이다. 과거 시점의 지표를 다시 계산하지 않고 당시 커밋된 파일을
그대로 읽으므로 룩어헤드가 없다. 스냅샷이 쌓이는 만큼 검증 기간도 늘어난다.

```bash
python screener/backtest.py                 # 미국, 최근 30일
python screener/backtest.py kr --days 90
python screener/backtest.py us --rule last  # 사후 규칙(낙관적 상한)
python screener/backtest.py us --no-prices  # 네트워크 없이(스냅샷 종가만)
python screener/backtest.py us --top 20     # 오늘의 추천 상위 20
python screener/test_backtest.py            # 단위 테스트 (네트워크 불필요)
```

**청산 규칙이 결과를 지배한다.** `gap`(기본)은 리스트에서 처음 빠진 날 매도로
실제 운용 가능한 규칙이고, `last`는 마지막 등장일 매도다. '마지막'인지는 나중에야
아는 사실이므로 `last`는 사후 정보가 섞인 낙관적 상한이다. 빠졌다 다시 들어오는
종목이 많아(최근 1개월 미국 147/367) 두 규칙의 차이가 크다.

**읽을 때 주의할 것:**
- 평균은 한 종목이 지배할 수 있다. 평균과 중앙값이 벌어지면 중앙값을 본다.
- 시나리오 B(끝까지 남은 종목)는 성과가 아니라 **생존 편향의 크기**다.
- 수수료·슬리피지·배당·세금 미반영, 동일 비중, 종가 단일 가격.
- 한 달은 전략 검증에 짧다. 상승·하락 국면이 모두 든 구간이어야 의미가 있다.

## Firebase 연동 (매매 기록 기기 간 동기화, 선택)

기본은 브라우저 localStorage. Google 로그인 동기화를 켜려면:

1. [Firebase 콘솔](https://console.firebase.google.com) → 프로젝트 추가 (Analytics 불필요)
2. **빌드 → Authentication → 시작하기 → Google** 사용 설정
   - Settings → 승인된 도메인에 `hsk81123-bot.github.io` 추가
3. **빌드 → Firestore Database → 데이터베이스 만들기** (프로덕션 모드)
   - 규칙 탭에 [firestore.rules](firestore.rules) 내용 붙여넣고 게시
4. **프로젝트 설정(⚙) → 내 앱 → 웹 앱 추가** → `firebaseConfig` 복사
5. [docs/js/firebase-config.js](docs/js/firebase-config.js)의 `window.FIREBASE_CONFIG = null;`을 받은 설정으로 교체

로그인하면 사이드바에 계정이 표시되고, 로컬에 쌓인 기록은 자동으로 클라우드에 병합된다.
데이터는 `users/{uid}/trades/`(매매기록)와 `users/{uid}/meta/watchlist`(관심종목)에 저장된다.

**접근 제한:** `firebase-config.js`의 `ALLOWED_EMAILS`에 있는 Google 계정만 앱에 입장 가능
(UI 게이트 + firestore.rules의 이메일 검증 이중 방어). 정적 호스팅 특성상 스크리닝 결과
JSON 자체는 공개 접근 가능하지만 개인 데이터는 Firestore 규칙이 서버에서 보호한다.

## 토스증권 체결내역 연동 (선택, 로컬 실행)

토스 Open API는 **허용 IP 등록제**라서 본인 PC에서 실행한다 (클라우드 불가).

1. 토스증권 PC 웹(WTS) → 설정 → Open API: `client_id`/`client_secret` 발급,
   **허용 IP 관리에 본인 공인 IP 등록** (IP가 바뀌면 갱신 필요)
2. [sync/config.example.json](sync/config.example.json)을 `sync/config.local.json`으로 복사해 값 입력
3. Firebase 콘솔 → 프로젝트 설정 → 서비스 계정 → **새 비공개 키 생성** →
   JSON을 `sync/serviceAccount.json`으로 저장 (둘 다 .gitignore에 포함됨)
4. 실행:
   ```bash
   pip install -r sync/requirements.txt
   python sync/toss_sync.py --dry-run   # 먼저 확인
   python sync/toss_sync.py             # Firestore 동기화
   ```

체결내역이 매매기록(`users/{uid}/trades`)에 `toss-{주문ID}` 문서로 추가된다.
이미 있는 문서는 건너뛰므로 앱에서 보완 입력한 손절가·셋업 유형은 보존된다.
조회 전용 — 주문 실행 API는 사용하지 않는다.

## 배포

GitHub 저장소 Settings → Pages → Source: `main` 브랜치 `/docs` 폴더.
스크리너는 GitHub Actions가 평일마다 자동 실행 (수동 실행: Actions 탭 → Daily Screener → Run workflow).

**부분 실패 처리:** 미국·한국 스크리닝은 서로 독립적으로 돌고, 성공한 시장의 결과만 커밋된다
(한쪽이 외부 데이터 소스 장애로 죽어도 다른 쪽 데이터는 살아남는다). 둘 다 실패할 때만 워크플로가
빨간불이 되고, 한쪽만 실패하면 경고 주석으로 남는다. 한국 유니버스(KRX 상장목록)는 조회가 실패하면
3회 재시도 후 직전 실행의 `tickers_kr.json`을 재사용한다.

## 로드맵

- [x] 유니버스 전 종목 확장 (NASDAQ/NYSE 공식 리스트)
- [x] VCP 다단계 수축 감지 (스윙 고점/저점 기반)
- [ ] 매매 기록 Firebase 연동 (기기 간 동기화)
- [ ] 통계: 셋업별 분석, R-multiple 분포, 손익 곡선
- [ ] 증권사 API 연동 (자동 기록)
