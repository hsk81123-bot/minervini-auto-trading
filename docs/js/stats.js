/* 4. 통계 페이지 — 뼈대 + 기본 지표 (평균단가 기반 실현손익) */
App.register("stats", async (page) => {
  const items = (await Journal.load()).sort((a, b) => a.date.localeCompare(b.date));

  // 종목별 평균단가 추적으로 실현손익/R-multiple 계산 (단순화 버전)
  const pos = {}; // ticker -> {shares, avgCost, stop}
  const closed = []; // {ticker, date, pnl, rMultiple, exitReason}
  for (const it of items) {
    const p = pos[it.ticker] || (pos[it.ticker] = { shares: 0, avgCost: 0, stop: null });
    if (it.side === "BUY") {
      p.avgCost = (p.avgCost * p.shares + it.price * it.shares) / (p.shares + it.shares);
      p.shares += it.shares;
      if (it.stop) p.stop = it.stop;
    } else {
      const sold = Math.min(it.shares, p.shares);
      if (sold > 0) {
        const pnl = (it.price - p.avgCost) * sold;
        const riskPerShare = p.stop ? p.avgCost - p.stop : null;
        closed.push({
          ticker: it.ticker, date: it.date, pnl,
          pnlPct: (it.price / p.avgCost - 1) * 100,
          rMultiple: riskPerShare > 0 ? (it.price - p.avgCost) / riskPerShare : null,
          exitReason: it.exitReason,
        });
        p.shares -= sold;
        if (p.shares === 0) { p.avgCost = 0; p.stop = null; }
      }
    }
  }

  const wins = closed.filter(c => c.pnl > 0);
  const losses = closed.filter(c => c.pnl <= 0);
  const sum = arr => arr.reduce((s, c) => s + c.pnl, 0);
  const avg = arr => arr.length ? sum(arr) / arr.length : 0;
  const winRate = closed.length ? wins.length / closed.length * 100 : null;
  const payoff = losses.length && avg(losses) !== 0 ? avg(wins) / -avg(losses) : null;
  const expectancy = closed.length ? sum(closed) / closed.length : null;
  const openPos = Object.entries(pos).filter(([, p]) => p.shares > 0);

  const tile = (label, value, cls = "") =>
    `<div class="stat-tile"><div class="label">${label}</div><div class="value ${cls}">${value}</div></div>`;

  page.innerHTML = `
    <h1>통계</h1>
    <div class="subtitle">매매 기록 기반 자동 집계 — 매도 시 평균단가 대비 실현손익으로 계산</div>

    <div class="stat-tiles">
      ${tile("청산 트레이드", closed.length)}
      ${tile("승률", winRate == null ? "-" : App.fmt(winRate, 0) + "%", winRate >= 50 ? "pos" : "")}
      ${tile("손익비 (평균익절/평균손절)", payoff == null ? "-" : App.fmt(payoff, 2))}
      ${tile("기대값/트레이드", expectancy == null ? "-" : "$" + App.fmt(expectancy, 0), expectancy > 0 ? "pos" : expectancy < 0 ? "neg" : "")}
      ${tile("총 실현손익", "$" + App.fmt(sum(closed), 0), sum(closed) > 0 ? "pos" : sum(closed) < 0 ? "neg" : "")}
      ${tile("보유 중 포지션", openPos.length)}
    </div>

    <div class="card" style="margin-top:16px">
      <h2 style="margin-top:0">청산 내역 (R-multiple)</h2>
      ${closed.length === 0 ? "<div class='empty'>청산된 트레이드가 없습니다. 매매 기록에 매수·매도를 입력하면 여기에 집계됩니다.</div>" : `
      <table>
        <thead><tr><th>날짜</th><th>종목</th><th>손익</th><th>수익률</th><th>R</th><th>청산 사유</th></tr></thead>
        <tbody>
          ${closed.slice().reverse().map(c => `
            <tr>
              <td>${c.date}</td>
              <td><a class="ticker-link" href="#/analysis?ticker=${c.ticker}">${c.ticker}</a></td>
              <td class="${App.signCls(c.pnl)}">$${App.fmt(c.pnl, 0)}</td>
              <td class="${App.signCls(c.pnl)}">${App.fmt(c.pnlPct, 1)}%</td>
              <td class="${App.signCls(c.rMultiple)}">${c.rMultiple == null ? "-" : App.fmt(c.rMultiple, 1) + "R"}</td>
              <td style="text-align:left">${c.exitReason || "-"}</td>
            </tr>`).join("")}
        </tbody>
      </table>`}
    </div>

    <div class="card">
      <h2 style="margin-top:0">추후 확장 예정</h2>
      <div style="color:var(--muted);font-size:13px;line-height:1.8">
        · 셋업 유형별 승률/기대값 비교 · R-multiple 분포 히스토그램 · 월별 손익 곡선 · 보유기간 분석
      </div>
    </div>`;
});
