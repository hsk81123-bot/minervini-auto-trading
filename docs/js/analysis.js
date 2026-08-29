/* 2. 심층 분석 페이지 — 3단 차트(RS/SPX/가격·거래량) + 지표 패널 + 포지션 사이징
   기본: lightweight-charts로 자체 렌더링 (스크리너가 내보낸 history JSON 사용)
   폴백/토글: TradingView 위젯 */
App.register("analysis", async (page, params) => {
  const data = await App.loadResults();
  const ticker = (params.get("ticker") || data.results[0]?.ticker || "AAPL").toUpperCase();
  const r = data.results.find(x => x.ticker === ticker);

  const check = (ok, label) =>
    `<div class="metric-row"><span>${label}</span><span class="${ok ? "pos" : "neg"}">${ok ? "통과" : "미달"}</span></div>`;

  const checkLabels = {
    price_above_150_200: "현재가 > 150·200일선",
    ma150_above_ma200: "150일선 > 200일선",
    ma200_rising_1m: "200일선 1개월+ 상승",
    ma50_above_150_200: "50일선 > 150·200일선",
    price_above_ma50: "현재가 > 50일선",
    above_52w_low_30pct: "52주 저점 +30% 이상",
    within_25pct_52w_high: "52주 고점 -25% 이내",
    rs_rank_70plus: "RS 순위 70 이상",
  };

  page.innerHTML = `
    <h1>심층 분석 — ${ticker}${r ? ` <span style="font-size:14px;color:var(--muted)">${r.name}</span>` : ""}</h1>
    <div class="subtitle">
      <input id="ticker-input" value="${ticker}" style="width:120px;display:inline-block;text-transform:uppercase">
      <button id="ticker-go">조회</button>
      <span class="mode-group">
        <button id="mode-d" class="mode-btn active">일봉</button><button id="mode-w" class="mode-btn">주봉</button>
      </span>
      <button id="chart-toggle" style="background:var(--panel2);border:1px solid var(--border)">TradingView 전환</button>
      ${r ? "" : " <span style='color:var(--amber)'>⚠ 필터 통과 종목이 아니라 자체 차트/지표 데이터가 없습니다 (TradingView만 표시)</span>"}
    </div>
    <div class="analysis-layout">
      <div id="chart-area">
        <div id="own-chart">
          <div class="pane-label">RS 라인 — ${ticker} ÷ S&P 500 <span style="color:var(--muted);font-weight:400">(비율 곡선 · 시작=100 정규화 · 기울기만 의미 있음, 1~99 RS 순위와는 다른 지표)</span></div>
          <div id="pane-rs"></div>
          <div class="pane-label">S&P 500</div>
          <div id="pane-spx"></div>
          <div class="pane-label" id="price-label"></div>
          <div id="pane-price"></div>
        </div>
        <div id="tv-chart" style="display:none"></div>
      </div>
      <div>
        ${r ? `
        <div class="card">
          <h2 style="margin-top:0">트렌드 템플릿</h2>
          ${Object.entries(checkLabels).map(([k, label]) => check(r.checks[k], label)).join("")}
          <div class="metric-row"><span>RS 순위 (시장 내 백분위, 1~99)</span><span><b>${r.rs_rank}</b> / 99</span></div>
        </div>
        <div class="card">
          <h2 style="margin-top:0">VCP 수축 감지 <span class="${r.vcp.score >= 70 ? "vcp-3" : r.vcp.score >= 40 ? "vcp-2" : "vcp-1"}">${r.vcp.score}/100</span></h2>
          ${r.vcp.count === 0 ? "<div style='color:var(--muted);font-size:12px;padding:6px 0'>최근 베이스에서 감지된 수축 시퀀스가 없습니다</div>" :
            r.vcp.contractions.map((c, i) => `
              <div class="metric-row">
                <span>T${i + 1} <span style="font-size:11px">${c.hi_date.slice(5)} → ${c.lo_date.slice(5)}</span></span>
                <span><b class="neg">-${c.depth_pct}%</b> <span style="color:var(--muted);font-size:11px">vol ${c.vol_ratio}</span></span>
              </div>`).join("")}
          ${check(r.vcp.count >= 2, `수축 횟수 ${r.vcp.count}회 (2회 이상)`)}
          ${check(r.vcp.final_depth_pct != null && r.vcp.final_depth_pct <= 10, `마지막 수축 타이트 (${r.vcp.final_depth_pct ?? "-"}% ≤ 10%)`)}
          ${check(r.vcp.vol_declining, "수축별 거래량 감소")}
          ${check(r.vcp.dryup, "거래량 드라이업 (5일 < 50일평균 65%)")}
          ${r.vcp.pivot ? `
          <div class="metric-row"><span>피봇 (매수 지점)</span><span><b>$${App.fmt(r.vcp.pivot)}</b></span></div>
          <div class="metric-row"><span>제안 손절 (마지막 수축 저점)</span><span>$${App.fmt(r.vcp.stop)} (${App.fmt((1 - r.vcp.stop / r.vcp.pivot) * 100, 1)}%)</span></div>
          <button id="use-pivot" style="margin-top:10px;width:100%">피봇 기준으로 포지션 계산 ↓</button>` : ""}
        </div>
        <div class="card">
          <div class="metric-row"><span>52주 고점 / 저점</span><span>$${App.fmt(r.high52)} / $${App.fmt(r.low52)}</span></div>
          <div class="metric-row"><span>50 / 150 / 200일선</span><span>$${App.fmt(r.ma50, 0)} / $${App.fmt(r.ma150, 0)} / $${App.fmt(r.ma200, 0)}</span></div>
        </div>` : ""}
        <div class="card">
          <h2 style="margin-top:0">포지션 사이징</h2>
          <div class="form-grid" style="grid-template-columns:1fr 1fr">
            <label>계좌 크기 ($)<input id="ps-account" type="number" value="${localStorage.getItem("ps-account") || 10000}"></label>
            <label>리스크 (%)<input id="ps-risk" type="number" value="1" step="0.25"></label>
            <label>진입가<input id="ps-entry" type="number" value="${r ? r.price : ""}" step="0.01"></label>
            <label>손절가<input id="ps-stop" type="number" step="0.01"></label>
          </div>
          <div id="ps-result" style="margin-top:12px"></div>
        </div>
      </div>
    </div>`;

  // ---------- 자체 3단 차트 ----------
  const loadScript = src => new Promise((ok, err) => {
    const s = document.createElement("script");
    s.src = src; s.onload = ok; s.onerror = err;
    document.head.appendChild(s);
  });

  let hist = null;      // 일봉 원본 (history JSON)
  let charts = [];      // 현재 렌더된 차트 인스턴스 (모드 전환 시 dispose)
  let chartMode = "D";  // "D" 일봉 | "W" 주봉

  // 일봉 → 주봉 집계 (월요일 기준 주 단위)
  function aggWeekly(h) {
    const out = { time: [], open: [], high: [], low: [], close: [], volume: [], spx: [], rs: [] };
    let key = null;
    for (let i = 0; i < h.time.length; i++) {
      const d = new Date(h.time[i] + "T00:00:00Z");
      const monday = new Date(d);
      monday.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7));
      const k = monday.toISOString().slice(0, 10);
      if (k !== key) {
        key = k;
        out.time.push(h.time[i]); out.open.push(h.open[i]); out.high.push(h.high[i]);
        out.low.push(h.low[i]); out.close.push(h.close[i]); out.volume.push(h.volume[i]);
        out.spx.push(h.spx[i]); out.rs.push(h.rs[i]);
      } else {
        const j = out.time.length - 1;
        out.high[j] = Math.max(out.high[j], h.high[i]);
        out.low[j] = Math.min(out.low[j], h.low[i]);
        out.close[j] = h.close[i]; out.volume[j] += h.volume[i];
        out.spx[j] = h.spx[i]; out.rs[j] = h.rs[i];
      }
    }
    return out;
  }

  function drawCharts(mode) {
    charts.forEach(c => c.remove());
    charts = [];
    const h = mode === "W" ? aggWeekly(hist) : hist;
    // 모드별 이동평균: 일봉 50/150/200일, 주봉 10/30/40주 (책과 동일)
    const maSet = mode === "W"
      ? [[10, "#e6b32a", "10주"], [30, "#4f8cff", "30주"], [40, "#b06fd8", "40주"]]
      : [[50, "#e6b32a", "50일"], [150, "#4f8cff", "150일"], [200, "#b06fd8", "200일"]];
    document.getElementById("price-label").innerHTML =
      `${ticker} ${mode === "W" ? "주봉" : "일봉"} · ` +
      maSet.map(([n, c, l]) => `<span style="color:${c}">${l}선</span>`).join("/") +
      " · 거래량 · 52주 고점/저점";

    const base = {
      layout: { background: { color: "transparent" }, textColor: "#8b93a7" },
      grid: { vertLines: { color: "#20242f" }, horzLines: { color: "#20242f" } },
      rightPriceScale: { borderColor: "#2a2f40" },
      timeScale: { borderColor: "#2a2f40" },
      crosshair: { mode: 0 },
      autoSize: true,
    };
    const mk = (id, px, extra = {}) => {
      const el = document.getElementById(id);
      el.style.height = px + "px";
      const c = LightweightCharts.createChart(el, { ...base, ...extra });
      charts.push(c);
      return c;
    };
    const rows = h.time.map((t, i) => i);

    // ① 상대강도 (종가/SPX, 기간 시작 = 100 정규화)
    const rsBase = h.rs[0];
    const cRS = mk("pane-rs", 110, { timeScale: { ...base.timeScale, visible: false } });
    cRS.addLineSeries({ color: "#2ecc71", lineWidth: 2 })
      .setData(rows.map(i => ({ time: h.time[i], value: h.rs[i] / rsBase * 100 })));

    // ② S&P 500
    const cSPX = mk("pane-spx", 110, { timeScale: { ...base.timeScale, visible: false } });
    cSPX.addLineSeries({ color: "#8b93a7", lineWidth: 2 })
      .setData(rows.map(i => ({ time: h.time[i], value: h.spx[i] })));

    // ③ 가격(캔들) + 이동평균 + 거래량
    const cPx = mk("pane-price", 360);
    const candles = cPx.addCandlestickSeries({
      upColor: "#2ecc71", downColor: "#e74c3c",
      wickUpColor: "#2ecc71", wickDownColor: "#e74c3c", borderVisible: false,
    });
    candles.setData(rows.map(i => ({
      time: h.time[i], open: h.open[i], high: h.high[i], low: h.low[i], close: h.close[i] })));

    // 52주 고점/저점 수평선 (일봉 252개 / 주봉 52개 기준)
    const nBars = Math.min(mode === "W" ? 52 : 252, h.high.length);
    const hi52 = Math.max(...h.high.slice(-nBars));
    const lo52 = Math.min(...h.low.slice(-nBars));
    const pline = (price, color, title) => candles.createPriceLine({
      price, color, lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true, title,
    });
    pline(hi52, "#f5b041", "52주 고점");
    pline(lo52, "#8b93a7", "52주 저점");
    pline(hi52 * 0.75, "rgba(245,176,65,.5)", "고점 -25%");

    // VCP 수축 오버레이 (피봇/손절 라인은 항상, 지그재그·마커는 일봉에서만)
    const v = r && r.vcp;
    if (v && v.contractions && v.contractions.length) {
      if (v.pivot) pline(v.pivot, "#4f8cff", "피봇");
      if (v.stop) pline(v.stop, "#e74c3c", "제안 손절");
      if (mode === "D") {
        const pts = [];
        v.contractions.forEach(c => {
          pts.push({ time: c.hi_date, value: c.high });
          pts.push({ time: c.lo_date, value: c.low });
        });
        const seen = new Set();
        const clean = pts.filter(p =>
          seen.has(p.time) ? false : (seen.add(p.time), true));
        if (clean.length >= 2) {
          cPx.addLineSeries({
            color: "#f5b041", lineWidth: 2,
            priceLineVisible: false, lastValueVisible: false,
            crosshairMarkerVisible: false,
          }).setData(clean);
        }
        candles.setMarkers(v.contractions.map((c, i) => ({
          time: c.hi_date, position: "aboveBar", color: "#f5b041",
          shape: "arrowDown", text: `T${i + 1} -${c.depth_pct}%`,
        })));
      }
    }

    const ma = (n, color) => {
      const s = cPx.addLineSeries({ color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
      const out = [];
      let sum = 0;
      for (let i = 0; i < h.close.length; i++) {
        sum += h.close[i];
        if (i >= n) sum -= h.close[i - n];
        if (i >= n - 1) out.push({ time: h.time[i], value: sum / n });
      }
      s.setData(out);
    };
    maSet.forEach(([n, color]) => ma(n, color));

    const vol = cPx.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" } });
    cPx.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    vol.setData(rows.map(i => ({
      time: h.time[i], value: h.volume[i],
      color: h.close[i] >= h.open[i] ? "rgba(46,204,113,.35)" : "rgba(231,76,60,.35)" })));

    // 시간축 동기화
    charts.forEach(c => c.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (!range) return;
      charts.forEach(o => { if (o !== c) o.timeScale().setVisibleLogicalRange(range); });
    }));
    cPx.timeScale().fitContent();
  }

  async function renderOwnChart() {
    const res = await fetch(`data/history/${ticker}.json`);
    if (!res.ok) throw new Error("no history");
    hist = await res.json();
    if (!window.LightweightCharts)
      await loadScript("https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js");
    drawCharts(chartMode);
  }

  // 일봉/주봉 토글
  ["mode-d", "mode-w"].forEach(id =>
    document.getElementById(id).addEventListener("click", () => {
      const mode = id === "mode-w" ? "W" : "D";
      if (mode === chartMode || !hist) return;
      chartMode = mode;
      document.getElementById("mode-d").classList.toggle("active", mode === "D");
      document.getElementById("mode-w").classList.toggle("active", mode === "W");
      drawCharts(mode);
    }));

  // ---------- TradingView 위젯 (토글/폴백) ----------
  let tvLoaded = false;
  async function renderTV() {
    if (!window.TradingView)
      await loadScript("https://s3.tradingview.com/tv.js");
    if (tvLoaded) return;
    new TradingView.widget({
      container_id: "tv-chart", symbol: ticker, interval: "D",
      theme: "dark", style: "1", locale: "kr", autosize: true,
    });
    tvLoaded = true;
  }

  const own = () => document.getElementById("own-chart");
  const tv = () => document.getElementById("tv-chart");
  document.getElementById("chart-toggle").addEventListener("click", async () => {
    const showTV = tv().style.display === "none";
    tv().style.display = showTV ? "block" : "none";
    own().style.display = showTV ? "none" : "block";
    document.getElementById("chart-toggle").textContent = showTV ? "자체 차트 전환" : "TradingView 전환";
    if (showTV) { tv().style.height = "580px"; await renderTV(); }
  });

  try {
    await renderOwnChart();
  } catch {
    // 이력 데이터 없는 종목 → TradingView로 폴백
    own().style.display = "none";
    tv().style.display = "block";
    tv().style.height = "580px";
    document.getElementById("chart-toggle").style.display = "none";
    await renderTV();
  }

  // ---------- 피봇 → 포지션 계산기 ----------
  const usePivot = document.getElementById("use-pivot");
  if (usePivot) usePivot.addEventListener("click", () => {
    const set = (id, val) => {
      const el = document.getElementById(id);
      el.value = val;
      el.dispatchEvent(new Event("input"));
    };
    set("ps-entry", r.vcp.pivot);
    set("ps-stop", r.vcp.stop);
    document.getElementById("ps-result").scrollIntoView({ behavior: "smooth", block: "center" });
  });

  // ---------- 티커 검색 ----------
  const go = () => {
    const t = document.getElementById("ticker-input").value.trim().toUpperCase();
    if (t) location.hash = `#/analysis?ticker=${t}`;
  };
  document.getElementById("ticker-go").addEventListener("click", go);
  document.getElementById("ticker-input").addEventListener("keydown", e => { if (e.key === "Enter") go(); });

  // ---------- 포지션 사이징 ----------
  const psCalc = () => {
    const acct = +document.getElementById("ps-account").value;
    const risk = +document.getElementById("ps-risk").value / 100;
    const entry = +document.getElementById("ps-entry").value;
    const stop = +document.getElementById("ps-stop").value;
    const out = document.getElementById("ps-result");
    localStorage.setItem("ps-account", acct);
    if (!acct || !entry || !stop || stop >= entry) {
      out.innerHTML = "<span style='color:var(--muted);font-size:12px'>진입가보다 낮은 손절가를 입력하세요</span>";
      return;
    }
    const riskPerShare = entry - stop;
    const shares = Math.floor((acct * risk) / riskPerShare);
    const cost = shares * entry;
    out.innerHTML = `
      <div class="metric-row"><span>매수 수량 (1R = $${App.fmt(acct * risk, 0)})</span><span><b>${shares}주</b></span></div>
      <div class="metric-row"><span>총 매수금액</span><span>$${App.fmt(cost, 0)} (계좌의 ${App.fmt(cost / acct * 100, 1)}%)</span></div>
      <div class="metric-row"><span>주당 리스크</span><span>$${App.fmt(riskPerShare)} (${App.fmt(riskPerShare / entry * 100, 1)}%)</span></div>`;
  };
  ["ps-account", "ps-risk", "ps-entry", "ps-stop"].forEach(id =>
    document.getElementById(id).addEventListener("input", psCalc));
  psCalc();
});
