/* 라우터 + 공통 유틸 + 시장(미국/한국) 상태 */
const App = (() => {
  const cache = {}; // market -> results.json
  let market = localStorage.getItem("market") || "us";

  const getMarket = () => market;
  function setMarket(m) {
    market = m;
    localStorage.setItem("market", m);
  }

  async function loadResults() {
    if (!cache[market]) {
      const file = market === "kr" ? "data/results_kr.json" : "data/results.json";
      const res = await fetch(file);
      if (!res.ok) throw new Error(`데이터 파일 없음 (${file}) — 스크리너 실행 필요`);
      cache[market] = await res.json();
    }
    const d = cache[market];
    const el = document.getElementById("data-date");
    if (el) el.textContent = `데이터: ${d.data_date}\n(${d.universe})`;
    return d;
  }

  function parseHash() {
    const hash = location.hash.replace(/^#\/?/, "") || "filter";
    const [path, query] = hash.split("?");
    const params = new URLSearchParams(query || "");
    return { path: path || "filter", params };
  }

  const routes = {};
  function register(name, renderFn) { routes[name] = renderFn; }

  async function render() {
    const { path, params } = parseHash();
    const page = document.getElementById("page");
    document.querySelectorAll(".sidebar a").forEach(a =>
      a.classList.toggle("active", a.dataset.route === path));
    const fn = routes[path] || routes.filter;
    page.innerHTML = "<div class='empty'>로딩 중...</div>";
    try {
      await fn(page, params);
    } catch (e) {
      page.innerHTML = `<div class='empty'>오류: ${e.message}</div>`;
      console.error(e);
    }
  }

  function start() {
    window.addEventListener("hashchange", render);
    render();
  }

  // ---- 유틸 ----
  const fmt = (n, d = 2) =>
    n == null || isNaN(n) ? "-" : Number(n).toLocaleString("en-US", {
      minimumFractionDigits: d, maximumFractionDigits: d });
  const signCls = n => (n > 0 ? "pos" : n < 0 ? "neg" : "");
  // 통화 표시: 한국 종목은 ₩ 정수, 미국은 $ 소수 2자리
  const money = (n, isKR, d) =>
    isKR ? "₩" + fmt(n, d ?? 0) : "$" + fmt(n, d ?? 2);
  // 한국 티커(005930.KS)의 표시용 코드
  const tickerDisp = t => (t || "").replace(/\.(KS|KQ)$/, "");
  const isKRTicker = t => /\.(KS|KQ)$/.test(t || "");
  // NASDAQ 디렉토리 이름의 "- Common Stock" 류 접미사 제거
  const cleanName = s => (s || "").split(" - ")[0]
    .replace(/\s*(Class [A-Z]\s*)?(Common Stock|Ordinary Shares?|Common Shares?).*$/i, "")
    .trim().replace(/,$/, "");

  return { start, register, loadResults, fmt, signCls, money,
           tickerDisp, isKRTicker, cleanName, getMarket, setMarket };
})();
