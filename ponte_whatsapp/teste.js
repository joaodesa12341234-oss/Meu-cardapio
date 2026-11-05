// Testa a camada HTTP com um WhatsApp falso (não precisa de npm install).
const assert = require("assert");
const { criarServidor } = require("./servidor");
let st = "open"; const enviados = [];
const g = { estado: () => st, enviar: async (n, t) => { enviados.push([n, t]); return "ID1"; } };
const s = criarServidor({ chave: "segredo", instancia: "lanchonete", gerenciador: g });
s.listen(0, "127.0.0.1", async () => {
  const b = `http://127.0.0.1:${s.address().port}`;
  const H = { apikey: "segredo", "Content-Type": "application/json" };
  let r = await fetch(`${b}/instance/connectionState/lanchonete`, { headers: { apikey: "errada" } });
  assert.equal(r.status, 401);
  r = await fetch(`${b}/instance/connectionState/lanchonete`, { headers: H });
  assert.equal((await r.json()).instance.state, "open");
  r = await fetch(`${b}/message/sendText/lanchonete`, { method: "POST", headers: H, body: JSON.stringify({ number: "5511999998888", text: "oi" }) });
  assert.equal(r.status, 201); assert.deepEqual(enviados[0], ["5511999998888", "oi"]);
  r = await fetch(`${b}/message/sendText/lanchonete`, { method: "POST", headers: H, body: JSON.stringify({ number: "1", text: "" }) });
  assert.equal(r.status, 400);
  st = "close";
  r = await fetch(`${b}/message/sendText/lanchonete`, { method: "POST", headers: H, body: JSON.stringify({ number: "5511999998888", text: "oi" }) });
  assert.equal(r.status, 503);
  r = await fetch(`${b}/outra`, { headers: H }); assert.equal(r.status, 404);
  console.log("OK: camada HTTP da ponte");
  s.close();
});
