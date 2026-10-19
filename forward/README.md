# forward/ — M9 실시간 모의 기록

PLAN.md §15 에 사전 등록된 실시간 모의의 산출물이다. **직접 고치지 않는다.**

| 파일 | 내용 |
|---|---|
| `spec.json` | 시작일(T0)·규칙·비용·골든 해시·코드 커밋 (init 때 한 번 생성, 이후 불변) |
| `log.csv` | 매 거래일 한 행, 추가만 한다 (관측한 입력·신호·적용 비중·일 수익·NAV) |
| `SUMMARY.md` | 오늘의 신호, 성과, 운영 점검, 체크포인트 (update 때마다 다시 만든다) |
| `INTERVENTIONS.md` | 규칙과 다르게 한 일이 있을 때만 사유와 함께 기록 (없으면 파일 없음) |

시작: main 머지 뒤 Actions → "M9 forward paper" → Run workflow → action=`init`.
이후 평일 22:30 UTC 에 자동으로 `update` 가 돈다. 사양·코드가 바뀌면 `update` 가 거부한다(새 T0 로 새로 시작).
