# -*- coding: utf-8 -*-
"""시점 기준 시가총액 — 시총 가중 포트폴리오와 'SPY 와 닮은 기준선'을 만들기 위해.

시총(d) = 발행주식수 × 그 날 실제 거래가.
  발행주식수  SEC 표지(dei) 값 중 공시일 < d 인 가장 최근 것 (pit_fund.shares_at). 기준일 이후
              d 까지 있었던 분할 배수를 곱한다.
  실제 거래가  캐시 가격은 분할·배당이 모두 조정된 값이다. 분할은 이후 분할 배수를 곱해
              되돌리고(history.split_factor_after), 배당 조정은 Dividends 열로 정확히 되돌린다.
              안 되돌리면 고배당주의 옛 시총이 누적 배당만큼(수십 %) 작게 나와 시총 가중이
              성장주 쪽으로 기운다.

배당 조정 되돌리기: 조정 종가 adj, 이후 배당 D_k (ex-date k) 일 때
  f(k-1) = f(k) / (1 + f(k) · D_k / adj(k-1)),  f(마지막) = 1,  실제 종가 = adj / f.
(Yahoo 의 조정 방식 adj = 실제 × Π(1 − D/전날 실제 종가) 의 역산이다. 테스트가 순방향으로
조정한 값에서 정확히 복원되는지 확인한다.)

검증: 풀 시총 가중 수익이 SPY 와 얼마나 닮았나 (baseline.py 가 출력).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import history as H
import pit_fund as PF


def unadjust_close(adj: np.ndarray, div: np.ndarray) -> np.ndarray:
    """배당 조정을 되돌린 종가 (분할은 조정된 상태 그대로). div[k] = k 일이 ex-date 인 배당."""
    f = np.ones(len(adj))
    cur = 1.0
    last = len(adj)
    for k in np.nonzero(div > 0)[0][::-1]:
        if k == 0:
            continue
        # k 일 이후 구간의 인자는 cur, k-1 일부터는 새 인자
        f[k:last] = cur
        cur = cur / (1.0 + cur * float(div[k]) / adj[k - 1])
        last = k
    f[:last] = cur
    return adj / f


OUTLIER_X = 3.0       # 이웃 공시 중앙값의 이 배수를 넘거나 1/배수 미만이면 이상치


def drop_share_outliers(rows: list[list], ratio_to_now: list[float], x: float = OUTLIER_X
                        ) -> tuple[list[list], int]:
    """공시 순서대로의 발행주식수에서 '이웃 공시와 동떨어진 한 점'을 뺀다.

    ratio_to_now[i] = 그 공시 기준일 이후 분할 배수의 곱 — 곱하면 지금 주식수 기준이라
    분할로 인한 정당한 점프는 이상치로 오인하지 않는다. 이웃은 앞뒤 2개씩(자기 제외)의
    중앙값. 단위 오류(천 단위/원 단위)나 오타 같은 고립된 점만 거른다 — 합병으로 서서히
    늘거나 줄어드는 추세는 그대로 둔다. 점이 4개 미만이면 손대지 않는다.
    """
    n = len(rows)
    if n < 4:
        return rows, 0
    adj = [r[2] * q for r, q in zip(rows, ratio_to_now)]
    keep = []
    for i in range(n):
        nb = sorted(adj[j] for j in range(max(0, i - 2), min(n, i + 3)) if j != i)
        med = nb[(len(nb) - 1) // 2]          # 아래쪽 중앙값 — 이웃 한쪽이 이상치여도 안 끌려간다
        keep.append(not (adj[i] > x * med or adj[i] * x < med))
    return [r for r, k in zip(rows, keep) if k], keep.count(False)


class Caps:
    """종목별 (날짜, 실제 거래가, 누적 분할배수)를 한 번 계산해 두고 시총을 조회한다."""

    def __init__(self, px: dict[str, pd.DataFrame], shares: dict[str, list[list]],
                 cik_of: dict[str, str] | None = None, backfill_days: int = 0):
        self.px, self.shares = px, shares
        self.cik_of = cik_of or {}
        self.backfill_days = backfill_days
        self.backfilled = 0
        self.dropped = 0          # 이상치로 뺀 발행주식수 점의 수
        self.clean: dict[str, list[list]] = {}
        self.memo: dict[str, tuple | None] = {}
        self.undone = 0           # 배당 조정을 되돌린 종목 수
        self.adjusted_only = 0    # Dividends 열이 없어 조정 가격 그대로 쓴 종목 수

    def _series(self, t: str):
        if t not in self.memo:
            df = self.px.get(t)
            if df is None or t not in self.shares:
                self.memo[t] = None
            else:
                c = df["Close"].astype("float64").to_numpy()
                if "Dividends" in df.columns:
                    c = unadjust_close(c, df["Dividends"].astype("float64").to_numpy())
                    self.undone += 1
                else:
                    self.adjusted_only += 1
                after = H.split_factor_after(df["Splits"] if "Splits" in df.columns
                                             else None, df.index).to_numpy()
                r = (df["Splits"].astype("float64").to_numpy()
                     if "Splits" in df.columns else np.zeros(len(df)))
                cum = np.cumprod(np.where(r > 0, r, 1.0))
                dates = np.array([d.strftime("%Y-%m-%d") for d in df.index])
                self.memo[t] = (dates, c * after, cum)
                rows = self.shares[t]
                q = []
                for r in rows:
                    j = int(np.searchsorted(dates, r[1], side="right")) - 1
                    q.append(float(cum[-1] / cum[j]) if j >= 0 else float(cum[-1]))
                self.clean[t], k = drop_share_outliers(rows, q)
                self.dropped += k
        return self.memo[t]

    def cap(self, t: str, d: str) -> float | None:
        s = self._series(t)
        if s is None:
            return None
        dates, price, cum = s
        sh = PF.shares_at(self.clean.get(t, self.shares[t]), d,
                          backfill_days=self.backfill_days)
        if sh is None:
            return None
        v, end = sh
        if end > d:
            self.backfilled += 1
        i = int(np.searchsorted(dates, d, side="right")) - 1       # d 이하 마지막 거래일
        if i < 0 or not price[i] > 0:
            return None
        j = int(np.searchsorted(dates, end, side="right")) - 1     # 기준일 이하 마지막 거래일
        ratio = cum[i] / cum[j] if j >= 0 else cum[i]              # (기준일, d] 의 분할배수
        return float(v * ratio * price[i])

    def debug(self, t: str, d: str) -> dict:
        """엉뚱하게 큰 비중이 나올 때 원인을 보려고: 쓴 주식수·기준일·가격·분할 배수."""
        s = self._series(t)
        if s is None:
            return {}
        dates, price, cum = s
        sh = PF.shares_at(self.clean.get(t, self.shares[t]), d,
                          backfill_days=self.backfill_days)
        i = int(np.searchsorted(dates, d, side="right")) - 1
        df = self.px[t]
        return {"ticker": t, "date": d, "shares": sh, "price_now": float(price[i]),
                "adj_close": float(df["Close"].iloc[i]),
                "splits": [(str(x.date()), float(v)) for x, v in
                           df["Splits"][df["Splits"] > 0].items()][-4:]
                if "Splits" in df.columns else [],
                "n_div": int((df["Dividends"] > 0).sum()) if "Dividends" in df.columns else 0,
                "first_rows": self.shares[t][:3], "cap": self.cap(t, d)}

    def price(self, t: str, d: str) -> float | None:
        """d 이하 마지막 거래일의 실제 거래가 (분할·배당 조정을 되돌린 값)."""
        s = self._series(t) if t in self.shares else None
        if s is None:
            df = self.px.get(t)
            if df is None:
                return None
            self.shares.setdefault(t, [])
            s = self._series(t)
        dates, price, _ = s
        i = int(np.searchsorted(dates, d, side="right")) - 1
        return float(price[i]) if i >= 0 else None

    def caps(self, names, d: str) -> dict[str, float]:
        """종목별 시총. 같은 회사(CIK)의 주식 종류가 여럿(GOOGL·GOOG 등)이면 각 종류가
        '전체 주식수 × 자기 가격' 이 되어 두 번 세어지므로, 풀에 있는 종류 수로 나눈다."""
        out = {}
        for t in names:
            c = self.cap(t, d)
            if c:
                out[t] = c
        groups: dict[str, list[str]] = {}
        for t in out:
            cik = self.cik_of.get(t)
            if cik:
                groups.setdefault(cik, []).append(t)
        for ts in groups.values():
            if len(ts) > 1:
                for t in ts:
                    out[t] /= len(ts)
        return out
