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
        if (it.setup) p.setup = it.setup; // 셋업별 통계용
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
            setup: p.setup || "기타",
          });
          p.shares -= sold;
          if (p.shares === 0) { p.avgCost = 0; p.stop = null; p.setup = null; }
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

  // 셋업 유형별 성과 표
  function setupTableHTML(closed, m) {
    if (!closed.length) return "";
    const groups = {};
    closed.forEach(c => (groups[c.setup] = groups[c.setup] || []).push(c));
    const rows = Object.entries(groups).map(([setup, arr]) => {
      const wins = arr.filter(c => c.pnl > 0).length;
      const rs = arr.map(c => c.rMultiple).filter(v => v != null);
      const avgR = rs.length ? rs.reduce((s, v) => s + v, 0) / rs.length : null;
      const total = arr.reduce((s, c) => s + c.pnl, 0);
      return { setup, n: arr.length, winRate: wins / arr.length * 100, avgR, total };
    }).sort((a, b) => b.n - a.n);
    return `
      <div class="card" style="margin-top:12px">
        <h2 style="margin-top:0">셋업 유형별 성과</h2>
        <table>
          <thead><tr><th>셋업</th><th>트레이드</th><th>승률</th><th>평균 R</th><th>총손익</th></tr></thead>
          <tbody>
            ${rows.map(g => `
              <tr>
                <td>${g.setup}</td>
                <td>${g.n}</td>
                <td class="${g.winRate >= 50 ? "pos" : "neg"}">${App.fmt(g.winRate, 0)}%</td>
                <td class="${App.signCls(g.avgR)}">${g.avgR == null ? "-" : App.fmt(g.avgR, 2) + "R"}</td>
                <td class="${App.signCls(g.total)}">${m(g.total)}</td>
              </tr>`).join("")}
          </tbody>
        </table>
      </div>`;
  }

  // R-multiple 분포 히스토그램 (CSS 막대)
  function rHistHTML(closed) {
    const rs = closed.map(c => c.rMultiple).filter(v => v != null);
    if (!rs.length) return "";
    const buckets = [
      ["≤ −2R", v => v <= -2, "var(--red)"],
      ["−2 ~ −1R", v => v > -2 && v <= -1, "var(--red)"],
      ["−1 ~ 0R", v => v > -1 && v < 0, "var(--red)"],
      ["0 ~ 1R", v => v >= 0 && v < 1, "var(--green)"],
      ["1 ~ 2R", v => v >= 1 && v < 2, "var(--green)"],
      ["2 ~ 3R", v => v >= 2 && v < 3, "var(--green)"],
      ["3R +", v => v >= 3, "var(--green)"],
    ].map(([label, fn, color]) => ({ label, color, n: rs.filter(fn).length }));
    const max = Math.max(...buckets.map(b => b.n), 1);
    return `
      <div class="card" style="margin-top:12px">
        <h2 style="margin-top:0">R-multiple 분포 <span style="color:var(--muted);font-weight:400;font-size:12px">(손절가 기록된 트레이드 ${rs.length}건)</span></h2>
        ${buckets.map(b => `
          <div style="display:flex;align-items:center;gap:10px;margin:5px 0;font-size:12px">
            <span style="width:70px;color:var(--muted);text-align:right">${b.label}</span>
            <div style="flex:1;background:var(--panel2);border-radius:4px;height:16px;overflow:hidden">
              <div style="width:${b.n / max * 100}%;height:100%;background:${b.color};opacity:.75"></div>
            </div>
            <span style="width:28px">${b.n}</span>
          </div>`).join("")}
        <div style="font-size:11px;color:var(--muted);margin-top:8px">
          이상적인 분포: 왼쪽은 −1R 부근에 몰리고(손절 규율), 오른쪽 꼬리가 김(수익은 길게).
          −2R 이하가 자주 나오면 손절 미준수 신호.
        </div>
      </div>`;
  }

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
      </div>
      ${setupTableHTML(s.closed, m)}
      ${rHistHTML(s.closed)}`;
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
        · 월별 손익 곡선 · 보유기간 분석
      </div>
    </div>`;
});
