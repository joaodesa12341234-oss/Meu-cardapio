// Camada HTTP da ponte: imita os 2 endpoints da Evolution API usados pelo cardápio.
// Não depende do WhatsApp: recebe um "gerenciador" com estado() e enviar(numero, texto).
const http = require("http");
const crypto = require("crypto");

function iguais(a, b) {
  const x = Buffer.from(String(a)), y = Buffer.from(String(b));
  return x.length === y.length && crypto.timingSafeEqual(x, y);
}

function criarServidor({ chave, instancia, gerenciador }) {
  return http.createServer((req, res) => {
    const responder = (codigo, corpo) => {
      const txt = JSON.stringify(corpo);
      res.writeHead(codigo, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(txt) });
      res.end(txt);
    };
    if (!chave || !iguais(req.headers["apikey"] || "", chave)) {
      return responder(401, { status: 401, error: "Unauthorized" });
    }
    const url = (req.url || "").split("?")[0];
    const estado = `/instance/connectionState/${instancia}`;
    const envio = `/message/sendText/${instancia}`;

    if (req.method === "GET" && url === estado) {
      return responder(200, { instance: { instanceName: instancia, state: gerenciador.estado() } });
    }
    if (req.method === "POST" && url === envio) {
      let dados = "";
      req.on("data", (c) => { dados += c; if (dados.length > 100000) req.destroy(); });
      req.on("end", async () => {
        let j;
        try { j = JSON.parse(dados || "{}"); } catch { return responder(400, { error: "JSON inválido" }); }
        const numero = String(j.number || "").replace(/\D/g, "");
        const texto = String(j.text || "");
        if (numero.length < 10 || !texto) return responder(400, { error: "number e text são obrigatórios" });
        if (gerenciador.estado() !== "open") return responder(503, { error: "WhatsApp desconectado" });
        try {
          const id = await gerenciador.enviar(numero, texto);
          responder(201, { key: { id }, status: "PENDING" });
        } catch (e) {
          responder(e.codigo || 500, { error: String(e.message || e) });
        }
      });
      return;
    }
    responder(404, { error: "Rota inexistente" });
  });
}

module.exports = { criarServidor };
