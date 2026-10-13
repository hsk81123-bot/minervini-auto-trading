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
│  ├─ papertrade.py                  # 모의매매: 손절·익절·추격손절 규칙 적용
│  ├─ validate.py                    # 엄격 검증: 코호트·무작위 대조군·파라미터 격자
│  ├─ robust.py                      # 1단계 견고성: 실행 가능한 진입가·종목 단위 신뢰구간
│  ├─ history.py                     # 3단계: 과거 추천 리스트 여러 해 재구성 + 검증
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

**데이터 신선도:** 장 마감 직후에는 yfinance 최신 일봉이 아직 빈 종목이 많다.
기준 거래일(벤치마크 지수의 마지막 일봉) 일봉을 가진 종목이 95% 미만이면 늦은
종목만 10분 뒤 다시 받고(최대 2회), 그래도 모자라면 다수가 실제로 가진 날짜를
`data_date`로 쓰고 `freshness.stale = true` 로 표시한다. 예전엔 종목 마지막 날짜의
최댓값을 썼는데, 2026-09~10월 28일 중 약 15일이 그 때문에 전날 데이터에 오늘
날짜가 붙어 있었다(`history.py fidelity` 로 확인).

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

## 매도 규칙과 모의매매

```bash
python screener/papertrade.py                            # -8% 손절 / +25% 익절
python screener/papertrade.py --stop -8 --trail -15 --no-target --breakeven 10
python screener/papertrade.py --time-stop 20 --top 5 --capital 10000000
python screener/test_papertrade.py
```

**성과는 수익률이 아니라 R-배수로 본다.** 1R = 진입가에서 최초 손절선까지의
거리다. 손절을 -5%로 걸든 -8%로 걸든 R로 환산하면 규칙끼리 비교할 수 있고,
"손익비 3:1"이 설계값이 아니라 실측값이 된다.

**전략의 생존 조건은 승률이 아니라 기대값 E[R]이다.** 승률 25%에 손익비 3:1이면
기대값은 0R이다. 승률 40%·손익비 3:1이면 +0.6R. 승률만 보면 거꾸로 읽는다.

규칙 구성:

| 옵션 | 뜻 | 왜 |
|---|---|---|
| `--stop` | 최초 손절 (%, 음수) | 1R의 정의. 갭하락 시 시가 체결이므로 실제 손실은 이보다 커질 수 있다 |
| `--target` | 고정 익절 (%) | 설계 손익비를 고정한다. 대신 승자를 자른다 |
| `--no-target` | 고정 익절 없음 | 미너비니식 — 상승을 열어 둔다 |
| `--trail` | 추격손절 (%, 최고가 대비) | 상승을 따라가며 이익을 지킨다. 손절선은 **전일까지의** 고가로만 올린다 |
| `--breakeven` | 본전손절 발동 (%) | 목표 도달 후 손절선을 진입가로 → 최악이 0R |
| `--time-stop` | 시간청산 (일) | 무반응 종목에 자금을 묶어두지 않는다 |

MAE/MFE(보유 중 최대 역행·순행)를 함께 낸다. 손절선이 노이즈에 걸리는지,
익절선이 상승을 자르는지는 이 두 숫자로만 판단할 수 있다.

거래비용을 항상 반영한다 (편도 수수료 5bp + 한국 매도 증권거래세 18bp).
0으로 두면 회전율이 높은 규칙이 과대평가된다. 슬리피지는 미반영이다.

## 규칙 엄격 검증 방침

`papertrade.py` 한 번 실행은 **진입일 하나**의 결과다. 진입일을 바꾸면 숫자가
크게 움직이므로, 좋은 날을 골라 보고하면 아무 규칙이나 좋아 보인다.
`validate.py`는 고를 수 없게 만든다.

```bash
python screener/validate.py us --stop -8 --trail -15 --no-target --grid
python screener/validate.py kr --days 90 --top 5 --json val.json
python screener/test_validate.py
```

1. **진입일을 고르지 않는다 (코호트·워크포워드).** 스냅샷 전체를 차례로
   진입일로 써서 같은 규칙을 반복한다. 보고 수치는 모든 코호트의 합이고,
   코호트별 기대값의 표준편차를 함께 낸다. 특정 날짜만 좋았다면 분산에서 드러난다.
2. **무작위 대조군을 둔다.** 같은 날 '통과 종목에서 무작위 N개'를 뽑아 같은
   규칙으로 굴린다. 통과 필터의 기여는 양쪽에 똑같이 들어가므로, 차이가 곧
   RS→VCP 순위가 더한 값이다. 차이가 0 이하면 순위는 값을 더하지 않았다.
   시드를 고정해 재현한다.
3. **파라미터 하나에 기대지 않는다 (민감도 격자).** 손절 3종 × 익절 4종
   (고정 익절 없음 포함)을 훑어 기대값 지도를 만든다. 한 칸만 좋고 이웃이
   나쁘면 과최적화다. 이웃까지 양수인 영역을 찾는 것이 목적이다.
4. **표본이 적으면 판정하지 않는다.** 실현 30거래 미만이면 수치는 내되
   "판정 보류"로 표시한다. 기대값이 +5R이어도 10거래면 통계가 아니다.
5. **벤치마크와 비용을 항상 함께 둔다.** 같은 기간 지수 수익률을 옆에 적는다.
6. **미래를 쓰지 않는다.** 진입 종목은 그 날 커밋된 스냅샷에서만, 손절선은
   전일까지의 고가로만. `backtest.py`·`papertrade.py`·`validate.py`가 같은
   스냅샷 로직을 공유한다.
7. **실현과 미실현을 둘 다 낸다.** 어느 한쪽만 보면 규칙을 잘못 고른다.
   "실현만"은 미실현 이익이 섞이지 않지만 **고정 익절이 없는 규칙에 불리하다** —
   승자는 창 끝까지 열려 있고 닫히는 건 손절뿐이라 실현 표본이 손실만 모인다
   (추격손절 규칙의 실현 승률이 3.8%까지 떨어진 것이 그 때문이었다).
   "전량 청산"은 창 끝 종가로 전부 닫아 규칙끼리 같은 조건에서 비교한다. 대신
   미실현 평가가 들어간다. **규칙 간 비교와 파라미터 격자는 전량 청산 기준,
   확정 성과는 실현 기준**으로 읽는다.

**진입 타이밍 — 피봇 괴리 필터.** 매도 규칙만 검증하고 진입은 "그 날 종가에
무조건 매수"로 두면, RS 상위 종목이 이미 VCP 피봇에서 한참 위로 달아난
지점에서 사게 된다. `--ext-grid`는 피봇 대비 괴리 구간별로 "구간 안에서 RS
상위 N"을 굴려 현재 규칙·같은 구간 무작위와 비교하고, 현재 규칙이 실제로 산
거래를 진입 시 괴리별로 쪼개 연장된 진입이 정말 나빴는지 직접 잰다.

```bash
python screener/validate.py us --no-target --trail -15 --ext-grid
python screener/validate.py us --ext-min 0 --ext-max 5    # 피봇 0~+5%만 진입
python screener/validate.py us --ext-caps 15,20,25,35,50   # 피봇 +X% 초과 제외, 상한 민감도
```

상한 필터(`--ext-caps`)는 피봇 없는 종목까지 함께 빼므로 "피봇 있는 종목만" 행이
대조군으로 붙는다. 상한값의 효과는 그 행과 비교해서 읽는다.

### 1단계 견고성 점검 (미국)

`validate.py`가 "규칙이 좋아 보이는가"를 잰다면 `robust.py`는 "그 숫자를 믿어도
되는가"를 잰다. 판정 기준 5개는 결과를 보기 전에 코드(`CHECKS`)에 고정했다.

```bash
python screener/robust.py --stop -8 --no-target --trail -15
python screener/test_robust.py
```

- **진입 시점.** 스크리너는 장 마감 뒤에 돈다. 신호를 만든 종가에는 살 수 없으므로
  모든 시뮬레이션의 기본 진입은 **신호 다음 거래일 시가**다(`--entry close`는
  비교용으로만 남긴다).
- **종목 단위 신뢰구간.** 코호트 19개 × 5종목이 95거래로 보여도 같은 종목이
  반복된다. 거래가 아니라 종목을 단위로 다시 뽑는 부트스트랩으로 신뢰구간을 낸다.
- **기여 상위 종목 제외.** R을 가장 많이 번 종목부터 빼고 다시 잰다.
- **무작위 대조군 200회.** 같은 날 같은 통과 종목 풀에서 무작위로 뽑기를 200번
  반복한 분포에서 로직이 95번째 백분위 이상이어야 한다.
- **RS 동점 처리.** RS는 1~99 정수라 상위 N이 사실상 동점 안에서 VCP 점수로
  정해진다. 반올림 전 원점수(스크리너와 같은 공식, 그 날까지의 종가로만) 순서,
  동점 안 무작위 200회와 비교한다.

### 3단계 과거 재구성 (미국)

git 스냅샷은 2026-08-30 부터라 6주·고유 18종목뿐이었고, 1단계 점검에서 그
표본으로는 실력과 운을 구분할 수 없었다. `history.py`는 과거 가격으로 "그 주에
스크리너를 돌렸다면 나왔을 리스트"를 주 단위로 다시 만든다(2019년~, 2020년 급락·
2022년 하락장 포함). 워크플로 `history.yml`이 Actions에서 돌린다.

```bash
python screener/history.py download     # 유니버스 일봉 7년 → .cache/ (커밋 안 함)
python screener/history.py rebuild      # 주간 리스트 재구성
python screener/history.py fidelity     # 재구성 vs 실제 커밋 스냅샷
python screener/history.py validate --stop -8 --no-target --trail -15
python screener/test_history.py
```

- **스크리너와 같은 계산.** 임계값은 `screener.CFG`, VCP는 `vcp.detect_vcp`를 그대로
  쓴다. 벡터화한 지표가 `screener.trend_template`·`rs_raw_score`와 통과 종목·RS를
  정확히 같게 내는지 합성 종목으로 테스트한다(분할 전·당일·후 포함).
- **미래 분할 되돌림.** yfinance 수정주가는 그 뒤 분할까지 소급 반영해 과거 가격이
  실제보다 낮게 나온다(예: 분할 전 $24 종목이 4:1 소급으로 $6). 가격·거래량 필터에는
  그 날 이후 분할을 되돌린 값을 쓴다.
- **충실도 점검.** 실제 스냅샷이 있는 날도 재구성해 통과 종목 겹침(자카드)·상위 5
  일치를 잰다. 재구성이 실제와 다르면 과거 결과도 믿을 수 없다.
- **연도별·시장 국면.** 해마다 기대값과 무작위 중앙값, S&P 500 수익률을 나란히 내고,
  "S&P 500 이 200일선 아래면 신규 매수 안 함" 필터를 같은 데이터로 비교한다.
- **생존 편향(고칠 수 없음).** 유니버스가 지금 상장된 종목뿐이라 그 사이 상장폐지된
  종목이 빠진다. 결과는 실제보다 좋게 기운다고 보고 읽는다.

판정 기준(결과 전에 고정): 1단계 5개 + 충실도(자카드 ≥ 0.90, 상위 5 평균 ≥ 4종목)
+ 기대값 플러스인 해가 절반 초과. 시장 국면 필터는 기대값과 최악의 해를 모두
개선하고 두 매도 규칙에서 같은 방향일 때만 채택한다.

다섯 스크립트의 단위 테스트(백테스트 17 · 모의매매 35 · 검증 23 · 견고성 10 ·
과거 재구성 10)는 모두 네트워크 없이 돌고, 합성 데이터로 손 검산한 값만 쓴다.

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
