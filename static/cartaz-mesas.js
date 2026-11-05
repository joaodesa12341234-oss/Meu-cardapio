"use strict";
/* Um cartaz por mesa. O QR de cada mesa abre o cardápio com ?mesa=N. Texto sempre por textContent. */
const campo = document.getElementById("url"), aviso = document.getElementById("aviso"), box = document.getElementById("cartazes");
const mesas = Number(document.body.dataset.mesas) || 0, loja = document.body.dataset.loja;
const el = (tag, classe, texto) => { const e = document.createElement(tag); if (classe) e.className = classe; if (texto !== undefined) e.textContent = texto; return e; };

function atualizar() {
  const base = campo.value.trim();
  aviso.hidden = true;
  box.replaceChildren();
  if (!/^https?:\/\//i.test(base)) { aviso.textContent = "Digite o endereço completo, começando com https://"; aviso.hidden = false; return; }
  if (/\/\/(localhost|127\.|192\.168\.|10\.)/i.test(base)) {
    aviso.textContent = "⚠️ Este endereço só funciona neste computador/rede local. Use o endereço público do site (ou defina PUBLIC_URL).";
    aviso.hidden = false;
  }
  for (let n = 1; n <= mesas; n++) {
    const url = `${base.replace(/\/+$/, "")}/?mesa=${n}`;
    const c = el("main", "cartaz cartaz-mesa");
    c.appendChild(el("h1", "", loja));
    c.appendChild(el("p", "mesa-numero", `Mesa ${n}`));
    c.appendChild(el("p", "chamada", "Peça pelo celular!"));
    const canvas = el("canvas"); canvas.setAttribute("aria-label", `QR code da mesa ${n}`);
    c.appendChild(canvas);
    c.appendChild(el("p", "passo", "Aponte a câmera para o código e faça seu pedido sem sair da mesa"));
    try { desenharQR(canvas, url, 8); } catch (e) { aviso.textContent = e.message; aviso.hidden = false; return; }
    box.appendChild(c);
  }
}
campo.addEventListener("input", atualizar);
document.getElementById("btn-imprimir").addEventListener("click", () => window.print());
window.addEventListener("load", atualizar);
