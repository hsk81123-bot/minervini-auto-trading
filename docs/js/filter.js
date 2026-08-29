/* 1. 종목 필터 페이지 — 트렌드 템플릿 통과 종목 테이블 */
App.register("filter", async (page) => {
  const data = await App.loadResults();
  const spSet = new Set(
    await fetch("data/sp500.json").then(r => r.ok ? r.json() : []).catch(() => []));
  let rows = [...data.results];
  let scope = "all"; // "all" | "sp500"
  let sortKey = "rs_rank", sortDir = -1;

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
    const visible = scope === "sp500" ? rows.filter(r => spSet.has(r.ticker)) : rows;
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
              <td><a class="ticker-link" href="#/analysis?ticker=${r.ticker}">${r.ticker}</a>
                  <span style="color:var(--muted);font-size:11px"> ${App.cleanName(r.name)}</span></td>
              <td><b>${r.rs_rank}</b></td>
              <td>$${App.fmt(r.price)}</td>
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
    const spCount = rows.filter(r => spSet.has(r.ticker)).length;
    page.innerHTML = `
      <h1>종목 필터</h1>
      <div class="subtitle">
        미너비니 트렌드 템플릿 8개 조건 전부 통과 —
        ${scope === "sp500" ? `S&P 500 내 ${spCount}종목` : `${data.passed}종목`}
        (분석 ${data.analyzed}종목, ${data.data_date} 기준) · 헤더 클릭으로 정렬
        <span class="mode-group" style="margin-left:10px">
          <button id="scope-all" class="mode-btn ${scope === "all" ? "active" : ""}">전체 (${data.passed})</button><button id="scope-sp" class="mode-btn ${scope === "sp500" ? "active" : ""}">S&P 500 (${spCount})</button>
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
    document.getElementById("scope-sp").addEventListener("click",
      () => { scope = "sp500"; draw(); });
  }
  draw();
});
