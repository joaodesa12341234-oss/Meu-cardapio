"use strict";
/* Página pública de acompanhamento. Texto sempre por textContent. */
const token = document.body.dataset.token;
const $ = (id) => document.getElementById(id);
const brl = (v) => Number(v || 0).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
const el = (tag, classe, texto) => { const e = document.createElement(tag); if (classe) e.className = classe; if (texto !== undefined) e.textContent = texto; return e; };

const FRASES = {
  novo: "Recebemos o seu pedido! 🙌",
  fazendo: "Estamos preparando o seu pedido 👨‍🍳",
  pronto: { balcao: "Pronto! Pode vir retirar 🍔", entrega: "Pronto! Já já sai para entrega 🛵", mesa: "Pronto! Já vai para a sua mesa 🍔" },
  saiu: "Saiu para entrega! 🛵",
  concluido: { balcao: "Pedido retirado. Bom apetite! 😋", entrega: "Pedido entregue. Bom apetite! 😋", mesa: "Pedido servido. Bom apetite! 😋" },
  cancelado: "Este pedido foi cancelado.",
};

let timer = null;
let notaEscolhida = 0, avaliado = false;

function pintarEstrelas() {
  document.querySelectorAll("#estrelas button").forEach((b) => {
    const n = Number(b.dataset.nota);
    b.textContent = n <= notaEscolhida ? "★" : "☆";
    b.classList.toggle("cheia", n <= notaEscolhida);
    b.setAttribute("aria-checked", String(n === notaEscolhida));
  });
  $("aval-enviar").disabled = avaliado || notaEscolhida === 0;
}

function montarEstrelas() {
  const box = $("estrelas");
  if (box.children.length) return;
  for (let n = 1; n <= 5; n++) {
    const b = el("button", "estrela", "☆");
    b.type = "button"; b.dataset.nota = n; b.setAttribute("role", "radio"); b.setAttribute("aria-label", `${n} ${n === 1 ? "estrela" : "estrelas"}`);
    b.addEventListener("click", () => { if (avaliado) return; notaEscolhida = n; pintarEstrelas(); });
    box.appendChild(b);
  }
}

function desenharAvaliacao(d) {
  const secao = $("avaliar");
  if (d.status !== "concluido") { secao.hidden = true; return; }
  montarEstrelas();
  secao.hidden = false;
  if (d.nota != null) {                       // já avaliou (agora ou numa visita anterior)
    avaliado = true; notaEscolhida = d.nota;
    $("aval-comentario").hidden = true; $("aval-enviar").hidden = true;
    $("aval-msg").textContent = "Obrigado pela sua avaliação! 💛";
  }
  pintarEstrelas();
}

$("aval-enviar").addEventListener("click", async () => {
  if (avaliado || !notaEscolhida) return;
  $("aval-enviar").disabled = true;
  try {
    const r = await fetch(`/api/acompanhar/${encodeURIComponent(token)}/avaliar`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nota: notaEscolhida, comentario: $("aval-comentario").value }),
    });
    const j = await r.json().catch(() => ({}));
    if (r.ok || r.status === 409) {
      avaliado = true; $("aval-comentario").hidden = true; $("aval-enviar").hidden = true;
      $("aval-msg").textContent = r.ok ? "Obrigado pela sua avaliação! 💛" : (j.erro || "Este pedido já foi avaliado.");
      pintarEstrelas();
    } else { $("aval-msg").textContent = j.erro || "Não foi possível enviar."; $("aval-enviar").disabled = false; }
  } catch (_) { $("aval-msg").textContent = "Sem conexão. Tente de novo."; $("aval-enviar").disabled = false; }
});

function desenhar(d) {
  $("senha").textContent = "#" + String(d.senha).padStart(3, "0");
  const frase = FRASES[d.status];
  const texto = typeof frase === "object" ? frase[d.mesa ? "mesa" : d.tipo] : frase;
  const sit = $("situacao");
  sit.textContent = d.agendado_para && d.status === "novo"
    ? `Pedido agendado para ${new Date(d.agendado_para).toLocaleString("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })} 📅`
    : texto;
  if (d.previsao_min && (d.status === "novo" || d.status === "fazendo") && !(d.agendado_para && d.status === "novo" && new Date(d.agendado_para) > new Date())) {
    sit.textContent += ` Previsão: cerca de ${d.previsao_min} min ⏱`;
  }
  sit.className = "situacao " + (d.status === "cancelado" ? "erro" : d.status === "concluido" ? "ok" : "");

  const lista = $("etapas");
  lista.replaceChildren();
  if (d.status !== "cancelado") {
    const ids = d.etapas.map((e) => e.id);
    const posAtual = ids.indexOf(d.status);
    d.etapas.forEach((e, i) => {
      const li = el("li", i < posAtual || d.status === "concluido" ? "feita" : i === posAtual ? "atual" : "", e.nome);
      lista.appendChild(li);
    });
  }
  const itens = $("itens");
  itens.replaceChildren();
  d.itens.forEach((i) => {
    const li = el("li", "", `${i.qtd}× ${i.nome}`);
    if (i.opcoes) li.appendChild(el("small", "", i.opcoes));
    itens.appendChild(li);
  });
  const descontos = [];
  if (d.desconto > 0) descontos.push(`cupom ${d.cupom}: -${brl(d.desconto)}`);
  if (d.fidelidade > 0) descontos.push(`fidelidade: -${brl(d.fidelidade)}`);
  $("total").textCont> descontos.length ? `${brl(d.total)} (${descontos.join(", ")})` : brl(d.total);
  desenharAvaliacao(d);
  if (d.status === "concluido" || d.status === "cancelado") {
    clearInterval(timer);
    $("rodape").textContent = "Obrigado pela preferência!";
  }
}

async function atualizar() {
  try {
    const r = await fetch(`/api/acompanhar/${encodeURIComponent(token)}`, { cache: "no-store" });
    if (r.status === 404) { $("situacao").textContent = "Pedido não encontrado."; clearInterval(timer); return; }
    if (r.ok) desenhar(await r.json());
  } catch (_) { /* sem rede agora: tenta no próximo ciclo */ }
}
atualizar();
timer = setInterval(atualizar, 10000);
