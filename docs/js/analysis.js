/* 2. 심층 분석 페이지 — 3단 차트(RS/SPX/가격·거래량) + 지표 패널 + 포지션 사이징
   기본: lightweight-charts로 자체 렌더링 (스크리너가 내보낸 history JSON 사용)
   폴백/토글: TradingView 위젯 */
App.register("analysis", async (page, params) => {
  const data = await App.loadResults();
  let ticker = (params.get("ticker") || data.results[0]?.ticker || "AAPL").toUpperCase();
  // 6자리 숫자 = 한국 종목 코드 (현재 시장 모드와 무관하게) → .KS/.KQ 접미사 해결
  if (/^\d{6}$/.test(ticker)) {
    const hit = data.results.find(x => x.ticker.startsWith(ticker + "."));
    if (hit) ticker = hit.ticker;
    else {
      try {
        const krNames = await fetch("data/tickers_kr.json")
          .then(res => res.ok ? res.json() : {});
        ticker = krNames[ticker + ".KQ"] ? ticker + ".KQ" : ticker + ".KS";
      } catch { ticker += ".KS"; }
    }
  }
  const isKR = App.isKRTicker(ticker) || App.getMarket() === "kr";
  const benchName = isKR ? "KOSPI" : "S&P 500";
  const disp = App.tickerDisp(ticker);
  const won = n => App.money(n, isKR);
  let r = data.results.find(x => x.ticker === ticker);
  const watched = (await Watchlist.load()).includes(ticker);

  // 이력 JSON 프리페치 — 온디맨드 생성분(meta 포함)이면 미통과 종목도 패널 표시
  let histData = null;
  try {
    const hres = await fetch(`data/${isKR ? "history_kr" : "history"}/${ticker}.json`);
    if (hres.ok) histData = await hres.json();
  } catch { /* 없으면 아래 안내 표시 */ }
  if (!r && histData && histData.meta)
    r = { ticker, name: "(온디맨드 분석 — 필터 미통과 종목)", ...histData.meta };

  const check = (ok, label) =>
    ok == null
      ? `<div class="metric-row"><span>${label}</span><span style="color:var(--muted)">측정 불가</span></div>`
      : `<div class="metric-row"><span>${label}</span><span class="${ok ? "pos" : "neg"}">${ok ? "통과" : "미달"}</span></div>`;

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
    <h1>심층 분석 — ${disp}
      <span id="watch-btn" class="star" style="font-size:20px" title="관심종목 토글">${watched ? "★" : "☆"}</span>${
        r ? ` <span style="font-size:14px;color:var(--muted)">${App.cleanName(r.name)}</span>` : ""}${
        r && r.rs_rank >= 85 && r.pct_off_high >= -15 && r.rs_nh
          ? ' <span class="badge lead" style="font-size:12px;vertical-align:middle" title="주도주: RS 85+ · 고점 −15% 이내 · RS라인 52주 신고가(5% 이내)">👑 주도주</span>' : ""}${
        r && r.pct_off_high >= -2
          ? ' <span class="badge lead" style="font-size:12px;vertical-align:middle" title="52주 고점 −2% 이내">신고가</span>' : ""}</h1>
    <div class="subtitle">
      <span style="position:relative;display:inline-block">
        <input id="ticker-input" value="${disp}" placeholder="티커 또는 종목명" style="width:160px;display:inline-block">
        <div id="suggest" class="suggest" style="display:none"></div>
      </span>
      <button id="ticker-go">조회</button>
      <span class="mode-group">
        <button id="mode-d" class="mode-btn active">일봉</button><button id="mode-w" class="mode-btn">주봉</button>
      </span>
      <button id="chart-toggle" style="background:var(--panel2);border:1px solid var(--border)">${isKR ? "TradingView ↗" : "TradingView 전환"}</button>
    </div>
    <div class="analysis-layout">
      <div id="chart-area">
        <div id="own-chart">
          <div class="pane-label">RS 라인 — ${disp} ÷ ${benchName} <span style="color:var(--muted);font-weight:400">(원시 비율 · 기울기만 의미 있음, 1~99 RS 순위와는 다른 지표)</span></div>
          <div id="pane-rs"></div>
          <div class="pane-label">${benchName}</div>
          <div id="pane-spx"></div>
          <div class="pane-label" id="price-label"></div>
          <div id="pane-price"></div>
        </div>
        <div id="tv-chart" style="display:none"></div>
        <div id="no-chart" class="card" style="display:none;text-align:center;padding:36px 20px">
          <div style="font-size:14px;margin-bottom:8px">필터 통과 종목이 아니라 자체 차트 데이터가 아직 없습니다</div>
          <div id="gen-status" style="color:var(--muted);font-size:12px;margin-bottom:16px">
            자체 차트를 생성하면 VCP 수축 분석·트렌드 템플릿 패널까지 볼 수 있습니다
            (GitHub Actions에서 계산, 약 3~4분 · 다음 자동 갱신 때 삭제되므로 필요 시 재생성)
          </div>
          <button id="btn-gen">⚙ 자체 차트 생성</button>
          <button id="btn-tv" style="background:var(--panel2);border:1px solid var(--border);margin-left:8px">TradingView로 보기</button>
        </div>
      </div>
      <div>
        ${r ? `
        <div class="card">
          <h2 style="margin-top:0">트렌드 템플릿</h2>
          ${Object.entries(checkLabels).map(([k, label]) => check(r.checks[k], label)).join("")}
          <div class="metric-row"><span>RS 순위 (시장 내 백분위, 1~99)</span><span><b>${r.rs_rank ?? "-"}</b> / 99</span></div>
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
          <div class="metric-row"><span>피봇 (매수 지점)</span><span><b>${won(r.vcp.pivot)}</b></span></div>
          <div class="metric-row"><span>제안 손절 (마지막 수축 저점)</span><span>${won(r.vcp.stop)} (${App.fmt((1 - r.vcp.stop / r.vcp.pivot) * 100, 1)}%)</span></div>
          <button id="use-pivot" style="margin-top:10px;width:100%">피봇 기준으로 포지션 계산 ↓</button>` : ""}
        </div>
        <div class="card">
          <div class="metric-row"><span>52주 고점 / 저점</span><span>${won(r.high52)} / ${won(r.low52)}</span></div>
          <div class="metric-row"><span>50 / 150 / 200일선</span><span>${App.money(r.ma50, isKR, 0)} / ${App.money(r.ma150, isKR, 0)} / ${App.money(r.ma200, isKR, 0)}</span></div>
        </div>` : ""}
        <div class="card">
          <h2 style="margin-top:0">포지션 사이징</h2>
          <div class="form-grid" style="grid-template-columns:1fr 1fr">
            <label>계좌 크기 (${isKR ? "₩" : "$"})<input id="ps-account" type="number" value="${localStorage.getItem(isKR ? "ps-account-kr" : "ps-account") || (isKR ? 10000000 : 10000)}"></label>
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
  let fitRO = null;     // 기본 시야 유지용 ResizeObserver

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
    if (fitRO) { fitRO.disconnect(); fitRO = null; }
    charts.forEach(c => c.remove());
    charts = [];
    const h = mode === "W" ? aggWeekly(hist) : hist;
    // 모드별 이동평균: 일봉 50/150/200일, 주봉 10/30/40주 (책과 동일)
    const maSet = mode === "W"
      ? [[10, "#e6b32a", "10주"], [30, "#4f8cff", "30주"], [40, "#b06fd8", "40주"]]
      : [[50, "#e6b32a", "50일"], [150, "#4f8cff", "150일"], [200, "#b06fd8", "200일"]];
    document.getElementById("price-label").innerHTML =
      `${App.tickerDisp(ticker)} ${mode === "W" ? "주봉" : "일봉"} · ` +
      maSet.map(([n, c, l]) => `<span style="color:${c}">${l}선</span>`).join("/") +
      (mode === "W"
        ? " · 거래량+<span style='color:#8b93a7'>10주평균</span>"
        : " · 거래량+<span style='color:#f5b041'>5일평균</span>/<span style='color:#8b93a7'>드라이업기준선(50일×65%)</span>") +
      " · 52주 고점/저점";

    const base = {
      layout: { background: { color: "transparent" }, textColor: "#8b93a7" },
      grid: { vertLines: { color: "#20242f" }, horzLines: { color: "#20242f" } },
      rightPriceScale: { borderColor: "#2a2f40", minimumWidth: 72 },
      timeScale: { borderColor: "#2a2f40" },
      crosshair: { mode: 0 },
      autoSize: true,
    };
    const mk = (id, px, extra = {}) => {
      const el = document.getElementById(id);
      el.innerHTML = ""; // 중복 렌더(인증 이벤트와의 레이스) 시 이중 차트 방지
      el.style.height = px + "px";
      const c = LightweightCharts.createChart(el, { ...base, ...extra });
      charts.push(c);
      return c;
    };
    const rows = h.time.map((t, i) => i);

    // 52주 구간 (일봉 252개 / 주봉 52개)
    const look = Math.min(mode === "W" ? 52 : 252, h.time.length);
    const t52start = h.time[h.time.length - look];
    // 52주 시작 지점 수직 점선 (차트 위 DOM 오버레이 — 줌/이동 시 좌표 추적)
    const vline52 = (chart, paneId) => {
      const pane = document.getElementById(paneId);
      pane.style.position = "relative";
      const el = document.createElement("div");
      el.title = "52주 시작";
      el.style.cssText = "position:absolute;top:0;bottom:0;width:0;" +
        "border-left:1px dashed rgba(139,147,167,.55);pointer-events:none;z-index:3";
      pane.appendChild(el);
      const upd = () => {
        const x = chart.timeScale().timeToCoordinate(t52start);
        if (x == null) { el.style.display = "none"; return; }
        el.style.display = "block";
        el.style.left = x + "px";
      };
      chart.timeScale().subscribeVisibleLogicalRangeChange(upd);
      setTimeout(upd, 0);
    };
    const line52 = (series, arr, title52 = "52주") => {
      const hi = Math.max(...arr.slice(-look));
      const lo = Math.min(...arr.slice(-look));
      const pl = (price, color, title) => series.createPriceLine({
        price, color, lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true, title,
      });
      pl(hi, "#f5b041", `${title52} 고점`);
      pl(lo, "#8b93a7", `${title52} 저점`);
    };

    // ① 상대강도 (종가/SPX 원시 비율 — 책의 StockCharts 표기와 동일)
    const rsMax = Math.max(...h.rs);
    const prec = rsMax < 0.01 ? 6 : rsMax < 0.1 ? 5 : rsMax < 10 ? 4 : 2;
    const cRS = mk("pane-rs", 110, { timeScale: { ...base.timeScale, visible: false } });
    const sRS = cRS.addLineSeries({
      color: "#2ecc71", lineWidth: 2,
      priceFormat: { type: "price", precision: prec, minMove: Math.pow(10, -prec) },
    });
    sRS.setData(rows.map(i => ({ time: h.time[i], value: h.rs[i] })));
    line52(sRS, h.rs);
    vline52(cRS, "pane-rs");

    // ② 벤치마크 지수 (S&P 500 / KOSPI)
    const cSPX = mk("pane-spx", 110, { timeScale: { ...base.timeScale, visible: false } });
    const sSPX = cSPX.addLineSeries({ color: "#8b93a7", lineWidth: 2 });
    sSPX.setData(rows.map(i => ({ time: h.time[i], value: h.spx[i] })));
    line52(sSPX, h.spx);
    vline52(cSPX, "pane-spx");

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
    vline52(cPx, "pane-price");

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

    // 거래량 이동평균선. scale·dashed로 '드라이업 기준선(50일×0.65)'도 그림 —
    // 5일선(주황)이 이 점선 아래면 드라이업 플래그와 정확히 1:1 대응
    const volMA = (n, color, scale = 1, dashed = false) => {
      const s = cPx.addLineSeries({
        color, lineWidth: 1, priceScaleId: "vol",
        lineStyle: dashed ? 2 : 0,
        priceLineVisible: false, lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      const out = [];
      let sum = 0;
      for (let i = 0; i < h.volume.length; i++) {
        sum += h.volume[i];
        if (i >= n) sum -= h.volume[i - n];
        if (i >= n - 1) out.push({ time: h.time[i], value: sum / n * scale });
      }
      s.setData(out);
    };
    if (mode === "W") volMA(10, "#8b93a7");                    // 10주 평균 (≈50일)
    else { volMA(50, "#8b93a7", 0.65, true); volMA(5, "#f5b041"); }

    // 시간축 동기화
    charts.forEach(c => c.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (!range) return;
      charts.forEach(o => { if (o !== c) o.timeScale().setVisibleLogicalRange(range); });
    }));
    // 기본 시야: 최근 52주 + 여유 15% (52주 시작 수직선이 왼쪽에 보이도록).
    // setVisibleLogicalRange는 autoSize 초기 리사이즈에 덮이므로,
    // barSpacing 옵션을 패널 폭에 맞춰 계산하고 폭이 바뀔 때마다 재적용한다.
    const fitRecent = () => {
      const w = document.getElementById("pane-price")?.clientWidth;
      if (!w) return;
      cPx.timeScale().applyOptions({
        rightOffset: 3,
        barSpacing: Math.max(0.5, w / (look * 1.15)),
      });
    };
    fitRecent();
    fitRO = new ResizeObserver(fitRecent);
    fitRO.observe(document.getElementById("pane-price"));
  }

  async function renderOwnChart() {
    if (!histData) throw new Error("no history");
    hist = histData;
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
      container_id: "tv-chart", symbol: isKR ? `KRX:${disp}` : ticker,
      interval: "D", theme: "dark", style: "1", locale: "kr", autosize: true,
    });
    tvLoaded = true;
  }

  const own = () => document.getElementById("own-chart");
  const tv = () => document.getElementById("tv-chart");
  // KRX 데이터는 TradingView 임베드 위젯이 지원하지 않음 → 사이트 새 탭으로
  const tvExternal = `https://kr.tradingview.com/chart/?symbol=KRX%3A${disp}`;
  document.getElementById("chart-toggle").addEventListener("click", async () => {
    if (isKR) { window.open(tvExternal, "_blank"); return; }
    const showTV = tv().style.display === "none";
    tv().style.display = showTV ? "block" : "none";
    own().style.display = showTV ? "none" : "block";
    document.getElementById("chart-toggle").textContent = showTV ? "자체 차트 전환" : "TradingView 전환";
    if (showTV) { tv().style.height = "580px"; await renderTV(); }
  });

  async function showTVOnly() {
    own().style.display = "none";
    document.getElementById("no-chart").style.display = "none";
    tv().style.display = "block";
    tv().style.height = "580px";
    document.getElementById("chart-toggle").style.display = "none";
    await renderTV();
  }

  if (histData) {
    try { await renderOwnChart(); } catch { await showTVOnly(); }
  } else if (r) {
    // 통과 종목인데 이력 파일만 없음 (예약어 티커 등) → TradingView 자동
    await showTVOnly();
  } else {
    // 미통과 + 이력 없음 → 안내 + [자체 차트 생성 | TradingView로 보기]
    own().style.display = "none";
    document.getElementById("chart-toggle").style.display = "none";
    document.getElementById("no-chart").style.display = "block";
    document.getElementById("btn-tv").addEventListener("click", () => {
      if (isKR) window.open(tvExternal, "_blank"); // KRX는 임베드 불가 → 새 탭
      else showTVOnly();
    });
    document.getElementById("btn-gen").addEventListener("click", async () => {
      const st = document.getElementById("gen-status");
      const btn = document.getElementById("btn-gen");
      btn.disabled = true;
      btn.textContent = "⏳ 생성 중...";
      st.style.color = "var(--accent)";
      st.style.fontWeight = "600";
      const concl = await Refresh.runWorkflow("ondemand.yml", { ticker },
        t => { st.textContent = t; });
      if (concl !== "success") {
        st.style.color = "var(--red)";
        if (concl) st.textContent = `❌ 생성 실패 (${concl}) — 티커가 맞는지 확인하세요`;
        btn.disabled = false;
        btn.textContent = "⚙ 자체 차트 생성";
        return;
      }
      st.style.color = "var(--green)";
      st.textContent = "✅ 생성 완료 — 사이트 배포 대기 중 (1~2분)...";
      const path = `data/${isKR ? "history_kr" : "history"}/${ticker}.json`;
      for (let i = 0; i < 20; i++) {
        await new Promise(w => setTimeout(w, 15000));
        try {
          const res = await fetch(path, { cache: "no-store" });
          if (res.ok) { location.reload(); return; }
        } catch { /* 다음 시도 */ }
        st.textContent = `사이트 배포 대기 중... ${(i + 1) * 15}초`;
      }
      st.textContent = "배포 확인 실패 — 잠시 후 새로고침(F5) 해보세요";
      btn.disabled = false;
    });
  }

  // ---------- 관심종목 토글 ----------
  document.getElementById("watch-btn").addEventListener("click", async e => {
    const on = await Watchlist.toggle(ticker);
    e.target.textContent = on ? "★" : "☆";
  });

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

  // ---------- 티커/종목명 검색 ----------
  let nameMap = null; // {ticker: 이름} — 스크리너가 내보낸 유니버스 사전
  async function loadNames() {
    if (nameMap) return nameMap;
    try {
      nameMap = await fetch(`data/tickers_${App.getMarket()}.json`)
        .then(res => res.ok ? res.json() : {});
    } catch { nameMap = {}; }
    return nameMap;
  }
  const navigate = t => { location.hash = `#/analysis?ticker=${t}`; };
  const suggestEl = () => document.getElementById("suggest");

  async function go() {
    const q = document.getElementById("ticker-input").value.trim();
    if (!q) return;
    suggestEl().style.display = "none";
    const upper = q.toUpperCase();
    const names = await loadNames();
    // 1) 티커 직접 일치
    if (names[upper]) { navigate(upper); return; }
    if (/^\d{6}$/.test(upper)) { // 한국 6자리 코드 → 접미사 해결
      if (names[upper + ".KS"]) { navigate(upper + ".KS"); return; }
      if (names[upper + ".KQ"]) { navigate(upper + ".KQ"); return; }
    }
    // 2) 종목명 부분 일치 검색
    const matches = Object.entries(names).filter(([t, n]) =>
      n.toUpperCase().includes(upper) || t.startsWith(upper)).slice(0, 8);
    if (matches.length === 1) { navigate(matches[0][0]); return; }
    if (matches.length > 1) {
      suggestEl().innerHTML = matches.map(([t, n]) =>
        `<div data-go="${t}"><b>${App.tickerDisp(t)}</b> <span style="color:var(--muted)">${n}</span></div>`).join("");
      suggestEl().style.display = "block";
      suggestEl().querySelectorAll("[data-go]").forEach(el =>
        el.addEventListener("click", () => navigate(el.dataset.go)));
      return;
    }
    navigate(upper); // 사전에 없는 심볼도 시도 (TradingView 폴백)
  }
  document.getElementById("ticker-go").addEventListener("click", go);
  document.getElementById("ticker-input").addEventListener("keydown", e => { if (e.key === "Enter") go(); });

  // ---------- 포지션 사이징 ----------
  const psCalc = () => {
    const acct = +document.getElementById("ps-account").value;
    const risk = +document.getElementById("ps-risk").value / 100;
    const entry = +document.getElementById("ps-entry").value;
    const stop = +document.getElementById("ps-stop").value;
    const out = document.getElementById("ps-result");
    localStorage.setItem(isKR ? "ps-account-kr" : "ps-account", acct);
    if (!acct || !entry || !stop || stop >= entry) {
      out.innerHTML = "<span style='color:var(--muted);font-size:12px'>진입가보다 낮은 손절가를 입력하세요</span>";
      return;
    }
    const riskPerShare = entry - stop;
    const shares = Math.floor((acct * risk) / riskPerShare);
    const cost = shares * entry;
    out.innerHTML = `
      <div class="metric-row"><span>매수 수량 (1R = ${App.money(acct * risk, isKR, 0)})</span><span><b>${shares}주</b></span></div>
      <div class="metric-row"><span>총 매수금액</span><span>${App.money(cost, isKR, 0)} (계좌의 ${App.fmt(cost / acct * 100, 1)}%)</span></div>
      <div class="metric-row"><span>주당 리스크</span><span>${won(riskPerShare)} (${App.fmt(riskPerShare / entry * 100, 1)}%)</span></div>`;
  };
  ["ps-account", "ps-risk", "ps-entry", "ps-stop"].forEach(id =>
    document.getElementById(id).addEventListener("input", psCalc));
  psCalc();
});
