/* 1. 종목 필터 페이지 — 트렌드 템플릿 통과 종목 테이블 */
App.register("filter", async (page) => {
  const data = await App.loadResults();
  let rows = [...data.results];
  let sortKey = "rs_rank", sortDir = -1;

  const vcpFlags = v => {
    const f = [];
    if (v.tightening) f.push("수축");
    if (v.volume_dryup) f.push("거래량↓");
    if (v.near_52w_high) f.push("고점권");
    return f;
  };

  function tableHTML() {
    const get = r => sortKey === "vcp" ? r.vcp.score : r[sortKey];
    rows.sort((a, b) => (get(a) > get(b) ? 1 : -1) * sortDir);
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
          ${rows.map(r => `
            <tr>
              <td><a class="ticker-link" href="#/analysis?ticker=${r.ticker}">${r.ticker}</a>
                  <span style="color:var(--muted);font-size:11px"> ${r.name || ""}</span></td>
              <td><b>${r.rs_rank}</b></td>
              <td>$${App.fmt(r.price)}</td>
              <td class="neg">${r.pct_off_high}%</td>
              <td class="pos">+${r.pct_above_low}%</td>
              <td class="vcp-${r.vcp.score}">${"●".repeat(r.vcp.score)}${"○".repeat(3 - r.vcp.score)}</td>
              <td style="text-align:left">${vcpFlags(r.vcp).map(f =>
                `<span class="badge on">${f}</span>`).join("") || "<span class='badge off'>-</span>"}</td>
            </tr>`).join("")}
        </tbody>
      </table>`;
  }

  function draw() {
    page.innerHTML = `
      <h1>종목 필터</h1>
      <div class="subtitle">
        미너비니 트렌드 템플릿 8개 조건 전부 통과 — ${data.passed}종목
        (분석 ${data.analyzed}종목, ${data.data_date} 기준) · 헤더 클릭으로 정렬
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
  }
  draw();
});
