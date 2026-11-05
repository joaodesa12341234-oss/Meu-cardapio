"use strict";
/* Instalar o cardápio como app: registra o service worker e mostra o botão "Instalar". */
(() => {
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});

  const botao = document.getElementById("btn-instalar");
  const dica = document.getElementById("dica-instalar");
  if (!botao) return;
  const jaInstalado = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  if (jaInstalado) return;

  let convite = null;
  window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); convite = e; botao.hidden = false; });
  window.addEventListener("appinstalled", () => { botao.hidden = true; if (dica) dica.hidden = true; });
  botao.addEventListener("click", async () => {
    if (convite) { convite.prompt(); await convite.userChoice.catch(() => {}); convite = null; botao.hidden = true; }
    else if (dica) dica.hidden = !dica.hidden;
  });

  // iPhone/iPad não têm o convite automático: mostramos o passo a passo
  const ios = /iphone|ipad|ipod/i.test(navigator.userAgent) && !window.MSStream;
  if (ios) botao.hidden = false;
})();
