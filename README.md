# Minervini Auto Trading

마크 미너비니(SEPA) 방법론 기반 주식 스크리닝 + 매매 관리 웹앱.

## 구조

```
├─ .github/workflows/screener.yml   # 평일 22:00 UTC(장 마감 후) 자동 스크리닝 → results.json 커밋
├─ screener/
│  ├─ screener.py                   # 트렌드 템플릿 8조건 + RS 랭킹 + VCP 후보 점수
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

**차트 이력:** 정렬 상위 150종목만 5년 이력 JSON 내보냄 (저장소 크기 관리) — 나머지는 TradingView 위젯 폴백.

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

## 배포

GitHub 저장소 Settings → Pages → Source: `main` 브랜치 `/docs` 폴더.
스크리너는 GitHub Actions가 평일마다 자동 실행 (수동 실행: Actions 탭 → Daily Screener → Run workflow).

## 로드맵

- [x] 유니버스 전 종목 확장 (NASDAQ/NYSE 공식 리스트)
- [x] VCP 다단계 수축 감지 (스윙 고점/저점 기반)
- [ ] 매매 기록 Firebase 연동 (기기 간 동기화)
- [ ] 통계: 셋업별 분석, R-multiple 분포, 손익 곡선
- [ ] 증권사 API 연동 (자동 기록)
