/* 관심종목 저장 계층 — Firestore(로그인 시) / localStorage(로컬 모드) */
const Watchlist = (() => {
  const KEY = "watchlist-v1";
  let mode = "local";
  let uid = null;
  let cache = null; // 로드된 배열 캐시

  const localLoad = () => {
    try { return JSON.parse(localStorage.getItem(KEY)) || []; } catch { return []; }
  };
  const localSave = arr => localStorage.setItem(KEY, JSON.stringify(arr));
  const docRef = () => firebase.firestore()
    .collection("users").doc(uid).collection("meta").doc("watchlist");

  return {
    setLocal() { mode = "local"; uid = null; cache = null; },

    async setCloud(user) {
      uid = user.uid;
      mode = "cloud";
      cache = null;
      // 로컬에 쌓인 관심종목이 있으면 클라우드에 병합
      const locals = localLoad();
      if (locals.length) {
        const snap = await docRef().get();
        const cur = snap.exists ? (snap.data().tickers || []) : [];
        await docRef().set({ tickers: [...new Set([...cur, ...locals])] });
        localStorage.removeItem(KEY);
      }
    },

    async load() {
      if (cache) return cache;
      if (mode === "cloud") {
        const snap = await docRef().get();
        cache = snap.exists ? (snap.data().tickers || []) : [];
      } else {
        cache = localLoad();
      }
      return cache;
    },

    /** 토글 후 현재 포함 여부를 반환 */
    async toggle(ticker) {
      const list = await this.load();
      const i = list.indexOf(ticker);
      if (i >= 0) list.splice(i, 1);
      else list.push(ticker);
      cache = list;
      if (mode === "cloud") await docRef().set({ tickers: list });
      else localSave(list);
      return i < 0;
    },
  };
})();
