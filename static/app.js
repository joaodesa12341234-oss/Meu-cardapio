const brl = (v) => v.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });

let menu = null;
let categoriaAtual = null;
let produtosPorId = {};
let ultimoMenu = "";
let loja = { aberta: true, aviso: "", taxa_entrega: 0, pedido_minimo: 0, pagamentos: [], bairros_modo: "livre", bairros: [], agendamento: { ativo: false, horarios: [] } };
const carrinho = {};   // chave da linha -> { id, qtd, extras: [nomes], sem: [nomes] }

const $ = (id) => document.getElementById(id);

const esgotado = (p) => p.disponivel === false;

function aplicarMenu(texto) {
  ultimoMenu = texto;
  menu = JSON.parse(texto);
  loja = menu.loja || loja;
  produtosPorId = {};
  menu.categorias.forEach((c) => c.produtos.forEach((p) => (produtosPorId[p.id] = p)));
  if (!menu.categorias.some((c) => c.id === categoriaAtual)) {
    categoriaAtual = menu.categorias.length ? menu.categorias[0].id : null;
  }
  const removidos = [];
  for (const [chave, l] of Object.entries(carrinho)) {
    const p = produtosPorId[l.id];
    const opcoesSumiram = p && (l.extras.some((n) => !p.extras.some((e) => e.nome === n)) || l.sem.some((n) => !p.sem.includes(n)));
    if (!p || esgotado(p) || opcoesSumiram) {
      removidos.push(p ? p.nome : "um item");
      delete carrinho[chave];
    }
  }
  return removidos;
}

async function buscarMenu(forcar = false) {
  const resp = await fetch("/api/menu", { cache: "no-cache" });
  const texto = await resp.text();
  if (!forcar && texto === ultimoMenu) return;
  const removidos = aplicarMenu(texto);
  renderTudo();
  if (removidos.length) {
    mostrarAviso(`Acabou: ${removidos.join(", ")}. Já tiramos do seu pedido.`);
  }
}

function renderTudo() {
  renderAvisoLoja();
  renderPagamentos();
  renderBairros();
  renderAgendamento();
  renderAbas();
  renderProdutos();
  renderBarra();
  if ($("modal-carrinho").open) renderCarrinho();
}

/** Com "só bairros cadastrados" ligado, o campo Bairro vira uma lista (e a taxa depende do bairro). */
const usaListaDeBairros = () => loja.bairros_modo === "lista" && loja.bairros.length > 0;
const campoBairro = () => (usaListaDeBairros() ? $("cli-bairro-lista") : $("cli-bairro"));

function renderBairros() {
  const lista = $("cli-bairro-lista");
  const escolhido = lista.value;
  $("cli-bairro").hidden = usaListaDeBairros();
  lista.hidden = !usaListaDeBairros();
  lista.replaceChildren(new Option("Escolha o bairro...", ""));
  loja.bairros.forEach((b) => lista.add(new Option(b.taxa > 0 ? `${b.nome} — ${brl(b.taxa)}` : `${b.nome} — grátis`, b.nome)));
  let alvo = escolhido;
  if (!alvo) { try { alvo = (JSON.parse(localStorage.getItem(CHAVE_DADOS) || "{}"))["cli-bairro-lista"] || ""; } catch (_) {} }
  if (loja.bairros.some((b) => b.nome === alvo)) lista.value = alvo;
}

function taxaDeEntrega() {
  if (!usaListaDeBairros()) return loja.taxa_entrega;
  const b = loja.bairros.find((x) => x.nome === $("cli-bairro-lista").value);
  return b ? b.taxa : 0;
}

/* pedidos agendados: os horários vêm do servidor (ele confere de novo na hora do pedido) */
const podeAgendar = () => loja.agendamento.ativo && loja.agendamento.horarios.length > 0 && !mesaAtual();   // mesa nunca agenda
const quandoEscolhido = () => document.querySelector('input[name="quando"]:checked')?.value || "agora";
const agendando = () => podeAgendar() && quandoEscolhido() === "agendar";

function renderAgendamento() {
  const bloco = $("bloco-agendar"), lista = $("cli-horario");
  bloco.hidden = !podeAgendar();
  const anterior = lista.value;
  lista.replaceChildren(new Option("Escolha o horário...", ""));
  loja.agendamento.horarios.forEach((h) => lista.add(new Option(h.rotulo, h.valor)));
  if (loja.agendamento.horarios.some((h) => h.valor === anterior)) lista.value = anterior;
  const agora = document.querySelector('input[name="quando"][value="agora"]');
  agora.disabled = !loja.aberta;                 // loja fechada: só dá para agendar
  if (!loja.aberta && podeAgendar()) document.querySelector('input[name="quando"][value="agendar"]').checked = true;
  if (!podeAgendar()) agora.checked = true;
  lista.hidden = !agendando();
}

document.querySelectorAll('input[name="quando"]').forEach((r) => (r.onchange = () => { $("cli-horario").hidden = !agendando(); limparErroDe("cli-horario"); atualizarPedido(); }));
$("cli-horario").addEventListener("change", () => limparErroDe("cli-horario"));

/* pedido por mesa: o QR code da mesa abre o cardápio com ?mesa=N */
const mesaDaUrl = (() => { const n = new URLSearchParams(location.search).get("mesa"); return /^\d{1,3}$/.test(n || "") ? String(Number(n)) : ""; })();
const mesaAtual = () => (mesaDaUrl && loja.mesas >= Number(mesaDaUrl) && Number(mesaDaUrl) >= 1 ? mesaDaUrl : "");

function renderMesa() {
  const m = mesaAtual();
  $("mesa-banner").hidden = !m;
  $("mesa-banner").textContent = m ? `🪑 Você está na Mesa ${m}. O pedido será levado até você.` : "";
  document.querySelector("fieldset.tipo").hidden = !!m;            // na mesa não há "retirar/entrega"
  if (m) { document.querySelector('input[name="tipo"][value="balcao"]').checked = true; $("campos-entrega").hidden = true; }
}

/* ?cupom=CODIGO no link: avisa no topo e aplica sozinho quando o cliente abre o carrinho */
const cupomDoLink = (() => {
  const v = (new URLSearchParams(location.search).get("cupom") || "").trim().toUpperCase();
  try { if (/^[A-Z0-9_-]{3,20}$/.test(v)) sessionStorage.setItem("cupom-link", v); return sessionStorage.getItem("cupom-link") || ""; }
  catch (_) { return /^[A-Z0-9_-]{3,20}$/.test(v) ? v : ""; }
})();

function aplicarCupomDoLink() {
  if (!cupomDoLink || cupom.codigo || $("cupom").value.trim() || conferindoCupom) return;
  $("cupom").value = cupomDoLink;
  conferirCupom(cupomDoLink, false);
}

/* pedir de novo: o último pedido deste aparelho fica guardado só aqui, no navegador */
const CHAVE_ULTIMO = "cardapio-ultimo-pedido";
function guardarUltimoPedido(itens) {
  try { localStorage.setItem(CHAVE_ULTIMO, JSON.stringify(itens.map((i) => ({ id: i.id, qtd: i.qtd, extras: i.extras || [], sem: i.sem || [] })))); } catch (_) {}
}
function lerUltimoPedido() {
  try {
    const v = JSON.parse(localStorage.getItem(CHAVE_ULTIMO) || "[]");
    return Array.isArray(v) ? v.filter((i) => i && typeof i.id === "string" && Number.isInteger(i.qtd) && i.qtd > 0) : [];
  } catch (_) { return []; }
}
function renderRepetir() {
  const b = $("btn-repetir");
  const itens = lerUltimoPedido().filter((i) => produtosPorId[i.id] && !esgotado(produtosPorId[i.id]));
  const ativo = itens.length > 0 && loja.aberta && !Object.keys(carrinho).length;
  b.hidden = !ativo;
  if (ativo) b.textContent = "🔁 Pedir de novo: " + itens.slice(0, 3).map((i) => `${i.qtd}× ${produtosPorId[i.id].nome}`).join(", ") + (itens.length > 3 ? "…" : "");
}
$("btn-repetir").addEventListener("click", () => {
  let pulou = 0;
  lerUltimoPedido().forEach((i) => {
    const p = produtosPorId[i.id];
    if (!p || esgotado(p)) { pulou++; return; }
    const extras = i.extras.filter((n) => (p.extras || []).some((e) => e.nome === n));
    const sem = i.sem.filter((n) => (p.sem || []).includes(n));
    const max = p.restam != null ? p.restam : 99;
    adicionarLinha(p.id, extras, sem, Math.min(i.qtd, max));
  });
  renderProdutos();
  renderBarra();
  renderRepetir();
  if (pulou) alert("Alguns itens do último pedido não estão mais disponíveis e foram deixados de fora.");
  $("btn-ver").click();
});

function renderPreparo() {
  const e = $("preparo-loja");
  const ativo = loja.preparo && loja.preparo.ativo && loja.aberta;
  e.hidden = !ativo;
  e.textContent = ativo ? `⏱ Seu pedido fica pronto em cerca de ${loja.preparo.minutos} min` : "";
}

function renderAvisoLoja() {
  renderPreparo();
  renderRepetir();
  $("cupom-banner").hidden = !cupomDoLink;
  $("cupom-banner").textContent = cupomDoLink ? `🏷 Cupom ${cupomDoLink} guardado! Ele é aplicado quando você abrir o carrinho.` : "";
  renderMesa();
  const el = $("aviso-loja");
  el.hidden = loja.aberta;
  el.textContent = loja.aberta ? "" : (loja.aviso || "Estamos fechados no momento.") +
    (podeAgendar() ? " Mas você pode agendar o seu pedido para quando estivermos abertos." : " Você pode ver o cardápio, mas ainda não dá para pedir.");
}

/* forma de pagamento: os botões vêm do servidor; a escolha é preservada quando o cardápio atualiza */
function renderPagamentos() {
  const box = $("opcoes-pagamento");
  const atual = pagamentoEscolhido();
  const ids = loja.pagamentos.map((p) => p.id).join();
  if (box.dataset.ids === ids) return;
  box.dataset.ids = ids;
  box.replaceChildren();
  loja.pagamentos.forEach((p) => {
    const rot = document.createElement("label");
    rot.className = "opcao";
    const r = document.createElement("input");
    r.type = "radio"; r.name = "pagamento"; r.value = p.id; r.checked = p.id === atual;
    r.onchange = atualizarPedido;
    rot.append(r, " " + ({ pix: "💠 ", cartao: "💳 ", dinheiro: "💵 " }[p.id] || "") + p.nome);
    box.appendChild(rot);
  });
}
const pagamentoEscolhido = () => document.querySelector('input[name="pagamento"]:checked')?.value || "";

function mostrarAviso(texto) {
  const el = $("aviso");
  el.textContent = texto;
  el.hidden = false;
  clearTimeout(mostrarAviso.t);
  mostrarAviso.t = setTimeout(() => (el.hidden = true), 8000);
}

async function iniciar() {
  await buscarMenu(true);
  setInterval(() => buscarMenu().catch(() => {}), 30000);
}

function renderAbas() {
  const nav = $("abas");
  nav.innerHTML = "";
  menu.categorias.forEach((c) => {
    const b = document.createElement("button");
    b.className = "aba" + (c.id === categoriaAtual ? " ativa" : "");
    b.textContent = `${c.icone} ${c.nome}`;
    b.onclick = () => {
      categoriaAtual = c.id;
      renderAbas();
      renderProdutos();
    };
    nav.appendChild(b);
  });
}

/* ------------------------------------------------ linhas do carrinho ---- */
const temOpcoes = (p) => (p.extras || []).length + (p.sem || []).length > 0;
const chaveLinha = (id, extras, sem) => `${id}|${[...extras].sort().join("+")}|${[...sem].sort().join("+")}`;
const qtdDoProduto = (id) => Object.values(carrinho).filter((l) => l.id === id).reduce((s, l) => s + l.qtd, 0);

function precoUnitario(l) {
  const p = produtosPorId[l.id];
  return p.preco + l.extras.reduce((s, n) => s + ((p.extras.find((e) => e.nome === n) || {}).preco || 0), 0);
}

const descricaoOpcoes = (l) => [...l.extras.map((n) => `+ ${n}`), ...l.sem.map((n) => `sem ${n}`)].join(", ");

/** Respeita o estoque que o servidor informa (só chega quando restam poucas unidades). */
function cabeMais(p, qtd = 1) {
  if (p.restam == null) return true;
  if (qtdDoProduto(p.id) + qtd > p.restam) {
    mostrarAviso(p.restam === 0 ? `${p.nome} acabou.` : `Só temos ${p.restam} de ${p.nome}.`);
    return false;
  }
  return true;
}

function adicionarLinha(id, extras, sem, qtd = 1) {
  const chave = chaveLinha(id, extras, sem);
  if (carrinho[chave]) carrinho[chave].qtd += qtd;
  else carrinho[chave] = { id, qtd, extras: [...extras], sem: [...sem] };
}

/* produto do cardápio: sem opções tem +/−; com opções abre a janela de escolha */
function controleProduto(p) {
  if (esgotado(p) || !temOpcoes(p)) return controleQtd(chaveLinha(p.id, [], []), p);
  const box = document.createElement("div");
  const b = document.createElement("button");
  b.className = "btn btn-vermelho";
  const n = qtdDoProduto(p.id);
  b.textContent = n ? `Escolher (${n} no pedido)` : "Escolher";
  b.onclick = () => abrirOpcoes(p);
  box.appendChild(b);
  return box;
}

function abrirOpcoes(p) {
  $("opcoes-titulo").textContent = p.nome;
  const montar = (idGrupo, idLista, itens, rotulo) => {
    $(idGrupo).hidden = itens.length === 0;
    const lista = $(idLista);
    lista.replaceChildren();
    itens.forEach((it) => {
      const lab = document.createElement("label");
      lab.className = "opcao-linha";
      const cx = document.createElement("input");
      cx.type = "checkbox";
      cx.value = it.nome;
      cx.addEventListener("change", atualizarBotaoOpcoes);
      lab.append(cx, " " + rotulo(it));
      lista.appendChild(lab);
    });
  };
  montar("opcoes-extras", "opcoes-extras-lista", p.extras, (e) => (e.preco > 0 ? `${e.nome} (+${brl(e.preco)})` : e.nome));
  montar("opcoes-sem", "opcoes-sem-lista", p.sem.map((n) => ({ nome: n })), (e) => `Sem ${e.nome}`);
  $("modal-opcoes").dataset.produto = p.id;
  atualizarBotaoOpcoes();
  $("modal-opcoes").showModal();
}

const marcados = (idLista) => [...$(idLista).querySelectorAll("input:checked")].map((i) => i.value);

function atualizarBotaoOpcoes() {
  const p = produtosPorId[$("modal-opcoes").dataset.produto];
  if (!p) return;
  const extras = marcados("opcoes-extras-lista");
  const unit = p.preco + extras.reduce((s, n) => s + p.extras.find((e) => e.nome === n).preco, 0);
  $("opcoes-adicionar").textContent = `Adicionar — ${brl(unit)}`;
}

$("opcoes-cancelar").onclick = () => $("modal-opcoes").close();
$("opcoes-adicionar").onclick = () => {
  const p = produtosPorId[$("modal-opcoes").dataset.produto];
  if (p && !esgotado(p) && cabeMais(p)) {
    adicionarLinha(p.id, marcados("opcoes-extras-lista"), marcados("opcoes-sem-lista"));
    renderProdutos();
    renderBarra();
    if ($("modal-carrinho").open) renderCarrinho();
  }
  $("modal-opcoes").close();
};

function controleQtd(chave, produto) {
  const qtd = carrinho[chave] ? carrinho[chave].qtd : 0;
  const box = document.createElement("div");
  if (produto && esgotado(produto)) {
    const b = document.createElement("button");
    b.className = "btn btn-claro";
    b.textContent = "Indisponível";
    b.disabled = true;
    box.appendChild(b);
  } else if (qtd === 0) {
    const add = document.createElement("button");
    add.className = "btn btn-vermelho";
    add.textContent = "Adicionar";
    add.onclick = () => mudar(chave, +1, produto);
    box.appendChild(add);
  } else {
    box.className = "qtd";
    const menos = document.createElement("button");
    menos.textContent = "−";
    menos.onclick = () => mudar(chave, -1);
    const n = document.createElement("span");
    n.textContent = qtd;
    const mais = document.createElement("button");
    mais.textContent = "+";
    mais.onclick = () => mudar(chave, +1);
    box.append(menos, n, mais);
  }
  return box;
}

function renderProdutos() {
  const cat = menu.categorias.find((c) => c.id === categoriaAtual);
  const grade = $("produtos");
  grade.innerHTML = "";
  if (!cat) {
    $("titulo-categoria").textContent = "";
    return;
  }
  $("titulo-categoria").textContent = cat.nome;

  cat.produtos.forEach((p) => {
    const card = document.createElement("article");
    card.className = "card" + (esgotado(p) ? " indisponivel" : "");

    const visual = document.createElement("div");
    visual.className = "visual";
    if (p.imagem) {
      const img = document.createElement("img");
      img.className = "foto";
      img.src = p.miniatura || p.imagem;
      if (p.miniatura) img.srcset = `${p.miniatura} 400w, ${p.imagem} 1000w`;
      img.sizes = "(min-width: 640px) 240px, 96px";
      img.alt = p.nome;
      img.width = 400;
      img.height = 300;
      img.loading = "lazy";
      img.decoding = "async";
      visual.appendChild(img);
    } else {
      visual.classList.add("emoji");
      visual.textContent = p.emoji;
    }
    if (esgotado(p)) {
      const selo = document.createElement("span");
      selo.className = "badge-esgotado";
      selo.textContent = p.aviso ? "Fora do horário" : "Esgotado";
      visual.appendChild(selo);
    }

    const info = document.createElement("div");
    info.className = "info";
    const titulo = document.createElement("h3");
    titulo.textContent = p.nome;
    const descricao = document.createElement("p");
    descricao.textContent = p.descricao;
    info.append(titulo, descricao);
    if (p.aviso && esgotado(p)) {
      const av = document.createElement("small");
      av.className = "aviso-produto";
      av.textContent = p.aviso;
      info.appendChild(av);
    } else if (p.restam != null && !esgotado(p)) {
      const av = document.createElement("small");
      av.className = "aviso-produto restam";
      av.textContent = p.restam === 1 ? "Resta só 1!" : `Restam só ${p.restam}`;
      info.appendChild(av);
    }

    const rodape = document.createElement("div");
    rodape.className = "rodape";
    const preco = document.createElement("span");
    preco.className = "preco";
    preco.textContent = (temOpcoes(p) ? "a partir de " : "") + brl(p.preco);
    rodape.append(preco, controleProduto(p));

    card.append(visual, info, rodape);
    grade.appendChild(card);
  });
}

function mudar(chave, delta, produto) {
  if (!carrinho[chave]) {
    if (delta < 0 || !produto || esgotado(produto) || !cabeMais(produto)) return;
    adicionarLinha(produto.id, [], []);
  } else {
    const p = produtosPorId[carrinho[chave].id];
    if (delta > 0 && (!p || esgotado(p) || !cabeMais(p))) return;
    carrinho[chave].qtd += delta;
    if (carrinho[chave].qtd <= 0) delete carrinho[chave];
  }
  renderProdutos();
  renderBarra();
  if ($("modal-carrinho").open) renderCarrinho();
}

function totais() {
  let itens = 0, total = 0;
  for (const l of Object.values(carrinho)) {
    itens += l.qtd;
    total += precoUnitario(l) * l.qtd;
  }
  return { itens, total };
}

/* cupom: o servidor confere tudo de novo ao fechar o pedido; aqui é só a prévia */
let cupom = { codigo: "", desconto: 0, base: -1 };
let conferindoCupom = false;

function mostrarMsgCupom(texto, erro) {
  const m = $("msg-cupom");
  m.hidden = !texto;
  m.textContent = texto || "";
  m.className = "msg-cupom" + (erro ? " erro" : "");
}

function tirarCupom(texto) {
  cupom = { codigo: "", desconto: 0, base: -1 };
  mostrarMsgCupom(texto || "", !!texto);
}

async function conferirCupom(codigo, silencioso) {
  if (conferindoCupom) return;
  conferindoCupom = true;
  const sub = totais().total;
  try {
    const r = await fetch("/api/cupom", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ codigo, subtotal: sub, telefone: $("cli-telefone").value }),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { tirarCupom(silencioso ? `Cupom removido: ${j.erro || "não vale mais"}` : j.erro || "Cupom inválido."); return; }
    cupom = { codigo: j.codigo, desconto: j.desconto, base: sub };
    mostrarMsgCupom(`Cupom ${j.codigo} aplicado: -${brl(j.desconto)} 🎉`, false);
  } catch (_) {
    if (!silencioso) mostrarMsgCupom("Sem conexão. Tente de novo.", true);
  } finally {
    conferindoCupom = false;
    atualizarPedido();
  }
}

/* fidelidade: desconto automático a cada N pedidos (identificado pelo telefone); o servidor confirma ao fechar */
let fid = { elegivel: false, falta: 0, pedidos: 0, percentual: 0, ativo: false };
let fidTelefone = "";

async function conferirFidelidade() {
  const dig = $("cli-telefone").value.replace(/\D/g, "");
  const msg = $("msg-fid");
  if (!(loja.fidelidade && loja.fidelidade.ativo) || dig.length < 10 || dig === fidTelefone) return;
  fidTelefone = dig;
  try {
    const r = await fetch(`/api/fidelidade?telefone=${encodeURIComponent(dig)}`, { cache: "no-store" });
    if (!r.ok) return;
    fid = await r.json();
    msg.hidden = false;
    msg.textContent = fid.elegivel
      ? `🎁 Você ganhou ${fid.percentual}% de desconto de fidelidade neste pedido!`
      : `🎁 Faltam ${fid.falta} ${fid.falta === 1 ? "pedido" : "pedidos"} para você ganhar ${fid.percentual}% de desconto.`;
  } catch (_) { /* sem rede: o servidor confere na hora de fechar */ }
  atualizarPedido();
}

function valorFinal() {
  const sub = totais().total;
  const taxa = tipoEscolhido() === "entrega" ? taxaDeEntrega() : 0;
  const desconto = cupom.codigo ? Math.min(cupom.desconto, sub) : 0;
  const base = Math.round((sub - desconto) * 100);
  const fidelidade = fid.elegivel ? Math.floor((base * fid.percentual) / 100) / 100 : 0;
  return { sub, taxa, desconto, fidelidade, total: Math.round((sub - desconto - fidelidade + taxa) * 100) / 100 };
}

/* atualiza total, taxa, aviso de pedido mínimo, campo de troco e o botão de finalizar */
function atualizarPedido() {
  const { sub, taxa, desconto, fidelidade, total } = valorFinal();
  $("linha-fid").hidden = !(fidelidade > 0);
  $("fid-valor").textContent = "-" + brl(fidelidade);
  if (cupom.codigo && cupom.base !== sub && !conferindoCupom) conferirCupom(cupom.codigo, true);   // carrinho mudou
  $("linha-desconto").hidden = !(cupom.codigo && desconto > 0);
  $("desc-codigo").textContent = cupom.codigo;
  $("desc-valor").textContent = "-" + brl(desconto);
  const entrega = tipoEscolhido() === "entrega";
  $("linha-taxa").hidden = !entrega;      // na entrega a taxa sempre aparece (valor, "grátis" ou pedido para escolher o bairro)
  $("taxa-valor").textContent = taxa > 0 ? brl(taxa)
    : (usaListaDeBairros() && !$("cli-bairro-lista").value ? "escolha o bairro" : "grátis");
  $("total").textContent = brl(total);
  $("campo-troco").hidden = pagamentoEscolhido() !== "dinheiro";
  const falta = loja.pedido_minimo > 0 && sub < loja.pedido_minimo;
  const aviso = $("aviso-minimo");
  aviso.hidden = !falta;
  aviso.textContent = falta ? `Pedido mínimo: ${brl(loja.pedido_minimo)}. Faltam ${brl(loja.pedido_minimo - sub)}.` : "";
  $("btn-finalizar").disabled = enviando || (!loja.aberta && !agendando()) || falta;
}

function renderBarra() {
  const { itens, total } = totais();
  $("barra").hidden = itens === 0;
  $("barra-resumo").textContent = `${itens} ${itens === 1 ? "item" : "itens"} · ${brl(total)}`;
}

function renderCarrinho() {
  const ul = $("lista-carrinho");
  ul.innerHTML = "";
  for (const [chave, l] of Object.entries(carrinho)) {
    const p = produtosPorId[l.id];
    const li = document.createElement("li");
    const nome = document.createElement("span");
    nome.className = "nome";
    nome.textContent = p.nome;
    const extra = descricaoOpcoes(l);
    if (extra) {
      const det = document.createElement("small");
      det.className = "det-opcoes";
      det.textContent = extra;
      nome.appendChild(det);
    }
    const preco = document.createElement("span");
    preco.textContent = brl(precoUnitario(l) * l.qtd);
    li.append(nome, controleQtd(chave, p), preco);
    ul.appendChild(li);
  }
  atualizarPedido();
  if (Object.keys(carrinho).length === 0) $("modal-carrinho").close();
}

$("btn-ver").onclick = () => {
  aplicarCupomDoLink();
  conferirFidelidade();
  renderCarrinho();
  $("modal-carrinho").showModal();
};
$("btn-voltar").onclick = () => $("modal-carrinho").close();

const tipoEscolhido = () => document.querySelector('input[name="tipo"]:checked').value;

document.querySelectorAll('input[name="tipo"]').forEach((r) => {
  r.onchange = () => {
    const entrega = tipoEscolhido() === "entrega";
    $("campos-entrega").hidden = !entrega;
    $("cli-nome").placeholder = "Informe o seu nome";
    atualizarPedido();
  };
});

let enviando = false;

$("btn-finalizar").onclick = async () => {
  if (enviando) return;
  
  const tipo = tipoEscolhido();
  const nome = $("cli-nome").value.trim();
  const telefone = $("cli-telefone").value.trim();
  const entrega = tipo === "entrega";
  const semNumero = $("cli-sem-numero").checked;

  limparErros();
  const faltas = [];
  if (agendando() && !$("cli-horario").value) faltas.push(["cli-horario", "Escolha o horário do pedido."]);
  if (nome.length < 2) faltas.push(["cli-nome", "Informe o seu nome."]);
  if (telefone.replace(/\D/g, "").length < 10) faltas.push(["cli-telefone", "Informe um WhatsApp válido com DDD."]);
  if (entrega) {
    if ($("cli-rua").value.trim().length < 3) faltas.push(["cli-rua", "Informe a rua ou avenida."]);
    if (!semNumero && !$("cli-numero").value.trim()) faltas.push(["cli-numero", "Informe o número (ou marque sem número)."]);
    if (campoBairro().value.trim().length < 2) faltas.push([campoBairro().id, usaListaDeBairros() ? "Escolha o bairro." : "Informe o bairro."]);
  }
  if (faltas.length) {
    faltas.forEach(([id, msg]) => marcarErro(id, msg));
    $(faltas[0][0]).focus();
    return;
  }

  if (!pagamentoEscolhido()) {
    alert("Escolha a forma de pagamento.");
    return;
  }

  const dados = {
    itens: Object.values(carrinho).map((l) => ({ id: l.id, qtd: l.qtd, extras: l.extras, sem: l.sem })),
    observacao: $("obs").value,
    tipo,
    nome,
    telefone,
    ...(entrega ? {
      rua: $("cli-rua").value.trim(),
      numero: semNumero ? "" : $("cli-numero").value.trim(),
      sem_numero: semNumero,
      bairro: campoBairro().value.trim(),
      complemento: $("cli-complemento").value.trim(),
      referencia: $("cli-referencia").value.trim(),
    } : {}),
    agendado_para: agendando() && !mesaAtual() ? $("cli-horario").value : "",
    cupom: cupom.codigo,
    ...(mesaAtual() ? { mesa: mesaAtual() } : {}),
    pagamento: pagamentoEscolhido(),
    troco_para: pagamentoEscolhido() === "dinheiro" ? $("troco").value.trim() : "",
  };

  enviando = true;
  $("btn-finalizar").disabled = true;
  try {
    const resp = await fetch("/api/pedido", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(dados),
    });

    if (!resp.ok) {
      const erro = await resp.json().catch(() => ({}));
      if (resp.status === 409) await buscarMenu(true).catch(() => {});   // esgotou ou loja fechou
      alert(erro.erro || "Não foi possível enviar o pedido. Tente novamente.");
      return;
    }

    const pedido = await resp.json();
    guardarDados();
    $("cupom").value = "";
    tirarCupom("");
    guardarUltimoPedido(dados.itens);
    fid = { ...fid, elegivel: false }; fidTelefone = ""; $("msg-fid").hidden = true;

    if (pedido && pedido.senha) {
      monitorizarPedido(pedido.senha, pedido.tipo);
    }

    $("modal-carrinho").close();
    $("senha").textContent = String(pedido.senha).padStart(3, "0");
    $("msg-senha").textContent = pedido.agendado_rotulo
      ? `Pedido agendado para ${pedido.agendado_rotulo}. Guarde esta senha.`
      : pedido.tipo === "entrega"
      ? "Seu pedido será entregue no endereço informado. Guarde esta senha."
      : "Aguarde ser chamado no balcão.";
    const link = $("link-acompanhar");
    link.hidden = !pedido.acompanhar;
    if (pedido.acompanhar) link.href = pedido.acompanhar;
    $("modal-senha").showModal();
  } catch (_) {
    alert("Sem conexão com o servidor. Tente novamente.");
  } finally {
    enviando = false;
    atualizarPedido();
  }
};

$("btn-novo").onclick = () => {
  Object.keys(carrinho).forEach((k) => delete carrinho[k]);
  ["obs", "troco"].forEach((id) => ($(id).value = ""));
  limparErros();
  restaurarDados();   // mantém os dados salvos (se o cliente deixou lembrar); senão esvazia
  document.querySelectorAll('input[name="pagamento"]').forEach((r) => (r.checked = false));
  document.querySelector('input[name="tipo"][value="balcao"]').checked = true;
  $("campos-entrega").hidden = true;
  $("modal-senha").close();
  atualizarPedido();
  renderProdutos();
  renderBarra();
};

/* -------------------------------------------------- monitoramento ---- */
let intervaloMonitorizacao = null;

function monitorizarPedido(senhaDoPedido, tipo) {
  if (intervaloMonitorizacao) clearInterval(intervaloMonitorizacao);

  intervaloMonitorizacao = setInterval(async () => {
    try {
      const resposta = await fetch(`/api/pedido-cliente/${senhaDoPedido}/status`);
      if (resposta.ok) {
        const dados = await resposta.json();
        const n = String(senhaDoPedido).padStart(3, "0");
        if (dados.status === "pronto" && tipo !== "entrega") {
          clearInterval(intervaloMonitorizacao);
          tocarSomNotificacaoCliente();
          alert(`O seu pedido #${n} está PRONTO! Pode vir retirar! 🍔😋`);
        } else if (dados.status === "saiu") {
          clearInterval(intervaloMonitorizacao);
          tocarSomNotificacaoCliente();
          alert(`O seu pedido #${n} saiu para entrega! 🛵`);
        } else if (dados.status === "concluido" || dados.status === "cancelado") {
          clearInterval(intervaloMonitorizacao);
        }
      }
    } catch (_) { /* sem rede agora: tenta de novo no próximo ciclo */ }
  }, 10000);
}

function tocarSomNotificacaoCliente() {
  try {
    const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = audioCtx.createOscillator();
    const ganho = audioCtx.createGain();
    osc.frequency.value = 1046.50; 
    ganho.gain.value = 0.2;
    osc.connect(ganho);
    ganho.connect(audioCtx.destination);
    osc.start(audioCtx.currentTime);
    osc.stop(audioCtx.currentTime + 0.3);
  } catch (e) { }
}

iniciar();

/* ---------------------------------------------- dados do cliente ---- */
const CAMPOS_CLIENTE = ["cli-nome", "cli-telefone", "cli-rua", "cli-numero", "cli-bairro", "cli-bairro-lista", "cli-complemento"];
const CHAVE_DADOS = "cardapio.cliente.v1";

function limparErros() {
  document.querySelectorAll(".msg-campo").forEach((m) => m.remove());
  document.querySelectorAll(".obs input.invalido").forEach((i) => i.classList.remove("invalido"));
}

function marcarErro(id, mensagem) {
  const campo = $(id);
  campo.classList.add("invalido");
  const msg = document.createElement("p");
  msg.className = "msg-campo";
  msg.dataset.campo = id;
  msg.textContent = mensagem;
  const ancora = campo.closest(".linha-campos") || campo.closest("label") || campo.closest("fieldset") || campo;   // fora da linha Número/Bairro
  let ultima = ancora;
  while (ultima.nextElementSibling && ultima.nextElementSibling.classList.contains("msg-campo")) ultima = ultima.nextElementSibling;
  ultima.after(msg);
}

function limparErroDe(id) {
  $(id).classList.remove("invalido");
  document.querySelectorAll(`.msg-campo[data-campo="${id}"]`).forEach((m) => m.remove());
}

/** Máscara do telefone: (87) 99999-9999. Só formata; a validação continua no servidor. */
function mascaraTelefone(valor) {
  const d = valor.replace(/\D/g, "").slice(0, 11);
  if (d.length <= 2) return d ? `(${d}` : "";
  if (d.length <= 6) return `(${d.slice(0, 2)}) ${d.slice(2)}`;
  if (d.length <= 10) return `(${d.slice(0, 2)}) ${d.slice(2, 6)}-${d.slice(6)}`;
  return `(${d.slice(0, 2)}) ${d.slice(2, 7)}-${d.slice(7)}`;
}

function guardarDados() {
  try {
    if (!$("cli-lembrar").checked) { localStorage.removeItem(CHAVE_DADOS); return; }
    const dados = {};
    CAMPOS_CLIENTE.forEach((id) => (dados[id] = $(id).value.trim()));
    dados.semNumero = $("cli-sem-numero").checked;
    localStorage.setItem(CHAVE_DADOS, JSON.stringify(dados));
  } catch (_) { /* navegador sem armazenamento: tudo bem */ }
}

function restaurarDados() {
  let dados = {};
  try { dados = JSON.parse(localStorage.getItem(CHAVE_DADOS) || "{}"); } catch (_) { dados = {}; }
  CAMPOS_CLIENTE.forEach((id) => ($(id).value = typeof dados[id] === "string" ? dados[id] : ""));
  $("cli-referencia").value = "";                 // referência muda a cada pedido
  $("cli-sem-numero").checked = dados.semNumero === true;
  $("cli-numero").disabled = $("cli-sem-numero").checked;
}

$("btn-cupom").addEventListener("click", () => {
  const codigo = $("cupom").value.trim();
  if (!codigo) { tirarCupom("Digite o código do cupom."); return; }
  conferirCupom(codigo, false);
});
$("cupom").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("btn-cupom").click(); } });
$("cupom").addEventListener("input", () => { if (cupom.codigo) tirarCupom(""); atualizarPedido(); });
$("cli-bairro-lista").addEventListener("change", () => { limparErroDe("cli-bairro-lista"); atualizarPedido(); });
$("cli-telefone").addEventListener("input", (e) => { e.target.value = mascaraTelefone(e.target.value); conferirFidelidade(); });
$("cli-sem-numero").addEventListener("change", (e) => {
  $("cli-numero").disabled = e.target.checked;
  if (e.target.checked) { $("cli-numero").value = ""; limparErroDe("cli-numero"); }
});
$("cli-lembrar").addEventListener("change", (e) => {
  if (!e.target.checked) { try { localStorage.removeItem(CHAVE_DADOS); } catch (_) {} }
});
CAMPOS_CLIENTE.concat(["cli-referencia"]).forEach((id) =>
  $(id).addEventListener("input", () => limparErroDe(id)));
restaurarDados();
