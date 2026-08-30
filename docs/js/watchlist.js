/* 관심종목 저장 계층 — Firestore(로그인 시) / localStorage(로컬 모드)
   문서 구조: { tickers: [..], notes: { ticker: "메모" } } (구버전 배열 자동 마이그레이션) */
const Watchlist = (() => {
  const KEY = "watchlist-v1";
  let mode = "local";
  let uid = null;
  let cache = null; // {tickers, notes}

  const normalize = d => {
    if (Array.isArray(d)) return { tickers: d, notes: {} }; // 구버전
    return { tickers: d?.tickers || [], notes: d?.notes || {} };
  };
  const localLoad = () => {
    try { return normalize(JSON.parse(localStorage.getItem(KEY))); }
    catch { return { tickers: [], notes: {} }; }
  };
  const localSave = doc => localStorage.setItem(KEY, JSON.stringify(doc));
  const docRef = () => firebase.firestore()
    .collection("users").doc(uid).collection("meta").doc("watchlist");

  async function loadDoc() {
    if (cache) return cache;
    if (mode === "cloud") {
      const snap = await docRef().get();
      cache = normalize(snap.exists ? snap.data() : null);
    } else {
      cache = localLoad();
    }
    return cache;
  }

  async function saveDoc() {
    if (mode === "cloud") await docRef().set(cache);
    else localSave(cache);
  }

  return {
    setLocal() { mode = "local"; uid = null; cache = null; },

    async setCloud(user) {
      uid = user.uid;
      mode = "cloud";
      cache = null;
      // 로컬에 쌓인 관심종목/노트가 있으면 클라우드에 병합
      const locals = localLoad();
      if (locals.tickers.length || Object.keys(locals.notes).length) {
        const snap = await docRef().get();
        const cur = normalize(snap.exists ? snap.data() : null);
        await docRef().set({
          tickers: [...new Set([...cur.tickers, ...locals.tickers])],
          notes: { ...locals.notes, ...cur.notes }, // 클라우드 우선
        });
        localStorage.removeItem(KEY);
      }
    },

    /** 관심종목 티커 배열 */
    async load() { return (await loadDoc()).tickers; },

    /** {ticker: 메모} 맵 */
    async notes() { return (await loadDoc()).notes; },

    async setNote(ticker, text) {
      await loadDoc();
      if (text && text.trim()) cache.notes[ticker] = text.trim();
      else delete cache.notes[ticker];
      await saveDoc();
    },

    /** 토글 후 현재 포함 여부를 반환 (제거 시 노트도 삭제) */
    async toggle(ticker) {
      await loadDoc();
      const i = cache.tickers.indexOf(ticker);
      if (i >= 0) {
        cache.tickers.splice(i, 1);
        delete cache.notes[ticker];
      } else {
        cache.tickers.push(ticker);
      }
      await saveDoc();
      return i < 0;
    },
  };
})();
