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

**RS 순위:** IBD 방식 — 최근 3개월 수익률 40% + 이전 3개 분기 각 20% 가중 후 유니버스 내 백분위(1~99).
현재 유니버스는 S&P 500이므로 "대형주 내 상위 %"임에 유의.

**VCP 후보 점수 (0~3, 휴리스틱):**
- 가격 수축: 최근 10일 등락폭 ≤ 직전 30일 등락폭의 50%
- 거래량 고갈: 10일 평균 거래량 < 50일 평균의 80%
- 고점권: 52주 고점 15% 이내

자동 판정이 아닌 "차트 확인할 후보" 선별용. 최종 판단은 심층 분석 페이지에서 수동 확인.

## 로컬 실행

```bash
pip install -r screener/requirements.txt
python screener/screener.py        # docs/data/results.json 생성
python -m http.server 8000 -d docs # http://localhost:8000
```

## 배포

GitHub 저장소 Settings → Pages → Source: `main` 브랜치 `/docs` 폴더.
스크리너는 GitHub Actions가 평일마다 자동 실행 (수동 실행: Actions 탭 → Daily Screener → Run workflow).

## 로드맵

- [ ] 유니버스 전 종목 확장 (NASDAQ/NYSE 공식 리스트)
- [ ] VCP 다단계 수축 감지 (스윙 고점/저점 기반)
- [ ] 매매 기록 Firebase 연동 (기기 간 동기화)
- [ ] 통계: 셋업별 분석, R-multiple 분포, 손익 곡선
- [ ] 증권사 API 연동 (자동 기록)
