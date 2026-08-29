# -*- coding: utf-8 -*-
"""VCP(변동성 수축 패턴) 다단계 수축 감지.

1) ZigZag(ATR 적응형 임계값)로 스윙 고점/저점 추출
2) 최근 베이스 구간의 수축(스윙고점→스윙저점) 목록 생성
3) '수축 폭이 단계적으로 감소'하는 트레일링 시퀀스를 찾아 0~100 스코어링

점수 배분: 수축 구조(40) + 마지막 수축 타이트함(20)
         + 거래량 감소(10) + 드라이업(15) + 피봇 근접도(15)
"""
import numpy as np
import pandas as pd

ATR_MULT = 2.0        # 스윙 반전 임계값 = ATR% × 배수
MIN_REV_PCT = 0.04    # 반전 임계값 하한 (4%)
BASE_LOOKBACK = 130   # 베이스 탐색 구간 (약 26주)
DECAY_TOL = 0.85      # 다음 수축 ≤ 직전 × 0.85 면 '감소'로 인정
MAX_DEPTH = 0.35      # 이보다 깊은 수축은 손상된 베이스로 보고 체인 리셋
MAX_CONTRACTIONS = 6  # 미너비니: 보통 2~4회, 최대 6회
DRYUP_RATIO = 0.65    # 드라이업: 최근 5일 평균 거래량 < 50일 평균 × 0.65


def _atr_pct(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return (tr.rolling(n).mean() / c).bfill()


def _zigzag(df: pd.DataFrame) -> list[dict]:
    """스윙 포인트 목록 [{'i', 'price', 'kind'('H'|'L')}] (시간순)."""
    h, l, c = df["High"].values, df["Low"].values, df["Close"].values
    ap = _atr_pct(df).values
    n = len(df)
    if n < 20:
        return []
    swings = []
    dirn = 1 if c[min(19, n - 1)] >= c[0] else -1
    ext_p = h[0] if dirn == 1 else l[0]
    ext_i = 0
    for i in range(1, n):
        thr = max(ap[i] * ATR_MULT, MIN_REV_PCT)
        if dirn == 1:
            if h[i] >= ext_p:
                ext_p, ext_i = h[i], i
            elif ext_p > 0 and (ext_p - l[i]) / ext_p >= thr:
                swings.append({"i": ext_i, "price": float(ext_p), "kind": "H"})
                dirn, ext_p, ext_i = -1, l[i], i
        else:
            if l[i] <= ext_p:
                ext_p, ext_i = l[i], i
            elif ext_p > 0 and (h[i] - ext_p) / ext_p >= thr:
                swings.append({"i": ext_i, "price": float(ext_p), "kind": "L"})
                dirn, ext_p, ext_i = 1, h[i], i
    return swings


def detect_vcp(df: pd.DataFrame) -> dict:
    """일봉 OHLCV 전체 이력(1년 이상 권장)에서 VCP 구조 감지."""
    vol50_full = df["Volume"].rolling(50).mean()
    sub = df.iloc[-min(len(df), BASE_LOOKBACK):]
    vol50 = vol50_full.reindex(sub.index).bfill()
    low, high, vol = sub["Low"].values, sub["High"].values, sub["Volume"].values
    close = float(sub["Close"].iloc[-1])

    swings = _zigzag(sub)

    # ---- 수축 목록: (스윙고점 → 다음 스윙저점), 마지막은 미확정 저점 허용 ----
    all_contr = []
    for k, s in enumerate(swings):
        if s["kind"] != "H":
            continue
        if k + 1 < len(swings) and swings[k + 1]["kind"] == "L":
            lo_i, lo_p = swings[k + 1]["i"], swings[k + 1]["price"]
        else:  # 진행 중인 마지막 수축: 고점 이후의 최저가
            seg = low[s["i"]:]
            lo_i = int(seg.argmin()) + s["i"]
            lo_p = float(seg.min())
        depth = (s["price"] - lo_p) / s["price"]
        v50 = float(vol50.iloc[lo_i]) or 1.0
        vr = float(vol[s["i"]: lo_i + 1].mean() / v50) if lo_i >= s["i"] else 1.0
        all_contr.append({
            "hi_i": s["i"], "lo_i": lo_i,
            "hi_date": sub.index[s["i"]].strftime("%Y-%m-%d"),
            "lo_date": sub.index[lo_i].strftime("%Y-%m-%d"),
            "high": round(s["price"], 2), "low": round(lo_p, 2),
            "depth_pct": round(depth * 100, 1),
            "vol_ratio": round(vr, 2),
        })

    # ---- 트레일링 감소 체인: 마지막 수축을 포함하는 '단계적 감소' 시퀀스 ----
    chain = []
    for c in all_contr:
        d = c["depth_pct"] / 100
        if d > MAX_DEPTH:
            chain = []  # 깊은 조정 → 베이스 손상, 처음부터
            continue
        if chain and d <= (chain[-1]["depth_pct"] / 100) * DECAY_TOL:
            chain.append(c)
        else:
            chain = [c]
    chain = chain[-MAX_CONTRACTIONS:]

    # ---- 스코어링 ----
    count = len(chain)
    structure = {0: 0, 1: 8, 2: 24, 3: 36}.get(count, 40)

    tight = 0
    final_depth = None
    if chain:
        final_depth = chain[-1]["depth_pct"]
        fd = final_depth / 100
        tight = 20 if fd <= 0.05 else 12 if fd <= 0.10 else 5 if fd <= 0.15 else 0

    vol_declining = bool(
        count >= 2 and chain[-1]["vol_ratio"] <= chain[0]["vol_ratio"] * 0.8)
    vol_pts = 10 if vol_declining else 0

    v50_now = float(vol50.iloc[-1]) or 1.0
    dryup = bool(vol[-5:].mean() < v50_now * DRYUP_RATIO)
    dry_pts = 15 if dryup else 0

    pivot = chain[-1]["high"] if chain else None
    stop = chain[-1]["low"] if chain else None
    if pivot:
        if close > pivot:
            prox = 5  # 이미 돌파 (늦은 진입)
        else:
            gap = (pivot - close) / pivot
            prox = 15 if gap <= 0.05 else 8 if gap <= 0.10 else 0
    else:
        prox = 0

    return {
        "score": int(structure + tight + vol_pts + dry_pts + prox),
        "count": count,
        "contractions": [
            {k: c[k] for k in ("hi_date", "lo_date", "high", "low",
                               "depth_pct", "vol_ratio")}
            for c in chain
        ],
        "pivot": pivot,
        "stop": stop,
        "final_depth_pct": final_depth,
        "vol_declining": vol_declining,
        "dryup": dryup,
        "base_days": int(len(sub) - chain[0]["hi_i"]) if chain else 0,
    }
