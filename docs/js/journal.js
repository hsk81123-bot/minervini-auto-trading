/* 3. 매매 기록 페이지 — localStorage 저장 (추후 Firebase 연동 예정) */
const Journal = {
  KEY: "trade-journal-v1",
  load() { try { return JSON.parse(localStorage.getItem(this.KEY)) || []; } catch { return []; } },
  save(items) { localStorage.setItem(this.KEY, JSON.stringify(items)); },
};

App.register("journal", async (page) => {
  const SETUPS = ["VCP 돌파", "저점 돌파", "되돌림 매수", "기타"];
  const EXITS = ["", "손절", "익절", "추세 이탈", "기타"];

  function draw() {
    const items = Journal.load().sort((a, b) => b.date.localeCompare(a.date));
    page.innerHTML = `
      <h1>매매 기록</h1>
      <div class="subtitle">체결 단위로 입력 — 분할 매수/매도는 각각 별도 행으로 (현재 로컬 저장, 추후 연동 예정)</div>

      <div class="card">
        <h2 style="margin-top:0">새 기록</h2>
        <div class="form-grid">
          <label>날짜<input id="j-date" type="date" value="${new Date().toISOString().slice(0, 10)}"></label>
          <label>종목<input id="j-ticker" placeholder="AAPL" style="text-transform:uppercase"></label>
          <label>구분<select id="j-side"><option value="BUY">매수</option><option value="SELL">매도</option></select></label>
          <label>수량<input id="j-shares" type="number" min="1"></label>
          <label>가격<input id="j-price" type="number" step="0.01"></label>
          <label>손절가<input id="j-stop" type="number" step="0.01"></label>
          <label>익절 목표가<input id="j-target" type="number" step="0.01"></label>
          <label>셋업 유형<select id="j-setup">${SETUPS.map(s => `<option>${s}</option>`).join("")}</select></label>
          <label>청산 사유 (매도 시)<select id="j-exit">${EXITS.map(s => `<option>${s}</option>`).join("")}</select></label>
          <label style="grid-column:span 2">메모<input id="j-notes" placeholder="선택"></label>
        </div>
        <div style="margin-top:12px"><button id="j-add">기록 추가</button></div>
      </div>

      <div class="card">
        ${items.length === 0 ? "<div class='empty'>아직 기록이 없습니다</div>" : `
        <table>
          <thead><tr><th>날짜</th><th>종목</th><th>구분</th><th>수량</th><th>가격</th>
            <th>손절가</th><th>목표가</th><th>셋업</th><th>청산사유</th><th>메모</th><th></th></tr></thead>
          <tbody>
            ${items.map(it => `
              <tr>
                <td>${it.date}</td>
                <td><a class="ticker-link" href="#/analysis?ticker=${it.ticker}">${it.ticker}</a></td>
                <td class="${it.side === "BUY" ? "pos" : "neg"}">${it.side === "BUY" ? "매수" : "매도"}</td>
                <td>${it.shares}</td>
                <td>$${App.fmt(it.price)}</td>
                <td>${it.stop ? "$" + App.fmt(it.stop) : "-"}</td>
                <td>${it.target ? "$" + App.fmt(it.target) : "-"}</td>
                <td style="text-align:left">${it.setup || "-"}</td>
                <td style="text-align:left">${it.exitReason || "-"}</td>
                <td style="text-align:left;color:var(--muted)">${it.notes || ""}</td>
                <td><button class="danger" data-del="${it.id}">삭제</button></td>
              </tr>`).join("")}
          </tbody>
        </table>`}
      </div>`;

    document.getElementById("j-add").addEventListener("click", () => {
      const v = id => document.getElementById(id).value.trim();
      const item = {
        id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
        date: v("j-date"), ticker: v("j-ticker").toUpperCase(),
        side: v("j-side"), shares: +v("j-shares"), price: +v("j-price"),
        stop: +v("j-stop") || null, target: +v("j-target") || null,
        setup: v("j-setup"), exitReason: v("j-exit") || null, notes: v("j-notes"),
      };
      if (!item.ticker || !item.shares || !item.price) {
        alert("종목, 수량, 가격은 필수입니다"); return;
      }
      if (item.side === "BUY" && !item.stop) {
        alert("매수 기록에는 손절가가 필수입니다 (리스크 관리!)"); return;
      }
      Journal.save([...Journal.load(), item]);
      draw();
    });

    page.querySelectorAll("[data-del]").forEach(btn =>
      btn.addEventListener("click", () => {
        if (!confirm("이 기록을 삭제할까요?")) return;
        Journal.save(Journal.load().filter(x => x.id !== btn.dataset.del));
        draw();
      }));
  }
  draw();
});
