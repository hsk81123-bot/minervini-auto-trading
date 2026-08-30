/* Firebase 초기화 + Google 로그인 게이트 (compat SDK)
   - FIREBASE_CONFIG 없으면: 게이트 없이 로컬 모드
   - 있으면: 로그인 + ALLOWED_EMAILS 검증을 통과해야 앱 진입 */
const Cloud = (() => {
  const VER = "10.14.1";

  const loadScript = src => new Promise((ok, err) => {
    const s = document.createElement("script");
    s.src = src; s.onload = ok; s.onerror = err;
    document.head.appendChild(s);
  });

  const gateEl = () => document.getElementById("gate");
  function showGate(msg, showLogin) {
    gateEl().hidden = false;
    document.getElementById("gate-msg").textContent = msg || "";
    document.getElementById("gate-login").style.display = showLogin ? "" : "none";
  }
  function hideGate() { gateEl().hidden = true; }

  function renderSlot(user) {
    const slot = document.getElementById("auth-slot");
    if (!slot) return;
    slot.innerHTML = user
      ? `<div class="auth-user" title="${user.email}">☁ ${user.email}</div>
         <button id="auth-btn" class="auth-btn">로그아웃</button>`
      : "";
    const b = document.getElementById("auth-btn");
    if (b) b.addEventListener("click", () => firebase.auth().signOut());
  }

  async function init() {
    if (!window.FIREBASE_CONFIG) { hideGate(); return; } // 로컬 모드
    showGate("인증 확인 중...", false);
    for (const f of ["app", "auth", "firestore"])
      await loadScript(
        `https://www.gstatic.com/firebasejs/${VER}/firebase-${f}-compat.js`);
    firebase.initializeApp(window.FIREBASE_CONFIG);

    document.getElementById("gate-login").addEventListener("click", () =>
      firebase.auth().signInWithPopup(new firebase.auth.GoogleAuthProvider())
        .catch(e => showGate("로그인 실패: " + e.message, true)));

    let first = true;
    let deniedEmail = null;
    firebase.auth().onAuthStateChanged(async user => {
      const allowed = window.ALLOWED_EMAILS || [];
      if (user && allowed.length && !allowed.includes(user.email)) {
        deniedEmail = user.email;
        await firebase.auth().signOut(); // null 이벤트가 다시 옴
        return;
      }
      if (user) {
        await Journal.setCloud(user);
        await Watchlist.setCloud(user);
        hideGate();
      } else {
        Journal.setLocal();
        Watchlist.setLocal();
        showGate(deniedEmail
          ? `접근 권한이 없는 계정입니다: ${deniedEmail}`
          : "승인된 Google 계정으로 로그인하세요", true);
        deniedEmail = null;
      }
      renderSlot(user);
      // 시작 직후의 '비로그인' 이벤트는 상태 변화가 아니므로 재렌더 생략
      if (first && !user) { first = false; return; }
      first = false;
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
  }

  return { init };
})();
