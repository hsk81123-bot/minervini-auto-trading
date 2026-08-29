/* 1. 종목 필터 페이지 — 시장(미국/한국) + 범위(전체/S&P500·KOSPI) 토글 */
App.register("filter", async (page) => {
  const isKR = App.getMarket() === "kr";
  const data = await App.loadResults();
  const spSet = isKR ? null : new Set(
    await fetch("data/sp500.json").then(r => r.ok ? r.json() : []).catch(() => []));
  let rows = [...data.results];
  let scope = "all"; // "all" | "sub" (S&P 500 또는 KOSPI)
  let sortKey = "rs_rank", sortDir = -1;

  const inSub = r => isKR ? r.ticker.endsWith(".KS") : spSet.has(r.ticker);
  const subLabel = isKR ? "KOSPI" : "S&P 500";

  const vcpFlags = (v, price) => {
    const f = [];
    if (v.count >= 2) f.push(`수축×${v.count}`);
    if (v.final_depth_pct != null && v.final_depth_pct <= 10) f.push("타이트");
    if (v.dryup) f.push("드라이업");
    if (v.pivot && price <= v.pivot && (v.pivot - price) / v.pivot <= 0.05) f.push("피봇근접");
    return f;
  };
  const vcpCls = s => (s >= 70 ? "vcp-3" : s >= 40 ? "vcp-2" : "vcp-1");

  function tableHTML() {
    const get = r => sortKey === "vcp" ? r.vcp.score : r[sortKey];
    rows.sort((a, b) => (get(a) > get(b) ? 1 : -1) * sortDir);
    const visible = scope === "sub" ? rows.filter(inSub) : rows;
    return `
      <table>
        <thead><tr>
          <th data-k="ticker">종목</th>
          <th data-k="rs_rank">RS</th>
          <th data-k="price">가격</th>
          <th data-k="pct_off_high">고점대비</th>
          <th data-k="pct_above_low">저점대비</th>
          <th data-k="vcp">VCP</th>
          <th>셋업 플래그</th>
        </tr></thead>
        <tbody>
          ${visible.map(r => `
            <tr>
              <td><a class="ticker-link" href="#/analysis?ticker=${r.ticker}">${App.tickerDisp(r.ticker)}</a>
                  <span style="color:var(--muted);font-size:11px"> ${App.cleanName(r.name)}</span></td>
              <td><b>${r.rs_rank}</b></td>
              <td>${App.money(r.price, isKR)}</td>
              <td class="neg">${r.pct_off_high}%</td>
              <td class="pos">+${r.pct_above_low}%</td>
              <td class="${vcpCls(r.vcp.score)}">${r.vcp.score}</td>
              <td style="text-align:left">${vcpFlags(r.vcp, r.price).map(f =>
                `<span class="badge on">${f}</span>`).join("") || "<span class='badge off'>-</span>"}</td>
            </tr>`).join("")}
        </tbody>
      </table>`;
  }

  function draw() {
    const subCount = rows.filter(inSub).length;
    page.innerHTML = `
      <h1>종목 필터</h1>
      <div class="subtitle">
        <span class="mode-group">
          <button id="mkt-us" class="mode-btn ${isKR ? "" : "active"}">🇺🇸 미국</button><button id="mkt-kr" class="mode-btn ${isKR ? "active" : ""}">🇰🇷 한국</button>
        </span>
        <span class="mode-group" style="margin-left:6px">
          <button id="scope-all" class="mode-btn ${scope === "all" ? "active" : ""}">전체 (${data.passed})</button><button id="scope-sub" class="mode-btn ${scope === "sub" ? "active" : ""}">${subLabel} (${subCount})</button>
        </span>
        <span style="margin-left:8px">
          트렌드 템플릿 8조건 통과 —
          ${scope === "sub" ? `${subLabel} 내 ${subCount}종목` : `${data.passed}종목`}
          (분석 ${data.analyzed}종목, ${data.data_date} 기준)
        </span>
      </div>
      <div class="card">${tableHTML()}</div>`;

    page.querySelectorAll("th[data-k]").forEach(th => {
      th.addEventListener("click", () => {
        const k = th.dataset.k;
        sortDir = (sortKey === k) ? -sortDir : -1;
        sortKey = k;
        draw();
      });
    });
    document.getElementById("scope-all").addEventListener("click",
      () => { scope = "all"; draw(); });
    document.getElementById("scope-sub").addEventListener("click",
      () => { scope = "sub"; draw(); });
    const switchMkt = m => () => {
      if (App.getMarket() === m) return;
      App.setMarket(m);
      window.dispatchEvent(new HashChangeEvent("hashchange")); // 페이지 재로딩
    };
    document.getElementById("mkt-us").addEventListener("click", switchMkt("us"));
    document.getElementById("mkt-kr").addEventListener("click", switchMkt("kr"));
  }
  draw();
});
