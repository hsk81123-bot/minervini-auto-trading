/* Firebase 초기화 + Google 로그인 (compat SDK, 설정 없으면 아무것도 안 함) */
const Cloud = (() => {
  const VER = "10.14.1";

  const loadScript = src => new Promise((ok, err) => {
    const s = document.createElement("script");
    s.src = src; s.onload = ok; s.onerror = err;
    document.head.appendChild(s);
  });

  function renderSlot(user) {
    const slot = document.getElementById("auth-slot");
    if (!slot) return;
    slot.innerHTML = user
      ? `<div class="auth-user" title="${user.email}">☁ ${user.email}</div>
         <button id="auth-btn" class="auth-btn">로그아웃</button>`
      : `<button id="auth-btn" class="auth-btn">Google 로그인<br><span style="font-weight:400">(매매기록 동기화)</span></button>`;
    document.getElementById("auth-btn").addEventListener("click", () => {
      if (user) firebase.auth().signOut();
      else firebase.auth().signInWithPopup(new firebase.auth.GoogleAuthProvider())
        .catch(e => alert("로그인 실패: " + e.message));
    });
  }

  async function init() {
    if (!window.FIREBASE_CONFIG) return; // 로컬 모드
    for (const f of ["app", "auth", "firestore"])
      await loadScript(
        `https://www.gstatic.com/firebasejs/${VER}/firebase-${f}-compat.js`);
    firebase.initializeApp(window.FIREBASE_CONFIG);
    let first = true;
    firebase.auth().onAuthStateChanged(async user => {
      if (user) await Journal.setCloud(user);
      else Journal.setLocal();
      renderSlot(user);
      // 시작 직후의 '비로그인' 이벤트는 상태 변화가 아니므로 재렌더 생략
      // (초기 렌더와 겹치면 차트가 이중으로 그려짐)
      if (first && !user) { first = false; return; }
      first = false;
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
  }

  return { init };
})();
