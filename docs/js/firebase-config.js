/* Firebase 설정 — 프로젝트: minervini-trading
   (apiKey는 비밀키가 아닌 공개 식별자. 접근 제어는 Firestore 규칙이 담당) */
window.FIREBASE_CONFIG = {
  apiKey: "AIzaSyB7mk_UGTSoNFzJInmnmqcxzUbvT1euFrU",
  authDomain: "minervini-trading.firebaseapp.com",
  projectId: "minervini-trading",
  storageBucket: "minervini-trading.firebasestorage.app",
  messagingSenderId: "784138978556",
  appId: "1:784138978556:web:f17cc3b3499c0149fc0379",
};

/* 접근 허용 계정 — 이 목록에 없는 Google 계정은 로그인해도 입장 불가.
   (UI 차원 게이트. 개인 데이터 보호는 Firestore 규칙이 담당) */
window.ALLOWED_EMAILS = ["hsk81123@gmail.com"];
