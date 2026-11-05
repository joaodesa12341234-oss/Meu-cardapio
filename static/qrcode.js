"use strict";
/* Gerador de QR Code próprio (sem bibliotecas). Modo byte (UTF-8), correção de erro M, versões 1–10
   (até ~213 caracteres — sobra para qualquer endereço de cardápio).
   Uso: const matriz = gerarQR("https://meusite.com.br"); // matriz[linha][coluna] === true → módulo preto */

const QR_VERSOES_M = { // [codewords de correção por bloco, [[nº de blocos, codewords de dados]...]]
  1: [10, [[1, 16]]], 2: [16, [[1, 28]]], 3: [26, [[1, 44]]], 4: [18, [[2, 32]]], 5: [24, [[2, 43]]],
  6: [16, [[4, 27]]], 7: [18, [[4, 31]]], 8: [22, [[2, 38], [2, 39]]], 9: [22, [[3, 36], [2, 37]]],
  10: [26, [[4, 43], [1, 44]]],
};
const QR_ALINHAMENTO = { 1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38],
  8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50] };

/* ---- Galois GF(256) ---- */
const QR_EXP = new Array(512), QR_LOG = new Array(256);
(() => { let x = 1; for (let i = 0; i < 255; i++) { QR_EXP[i] = x; QR_LOG[x] = i; x <<= 1; if (x & 256) x ^= 0x11d; }
  for (let i = 255; i < 512; i++) QR_EXP[i] = QR_EXP[i - 255]; })();

function qrCorrecao(dados, n) {
  let gerador = [1];
  for (let i = 0; i < n; i++) {
    const prox = new Array(gerador.length + 1).fill(0);
    gerador.forEach((c, j) => {
      prox[j] ^= c;
      prox[j + 1] ^= c ? QR_EXP[QR_LOG[c] + i] : 0;
    });
    gerador = prox;
  }
  const resto = dados.concat(new Array(n).fill(0));
  for (let i = 0; i < dados.length; i++) {
    const f = resto[i];
    if (f) for (let j = 0; j < gerador.length; j++) resto[i + j] ^= gerador[j] ? QR_EXP[QR_LOG[f] + QR_LOG[gerador[j]]] : 0;
  }
  return resto.slice(dados.length);
}

function qrBits(valor, n) { const r = []; for (let i = n - 1; i >= 0; i--) r.push((valor >> i) & 1); return r; }

function qrBch(valor, poly, bitsPoly) {
  const grau = (x) => 31 - Math.clz32(x);
  let r = valor << grau(poly);
  while (grau(r) >= grau(poly)) r ^= poly << (grau(r) - grau(poly));
  return r;
}

function qrDadosCodewords(bytes, versao) {
  const [, blocos] = QR_VERSOES_M[versao];
  const totalDados = blocos.reduce((s, [n, d]) => s + n * d, 0);
  let bits = [0, 1, 0, 0].concat(qrBits(bytes.length, versao >= 10 ? 16 : 8));
  bytes.forEach((b) => bits.push(...qrBits(b, 8)));
  const cap = totalDados * 8;
  bits.push(...new Array(Math.min(4, cap - bits.length)).fill(0));
  while (bits.length % 8) bits.push(0);
  const out = [];
  for (let i = 0; i < bits.length; i += 8) out.push(parseInt(bits.slice(i, i + 8).join(""), 2));
  for (let pad = 0; out.length < totalDados; pad++) out.push(pad % 2 ? 0x11 : 0xec);
  return out;
}

function qrIntercalar(dados, versao) {
  const [nEc, grupos] = QR_VERSOES_M[versao];
  const blocos = []; let pos = 0;
  grupos.forEach(([n, d]) => { for (let i = 0; i < n; i++) { blocos.push(dados.slice(pos, pos + d)); pos += d; } });
  const ecs = blocos.map((b) => qrCorrecao(b, nEc));
  const saida = [];
  const maxD = Math.max(...blocos.map((b) => b.length));
  for (let i = 0; i < maxD; i++) blocos.forEach((b) => { if (i < b.length) saida.push(b[i]); });
  for (let i = 0; i < nEc; i++) ecs.forEach((e) => saida.push(e[i]));
  return saida;
}

function qrMascara(m, r, c) {
  switch (m) {
    case 0: return (r + c) % 2 === 0;
    case 1: return r % 2 === 0;
    case 2: return c % 3 === 0;
    case 3: return (r + c) % 3 === 0;
    case 4: return (Math.floor(r / 2) + Math.floor(c / 3)) % 2 === 0;
    case 5: return ((r * c) % 2) + ((r * c) % 3) === 0;
    case 6: return (((r * c) % 2) + ((r * c) % 3)) % 2 === 0;
    default: return (((r + c) % 2) + ((r * c) % 3)) % 2 === 0;
  }
}

function qrPenalidade(m) {
  const n = m.length; let p = 0;
  for (let t = 0; t < 2; t++) {
    for (let i = 0; i < n; i++) {
      let seq = 1;
      for (let j = 1; j < n; j++) {
        const a = t ? m[j][i] : m[i][j], b = t ? m[j - 1][i] : m[i][j - 1];
        if (a === b) { seq++; if (seq === 5) p += 3; else if (seq > 5) p++; } else seq = 1;
      }
    }
  }
  for (let i = 0; i < n - 1; i++) for (let j = 0; j < n - 1; j++)
    if (m[i][j] === m[i][j + 1] && m[i][j] === m[i + 1][j] && m[i][j] === m[i + 1][j + 1]) p += 3;
  const pad1 = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0], pad2 = pad1.slice().reverse();
  for (let t = 0; t < 2; t++) for (let i = 0; i < n; i++) for (let j = 0; j <= n - 11; j++) {
    let a = true, b = true;
    for (let k = 0; k < 11; k++) {
      const v = (t ? m[j + k][i] : m[i][j + k]) ? 1 : 0;
      if (v !== pad1[k]) a = false; if (v !== pad2[k]) b = false;
    }
    if (a) p += 40; if (b) p += 40;
  }
  let pretos = 0; m.forEach((l) => l.forEach((v) => { if (v) pretos++; }));
  p += Math.floor(Math.abs((pretos * 100) / (n * n) - 50) / 5) * 10;
  return p;
}

function gerarQR(texto) {
  const bytes = Array.from(new TextEncoder().encode(texto));
  let versao = 0;
  for (let v = 1; v <= 10; v++) {
    const d = QR_VERSOES_M[v][1].reduce((s, [n, k]) => s + n * k, 0);
    if (4 + (v >= 10 ? 16 : 8) + bytes.length * 8 <= d * 8) { versao = v; break; }
  }
  if (!versao) throw new Error("Endereço muito longo para o QR (máximo ~210 caracteres).");

  const n = 17 + 4 * versao;
  const mod = Array.from({ length: n }, () => new Array(n).fill(false));
  const reservado = Array.from({ length: n }, () => new Array(n).fill(false));
  const pôr = (r, c, v) => { mod[r][c] = v; reservado[r][c] = true; };

  const localizador = (r0, c0) => {
    for (let r = -1; r <= 7; r++) for (let c = -1; c <= 7; c++) {
      const rr = r0 + r, cc = c0 + c;
      if (rr < 0 || cc < 0 || rr >= n || cc >= n) continue;
      const preto = r >= 0 && r <= 6 && c >= 0 && c <= 6 && (r === 0 || r === 6 || c === 0 || c === 6 || (r >= 2 && r <= 4 && c >= 2 && c <= 4));
      pôr(rr, cc, preto);
    }
  };
  localizador(0, 0); localizador(0, n - 7); localizador(n - 7, 0);
  for (let i = 8; i < n - 8; i++) { pôr(6, i, i % 2 === 0); pôr(i, 6, i % 2 === 0); }
  const al = QR_ALINHAMENTO[versao];
  al.forEach((r) => al.forEach((c) => {
    const ult = al[al.length - 1];
    if ((r === 6 && c === 6) || (r === 6 && c === ult) || (r === ult && c === 6)) return; // colide com um localizador
    for (let dr = -2; dr <= 2; dr++) for (let dc = -2; dc <= 2; dc++)
      pôr(r + dr, c + dc, Math.max(Math.abs(dr), Math.abs(dc)) !== 1);
  }));
  pôr(n - 8, 8, true); // módulo escuro fixo
  for (let i = 0; i < 9; i++) { if (!reservado[8][i]) pôr(8, i, false); if (!reservado[i][8]) pôr(i, 8, false); }
  for (let i = 0; i < 8; i++) { if (!reservado[8][n - 1 - i]) pôr(8, n - 1 - i, false); if (!reservado[n - 1 - i][8]) pôr(n - 1 - i, 8, false); }
  if (versao >= 7) {
    const info = (versao << 12) | qrBch(versao, 0x1f25);
    for (let i = 0; i < 18; i++) {
      const v = ((info >> i) & 1) === 1, a = Math.floor(i / 3), b = (i % 3) + n - 11;
      pôr(a, b, v); pôr(b, a, v);
    }
  }

  const palavras = qrIntercalar(qrDadosCodewords(bytes, versao), versao);
  const fluxo = []; palavras.forEach((w) => fluxo.push(...qrBits(w, 8)));

  const colocar = (mascara) => {
    const m = mod.map((l) => l.slice());
    let k = 0, sobe = true;
    for (let col = n - 1; col > 0; col -= 2) {
      if (col === 6) col--;
      for (let i = 0; i < n; i++) {
        const r = sobe ? n - 1 - i : i;
        for (let c = col; c >= col - 1; c--) {
          if (reservado[r][c]) continue;
          let v = k < fluxo.length ? fluxo[k] === 1 : false; k++;
          if (qrMascara(mascara, r, c)) v = !v;
          m[r][c] = v;
        }
      }
      sobe = !sobe;
    }
    // informação de formato (nível M = 00)
    const dados = (0b00 << 3) | mascara;
    const fmt = ((dados << 10) | qrBch(dados, 0x537)) ^ 0x5412;
    const bit = (i) => ((fmt >> i) & 1) === 1;
    for (let i = 0; i <= 5; i++) m[i][8] = bit(i);
    m[7][8] = bit(6); m[8][8] = bit(7); m[8][7] = bit(8);
    for (let i = 9; i < 15; i++) m[8][14 - i] = bit(i);
    for (let i = 0; i < 8; i++) m[8][n - 1 - i] = bit(i);
    for (let i = 8; i < 15; i++) m[n - 15 + i][8] = bit(i);
    m[n - 8][8] = true;
    return m;
  };

  let melhor = null, melhorP = Infinity;
  for (let mk = 0; mk < 8; mk++) { const m = colocar(mk), p = qrPenalidade(m); if (p < melhorP) { melhorP = p; melhor = m; } }
  return melhor;
}

/** Desenha o QR num <canvas> (módulo = px, margem de 4 módulos). */
function desenharQR(canvas, texto, modulo) {
  const m = gerarQR(texto), margem = 4, tam = (m.length + margem * 2) * modulo;
  canvas.width = canvas.height = tam;
  const g = canvas.getContext("2d");
  g.fillStyle = "#fff"; g.fillRect(0, 0, tam, tam);
  g.fillStyle = "#000";
  m.forEach((l, r) => l.forEach((v, c) => { if (v) g.fillRect((c + margem) * modulo, (r + margem) * modulo, modulo, modulo); }));
}
