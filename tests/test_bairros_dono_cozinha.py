import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conftest import pedido


def _salvar_loja(c, csrf, **extra):
    cfg = c.get("/admin/api/loja").get_json()
    corpo = {"modo": "sempre", "dias": cfg["dias"], "taxa_entrega": "4,00", "pedido_minimo": "0"}
    corpo.update(extra)
    return c.put("/admin/api/loja", json=corpo, headers={"X-CSRF-Token": csrf})


# ---------------------------------------------------------- bairros ----
def test_bairros_cobram_taxa_propria(cliente, admin, produto_id):
    c, csrf = admin
    r = _salvar_loja(c, csrf, bairros_modo="lista", bairros_texto="Centro; 5,00\nAlto da Boa Vista; 7,50\nVila Nova")
    assert r.status_code == 200
    publico = cliente.get("/api/menu").get_json()["loja"]
    assert publico["bairros_modo"] == "lista" and [b["nome"] for b in publico["bairros"]] == ["Centro", "Alto da Boa Vista", "Vila Nova"]

    base = dict(tipo="entrega", rua="Rua A", numero="1")
    d = pedido(cliente, produto_id, bairro="alto da boa vista", **base).get_json()
    assert d["taxa_entrega"] == pytest.approx(7.5) and d["bairro"] == "Alto da Boa Vista"      # nome oficial, taxa do bairro
    assert pedido(cliente, produto_id, bairro="CENTRO", **base).get_json()["taxa_entrega"] == pytest.approx(5.0)
    assert pedido(cliente, produto_id, bairro="Vila Nova", **base).get_json()["taxa_entrega"] == pytest.approx(4.0)  # sem valor = taxa única
    assert pedido(cliente, produto_id, bairro="Bairro Distante", **base).status_code == 400
    assert pedido(cliente, produto_id, tipo="entrega", endereco="Rua A, 1, Centro").status_code == 400  # cliente antigo
    assert pedido(cliente, produto_id).status_code == 201                                       # balcão não é afetado


def test_acentos_nao_atrapalham(cliente, admin, produto_id):
    c, csrf = admin
    _salvar_loja(c, csrf, bairros_modo="lista", bairros_texto="São José; 3,00")
    d = pedido(cliente, produto_id, tipo="entrega", rua="Rua A", numero="1", bairro="sao jose").get_json()
    assert d["bairro"] == "São José"


@pytest.mark.parametrize("texto", ["", "A; 5", "Centro; 5\ncentro; 6", "Centro; abc", "Centro; -2"])
def test_lista_de_bairros_invalida(admin, texto):
    c, csrf = admin
    assert _salvar_loja(c, csrf, bairros_modo="lista", bairros_texto=texto).status_code == 400


def test_modo_livre_ignora_a_lista(cliente, admin, produto_id):
    c, csrf = admin
    _salvar_loja(c, csrf, bairros_modo="livre", bairros_texto="Centro; 5,00")
    d = pedido(cliente, produto_id, tipo="entrega", rua="Rua A", numero="1", bairro="Qualquer").get_json()
    assert d["taxa_entrega"] == pytest.approx(4.0)


# ------------------------------------------------------ alerta do dono ----
class _Evolution(BaseHTTPRequestHandler):
    recebidas = []

    def do_POST(self):
        corpo = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        _Evolution.recebidas.append(json.loads(corpo))
        self.send_response(201); self.end_headers(); self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


@pytest.fixture()
def evolution(monkeypatch):
    _Evolution.recebidas = []
    srv = HTTPServer(("127.0.0.1", 0), _Evolution)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("EVOLUTION_BASE_URL", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setenv("EVOLUTION_API_KEY", "chave-de-teste")
    monkeypatch.setenv("WHATSAPP_ATIVO", "1")
    yield _Evolution.recebidas
    srv.shutdown()


def _esperar(recebidas, n, limite=3.0):
    fim = time.time() + limite
    while len(recebidas) < n and time.time() < fim:
        time.sleep(0.05)


def test_dono_recebe_aviso_de_pedido_novo(cliente, admin, produto_id, evolution):
    c, csrf = admin
    assert _salvar_loja(c, csrf, dono_telefone="(75) 99999-0000").status_code == 200
    assert c.get("/admin/api/loja").get_json()["dono_telefone"] == "75999990000"
    pedido(cliente, produto_id, telefone="(75) 98765-4321")
    _esperar(evolution, 2)
    destinos = {m["number"]: m["text"] for m in evolution}
    assert "5575987654321" in destinos                      # cliente: pedido recebido
    assert "5575999990000" in destinos                      # dono: alerta
    assert "Novo pedido" in destinos["5575999990000"] and "X-Burger" in destinos["5575999990000"]


def test_sem_numero_do_dono_nao_envia_alerta(cliente, admin, produto_id, evolution):
    c, csrf = admin
    _salvar_loja(c, csrf, dono_telefone="")
    pedido(cliente, produto_id, telefone="(75) 98765-4321")
    _esperar(evolution, 1)
    time.sleep(0.3)
    assert [m["number"] for m in evolution] == ["5575987654321"]


def test_telefone_do_dono_invalido(admin):
    c, csrf = admin
    assert _salvar_loja(c, csrf, dono_telefone="123").status_code == 400


# ------------------------------------------------------------ cozinha ----
def test_tela_da_cozinha(cliente, admin):
    assert cliente.get("/admin/cozinha").status_code in (302, 401, 403)
    c, _ = admin
    r = c.get("/admin/cozinha")
    assert r.status_code == 200 and b"cozinha.js" in r.data and b"<script>" not in r.data
