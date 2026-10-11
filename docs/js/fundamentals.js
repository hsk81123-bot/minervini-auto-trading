/* 펀더멘털 패널 — 심층 분석 페이지의 분기 실적 추이와 SEPA 기준 판정.
   데이터는 두 갈래로 읽는다 (수집 설계와 같다):
   - data/fundamentals[_kr].json        : 종목별 지표 요약 (점수·성장률·판정)
   - data/fundamentals[_kr]/<T>.json    : 분기 원시치 (차트용)

   차트는 미니차트 3개로 나눈다. 매출·EPS·영업이익률은 단위가 서로 달라서
   한 축에 겹쳐 그리면 안 된다 (이중축 금지). 계열이 하나씩이라 색으로 구분할
   필요가 없어 전부 같은 색을 쓰고, 구분은 제목과 라벨이 한다. */
const Fundamentals = (() => {
  const QN = 8;                 // 보여줄 분기 수
  const COLOR = "var(--accent)";
  const summaryCache = {};
  const detailCache = {};

  const file = isKR => (isKR ? "fundamentals_kr" : "fundamentals");

  async function loadSummary(isKR) {
    const key = file(isKR);
    if (!(key in summaryCache)) {
      try {
        const res = await fetch(`data/${key}.json`);
        summaryCache[key] = res.ok ? await res.json() : {};
      } catch { summaryCache[key] = {}; }
    }
    return summaryCache[key];
  }

  async function loadDetail(ticker, isKR) {
    const key = `${file(isKR)}/${ticker}`;
    if (!(key in detailCache)) {
      try {
        const res = await fetch(`data/${key}.json`);
        detailCache[key] = res.ok ? await res.json() : null;
      } catch { detailCache[key] = null; }
    }
    return detailCache[key];
  }

  // ---- 숫자 표기 ----
  const pct = (v, d = 1) =>
    v == null ? "-" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(d)}%`;
  const pp = v => (v == null ? "-" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%p`);
  const big = (v, isKR) => {
    if (v == null) return "-";
    const a = Math.abs(v);
    if (isKR) return `${(v / 1e12).toFixed(a >= 1e13 ? 0 : 1)}조`;
    if (a >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
    if (a >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
    return `$${v.toFixed(0)}`;
  };
  const esc = s => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");

  /** 막대 미니차트. 0을 기준선으로 두고 음수는 아래로 그린다.
      라벨은 마지막 분기에만 붙인다 (모든 점에 숫자를 달지 않는다). */
  function bars(points, fmt) {
    const vals = points.map(p => p.v).filter(v => v != null);
    if (!vals.length) return "";
    const W = 300, H = 64, PAD = 2, n = points.length;
    const max = Math.max(...vals, 0), min = Math.min(...vals, 0);
    const span = (max - min) || 1;
    const y0 = PAD + (max / span) * (H - PAD * 2);     // 0 위치
    const bw = (W - (n - 1) * 4) / n;                   // 막대 사이 4px
    const marks = points.map((p, i) => {
      if (p.v == null) return "";
      const x = i * (bw + 4);
      const h = Math.abs(p.v) / span * (H - PAD * 2);
      const y = p.v >= 0 ? y0 - h : y0;
      return `<rect class="fbar" x="${x.toFixed(1)}" y="${y.toFixed(1)}" `
        + `width="${bw.toFixed(1)}" height="${Math.max(h, 1).toFixed(1)}" `
        + `rx="3" fill="${COLOR}"${p.v < 0 ? ' opacity="0.55"' : ""}>`
        + `<title>${esc(p.label)}: ${esc(fmt(p.v))}</title></rect>`;
    }).join("");
    const zero = min < 0
      ? `<line x1="0" y1="${y0.toFixed(1)}" x2="${W}" y2="${y0.toFixed(1)}" `
        + `stroke="var(--border)" stroke-width="1"/>`
      : "";
    return `<svg class="fchart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"
      role="img" aria-label="분기 추이">${zero}${marks}</svg>`;
  }

  /** 선 미니차트 (영업이익률). 마지막 점에만 마커를 둔다.

      막대와 달리 선은 0에 묶지 않는다. 0을 기준으로 잡으면 30%→33% 같은
      변화가 평평하게 눌려 보이는데, 영업이익률 확대는 SEPA 기준 중 하나라
      그 변화가 읽혀야 한다. 대신 값이 음수로 내려가면 0선을 그려 부호를 밝힌다. */
  function line(points, fmt) {
    const pts = points.filter(p => p.v != null);
    if (pts.length < 2) return "";
    const W = 300, H = 64, PAD = 6, n = points.length;
    const vals = pts.map(p => p.v);
    const hi = Math.max(...vals), lo = Math.min(...vals);
    const pad = (hi - lo) * 0.15 || Math.abs(hi) * 0.1 || 1;
    const max = hi + pad, min = lo - pad;
    const span = (max - min) || 1;
    const xy = p => {
      const i = points.indexOf(p);
      return [i / Math.max(n - 1, 1) * W,
              PAD + (1 - (p.v - min) / span) * (H - PAD * 2)];
    };
    const d = pts.map((p, i) => {
      const [x, y] = xy(p);
      return `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");
    const last = pts[pts.length - 1];
    const [lx, ly] = xy(last);
    const dots = pts.map(p => {
      const [x, y] = xy(p);
      return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="5" `
        + `fill="transparent"><title>${esc(p.label)}: ${esc(fmt(p.v))}</title></circle>`;
    }).join("");
    const zeroY = PAD + (1 - (0 - min) / span) * (H - PAD * 2);
    const zero = (min < 0 && max > 0)
      ? `<line x1="0" y1="${zeroY.toFixed(1)}" x2="${W}" y2="${zeroY.toFixed(1)}"
          stroke="var(--border)" stroke-width="1"/>` : "";
    return `<svg class="fchart" viewBox="0 0 ${W} ${H}" role="img"
      aria-label="영업이익률 추이">${zero}<path d="${d}" fill="none" stroke="${COLOR}"
      stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
      <circle cx="${lx.toFixed(1)}" cy="${ly.toFixed(1)}" r="4" fill="${COLOR}"
        stroke="var(--panel)" stroke-width="2"/>${dots}</svg>`;
  }

  function miniChart(title, latest, svg, note) {
    if (!svg) return "";
    return `<div class="fmini">
      <div class="fmini-head"><span>${esc(title)}</span>
        <span class="fmini-val">${latest}</span></div>
      ${svg}
      <div class="fmini-foot">${esc(note)}</div>
    </div>`;
  }

  const row = (label, state, detail, hint) => {
    const cls = state == null ? "" : state ? "pos" : "neg";
    const text = state == null ? "측정 불가" : state ? "통과" : "미달";
    return `<div class="metric-row"><span${hint ? ` title="${esc(hint)}"` : ""}>${esc(label)}</span>
      <span class="${cls}">${text}${detail ? ` <span style="color:var(--muted)">${esc(detail)}</span>` : ""}</span></div>`;
  };

  /** 패널 HTML. 데이터가 없으면 빈 문자열 (호출부에서 카드를 그리지 않는다) */
  async function render(ticker, isKR) {
    const [summary, detail] = await Promise.all([
      loadSummary(isKR), loadDetail(ticker, isKR)]);
    const m = summary[ticker];
    if (!m && !detail) return "";

    let charts = "";
    if (detail && detail.quarters && detail.quarters.length) {
      const qs = detail.quarters.slice(0, QN).reverse();   // 오래된 → 최신
      const lab = q => q.label || q.end;
      const epsKey = isKR ? "net_income" : "eps_diluted";
      const epsName = isKR ? "순이익" : "희석 EPS";
      const epsFmt = isKR ? v => big(v, true) : v => `$${v.toFixed(2)}`;
      const revPts = qs.map(q => ({ label: lab(q), v: q.revenue ?? null }));
      const epsPts = qs.map(q => ({ label: lab(q), v: q[epsKey] ?? null }));
      const opmPts = qs.map(q => ({
        label: lab(q),
        v: (q.revenue && q.revenue > 0 && q.operating_income != null)
          ? q.operating_income / q.revenue : null }));
      const lastOf = pts => [...pts].reverse().find(p => p.v != null);
      const lr = lastOf(revPts), le = lastOf(epsPts), lo = lastOf(opmPts);
      charts = `<div class="fcharts">
        ${miniChart("매출", lr ? big(lr.v, isKR) : "-",
          bars(revPts, v => big(v, isKR)),
          `최근 ${qs.length}개 분기 · 전년 동기비 ${pct(m && m.revenue_growth)}`)}
        ${miniChart(epsName, le ? epsFmt(le.v) : "-",
          bars(epsPts, epsFmt),
          `최근 ${qs.length}개 분기 · 전년 동기비 ${pct(m && m.eps_growth)}`)}
        ${miniChart("영업이익률", lo ? pct(lo.v) : "-",
          line(opmPts, v => pct(v)),
          `최근 ${qs.length}개 분기 · 전년 동기 대비 ${pp(m && m.opm_change)}`)}
      </div>`;
    }

    if (!m) {
      return `<div class="card"><h2 style="margin-top:0">펀더멘털</h2>
        ${charts}
        <div style="color:var(--muted);font-size:12px">지표 요약이 아직 없습니다 — 분기 추이만 표시합니다</div></div>`;
    }

    const score = m.code33;
    const scoreCls = score == null ? "off" : score >= 70 ? "on" : "";
    const derived = (detail && detail.quarters || []).some(q => q.derived);
    const notes = [];
    if (m.coverage != null && m.coverage < 1)
      notes.push(`지표 ${Math.round(m.coverage * 100)}%만 측정됨 — 나머지는 배점에서 제외`);
    if (isKR) notes.push("한국은 EPS 공시가 없어 순이익 성장률로 대체");
    if (derived) notes.push("4분기는 연간에서 1~3분기를 뺀 역산값");

    return `<div class="card">
      <h2 style="margin-top:0;display:flex;justify-content:space-between;align-items:center">
        <span>펀더멘털</span>
        <span class="badge ${scoreCls}" title="매출·이익·마진을 한 수치로 (0~100)">코드33 ${score ?? "-"}</span>
      </h2>
      <div style="color:var(--muted);font-size:12px;margin-bottom:10px">
        ${esc(m.as_of ?? "-")} vs ${esc(m.compared_to ?? "-")} (전년 동기)
      </div>
      ${row("분기 EPS 성장 +25% 이상", m.checks.eps_25plus, pct(m.eps_growth))}
      ${row("분기 매출 성장 +20% 이상", m.checks.revenue_20plus, pct(m.revenue_growth))}
      ${row("영업이익률 확대", m.checks.opm_expanding, pp(m.opm_change))}
      ${row("EPS 성장 가속", m.checks.eps_accelerating,
        (m.eps_growth_3q || []).filter(v => v != null).map(v => pct(v, 0)).join(" ← "),
        "최근 3개 분기의 전년 동기비 성장률이 계단식으로 올라가는지 (최신이 왼쪽)")}
      ${row("연간 EPS 3년 +25% 이상", m.checks.annual_eps_25plus, pct(m.annual_eps_cagr))}
      ${row("ROE 17% 이상", m.checks.roe_17plus, pct(m.roe))}
      ${charts}
      ${m.flags && m.flags.length ? `<div class="fwarn">⚠ 원자료 확인 필요: ${m.flags.map(esc).join(" · ")}</div>` : ""}
      ${notes.length ? `<div class="fnote">${notes.map(esc).join(" · ")}</div>` : ""}
    </div>`;
  }

  return { render, loadSummary };
})();
