/* 4. 통계 페이지 — 시장별(미국 USD / 한국 KRW) 분리 집계 */
App.register("stats", async (page) => {
  const items = (await Journal.load()).sort((a, b) => a.date.localeCompare(b.date));
  const isKRT = t => /^\d{6}(\.(KS|KQ))?$/.test(t || "");

  // 평균단가 추적으로 실현손익/R-multiple 계산 (단순화 버전)
  function compute(trades) {
    const pos = {};      // ticker -> {shares, avgCost, stop}
    const closed = [];   // {ticker, date, pnl, pnlPct, rMultiple, exitReason}
    for (const it of trades) {
      const p = pos[it.ticker] || (pos[it.ticker] = { shares: 0, avgCost: 0, stop: null });
      if (it.side === "BUY") {
        p.avgCost = (p.avgCost * p.shares + it.price * it.shares) / (p.shares + it.shares);
        p.shares += it.shares;
        if (it.stop) p.stop = it.stop;
      } else {
        const sold = Math.min(it.shares, p.shares);
        if (sold > 0) {
          const riskPerShare = p.stop ? p.avgCost - p.stop : null;
          closed.push({
            ticker: it.ticker, date: it.date,
            pnl: (it.price - p.avgCost) * sold,
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
    return {
      closed,
      winRate: closed.length ? wins.length / closed.length * 100 : null,
      payoff: losses.length && avg(losses) !== 0 ? avg(wins) / -avg(losses) : null,
      expectancy: closed.length ? sum(closed) / closed.length : null,
      totalPnl: sum(closed),
      openCount: Object.values(pos).filter(p => p.shares > 0).length,
    };
  }

  const tile = (label, value, cls = "") =>
    `<div class="stat-tile"><div class="label">${label}</div><div class="value ${cls}">${value}</div></div>`;

  function sectionHTML(label, trades, kr) {
    const s = compute(trades);
    const m = n => App.money(n, kr, 0);
    return `
      <h2>${label} <span style="color:var(--muted);font-weight:400">— 기록 ${trades.length}건</span></h2>
      <div class="stat-tiles">
        ${tile("청산 트레이드", s.closed.length)}
        ${tile("승률", s.winRate == null ? "-" : App.fmt(s.winRate, 0) + "%", s.winRate >= 50 ? "pos" : "")}
        ${tile("손익비", s.payoff == null ? "-" : App.fmt(s.payoff, 2))}
        ${tile("기대값/트레이드", s.expectancy == null ? "-" : m(s.expectancy), s.expectancy > 0 ? "pos" : s.expectancy < 0 ? "neg" : "")}
        ${tile("총 실현손익", m(s.totalPnl), s.totalPnl > 0 ? "pos" : s.totalPnl < 0 ? "neg" : "")}
        ${tile("보유 중 포지션", s.openCount)}
      </div>
      <div class="card" style="margin-top:12px">
        <h2 style="margin-top:0">청산 내역 (R-multiple)</h2>
        ${s.closed.length === 0 ? "<div class='empty'>청산된 트레이드가 없습니다</div>" : `
        <table>
          <thead><tr><th>날짜</th><th>종목</th><th>손익</th><th>수익률</th><th>R</th><th>청산 사유</th></tr></thead>
          <tbody>
            ${s.closed.slice().reverse().map(c => `
              <tr>
                <td>${c.date}</td>
                <td><a class="ticker-link" href="#/analysis?ticker=${c.ticker}">${App.tickerDisp(c.ticker)}</a></td>
                <td class="${App.signCls(c.pnl)}">${m(c.pnl)}</td>
                <td class="${App.signCls(c.pnl)}">${App.fmt(c.pnlPct, 1)}%</td>
                <td class="${App.signCls(c.rMultiple)}">${c.rMultiple == null ? "-" : App.fmt(c.rMultiple, 1) + "R"}</td>
                <td style="text-align:left">${c.exitReason || "-"}</td>
              </tr>`).join("")}
          </tbody>
        </table>`}
      </div>`;
  }

  const groups = [
    { label: "🇺🇸 미국 (USD)", trades: items.filter(i => !isKRT(i.ticker)), kr: false },
    { label: "🇰🇷 한국 (KRW)", trades: items.filter(i => isKRT(i.ticker)), kr: true },
  ].filter(g => g.trades.length > 0);

  page.innerHTML = `
    <h1>통계</h1>
    <div class="subtitle">매매 기록 기반 자동 집계 — 시장별(통화별) 분리, 매도 시 평균단가 대비 실현손익으로 계산</div>
    ${groups.length === 0
      ? "<div class='card'><div class='empty'>매매 기록이 없습니다. 매매 기록 페이지에 매수·매도를 입력하면 여기에 집계됩니다.</div></div>"
      : groups.map(g => sectionHTML(g.label, g.trades, g.kr)).join("<div style='height:20px'></div>")}
    <div class="card" style="margin-top:16px">
      <h2 style="margin-top:0">추후 확장 예정</h2>
      <div style="color:var(--muted);font-size:13px;line-height:1.8">
        · 셋업 유형별 승률/기대값 비교 · R-multiple 분포 히스토그램 · 월별 손익 곡선 · 보유기간 분석
      </div>
    </div>`;
});
