/* Firebase 설정 — 콘솔(프로젝트 설정 → 내 앱 → 웹)에서 받은 firebaseConfig로 교체.
   null이면 클라우드 동기화 없이 localStorage 로컬 모드로 동작한다. */
window.FIREBASE_CONFIG = null;
/* 예시:
window.FIREBASE_CONFIG = {
  apiKey: "AIza....",
  authDomain: "xxx.firebaseapp.com",
  projectId: "xxx",
  storageBucket: "xxx.appspot.com",
  messagingSenderId: "...",
  appId: "1:...:web:...",
};
*/
