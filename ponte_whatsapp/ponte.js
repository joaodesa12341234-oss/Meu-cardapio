// Ponte WhatsApp (sem Docker) para o cardápio. Imita a Evolution API.
const fs = require("fs");
const path = require("path");

function lerEnv(arquivo) {
  let txt;
  try { txt = fs.readFileSync(arquivo, "utf8"); }
  catch { return; }
  console.log("Lendo " + arquivo);
  for (const linha of txt.replace(/^\uFEFF/, "").split(/\r?\n/)) {
    const m = linha.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$/);
    if (m && !process.env[m[1]]) process.env[m[1]] = m[2].replace(/^["']|["']$/g, "");
  }
}
// procura o .env na pasta da ponte e nas 3 pastas acima (o primeiro que existir)
for (const nivel of ["", "..", "../..", "../../.."]) {
  const caminho = path.resolve(__dirname, nivel, ".env");
  if (fs.existsSync(caminho)) { lerEnv(caminho); break; }
}
if (!process.env.EVOLUTION_API_KEY) console.log("Nenhum .env com a chave foi encontrado perto de " + __dirname);


const chave = (process.env.EVOLUTION_API_KEY || "").trim();
const instancia = process.env.EVOLUTION_INSTANCE || "lanchonete";
const base = new URL(process.env.EVOLUTION_BASE_URL || "http://127.0.0.1:8080");
const porta = Number(base.port || 8080);
const telefonePareamento = (process.env.PONTE_PAREAR_NUMERO || "").replace(/\D/g, "");

if (!chave) {
  console.error("\nERRO: EVOLUTION_API_KEY está vazia ou ausente no .env (veja acima qual arquivo foi lido).\nConfira: nome exato \".env\" (não \".env.txt\"), linha sem # no começo, e arquivo salvo com Ctrl+S.\n");
  process.exit(1);
}

let baileys, pino, qrcode;
try {
  baileys = require("@whiskeysockets/baileys");
  pino = require("pino");
  qrcode = require("qrcode-terminal");
} catch (e) {
  console.error("\nFaltam as dependências. Rode 'npm install' dentro da pasta ponte_whatsapp.\n", e.message);
  process.exit(1);
}
const { criarServidor } = require("./servidor");
const makeWASocket = baileys.default || baileys.makeWASocket;
const { useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion, Browsers } = baileys;

let sock = null;
let estado = "close";
let tentativas = 0;

const gerenciador = {
  estado: () => estado,
  async enviar(numero, texto) {
    if (!sock || estado !== "open") throw Object.assign(new Error("WhatsApp desconectado"), { codigo: 503 });
    const r = await sock.onWhatsApp(numero);
    const alvo = r && r[0];
    if (!alvo || !alvo.exists) throw Object.assign(new Error("Número não tem WhatsApp"), { codigo: 400 });
    const env = await sock.sendMessage(alvo.jid, { text: texto });
    return env && env.key && env.key.id;
  },
};

async function conectar() {
  const { state, saveCreds } = await useMultiFileAuthState(path.join(__dirname, "auth"));
  let version;
  try { version = (await fetchLatestBaileysVersion()).version; } catch {}
  estado = "connecting";
  sock = makeWASocket({
    version,
    auth: state,
    logger: pino({ level: "silent" }),
    browser: Browsers ? Browsers.macOS("Desktop") : undefined,
    printQRInTerminal: false,
    markOnlineOnConnect: false,
    syncFullHistory: false,
  });
  sock.ev.on("creds.update", saveCreds);

  if (!state.creds.registered && telefonePareamento) {
    setTimeout(async () => {
      try {
        const codigo = await sock.requestPairingCode(telefonePareamento);
        console.log(`\nCódigo de pareamento: ${codigo}\nNo celular: WhatsApp > Aparelhos conectados > Conectar com número de telefone.\n`);
      } catch (e) { console.error("Falha ao gerar código:", e.message); }
    }, 3000);
  }

  sock.ev.on("connection.update", (u) => {
    const { connection, lastDisconnect, qr } = u;
    if (qr && !telefonePareamento) {
      console.log("\nEscaneie o QR code: WhatsApp > Aparelhos conectados > Conectar um aparelho\n");
      qrcode.generate(qr, { small: true });
    }
    if (connection === "open") {
      estado = "open"; tentativas = 0;
      console.log("✔ WhatsApp conectado. Pode usar o cardápio.");
    } else if (connection === "connecting") {
      estado = "connecting";
    } else if (connection === "close") {
      estado = "close";
      const codigo = lastDisconnect && lastDisconnect.error && lastDisconnect.error.output && lastDisconnect.error.output.statusCode;
      if (codigo === DisconnectReason.loggedOut) {
        console.log("Sessão encerrada pelo celular. Apague a pasta ponte_whatsapp\\auth e abra de novo para ler outro QR.");
        return;
      }
      const espera = Math.min(30000, 2000 * ++tentativas);
      console.log(`Conexão caiu (${codigo || "?"}). Tentando de novo em ${espera / 1000}s...`);
      setTimeout(() => conectar().catch((e) => console.error(e.message)), espera);
    }
  });
}

const servidor = criarServidor({ chave, instancia, gerenciador });
servidor.on("error", (e) => {
  if (e.code === "EADDRINUSE") {
    console.error(`\nA porta ${porta} já está em uso. Provavelmente a ponte já está aberta em outra janela.\nFeche as outras janelas da ponte (ou rode: netstat -ano | findstr :${porta}) e abra de novo.\n`);
    process.exit(2);
  }
  console.error("Erro no servidor:", e.message);
  process.exit(1);
});
servidor.listen(porta, "127.0.0.1", () => {
  console.log(`Ponte WhatsApp ouvindo em http://127.0.0.1:${porta} (instância "${instancia}")`);
  conectar().catch((e) => console.error("Erro ao conectar:", e));
});
process.on("unhandledRejection", (e) => console.error("Erro:", e && e.message));
