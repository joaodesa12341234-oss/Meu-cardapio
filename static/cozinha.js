"use strict";
/* Tela da cozinha. Todo texto vindo do banco entra por textContent (nunca innerHTML). */
const $ = (id) => document.getElementById(id);
const CSRF = document.querySelector('meta[name="csrf-token"]')?.content || "";
const pad = (n) => String(n).padStart(3, "0");

function el(tag, classe, texto) {
  const e = document.createElement(tag);
  if (classe) e.className = classe;
  if (texto !== undefined) e.textContent = texto;
  return e;
}

const minutosDesde = (iso) => Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 60000));
const ATENCAO_MIN = 15, ATRASADO_MIN = 30;

/* ---------------------------------------------------------- som ---- */
try { $("som").checked = localStorage.getItem("som-cozinha") !== "off"; } catch (_) {}
$("som").addEventListener("change", () => {
  try { localStorage.setItem("som-cozinha", $("som").checked ? "on" : "off"); } catch (_) {}
  if ($("som").checked) bipar();
});
let audioCtx = null;
function bipar() {
  if (!$("som").checked) return;
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === "suspended") audioCtx.resume();
    [0, 0.25, 0.5].forEach((atraso) => {
      const osc = audioCtx.createOscillator(), ganho = audioCtx.createGain();
      osc.frequency.value = 988; ganho.gain.value = 0.2;
      osc.connect(ganho); ganho.connect(audioCtx.destination);
      osc.start(audioCtx.currentTime + atraso); osc.stop(audioCtx.currentTime + atraso + 0.16);
    });
  } catch (_) { /* sem som: o aviso visual continua */ }
}

/* ------------------------------------------------------- pedidos ---- */
let pedidos = [];
let conhecidos = null;   // senhas já vistas (a 1ª carga não apita)

async function carregar() {
  try {
    const r = await fetch("/admin/api/pedidos", { headers: { "X-CSRF-Token": CSRF } });
    if (r.status === 401 || r.status === 403) { location.href = "/admin/login"; return; }
    if (!r.ok) throw new Error();
    pedidos = (await r.json()).filter((p) => p.liberado);        // agendado ainda cedo fica de fora
    $("aviso").hidden = true;
    const senhas = new Set(pedidos.map((p) => p.senha));
    if (conhecidos && pedidos.some((p) => p.status === "novo" && !conhecidos.has(p.senha))) bipar();
    conhecidos = senhas;
    desenhar();
  } catch (_) {
    $("aviso").textContent = "⚠️ Sem conexão com o servidor. Tentando de novo...";
    $("aviso").hidden = false;
  }
}

async function mover(p, novo, botao) {
  botao.disabled = true;
  try {
    const r = await fetch(`/admin/api/pedidos/${encodeURIComponent(p.senha)}/status`, {
      method: "PATCH", headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF },
      body: JSON.stringify({ status: novo }),
    });
    if (!r.ok) throw new Error();
  } catch (_) {
    $("aviso").textContent = "⚠️ Não foi possível mudar o pedido. Confira a conexão.";
    $("aviso").hidden = false;
  }
  await carregar();
}

function ficha(p, proximo, rotulo) {
  const min = minutosDesde(p.hora);
  const classe = p.status === "pronto" ? "" : (p.atrasado || min >= ATRASADO_MIN) ? " atrasado" : min >= ATENCAO_MIN ? " atencao" : "";
  const f = el("article", "ficha" + classe);
  const topo = el("div", "ficha-topo");
  topo.append(el("span", "senha", "#" + pad(p.senha)),
              ...(p.agendado_para ? [el("span", "tipo", "📅 " + new Date(p.agendado_para).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }))] : []),
              el("span", "tipo", p.tipo === "entrega" ? "🛵 Entrega" : p.mesa ? `🪑 Mesa ${p.mesa}` : "🏪 Balcão"),
              el("span", "tempo", `${min} min`));
  f.appendChild(topo);
  if (p.nome) f.appendChild(el("p", "nome", p.nome));
  const ul = el("ul", "itens");
  p.itens.forEach((i) => {
    const li = el("li");
    li.append(el("span", "qtd", `${i.qtd}×`), " " + i.nome);
    if (i.opcoes) li.appendChild(el("small", "opcoes", i.opcoes));
    ul.appendChild(li);
  });
  f.appendChild(ul);
  if (p.observacao) f.appendChild(el("div", "obs", "📝 " + p.observacao));
  if (proximo) {
    const b = el("button", "acao" + (proximo === "pronto" ? " pronto" : ""), rotulo);
    b.type = "button";
    b.addEventListener("click", () => mover(p, proximo, b));
    f.appendChild(b);
  }
  return f;
}

function desenhar() {
  const colunas = [["novo", "fazendo", "▶ Começar a fazer"], ["fazendo", "pronto", "✔ Pronto!"], ["pronto", null, ""]];
  colunas.forEach(([status, proximo, rotulo]) => {
    const lista = pedidos.filter((p) => p.status === status);
    $("cont-" + status).textContent = lista.length;
    const col = $("col-" + status);
    col.replaceChildren();
    if (!lista.length) col.appendChild(el("p", "vazio", status === "novo" ? "Nenhum pedido novo" : "—"));
    lista.forEach((p) => col.appendChild(ficha(p, proximo, rotulo)));
  });
}

/* ------------------------------------------------- relógio / tela ---- */
function relogio() {
  $("relogio").textContent = new Date().toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  if (pedidos.length) desenhar();    // atualiza os minutos e as cores de atraso
}
$("btn-tela").addEventListener("click", () => {
  if (document.fullscreenElement) document.exitFullscreen();
  else document.documentElement.requestFullscreen?.().catch(() => {});
});

let luz = null;   // mantém a tela acesa enquanto a página está aberta (quando o navegador permite)
async function manterTelaAcesa() {
  try { luz = await navigator.wakeLock?.request("screen"); } catch (_) { /* tudo bem */ }
}
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") manterTelaAcesa(); });

carregar();
setInterval(carregar, 5000);
setInterval(relogio, 30000);
relogio();
manterTelaAcesa();
