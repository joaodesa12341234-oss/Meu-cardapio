"use strict";
/* Aba Financeiro. Usa $, el, brl e api de admin-pedidos.js (carregado antes).
   Valores chegam do servidor em centavos. Nada daqui usa innerHTML. */

const reais = (centavos) => brl((centavos || 0) / 100);
const pct = (v) => `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(0)}%`;

/* ---------------------------------------------------------- datas ---- */
const isoLocal = (d) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const dataBr = (iso) => iso.split("-").reverse().join("/");
const somarDias = (d, n) => { const r = new Date(d); r.setDate(r.getDate() + n); return r; };

function periodoDoAtalho(atalho) {
  const hoje = new Date();
  switch (atalho) {
    case "hoje": return [hoje, hoje];
    case "ontem": return [somarDias(hoje, -1), somarDias(hoje, -1)];
    case "7": return [somarDias(hoje, -6), hoje];
    case "30": return [somarDias(hoje, -29), hoje];
    case "mes": return [new Date(hoje.getFullYear(), hoje.getMonth(), 1), hoje];
    case "mes-passado":
      return [new Date(hoje.getFullYear(), hoje.getMonth() - 1, 1), new Date(hoje.getFullYear(), hoje.getMonth(), 0)];
  }
  return [hoje, hoje];
}

function marcarAtalho(atalho) {
  document.querySelectorAll("#fin-atalhos .btn").forEach((b) =>
    b.classList.toggle("btn-vermelho", b.dataset.atalho === atalho));
  document.querySelectorAll("#fin-atalhos .btn").forEach((b) =>
    b.classList.toggle("btn-claro", b.dataset.atalho !== atalho));
}

/* -------------------------------------------------------- carregar ---- */
let finPedido = 0; // ignora respostas antigas se o usuário clicar rápido em vários filtros

async function carregarFinanceiro() {
  const inicio = $("fin-inicio").value, fim = $("fin-fim").value;
  if (!inicio || !fim) return;
  const consulta = `inicio=${encodeURIComponent(inicio)}&fim=${encodeURIComponent(fim)}`;
  $("fin-csv").href = `/admin/api/financeiro.csv?${consulta}`;
  $("fin-xlsx").href = `/admin/api/financeiro.xlsx?${consulta}`;
  $("fin-status").textContent = "Carregando...";
  const minha = ++finPedido;
  try {
    const r = await api(`/admin/api/financeiro?${consulta}`);
    if (minha !== finPedido) return;
    desenhar(r);
    carregarAvaliacoes(consulta, minha);
    $("fin-status").textContent = inicio === fim
      ? `Dia ${dataBr(inicio)}`
      : `De ${dataBr(inicio)} até ${dataBr(fim)}`;
  } catch (err) {
    if (minha === finPedido) $("fin-status").textContent = "⚠️ " + err.message;
  }
}

async function carregarAvaliacoes(consulta, minha) {
  try {
    const a = await api(`/admin/api/avaliacoes?${consulta}`);
    if (minha !== finPedido) return;
    const resumo = $("aval-resumo"), dist = $("aval-distribuicao"), lista = $("aval-lista");
    dist.replaceChildren(); lista.replaceChildren();
    if (!a.total) { resumo.textContent = "Nenhuma avaliação neste período. O cliente avalia pelo link de acompanhamento, depois que o pedido é concluído."; return; }
    resumo.replaceChildren(el("span", "aval-media", `⭐ ${String(a.media).replace(".", ",")}`), document.createTextNode(`  (${a.total} ${a.total === 1 ? "avaliação" : "avaliações"})`));
    for (let n = 5; n >= 1; n--) {
      const q = a.distribuicao[n] || 0;
      const barra = el("div", "aval-barra");
      const trilho = el("span", "trilho"), cheio = el("span", "preenchido");
      cheio.style.width = `${Math.round((q / a.total) * 100)}%`;
      trilho.appendChild(cheio);
      barra.append(el("span", "", `${n}★`), trilho, el("span", "", String(q)));
      dist.appendChild(barra);
    }
    a.ultimas.forEach((u) => {
      const li = el("li");
      li.appendChild(el("span", "estrelas-adm", "★".repeat(u.nota) + "☆".repeat(5 - u.nota)));
      li.appendChild(document.createTextNode(` #${String(u.senha).padStart(3, "0")}${u.nome ? " · " + u.nome : ""}`));
      if (u.comentario) li.appendChild(el("small", "", u.comentario));
      lista.appendChild(li);
    });
  } catch (_) { /* o resto do financeiro já mostra o erro */ }
}

function aplicarAtalho(atalho) {
  const [a, b] = periodoDoAtalho(atalho);
  $("fin-inicio").value = isoLocal(a);
  $("fin-fim").value = isoLocal(b);
  marcarAtalho(atalho);
  carregarFinanceiro();
}

/* --------------------------------------------------------- desenhar ---- */
function desenhar(r) {
  $("kpi-faturamento").textContent = reais(r.faturamento);
  $("kpi-pedidos").textContent = r.concluidos;
  $("kpi-ticket").textContent = reais(r.ticket_medio);
  $("kpi-cancelados").textContent = r.cancelados.pedidos;
  $("kpi-cancelados-valor").textContent = r.cancelados.pedidos ? reais(r.cancelados.total) + " não vendidos" : "";
  $("kpi-andamento").textContent = r.em_andamento.pedidos;
  $("kpi-andamento-valor").textContent = r.em_andamento.pedidos ? reais(r.em_andamento.total) + " a receber" : "";

  const l = r.lucro;
  $("kpi-lucro").textContent = l.receita_com_custo ? reais(l.estimado) : "—";
  $("kpi-lucro-info").textContent = !l.receita_itens ? ""
    : !l.receita_com_custo ? "Informe o custo dos produtos no Cardápio"
    : `margem ${l.margem.toFixed(0)}% · custo informado em ${l.cobertura}% das vendas`;

  const comp = $("kpi-comparacao");
  const ant = r.periodo_anterior.faturamento;
  comp.className = "";
  if (ant > 0) {
    const v = ((r.faturamento - ant) / ant) * 100;
    comp.textContent = `${pct(v)} vs. período anterior (${reais(ant)})`;
    comp.className = v >= 0 ? "sobe" : "desce";
  } else {
    comp.textContent = r.faturamento > 0 ? "Sem vendas no período anterior" : "";
  }

  graficoDias(r.por_dia);
  graficoHoras(r.por_hora);
  blocoTipos(r);
  blocoTop(r.mais_vendidos);
  blocoPagamentos(r);
  blocoBairros(r.por_bairro);
}

function blocoPagamentos(r) {
  const box = $("fin-pagamentos");
  box.replaceChildren();
  const nomes = { pix: "💠 Pix", cartao: "💳 Cartão", dinheiro: "💵 Dinheiro", "": "❔ Não informado" };
  Object.entries(r.por_pagamento).forEach(([chave, v]) => {
    if (chave === "" && v.pedidos === 0) return;
    const parte = r.faturamento > 0 ? (v.total / r.faturamento) * 100 : 0;
    const linha = el("div", "tipo-linha");
    const topo = el("div", "tipo-topo");
    topo.append(el("span", "", nomes[chave]), el("strong", "", reais(v.total)));
    const trilho = el("div", "tipo-trilho");
    const preench = el("div", "tipo-barra pg-" + (chave || "nada"));
    preench.style.width = `${parte}%`;
    trilho.appendChild(preench);
    linha.append(topo, trilho, el("small", "", `${v.pedidos} pedido(s) · ${parte.toFixed(0)}%`));
    box.appendChild(linha);
  });
  $("fin-taxas").textContent = r.taxas_entrega > 0 ? `Inclui ${reais(r.taxas_entrega)} de taxas de entrega.` : "";
}

function blocoBairros(lista) {
  const box = $("fin-bairros");
  box.replaceChildren();
  if (!lista.length) { box.appendChild(el("p", "vazio-fila", "Sem entregas neste período.")); return; }
  const tabela = el("table", "tabela-fin");
  const cab = el("tr");
  ["Bairro", "Pedidos", "Total"].forEach((t) => cab.appendChild(el("th", "", t)));
  tabela.appendChild(cab);
  lista.forEach((b) => {
    const tr = el("tr");
    tr.append(el("td", "", b.bairro), el("td", "num", String(b.pedidos)), el("td", "num", reais(b.total)));
    tabela.appendChild(tr);
  });
  box.appendChild(tabela);
}

/** Barras verticais. A altura é definida por JS (permitido pela CSP, ao contrário de style="" no HTML). */
function barras(container, itens, { rotulo, dica, mostrarRotulo }) {
  container.replaceChildren();
  const max = Math.max(...itens.map((i) => i.valor), 0);
  if (max === 0) {
    container.appendChild(el("p", "vazio-fila", "Sem vendas neste período."));
    return;
  }
  itens.forEach((it, idx) => {
    const col = el("div", "barra-col");
    col.title = dica(it);
    const trilho = el("div", "barra-trilho");
    const b = el("div", "gbarra" + (it.valor === max ? " gbarra-max" : ""));
    b.style.height = `${Math.max((it.valor / max) * 100, it.valor > 0 ? 3 : 0)}%`;
    trilho.appendChild(b);
    col.appendChild(trilho);
    col.appendChild(el("span", "barra-rotulo", mostrarRotulo(idx, itens.length) ? rotulo(it) : ""));
    container.appendChild(col);
  });
}

function graficoDias(dias) {
  const passo = Math.ceil(dias.length / 12); // no máx. ~12 rótulos no eixo
  barras($("fin-grafico-dias"), dias.map((d) => ({ ...d, valor: d.total })), {
    rotulo: (d) => d.data.slice(8) + "/" + d.data.slice(5, 7),
    dica: (d) => `${dataBr(d.data)}: ${reais(d.total)} em ${d.pedidos} pedido(s)`,
    mostrarRotulo: (i) => i % passo === 0,
  });
}

function graficoHoras(horas) {
  barras($("fin-grafico-horas"), horas.map((n, h) => ({ h, valor: n })), {
    rotulo: (x) => String(x.h).padStart(2, "0"),
    dica: (x) => `${String(x.h).padStart(2, "0")}h: ${x.valor} pedido(s)`,
    mostrarRotulo: (i) => i % 2 === 0,
  });
}

function blocoTipos(r) {
  const box = $("fin-tipos");
  box.replaceChildren();
  const total = r.faturamento;
  [["balcao", "🏪 Balcão"], ["entrega", "🛵 Entrega"]].forEach(([chave, nome]) => {
    const t = r.por_tipo[chave];
    const parte = total > 0 ? (t.total / total) * 100 : 0;
    const linha = el("div", "tipo-linha");
    const topo = el("div", "tipo-topo");
    topo.append(el("span", "", nome), el("strong", "", reais(t.total)));
    const trilho = el("div", "tipo-trilho");
    const preench = el("div", "tipo-barra " + chave);
    preench.style.width = `${parte}%`;
    trilho.appendChild(preench);
    linha.append(topo, trilho, el("small", "", `${t.pedidos} pedido(s) · ${parte.toFixed(0)}% do faturamento`));
    box.appendChild(linha);
  });
}

function blocoTop(lista) {
  const box = $("fin-top");
  box.replaceChildren();
  if (!lista.length) {
    box.appendChild(el("p", "vazio-fila", "Sem vendas neste período."));
    return;
  }
  const tabela = el("table", "tabela-fin");
  const cab = el("tr");
  ["#", "Produto", "Qtd", "Total", "Lucro"].forEach((t) => cab.appendChild(el("th", "", t)));
  tabela.appendChild(cab);
  lista.forEach((p, i) => {
    const tr = el("tr");
    tr.append(el("td", "", String(i + 1)), el("td", "", p.nome), el("td", "num", String(p.qtd)), el("td", "num", reais(p.total)),
      el("td", "num", p.lucro == null ? "—" : reais(p.lucro) + (p.custo_completo ? "" : "*")));
    tabela.appendChild(tr);
  });
  box.appendChild(tabela);
  if (lista.some((p) => p.lucro != null && !p.custo_completo))
    box.appendChild(el("p", "fin-nota", "* parte das vendas deste produto não tinha custo informado."));
}

/* ------------------------------------------------------ inicialização ---- */
document.querySelectorAll("#fin-atalhos .btn").forEach((b) =>
  b.addEventListener("click", () => aplicarAtalho(b.dataset.atalho)));
$("fin-aplicar").addEventListener("click", () => {
  if ($("fin-inicio").value > $("fin-fim").value) {
    $("fin-status").textContent = "⚠️ A data inicial não pode ser depois da final.";
    return;
  }
  marcarAtalho(null);
  carregarFinanceiro();
});

let finIniciado = false;
aoAbrirAba.financeiro = () => {
  if (!finIniciado) { finIniciado = true; aplicarAtalho("7"); }
  else carregarFinanceiro(); // reabrir a aba atualiza os números
};
