/* 라우터 + 공통 유틸 */
const App = (() => {
  let screenData = null; // results.json 캐시

  async function loadResults() {
    if (screenData) return screenData;
    const res = await fetch("data/results.json");
    screenData = await res.json();
    const el = document.getElementById("data-date");
    if (el) el.textContent = `데이터: ${screenData.data_date}\n(${screenData.universe})`;
    return screenData;
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
  // NASDAQ 디렉토리 이름의 "- Common Stock" 류 접미사 제거
  const cleanName = s => (s || "").split(" - ")[0]
    .replace(/\s*(Class [A-Z]\s*)?(Common Stock|Ordinary Shares?|Common Shares?).*$/i, "")
    .trim().replace(/,$/, "");

  return { start, register, loadResults, fmt, signCls, cleanName };
})();
