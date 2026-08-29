# -*- coding: utf-8 -*-
"""새 VCP 점수 상위 종목 확인용."""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")
d = json.load(open("docs/data/results.json", encoding="utf-8"))
rs = sorted(d["results"], key=lambda r: -r["vcp"]["score"])[:12]
for r in rs:
    v = r["vcp"]
    depths = [c["depth_pct"] for c in v["contractions"]]
    print(f"{r['ticker']:<6} RS={r['rs_rank']:<3} VCP={v['score']:<4} "
          f"count={v['count']} depths={depths} pivot={v['pivot']} "
          f"dryup={v['dryup']}")
