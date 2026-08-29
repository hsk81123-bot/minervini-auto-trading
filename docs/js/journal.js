/* 3. 매매 기록 페이지 — 저장 계층: Firestore(로그인 시) / localStorage(기본) */
const Journal = (() => {
  const KEY = "trade-journal-v1";
  let mode = "local";
  let uid = null;

  const localLoad = () => {
    try { return JSON.parse(localStorage.getItem(KEY)) || []; } catch { return []; }
  };
  const localSave = items => localStorage.setItem(KEY, JSON.stringify(items));
  const col = () => firebase.firestore()
    .collection("users").doc(uid).collection("trades");

  return {
    mode: () => mode,

    setLocal() { mode = "local"; uid = null; },

    async setCloud(user) {
      uid = user.uid;
      mode = "cloud";
      // 로컬에 쌓인 기록이 있으면 클라우드로 병합 업로드 후 로컬 비움
      const locals = localLoad();
      if (locals.length) {
        const batch = firebase.firestore().batch();
        locals.forEach(it => batch.set(col().doc(it.id), it, { merge: true }));
        await batch.commit();
        localStorage.removeItem(KEY);
      }
    },

    async load() {
      if (mode === "cloud") {
        const snap = await col().get();
        return snap.docs.map(d => d.data());
      }
      return localLoad();
    },

    async add(item) {
      if (mode === "cloud") await col().doc(item.id).set(item);
      else localSave([...localLoad(), item]);
    },

    async remove(id) {
      if (mode === "cloud") await col().doc(id).delete();
      else localSave(localLoad().filter(x => x.id !== id));
    },
  };
})();

App.register("journal", async (page) => {
  const SETUPS = ["VCP 돌파", "저점 돌파", "되돌림 매수", "기타"];
  const EXITS = ["", "손절", "익절", "추세 이탈", "기타"];

  async function draw() {
    const items = (await Journal.load()).sort((a, b) => b.date.localeCompare(a.date));
    const modeBadge = Journal.mode() === "cloud"
      ? "<span class='badge on'>☁ 클라우드 동기화</span>"
      : "<span class='badge off'>로컬 저장 (로그인하면 기기 간 동기화)</span>";
    page.innerHTML = `
      <h1>매매 기록</h1>
      <div class="subtitle">체결 단위로 입력 — 분할 매수/매도는 각각 별도 행으로 ${modeBadge}</div>

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

    document.getElementById("j-add").addEventListener("click", async () => {
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
      await Journal.add(item);
      draw();
    });

    page.querySelectorAll("[data-del]").forEach(btn =>
      btn.addEventListener("click", async () => {
        if (!confirm("이 기록을 삭제할까요?")) return;
        await Journal.remove(btn.dataset.del);
        draw();
      }));
  }
  await draw();
});
