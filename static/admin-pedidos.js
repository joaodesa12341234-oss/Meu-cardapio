"use strict";
/* Painel do dono: pedidos (kanban) e gestão do cardápio.
   Todo texto vindo do banco (nome, endereço, observação...) entra no HTML por textContent,
   nunca por innerHTML: assim nada digitado por um cliente vira código na tela do painel. */

const $ = (id) => document.getElementById(id);
const CSRF = document.querySelector('meta[name="csrf-token"]')?.content || "";

const brl = (v) => Number(v || 0).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
const horaCurta = (iso) => new Date(iso).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
const pad = (n) => String(n).padStart(3, "0");

function haQuanto(iso) {
  const min = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (min < 1) return "agora";
  if (min < 60) return `há ${min} min`;
  if (min < 1440) return `há ${Math.floor(min / 60)} h ${min % 60} min`;
  return `há ${Math.floor(min / 1440)} d ${Math.floor((min % 1440) / 60)} h`;
}

/** (75) 98765-4321 a partir de qualquer formato (com ou sem 55). */
function telBonito(t) {
  let d = String(t || "").replace(/\D/g, "");
  if (d.length > 11 && d.startsWith("55")) d = d.slice(2);
  if (d.length === 11) return `(${d.slice(0, 2)}) ${d.slice(2, 7)}-${d.slice(7)}`;
  if (d.length === 10) return `(${d.slice(0, 2)}) ${d.slice(2, 6)}-${d.slice(6)}`;
  return t;
}

/** Cria um elemento com classe e texto (texto sempre como texto). */
function el(tag, classe, texto) {
  const e = document.createElement(tag);
  if (classe) e.className = classe;
  if (texto !== undefined) e.textContent = texto;
  return e;
}

async function api(url, metodo = "GET", corpo = null) {
  const opcoes = { method: metodo, headers: { "X-CSRF-Token": CSRF } };
  if (corpo instanceof FormData) {
    opcoes.body = corpo; // o navegador define o Content-Type (com boundary) sozinho
  } else if (corpo) {
    opcoes.headers["Content-Type"] = "application/json";
    opcoes.body = JSON.stringify(corpo);
  }
  const resp = await fetch(url, opcoes);
  if (resp.status === 401) {
    location.href = "/admin/login";
    throw new Error("Sua sessão expirou. Entre de novo.");
  }
  const dados = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(dados.erro || "Ocorreu um erro no servidor.");
  return dados;
}

/* ------------------------------------------------------------ abas ---- */
const ABAS = ["pedidos", "cardapio", "loja", "financeiro", "historico"];
const aoAbrirAba = {}; // outras partes do painel registram aqui o que fazer ao abrir sua aba

function alternarAba(aba) {
  if (!ABAS.includes(aba) || !$("aba-" + aba)) aba = "pedidos"; // aba inexistente ou sem permissão
  ABAS.forEach((nome) => {
    const secao = $("aba-" + nome), botao = $("btn-tab-" + nome);
    if (secao) secao.hidden = nome !== aba;
    if (botao) botao.classList.toggle("ativa", nome === aba);
  });
  try { history.replaceState(null, "", "#" + aba); } catch (_) {}
  if (aba === "cardapio") carregarGestaoCardapio();
  if (aoAbrirAba[aba]) aoAbrirAba[aba]();
}

/* ------------------------------------------------------- aviso sonoro ---- */
try { $("som").checked = localStorage.getItem("som-pedidos") !== "off"; } catch (_) {}
$("som").addEventListener("change", () => {
  try { localStorage.setItem("som-pedidos", $("som").checked ? "on" : "off"); } catch (_) {}
  if ($("som").checked) bipar(); // toca uma vez, para o navegador liberar o som
});

let audioCtx = null;
function bipar() {
  if (!$("som").checked) return;
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === "suspended") audioCtx.resume();
    [0, 0.3].forEach((atraso) => {
      const osc = audioCtx.createOscillator();
      const ganho = audioCtx.createGain();
      osc.frequency.value = 880;
      ganho.gain.value = 0.15;
      osc.connect(ganho);
      ganho.connect(audioCtx.destination);
      osc.start(audioCtx.currentTime + atraso);
      osc.stop(audioCtx.currentTime + atraso + 0.18);
    });
  } catch (_) { /* sem som: o aviso visual continua */ }
}

/* ----------------------------------------------------------- pedidos ---- */
const ABAS_STATUS = [
  { id: "novo", vazio: "Nenhum pedido novo." },
  { id: "fazendo", vazio: "Nada sendo preparado agora." },
  { id: "pronto", vazio: "Nenhum pedido pronto." },
  { id: "saiu", vazio: "Nenhuma entrega." },
];
const TITULO_BASE = document.title;
const NOMES_PAGAMENTO = { pix: "Pix", cartao: "Cartão", dinheiro: "Dinheiro" };
let pedidos = [];
let senhasVistas = null; // para apitar só pelos pedidos que chegam depois da primeira carga
let arrastando = false;  // não redesenha a tela no meio de um arrastar

async function carregarPedidos() {
  let lista;
  try {
    lista = await api("/admin/api/pedidos");
  } catch (_) {
    $("atualizado").textContent = "⚠️ Sem conexão com o servidor. Tentando de novo...";
    return;
  }
  pedidos = lista;

  const novos = pedidos.filter((p) => p.status === "novo" && p.liberado);   // agendado ainda cedo não apita
  if (senhasVistas === null) {
    senhasVistas = new Set(pedidos.filter((p) => p.liberado).map((p) => p.senha));
  } else {
    const chegaram = novos.filter((p) => !senhasVistas.has(p.senha));
    pedidos.filter((p) => p.liberado).forEach((p) => senhasVistas.add(p.senha));
    if (chegaram.length) bipar();
  }

  if (!arrastando) renderPedidos();
  verificarAntigos();
  document.title = (novos.length ? `(${novos.length}) ` : "") + TITULO_BASE;
  $("atualizado").textContent = "Atualizado às " + horaCurta(new Date().toISOString());
}

/* aviso de pedidos esquecidos na fila (só administrador: marcar como entregue mexe no financeiro) */
async function verificarAntigos() {
  const banner = $("banner-antigos");
  if (!banner) return;
  try {
    const r = await api("/admin/api/pedidos/antigos");
    banner.hidden = r.quantidade === 0;
    if (r.quantidade) {
      $("banner-antigos-texto").textContent =
        `⚠️ ${r.quantidade} pedido(s) na fila há mais de ${r.horas} horas. Provavelmente já foram resolvidos:`;
    }
  } catch (_) { /* tenta de novo no próximo ciclo */ }
}

async function encerrarAntigos(destino) {
  const nome = destino === "concluido" ? "ENTREGUES (entram no faturamento)" : "CANCELADOS";
  if (!confirm(`Marcar todos os pedidos antigos como ${nome}? Os clientes não serão avisados.`)) return;
  try {
    const r = await api("/admin/api/pedidos/encerrar-antigos", "POST", { destino });
    alert(`${r.encerrados} pedido(s) encerrado(s).`);
  } catch (err) { alert(err.message); }
  await carregarPedidos();
}
$("btn-antigos-concluir")?.addEventListener("click", () => encerrarAntigos("concluido"));
$("btn-antigos-cancelar")?.addEventListener("click", () => encerrarAntigos("cancelado"));

function renderPedidos() {
  const contagem = { novo: 0, fazendo: 0, pronto: 0, saiu: 0 };
  pedidos.forEach((p) => { if (p.status in contagem && p.liberado) contagem[p.status]++; });
  renderAgendados();

  const cont = $("cont-novos");
  cont.textContent = contagem.novo;
  cont.hidden = contagem.novo === 0;

  ABAS_STATUS.forEach((aba) => {
    const fila = $("fila-" + aba.id);
    fila.replaceChildren();
    const lista = pedidos.filter((p) => p.status === aba.id && p.liberado);
    if (lista.length === 0) fila.appendChild(el("p", "vazio-fila", aba.vazio));
    else lista.forEach((p) => fila.appendChild(cartaoPedido(p)));
  });
}

/** Pedidos marcados para mais tarde: ficam numa faixa própria até chegar perto do horário. */
function renderAgendados() {
  const futuros = pedidos.filter((p) => !p.liberado)
    .sort((a, b) => a.agendado_para.localeCompare(b.agendado_para));
  $("agendados").hidden = futuros.length === 0;
  const box = $("lista-agendados");
  box.replaceChildren();
  futuros.forEach((p) => {
    const quando = new Date(p.agendado_para);
    const hoje = new Date().toDateString() === quando.toDateString();
    const linha = el("div", "agendado");
    linha.append(
      el("strong", "", `${hoje ? "Hoje" : quando.toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit" })} ${horaCurta(p.agendado_para)}`),
      el("span", "", `#${pad(p.senha)} · ${p.tipo === "entrega" ? "Entrega" : "Balcão"} · ${p.nome} · ${brl(p.total)}`),
      el("small", "", p.itens.map((i) => `${i.qtd}× ${i.nome}${i.opcoes ? " (" + i.opcoes + ")" : ""}`).join(", ")),
    );
    const iniciar = el("button", "btn btn-claro", "Começar já");
    iniciar.type = "button";
    iniciar.addEventListener("click", () => mudarStatus(p, "fazendo"));
    const cancelar = el("button", "btn btn-perigo", "Cancelar");
    cancelar.type = "button";
    cancelar.addEventListener("click", () => { if (confirm(`Cancelar o pedido #${pad(p.senha)}?`)) mudarStatus(p, "cancelado"); });
    linha.append(iniciar, cancelar);
    box.appendChild(linha);
  });
}

function linhaInfo(emoji, conteudo) {
  const div = el("div");
  div.append(emoji + " ", conteudo);
  return div;
}

function cartaoPedido(p) {
  const entrega = p.tipo === "entrega";
  const card = el("article", "pedido cartao-pedido" + (entrega ? " entrega" : "") + (p.atrasado ? " atrasado" : ""));
  card.draggable = true;
  card.dataset.senha = String(p.senha);

  const topo = el("div", "topo-ped");
  topo.append(
    el("span", "senha-ped", "#" + pad(p.senha)),
    el("span", "tag" + (p.mesa ? " tag-mesa" : ""), entrega ? "Entrega" : p.mesa ? `🪑 Mesa ${p.mesa}` : "Balcão"),
    el("span", "tempo", `${horaCurta(p.hora)} • ${haQuanto(p.hora)}`),
    ...(p.atrasado ? [el("span", "tag-atraso", "⏰ Atrasado")] : []),
    ...(p.agendado_para ? [el("span", "tag-agendado", "📅 " + horaCurta(p.agendado_para))] : []),
  );
  card.appendChild(topo);

  const cli = el("div", "info-cli");
  if (p.nome) cli.appendChild(linhaInfo("👤", p.nome));
  if (p.telefone) {
    const digitos = p.telefone.replace(/\D/g, "");
    const link = el("a", "", telBonito(p.telefone));
    link.href = "https://wa.me/" + (digitos.length <= 11 ? "55" : "") + digitos;
    link.target = "_blank";
    link.rel = "noopener";
    cli.appendChild(linhaInfo("📞", link));
  }
  if (entrega && p.endereco) {
    if (p.bairro) cli.appendChild(linhaInfo("🏘️", el("strong", "", p.bairro)));
    const mapa = el("a", "", p.endereco);
    mapa.href = "https://www.google.com/maps/search/?api=1&query=" + encodeURIComponent(p.endereco);
    mapa.target = "_blank";
    mapa.rel = "noopener";
    cli.appendChild(linhaInfo("📍", mapa));
  }
  card.appendChild(cli);

  const ul = el("ul", "itens");
  p.itens.forEach((i) => {
    const li = el("li", "", `${i.qtd}× ${i.nome}`);
    if (i.opcoes) li.appendChild(el("small", "det-opcoes", i.opcoes));
    ul.appendChild(li);
  });
  card.appendChild(ul);

  if (p.observacao) card.appendChild(el("div", "obs-ped", "📝 " + p.observacao));
  if (p.pagamento) {
    let txt = "💳 " + (NOMES_PAGAMENTO[p.pagamento] || p.pagamento);
    if (p.pagamento === "dinheiro" && p.troco_para > 0) {
      txt += ` · troco para ${brl(p.troco_para)} (devolver ${brl(p.troco_para - p.total)})`;
    }
    card.appendChild(el("div", "pg-ped", txt));
  }
  if (p.fidelidade > 0) card.appendChild(el("div", "taxa-ped", `🎁 fidelidade: -${brl(p.fidelidade)}`));
  if (p.desconto > 0) card.appendChild(el("div", "taxa-ped", `🏷 cupom ${p.cupom}: -${brl(p.desconto)}`));
  if (p.taxa_entrega > 0) card.appendChild(el("div", "taxa-ped", `🛵 inclui taxa de entrega ${brl(p.taxa_entrega)}`));

  const rodape = el("div", "rodape-ped");
  rodape.append(el("strong", "preco", brl(p.total)), botoesPedido(p, entrega));
  card.appendChild(rodape);
  return card;
}

function botoesPedido(p, entrega) {
  const box = el("div", "acoes-ped");
  const btn = (texto, classe, acao) => {
    const b = el("button", "btn " + classe, texto);
    b.type = "button";
    b.addEventListener("click", acao);
    box.appendChild(b);
  };

  if (p.status === "novo") btn("Começar", "btn-vermelho", () => mudarStatus(p, "fazendo"));
  if (p.status === "fazendo") btn("Pronto", "btn-vermelho", () => mudarStatus(p, "pronto"));
  if (p.status === "pronto") {
    if (entrega) btn("Saiu p/ Entrega", "btn-vermelho", () => mudarStatus(p, "saiu"));
    else btn("Entregue", "btn-vermelho", () => mudarStatus(p, "concluido"));
  }
  if (p.status === "saiu") btn("Entregue", "btn-vermelho", () => mudarStatus(p, "concluido"));

  const anterior = { fazendo: "novo", pronto: "fazendo", saiu: "pronto" }[p.status];
  if (anterior) btn("← Voltar", "btn-claro", () => mudarStatus(p, anterior));

  const comanda = el("a", "btn btn-claro btn-link", "🖨 Comanda");
  comanda.href = `/admin/pedidos/${encodeURIComponent(p.senha)}/comanda`;
  comanda.target = "_blank";
  comanda.rel = "noopener";
  box.appendChild(comanda);

  btn("Cancelar", "btn-perigo", () => {
    if (confirm(`Cancelar o pedido #${pad(p.senha)}?`)) mudarStatus(p, "cancelado");
  });
  return box;
}

async function mudarStatus(p, novo) {
  try {
    await api(`/admin/api/pedidos/${encodeURIComponent(p.senha)}/status`, "PATCH", { status: novo });
  } catch (err) {
    alert(err.message);
  }
  await carregarPedidos();
}

/* ------------------------------------------------- arrastar e soltar ---- */
function iniciarKanban() {
  const quadro = document.querySelector(".kanban-board");
  const destino = (ev) => ev.target.closest?.(".kanban-coluna")?.querySelector(".kanban-conteudo");
  const limpar = () => document.querySelectorAll(".arrastando-sobre")
    .forEach((c) => c.classList.remove("arrastando-sobre"));

  quadro.addEventListener("dragstart", (ev) => {
    const cartao = ev.target.closest?.(".cartao-pedido");
    if (!cartao) return;
    arrastando = true;
    ev.dataTransfer.setData("text/plain", cartao.dataset.senha);
    ev.dataTransfer.effectAllowed = "move";
  });
  quadro.addEventListener("dragend", () => { arrastando = false; limpar(); });
  quadro.addEventListener("dragover", (ev) => {
    const alvo = destino(ev);
    if (!alvo) return;
    ev.preventDefault();
    ev.dataTransfer.dropEffect = "move";
    if (!alvo.classList.contains("arrastando-sobre")) { limpar(); alvo.classList.add("arrastando-sobre"); }
  });
  quadro.addEventListener("drop", async (ev) => {
    ev.preventDefault();
    limpar();
    arrastando = false;
    const alvo = destino(ev);
    const senha = ev.dataTransfer.getData("text/plain");
    if (!alvo || !senha) return;
    const pedido = pedidos.find((x) => String(x.senha) === senha);
    const novo = alvo.dataset.status;
    if (!pedido || pedido.status === novo) return;

    const antigo = pedido.status;
    pedido.status = novo; // atualização otimista: o cartão muda de coluna na hora
    renderPedidos();
    try {
      await api(`/admin/api/pedidos/${encodeURIComponent(senha)}/status`, "PATCH", { status: novo });
    } catch (err) {
      pedido.status = antigo;
      renderPedidos();
      alert("Erro ao alterar o status: " + err.message);
    }
  });
}

/* ------------------------------------------------ gestão do cardápio ---- */
async function carregarGestaoCardapio() {
  const container = $("lista-gestao-cardapio");
  const selectCat = $("prod-categoria");
  if (!container) return;
  try {
    const menu = await api("/api/menu");
    custos = await api("/admin/api/custos").catch(() => ({}));
    gestao = await api("/admin/api/produtos-extra").catch(() => ({}));
    insumos = await api("/admin/api/insumos").catch(() => []);
    renderInsumos(menu);

    const escolhida = selectCat.value;
    selectCat.replaceChildren(new Option("Selecione a Categoria...", ""));
    menu.categorias.forEach((c) => selectCat.add(new Option(`${c.icone} ${c.nome}`, c.id)));
    if (menu.categorias.some((c) => c.id === escolhida)) selectCat.value = escolhida;

    container.replaceChildren();
    if (!menu.categorias.length) {
      container.appendChild(el("p", "", "Nenhuma categoria cadastrada ainda."));
      return;
    }
    menu.categorias.forEach((c) => {
      const bloco = el("div", "cat-gestao");
      const cab = el("div", "cat-cab");
      cab.appendChild(el("h4", "", `${c.icone} ${c.nome}`));
      const delCat = el("button", "btn btn-perigo", "Excluir categoria"); delCat.type = "button";
      delCat.addEventListener("click", () => {
        const n = c.produtos.length;
        const aviso = n ? `Excluir a categoria "${c.nome}" e os ${n} produto(s) dela? Não dá para desfazer.` : `Excluir a categoria "${c.nome}"?`;
        if (confirm(aviso)) acao(() => api(`/admin/api/categorias/${encodeURIComponent(c.id)}`, "DELETE"));
      });
      cab.appendChild(delCat);
      bloco.appendChild(cab);
      const ul = el("ul", "lista-gestao");
      c.produtos.forEach((p) => ul.appendChild(linhaProduto({ ...p, categoria_id: c.id })));
      bloco.appendChild(ul);
      container.appendChild(bloco);
    });
  } catch (_) {
    container.replaceChildren(el("p", "", "Erro ao carregar o cardápio."));
  }
}

function linhaProduto(p) {
  const li = el("li", "item-gestao");
  li.appendChild(el("span", "", `${p.emoji || "🍔"} ${p.nome} — ${brl(p.preco)}`));

  const acoes = el("div", "acoes-gestao");
  const g = gestao[p.id] || {};
  const notas = [];
  if (g.estoque != null) notas.push(`estoque: ${g.estoque}`);
  const ins = insumos.find((i) => i.id === g.insumo_id);
  if (ins) notas.push(`usa ${g.consumo}× ${ins.nome}`);
  if (g.janela_ini) notas.push(`🕐 ${g.janela_ini}–${g.janela_fim}`);
  if (p.ativo && p.aviso) notas.push("fora do horário agora");
  if (notas.length) li.firstChild.appendChild(el("small", "nota-gestao", "  " + notas.join(" · ")));
  const disp = el("button", p.ativo ? "btn btn-claro" : "btn btn-perigo", p.ativo ? "Disponível" : "Esgotado");
  disp.type = "button";
  disp.addEventListener("click", () => acao(() =>
    api(`/admin/api/produtos/${encodeURIComponent(p.id)}/disponibilidade`, "PATCH", { disponivel: !p.ativo })));
  const del = el("button", "btn btn-perigo", "Excluir");
  del.type = "button";
  del.addEventListener("click", () => {
    if (confirm(`Excluir ${p.nome}?`)) acao(() => api(`/admin/api/produtos/${encodeURIComponent(p.id)}`, "DELETE"));
  });
  const edit = el("button", "btn btn-claro", "Editar");
  edit.type = "button";
  edit.addEventListener("click", () => iniciarEdicao(p));
  acoes.append(disp, edit, del);
  li.appendChild(acoes);
  return li;
}

/** Executa uma ação do painel, mostra o erro (se houver) e recarrega a lista. */
async function acao(fn) {
  try { await fn(); } catch (err) { alert(err.message); }
  await carregarGestaoCardapio();
  atualizarChips();
}

function iniciarFormularios() {
  const formNome = $("form-nome-loja");
  if (!formNome) return; // usuário "cozinha" não tem a aba Cardápio

  formNome.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await api("/admin/api/lanchonete", "PUT", { nome: $("input-nome-loja").value });
      $("nome-loja-header").textContent = r.nome;
      alert("Nome atualizado!");
    } catch (err) { alert(err.message); }
  });

  $("form-nova-categoria").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/admin/api/categorias", "POST", { icone: $("cat-icone").value, nome: $("cat-nome").value });
      $("cat-icone").value = "";
      $("cat-nome").value = "";
      await carregarGestaoCardapio();
    } catch (err) { alert(err.message); }
  });

  $("form-novo-produto").addEventListener("submit", salvarProduto);
  $("btn-cancelar-edicao").addEventListener("click", sairDaEdicao);
}

let insumos = [];  // estoque geral (pão etc.)
let gestao = {};   // id -> { estoque, janela_ini, janela_fim } (só o painel recebe)
let custos = {}; // id do produto -> custo em centavos (ou null). Só o painel recebe isso.
let editando = null; // produto em edição (null = cadastrando um novo)

function iniciarEdicao(p) {
  editando = p;
  $("prod-categoria").value = p.categoria_id;
  $("prod-nome").value = p.nome;
  $("prod-preco").value = Number(p.preco).toFixed(2);
  $("prod-custo").value = custos[p.id] == null ? "" : (custos[p.id] / 100).toFixed(2);
  $("prod-emoji").value = p.emoji || "";
  const g = gestao[p.id] || {};
  $("prod-estoque").value = g.estoque == null ? "" : g.estoque;
  $("prod-insumo").value = g.insumo_id || "";
  $("prod-consumo").value = g.consumo || 1;
  $("prod-consumo").hidden = !$("prod-insumo").value;
  $("prod-janela-ini").value = g.janela_ini || "";
  $("prod-janela-fim").value = g.janela_fim || "";
  $("prod-extras").value = (p.extras || []).map((e) => `${e.nome}; ${virgula(e.preco)}`).join("\n");
  $("prod-sem").value = (p.sem || []).join("\n");
  $("prod-desc").value = p.descricao || "";
  $("prod-foto").value = "";
  $("prod-remover-foto").checked = false;
  const tem = !!p.imagem;
  $("prod-foto-atual").hidden = !tem;
  $("rotulo-remover-foto").hidden = !tem;
  if (tem) $("prod-foto-atual").src = p.miniatura || p.imagem;
  $("titulo-form-produto").textContent = "Editando: " + p.nome;
  $("btn-salvar-produto").textContent = "Salvar alterações";
  $("btn-cancelar-edicao").hidden = false;
  $("form-novo-produto").closest(".card-admin").classList.add("editando");
  $("form-novo-produto").scrollIntoView({ behavior: "smooth", block: "center" });
  $("prod-nome").focus();
}

function sairDaEdicao() {
  editando = null;
  $("form-novo-produto").reset();
  $("prod-foto-atual").hidden = true;
  $("rotulo-remover-foto").hidden = true;
  $("titulo-form-produto").textContent = "Novo Produto";
  $("btn-salvar-produto").textContent = "Cadastrar Produto";
  $("btn-cancelar-edicao").hidden = true;
  $("form-novo-produto").closest(".card-admin").classList.remove("editando");
}

async function salvarProduto(e) {
  e.preventDefault();
  const foto = $("prod-foto");
  const botao = $("btn-salvar-produto");
  botao.disabled = true;
  try {
    const corpo = {
      categoria_id: $("prod-categoria").value,
      nome: $("prod-nome").value,
      preco: $("prod-preco").value,
      custo: $("prod-custo").value,
      estoque: $("prod-estoque").value,
      insumo_id: $("prod-insumo").value,
      consumo: $("prod-consumo").value,
      janela_ini: $("prod-janela-ini").value,
      janela_fim: $("prod-janela-fim").value,
      extras_texto: $("prod-extras").value,
      sem_texto: $("prod-sem").value,
      emoji: $("prod-emoji").value,
      descricao: $("prod-desc").value,
    };
    if (foto.files.length) {
      const dados = new FormData();
      dados.append("foto", foto.files[0]);
      corpo.imagem = (await api("/admin/api/upload", "POST", dados)).url || "";
    } else if (editando && $("prod-remover-foto").checked) {
      corpo.imagem = "";                       // só apaga a foto se o dono pediu
    } else if (!editando) {
      corpo.imagem = "";
    }                                          // editando sem mexer na foto: não envia "imagem"

    if (editando) {
      await api(`/admin/api/produtos/${encodeURIComponent(editando.id)}`, "PUT", corpo);
      alert("Produto atualizado!");
      sairDaEdicao();
    } else {
      await api("/admin/api/produtos", "POST", corpo);
      ["prod-nome", "prod-preco", "prod-custo", "prod-estoque", "prod-consumo", "prod-janela-ini", "prod-janela-fim", "prod-extras", "prod-sem", "prod-desc", "prod-emoji", "prod-foto"].forEach((id) => ($(id).value = ""));
      alert("Produto cadastrado com sucesso!");
    }
    await carregarGestaoCardapio();
  } catch (err) {
    alert("Não foi possível salvar: " + err.message);
  } finally {
    botao.disabled = false;
  }
}

/* ------------------------------------------- funcionamento da loja ---- */
const virgula = (n) => Number(n || 0).toFixed(2).replace(".", ",");

function montarDias(dias, nomes) {
  const box = $("loja-dias");
  box.replaceChildren();
  dias.forEach((d, i) => {
    const linha = el("div", "loja-dia");
    const aberto = el("input"); aberto.type = "checkbox"; aberto.checked = d.aberto;
    const nome = el("label", "loja-dia-nome");
    nome.append(aberto, " " + nomes[i][0].toUpperCase() + nomes[i].slice(1));
    const abre = el("input"); abre.type = "time"; abre.value = d.abre; abre.required = true;
    const fecha = el("input"); fecha.type = "time"; fecha.value = d.fecha; fecha.required = true;
    const ate = el("span", "", "às");
    const sync = () => { abre.disabled = fecha.disabled = !aberto.checked; };
    aberto.addEventListener("change", sync); sync();
    linha.append(nome, abre, ate, fecha);
    linha.dataset.indice = i;
    box.appendChild(linha);
  });
}

function lerDias() {
  return [...$("loja-dias").children].map((linha) => {
    const [aberto, abre, fecha] = linha.querySelectorAll("input");
    return { aberto: aberto.checked, abre: abre.value, fecha: fecha.value };
  });
}

async function carregarLoja() {
  if (!$("form-loja")) return; // usuário "cozinha"
  try {
    const c = await api("/admin/api/loja");
    $("loja-modo").value = c.modo;
    montarDias(c.dias, c.dias_nomes);
    $("loja-taxa").value = virgula(c.taxa_entrega);
    $("loja-minimo").value = virgula(c.pedido_minimo);
    $("loja-bairros-modo").value = c.bairros_modo;
    $("loja-bairros").value = c.bairros_texto;
    $("loja-dono").value = c.dono_telefone ? telBonito(c.dono_telefone) : "";
    $("loja-atraso").value = c.atraso_minutos;
    $("loja-preparo").value = c.preparo_minutos;
    $("loja-mesas").value = c.mesas;
    $("loja-relatorio").value = c.relatorio_hora;
    $("loja-fid-n").value = c.fidelidade_pedidos;
    $("loja-fid-pct").value = c.fidelidade_percentual;
    $("loja-agendamento").checked = c.agendamento;
    $("loja-agend-minimo").value = c.agend_minimo;
    $("loja-agend-liberar").value = c.agend_liberar;
    $("loja-estado").textContent = c.aberta_agora ? "🟢 A loja está ABERTA agora." : "🔴 A loja está FECHADA agora. " + c.aviso;
  } catch (err) { $("loja-estado").textContent = "⚠️ " + err.message; }
}
aoAbrirAba.loja = () => { carregarLoja(); carregarCupons(); carregarSumidos(); };

function iniciarLoja() {
  const form = $("form-loja");
  if (!form) return;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/admin/api/loja", "PUT", {
        modo: $("loja-modo").value, dias: lerDias(),
        taxa_entrega: $("loja-taxa").value, pedido_minimo: $("loja-minimo").value,
        bairros_modo: $("loja-bairros-modo").value, bairros_texto: $("loja-bairros").value,
        dono_telefone: $("loja-dono").value, atraso_minutos: $("loja-atraso").value, preparo_minutos: $("loja-preparo").value, mesas: $("loja-mesas").value, relatorio_hora: $("loja-relatorio").value,
        fidelidade_pedidos: $("loja-fid-n").value, fidelidade_percentual: $("loja-fid-pct").value,
        agendamento: $("loja-agendamento").checked, agend_minimo: $("loja-agend-minimo").value, agend_liberar: $("loja-agend-liberar").value,
      });
      alert("Funcionamento salvo!");
      await carregarLoja();
    } catch (err) { alert("Não foi possível salvar: " + err.message); }
  });
}

/* ------------------------------------------- clientes sumidos ---- */
let sumidosMsgPadrao = "";

async function carregarSumidos() {
  const lista = $("sum-lista");
  if (!lista) return;
  try {
    const r = await api(`/admin/api/sumidos?dias=${encodeURIComponent($("sum-dias").value)}`);
    if (!$("sum-mensagem").value) $("sum-mensagem").value = r.mensagem_padrao;
    sumidosMsgPadrao = r.mensagem_padrao;
    lista.replaceChildren();
    $("sum-vazio").hidden = r.clientes.length > 0;
    r.clientes.forEach((c) => {
      const li = el("li", "sum-item" + (c.em_espera ? " feito" : ""));
      li.append(el("strong", "", c.nome || "Cliente"), el("span", "", c.telefone),
                el("small", "", `${c.pedidos} pedido(s) · ${brl(c.total)} · sem pedir há ${c.dias_sem_pedir} dias`));
      const b = el("button", "btn btn-vermelho", c.em_espera ? "Já enviado ✔" : "Enviar mensagem"); b.type = "button"; b.disabled = c.em_espera;
      b.addEventListener("click", async () => {
        b.disabled = true;
        try {
          await api("/admin/api/sumidos/enviar", "POST", { telefone: c.telefone, mensagem: $("sum-mensagem").value, cupom: $("sum-cupom").value });
          b.textContent = "Enviado ✔";
        } catch (e) { alert(e.message); b.disabled = false; }
      });
      li.appendChild(b);
      lista.appendChild(li);
    });
    const sel = $("sum-cupom"), atual = sel.value;
    const cupons = await api("/admin/api/cupons").catch(() => []);
    sel.replaceChildren(new Option("Sem cupom", ""));
    cupons.filter((x) => x.ativo && !x.vencido).forEach((x) => sel.add(new Option(x.codigo, x.codigo)));
    sel.value = cupons.some((x) => x.codigo === atual) ? atual : "";
  } catch (_) { /* o resto do painel já mostra erros de conexão */ }
}

function iniciarSumidos() {
  if (!$("sum-dias")) return;
  $("sum-dias").addEventListener("change", carregarSumidos);
  $("btn-relatorio-agora").addEventListener("click", async () => {
    try { await api("/admin/api/relatorio/enviar", "POST"); alert("Relatório enviado para o WhatsApp do dono!"); }
    catch (e) { alert(e.message); }
  });
}

/* -------------------------------------------------------- histórico ---- */
const AREAS_HIST = { cliente: "Clientes", pedido: "Pedidos", produto: "Produtos", categoria: "Categorias", estoque: "Estoque geral", cupom: "Cupons", loja: "Loja" };
let histUltimo = null;

async function carregarHistorico(mais) {
  const lista = $("hist-lista");
  if (!lista) return;
  if (!mais) { lista.replaceChildren(); histUltimo = null; }
  const q = new URLSearchParams({ limite: "50" });
  if ($("hist-area").value) q.set("area", $("hist-area").value);
  if ($("hist-busca").value.trim()) q.set("busca", $("hist-busca").value.trim());
  if (mais && histUltimo) q.set("antes", String(histUltimo));
  try {
    const r = await api(`/admin/api/historico?${q}`);
    r.itens.forEach((i) => {
      const li = el("li", "hist-item");
      li.append(el("span", "hist-quando", new Date(i.quando).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })),
                el("span", "hist-area hist-" + i.area, i.area_nome),
                el("span", "hist-texto", i.texto),
                el("small", "hist-quem", "por " + i.usuario));
      lista.appendChild(li);
      histUltimo = i.id;
    });
    $("hist-mais").hidden = !r.mais;
    $("hist-vazio").hidden = lista.children.length > 0;
  } catch (err) { alert(err.message); }
}

function iniciarHistorico() {
  if (!$("hist-area")) return;
  Object.entries(AREAS_HIST).forEach(([id, nome]) => $("hist-area").add(new Option(nome, id)));
  $("hist-aplicar").addEventListener("click", () => carregarHistorico(false));
  $("hist-busca").addEventListener("keydown", (e) => { if (e.key === "Enter") carregarHistorico(false); });
  $("hist-area").addEventListener("change", () => carregarHistorico(false));
  $("hist-mais").addEventListener("click", () => carregarHistorico(true));
  aoAbrirAba.historico = () => carregarHistorico(false);
}

/* ---------------------------------------------------------- cupons ---- */
async function carregarCupons() {
  const lista = $("lista-cupons");
  if (!lista) return;
  let cupons = [];
  try { cupons = await api("/admin/api/cupons"); } catch (_) { return; }
  lista.replaceChildren();
  if (!cupons.length) lista.appendChild(el("p", "vazio-fila", "Nenhum cupom ainda. Crie o primeiro abaixo (ex.: PRIMEIRA10)."));
  cupons.forEach((c) => {
    const linha = el("div", "cupom-item" + (c.ativo && !c.vencido ? "" : " inativo"));
    linha.appendChild(el("strong", "cupom-codigo", c.codigo));
    const desc = c.tipo === "percentual" ? `${c.valor}% off` : `${brl(c.valor)} off`;
    const regras = [desc];
    if (c.minimo > 0) regras.push(`mín. ${brl(c.minimo)}`);
    if (c.valido_ate) regras.push(`até ${c.valido_ate.split("-").reverse().join("/")}${c.vencido ? " (vencido)" : ""}`);
    if (c.uma_vez) regras.push("1 por cliente");
    if (c.primeira_compra) regras.push("1ª compra");
    linha.appendChild(el("span", "cupom-regras", regras.join(" · ")));
    linha.appendChild(el("span", "cupom-usos", `usado ${c.usos}${c.limite ? ` de ${c.limite}` : ""}×`));
    const tog = el("button", "btn btn-claro", c.ativo ? "Desativar" : "Ativar"); tog.type = "button";
    tog.addEventListener("click", async () => {
      try { await api(`/admin/api/cupons/${encodeURIComponent(c.codigo)}`, "PUT", { ativo: !c.ativo }); } catch (e) { alert(e.message); }
      carregarCupons();
    });
    const del = el("button", "btn btn-perigo", "Excluir"); del.type = "button";
    del.addEventListener("click", async () => {
      if (!confirm(`Excluir o cupom ${c.codigo}? Pedidos antigos continuam com o desconto que tiveram.`)) return;
      try { await api(`/admin/api/cupons/${encodeURIComponent(c.codigo)}`, "DELETE"); } catch (e) { alert(e.message); }
      carregarCupons();
    });
    const link = el("button", "btn btn-claro", "Copiar link"); link.type = "button";
    link.title = "Link que já aplica o cupom: " + c.link;
    link.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(c.link); link.textContent = "Copiado ✔"; }
      catch (_) { prompt("Copie o link:", c.link); }
      setTimeout(() => { link.textContent = "Copiar link"; }, 2000);
    });
    linha.append(link, tog, del);
    lista.appendChild(linha);
  });
}

function iniciarCupons() {
  const form = $("form-cupom");
  if (!form) return;
  const ajustaRotulo = () => {
    const pct = $("cup-tipo").value === "percentual";
    $("cup-valor-rotulo").textContent = pct ? "Quantos %" : "Quantos R$";
    $("cup-valor").placeholder = pct ? "10" : "5,00";
  };
  $("cup-tipo").addEventListener("change", ajustaRotulo);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/admin/api/cupons", "POST", {
        codigo: $("cup-codigo").value, tipo: $("cup-tipo").value, valor: $("cup-valor").value,
        minimo: $("cup-minimo").value, valido_ate: $("cup-validade").value, limite: $("cup-limite").value,
        uma_vez: $("cup-uma-vez").checked, primeira_compra: $("cup-primeira").checked,
      });
      form.reset(); ajustaRotulo();
      await carregarCupons();
    } catch (err) { alert("Não foi possível criar o cupom: " + err.message); }
  });
}

/* ------------------------------------------------ estoque geral ---- */
function renderInsumos(menu) {
  const lista = $("lista-insumos");
  if (!lista) return;
  lista.replaceChildren();
  if (!insumos.length) lista.appendChild(el("p", "vazio-fila", "Nenhum estoque criado ainda. Crie um (ex.: Pão) e ligue aos lanches."));
  insumos.forEach((i) => {
    const linha = el("div", "insumo" + (i.baixo ? " baixo" : ""));
    const nome = el("strong", "", i.nome);
    const qtd = el("input"); qtd.type = "number"; qtd.min = "0"; qtd.max = "99999"; qtd.step = "1"; qtd.value = i.quantidade;
    qtd.title = "Quantidade que você tem agora (digite o total contado)";
    const min = el("input"); min.type = "number"; min.min = "0"; min.max = "99999"; min.step = "1"; min.value = i.minimo;
    min.title = "Avisar quando restar até esta quantidade";
    const rotMin = el("small", "", "avisar em");
    const uso = el("small", "", i.produtos ? `usado por ${i.produtos} produto(s)` : "nenhum produto usa ainda");
    const salvar = el("button", "btn btn-claro", "Salvar"); salvar.type = "button";
    salvar.addEventListener("click", () => acao(() => api(`/admin/api/insumos/${encodeURIComponent(i.id)}`, "PUT", { quantidade: qtd.value, minimo: min.value })));
    const mais = el("button", "btn btn-claro", "+10"); mais.type = "button";
    mais.title = "Somar 10 à quantidade atual";
    mais.addEventListener("click", () => acao(() => api(`/admin/api/insumos/${encodeURIComponent(i.id)}`, "PUT", { quantidade: Number(qtd.value || 0) + 10 })));
    const del = el("button", "btn btn-perigo", "Excluir"); del.type = "button";
    del.addEventListener("click", () => { if (confirm(`Excluir o estoque "${i.nome}"? Os produtos deixam de usá-lo.`)) acao(() => api(`/admin/api/insumos/${encodeURIComponent(i.id)}`, "DELETE")); });
    const escolher = el("button", "btn btn-claro", "Escolher lanches"); escolher.type = "button";
    const painel = el("div", "painel-escolha"); painel.hidden = true;
    escolher.addEventListener("click", () => { painel.hidden = !painel.hidden; });
    montarEscolha(painel, i, menu);
    linha.append(nome, qtd, mais, rotMin, min, salvar, escolher, del, uso, painel);
    lista.appendChild(linha);
  });

  const selIns = $("prod-insumo"), anterior = selIns.value;
  selIns.replaceChildren(new Option("Não usa estoque geral", ""));
  insumos.forEach((i) => selIns.add(new Option(i.nome, i.id)));
  selIns.value = insumos.some((i) => i.id === anterior) ? anterior : "";
  $("prod-consumo").hidden = !selIns.value;

  $("form-vincular").hidden = !(insumos.length && menu.categorias.length);
  const vc = $("vinc-categoria"), vi = $("vinc-insumo");
  vc.replaceChildren(); menu.categorias.forEach((c) => vc.add(new Option(`${c.icone} ${c.nome}`, c.id)));
  vi.replaceChildren(); insumos.forEach((i) => vi.add(new Option(i.nome, i.id)));
}

/** Painel "Escolher lanches": marque um a um quais produtos gastam deste estoque e quanto cada um gasta. */
function montarEscolha(painel, ins, menu) {
  const linhas = [];
  menu.categorias.forEach((c) => {
    if (!c.produtos.length) return;
    painel.appendChild(el("h5", "", `${c.icone} ${c.nome}`));
    c.produtos.forEach((p) => {
      const g = gestao[p.id] || {};
      const usa = g.insumo_id === ins.id;
      const outro = g.insumo_id && !usa ? insumos.find((x) => x.id === g.insumo_id) : null;
      const rot = el("label", "linha-escolha");
      const chk = el("input"); chk.type = "checkbox"; chk.checked = usa;
      const q = el("input"); q.type = "number"; q.min = "1"; q.max = "50"; q.step = "1"; q.value = usa ? g.consumo : 1;
      q.title = "Quanto cada unidade vendida gasta";
      rot.append(chk, el("span", "", `${p.emoji || "🍔"} ${p.nome}`), q, el("small", "", "por unidade"));
      if (outro) rot.appendChild(el("small", "aviso-troca", `(hoje usa ${outro.nome}; marcar aqui troca)`));
      painel.appendChild(rot);
      linhas.push({ id: p.id, chk, q });
    });
  });
  const salvar = el("button", "btn btn-vermelho", "Salvar escolha"); salvar.type = "button";
  salvar.addEventListener("click", () => acao(() => api(`/admin/api/insumos/${encodeURIComponent(ins.id)}/produtos`, "PUT",
    { produtos: linhas.filter((l) => l.chk.checked).map((l) => ({ id: l.id, consumo: l.q.value })) })));
  painel.appendChild(salvar);
}

function iniciarInsumos() {
  const form = $("form-novo-insumo");
  if (!form) return;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/admin/api/insumos", "POST", { nome: $("ins-nome").value, quantidade: $("ins-qtd").value, minimo: $("ins-min").value === "" ? 10 : $("ins-min").value });
      form.reset();
      await carregarGestaoCardapio();
      atualizarChips();
    } catch (err) { alert("Não foi possível criar: " + err.message); }
  });
  $("form-vincular").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await api(`/admin/api/insumos/${encodeURIComponent($("vinc-insumo").value)}/vincular`, "POST",
        { categoria_id: $("vinc-categoria").value, consumo: $("vinc-consumo").value });
      alert(`Pronto! ${r.produtos} produto(s) agora gastam do estoque geral.`);
      await carregarGestaoCardapio();
    } catch (err) { alert("Não foi possível aplicar: " + err.message); }
  });
  $("prod-insumo").addEventListener("change", () => { $("prod-consumo").hidden = !$("prod-insumo").value; });
}

/** Contagem sempre à vista na aba Pedidos (admin e cozinha). */
async function atualizarChips() {
  const box = $("chips-estoque");
  try {
    const lista = await api("/admin/api/insumos");
    box.hidden = lista.length === 0;
    box.replaceChildren();
    lista.forEach((i) => box.appendChild(el("span", "chip-estoque" + (i.quantidade === 0 ? " zerado" : i.baixo ? " baixo" : ""),
      `🍞 ${i.nome}: ${i.quantidade}`)));
  } catch (_) { /* sem conexão: o resto do painel já avisa */ }
}

/* ------------------------------------------- estado do WhatsApp ---- */
async function verificarWhatsapp() {
  const chip = $("wa-status"), faixa = $("wa-faixa");
  try {
    const r = await api("/admin/api/whatsapp");
    chip.textContent = r.ok ? "🟢 WhatsApp conectado" : "🔴 WhatsApp com problema";
    chip.className = "wa-status " + (r.ok ? "ok" : "erro");
    chip.title = r.mensagem;
    faixa.hidden = r.ok;
    if (!r.ok) faixa.textContent = `⚠️ ${r.mensagem} Enquanto isso, os clientes NÃO recebem avisos pelo WhatsApp (os pedidos continuam chegando normalmente).`;
  } catch (_) { /* sem conexão com o servidor: o resto do painel já avisa */ }
}

/* -------------------------------------------------------- inicialização ---- */
$("btn-tab-pedidos").addEventListener("click", () => alternarAba("pedidos"));
$("btn-tab-cardapio")?.addEventListener("click", () => alternarAba("cardapio"));
$("btn-tab-loja")?.addEventListener("click", () => alternarAba("loja"));
$("btn-tab-historico")?.addEventListener("click", () => alternarAba("historico"));
$("btn-tab-financeiro")?.addEventListener("click", () => alternarAba("financeiro"));
iniciarKanban();
iniciarFormularios();
iniciarLoja();
window.addEventListener("DOMContentLoaded", () => alternarAba(location.hash.slice(1)));
carregarPedidos();
setInterval(carregarPedidos, 10000);
verificarWhatsapp();
setInterval(verificarWhatsapp, 30000);
atualizarChips();
setInterval(atualizarChips, 20000);
iniciarInsumos();
iniciarCupons();
iniciarHistorico();
iniciarSumidos();
