"use strict";
const campo = document.getElementById("url"), aviso = document.getElementById("aviso");
const canvas = document.getElementById("qr"), texto = document.getElementById("url-texto");

function atualizar() {
  const url = campo.value.trim();
  aviso.hidden = true;
  if (!/^https?:\/\//i.test(url)) { aviso.textContent = "Digite o endereço completo, começando com https://"; aviso.hidden = false; return; }
  if (/\/\/(localhost|127\.|192\.168\.|10\.)/i.test(url)) {
    aviso.textContent = "⚠️ Este endereço só funciona neste computador/rede local. Para os clientes escanearem de fora, use o endereço público do site (ou defina PUBLIC_URL).";
    aviso.hidden = false;
  }
  try { desenharQR(canvas, url, 8); texto.textContent = url; }
  catch (e) { aviso.textContent = e.message; aviso.hidden = false; }
}
campo.addEventListener("input", atualizar);
document.getElementById("btn-imprimir").addEventListener("click", () => window.print());
window.addEventListener("load", atualizar);
