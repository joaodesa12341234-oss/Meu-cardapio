"""Fixtures dos testes. Tudo roda num banco e numa pasta de fotos temporários: o seu cardapio.db nunca é tocado."""
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
_TMP = tempfile.mkdtemp(prefix="cardapio-teste-")
os.environ.update(
    DATABASE_PATH=os.path.join(_TMP, "teste.db"),
    UPLOADS_DIR=os.path.join(_TMP, "uploads"),
    WHATSAPP_ATIVO="0",
    ADMIN_SENHA="Senha-Forte-123",
    SECRET_KEY="chave-so-para-testes-com-mais-de-32-caracteres",
    SEM_VIGIA="1",
    SEM_ENV="1",
)
os.environ.pop("PUBLIC_URL", None)
sys.path.insert(0, str(RAIZ))

import pytest  # noqa: E402

import app as aplicacao  # noqa: E402
import db  # noqa: E402


@pytest.fixture()
def cliente():
    """Cliente HTTP anônimo (como um cliente da lanchonete)."""
    aplicacao.app.config["TESTING"] = True
    return aplicacao.app.test_client()


@pytest.fixture()
def admin():
    """Cliente já logado como admin. Devolve (cliente, csrf)."""
    c = aplicacao.app.test_client()
    c.get("/admin/login")
    with c.session_transaction() as s:
        token = s["csrf"]
    r = c.post("/admin/login", data={"usuario": "admin", "senha": "Senha-Forte-123", "csrf": token})
    assert r.status_code == 302, "login do admin falhou nos testes"
    with c.session_transaction() as s:
        token = s["csrf"]
    return c, token


@pytest.fixture()
def con():
    c = db.conectar()
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _limpa_pedidos():
    """Cada teste começa sem pedidos e sem limite de IP acumulado."""
    c = db.conectar()
    c.execute("DELETE FROM pedido_itens"); c.execute("DELETE FROM pedidos"); c.execute("DELETE FROM limite_pedidos")
    c.execute("DELETE FROM config WHERE chave = 'loja'")
    c.execute("DELETE FROM insumos"); c.execute("UPDATE produtos SET insumo_id = '', consumo = 1")
    c.execute("DELETE FROM cupons"); c.execute("DELETE FROM auditoria"); c.execute("DELETE FROM reativacoes"); c.execute("DELETE FROM config WHERE chave = 'relatorio_enviado'")
    c.commit(); c.close()
    yield


@pytest.fixture()
def produto_id(cliente):
    return cliente.get("/api/menu").get_json()["categorias"][0]["produtos"][0]["id"]


def pedido(cliente, produto_id, **extra):
    corpo = dict(itens=[{"id": produto_id, "qtd": 2}], tipo="balcao", nome="Ana",
                 telefone="(75) 98765-4321", pagamento="pix")
    corpo.update(extra)
    return cliente.post("/api/pedido", json=corpo)
