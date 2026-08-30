/* 1. 종목 필터 페이지 — 시장(미국/한국) + 범위(전체/S&P500·KOSPI) 토글 */

/* 수동 데이터 새로고침: GitHub Actions workflow_dispatch 호출.
   토큰은 최초 1회 입력받아 본인 전용 Firestore 문서에 저장 (규칙으로 보호). */
const Refresh = (() => {
  const REPO = "hsk81123-bot/minervini-auto-trading";
  const API = `https://api.github.com/repos/${REPO}/actions/workflows/screener.yml`;
  let polling = false;

  const settingsRef = () => {
    const user = window.firebase?.auth?.().currentUser;
    if (!user) return null;
    return firebase.firestore()
      .collection("users").doc(user.uid).collection("meta").doc("settings");
  };

  async function getToken() {
    const ref = settingsRef();
    if (!ref) return { err: "로그인 후 사용 가능합니다" };
    const snap = await ref.get();
    let tok = snap.exists ? snap.data().ghToken : null;
    if (!tok) {
      tok = prompt(
        "수동 새로고침에는 GitHub 토큰이 필요합니다 (최초 1회만 입력).\n\n" +
        "발급: github.com → Settings → Developer settings →\n" +
        "Fine-grained tokens → Generate new token\n" +
        `— Repository access: ${REPO}\n` +
        "— Permissions: Actions = Read and write\n\n" +
        "발급받은 토큰을 붙여넣으세요:");
      if (!tok || !tok.trim()) return { err: "취소됨" };
      tok = tok.trim();
      await ref.set({ ghToken: tok }, { merge: true });
    }
    return { tok };
  }

  async function clearToken() {
    const ref = settingsRef();
    if (ref) await ref.set({ ghToken: null }, { merge: true });
  }

  function poll(statusEl, btn) {
    if (polling) return;
    polling = true;
    const started = Date.now();
    const timer = setInterval(async () => {
      try {
        const r = await fetch(`${API}/runs?per_page=1`);
        const run = (await r.json()).workflow_runs?.[0];
        if (!run) return;
        const el = document.getElementById("refresh-status");
        if (!el) return; // 페이지 이동함
        const mins = Math.round((Date.now() - started) / 60000);
        if (run.status !== "completed") {
          el.textContent = `스크리닝 실행 중... ${mins}분 경과 (총 20~30분 소요)`;
        } else {
          clearInterval(timer);
          polling = false;
          el.textContent = run.conclusion === "success"
            ? "✅ 완료! 1~2분 후 페이지를 새로고침(F5)하면 새 데이터가 보입니다"
            : `❌ 실패 (${run.conclusion}) — GitHub Actions 로그 확인 필요`;
          const b = document.getElementById("refresh-data");
          if (b) b.disabled = false;
        }
      } catch { /* 일시 오류는 다음 폴링에서 */ }
    }, 60000);
  }

  async function trigger(statusEl, btn) {
    if (!window.FIREBASE_CONFIG) {
      statusEl.textContent = "로컬 모드 — 터미널에서 python screener/screener.py 실행";
      return;
    }
    const { tok, err } = await getToken();
    if (err) { statusEl.textContent = err; return; }
    btn.disabled = true;
    statusEl.textContent = "실행 요청 중...";
    try {
      const res = await fetch(`${API}/dispatches`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${tok}`,
          Accept: "application/vnd.github+json",
        },
        body: JSON.stringify({ ref: "main" }),
      });
      if (res.status === 204) {
        statusEl.textContent = "요청 완료 — 미국+한국 전 종목 스크리닝 시작 (20~30분 소요)";
        poll(statusEl, btn);
      } else if (res.status === 401 || res.status === 403) {
        await clearToken();
        statusEl.textContent = "토큰이 유효하지 않습니다 — 다시 눌러 새 토큰을 입력하세요";
        btn.disabled = false;
      } else {
        statusEl.textContent = `실패: HTTP ${res.status}`;
        btn.disabled = false;
      }
    } catch (e) {
      statusEl.textContent = "요청 실패: " + e.message;
      btn.disabled = false;
    }
  }

  return { trigger };
})();

App.register("filter", async (page) => {
  const isKR = App.getMarket() === "kr";
  const data = await App.loadResults();
  const spSet = isKR ? null : new Set(
    await fetch("data/sp500.json").then(r => r.ok ? r.json() : []).catch(() => []));
  const watch = new Set(await Watchlist.load());
  let rows = [...data.results];
  let scope = "all"; // "all" | "sub"(S&P500·KOSPI) | "watch"(관심종목)
  let sortKey = "vcp", sortDir = -1; // 기본 정렬: VCP 점수 내림차순

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
    const visible = scope === "sub" ? rows.filter(inSub)
      : scope === "watch" ? rows.filter(r => watch.has(r.ticker)) : rows;
    return `
      <table>
        <thead><tr>
          <th style="cursor:default">⭐</th>
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
              <td class="star" data-star="${r.ticker}" title="관심종목 토글">${watch.has(r.ticker) ? "★" : "☆"}</td>
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
      <h1 style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
        종목 필터
        <span style="font-size:12px;font-weight:400;display:flex;align-items:center;gap:8px">
          <span id="refresh-status" style="color:var(--muted)"></span>
          <button id="refresh-data" class="mode-btn" style="border:1px solid var(--border);border-radius:8px" title="GitHub Actions로 최신 데이터 스크리닝 실행">🔄 새로고침</button>
        </span>
      </h1>
      <div class="subtitle">
        <span class="mode-group">
          <button id="mkt-us" class="mode-btn ${isKR ? "" : "active"}">🇺🇸 미국</button><button id="mkt-kr" class="mode-btn ${isKR ? "active" : ""}">🇰🇷 한국</button>
        </span>
        <span class="mode-group" style="margin-left:6px">
          <button id="scope-all" class="mode-btn ${scope === "all" ? "active" : ""}">전체 (${data.passed})</button><button id="scope-sub" class="mode-btn ${scope === "sub" ? "active" : ""}">${subLabel} (${subCount})</button><button id="scope-watch" class="mode-btn ${scope === "watch" ? "active" : ""}">⭐ 관심 (${rows.filter(r => watch.has(r.ticker)).length})</button>
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
    document.getElementById("scope-watch").addEventListener("click",
      () => { scope = "watch"; draw(); });
    document.getElementById("refresh-data").addEventListener("click", () =>
      Refresh.trigger(document.getElementById("refresh-status"),
                      document.getElementById("refresh-data")));
    page.querySelectorAll("[data-star]").forEach(el =>
      el.addEventListener("click", async () => {
        const t = el.dataset.star;
        const on = await Watchlist.toggle(t);
        if (on) watch.add(t); else watch.delete(t);
        draw();
      }));
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
