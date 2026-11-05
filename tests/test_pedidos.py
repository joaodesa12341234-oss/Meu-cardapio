import pytest

import db
from conftest import pedido


def test_pedido_valido_calcula_total(cliente, produto_id):
    r = pedido(cliente, produto_id)
    assert r.status_code == 201
    assert r.get_json()["total"] == pytest.approx(37.8)


@pytest.mark.parametrize("ruim", [
    dict(itens=[{"id": "x", "qtd": "abc"}]),
    dict(itens=["lixo"]),
    dict(telefone="123"),
    dict(tipo="voando"),
    dict(nome="a" * 500),
    dict(pagamento=""),
])
def test_pedido_invalido_e_recusado(cliente, produto_id, ruim):
    if "itens" in ruim:
        ruim["itens"] = [dict(i, id=produto_id) if isinstance(i, dict) else i for i in ruim["itens"]]
    assert pedido(cliente, produto_id, **ruim).status_code == 400


def test_quantidade_absurda_e_recusada(cliente, produto_id):
    assert pedido(cliente, produto_id, itens=[{"id": produto_id, "qtd": 999}]).status_code == 400


def test_entrega_exige_endereco_e_cobra_taxa(cliente, admin, produto_id):
    c, csrf = admin
    c.put("/admin/api/loja", json={"modo": "sempre", "taxa_entrega": "5,00", "pedido_minimo": "0",
                                    "dias": c.get("/admin/api/loja").get_json()["dias"]},
          headers={"X-CSRF-Token": csrf})
    sem = pedido(cliente, produto_id, tipo="entrega")
    assert sem.status_code == 400
    ok = pedido(cliente, produto_id, tipo="entrega", rua="Rua A", numero="10", bairro="Centro")
    assert ok.status_code == 201 and ok.get_json()["taxa_entrega"] == pytest.approx(5.0)


def test_limite_de_pedidos_por_ip(cliente, produto_id, monkeypatch):
    monkeypatch.setattr(db, "PEDIDOS_MAX", 3)
    codigos = [pedido(cliente, produto_id).status_code for _ in range(4)]
    assert codigos[:3] == [201, 201, 201] and codigos[3] == 429


def test_loja_fechada_recusa_pedido(cliente, admin, produto_id):
    c, csrf = admin
    cfg = c.get("/admin/api/loja").get_json()
    cfg["modo"] = "fechado"
    assert c.put("/admin/api/loja", json=cfg, headers={"X-CSRF-Token": csrf}).status_code == 200
    assert pedido(cliente, produto_id).status_code == 409


def test_status_do_pedido_para_o_cliente(cliente, produto_id):
    senha = pedido(cliente, produto_id).get_json()["senha"]
    assert cliente.get(f"/api/pedido-cliente/{senha}/status").get_json()["status"] == "novo"
    assert cliente.get("/api/pedido-cliente/99999/status").status_code == 404


def test_endereco_separado_e_montado(cliente, produto_id):
    r = pedido(cliente, produto_id, tipo="entrega", rua="Rua das Flores", numero="120A", bairro="Centro",
               complemento="Apto 3", referencia="perto da padaria")
    assert r.status_code == 201
    d = r.get_json()
    assert (d["rua"], d["numero"], d["bairro"]) == ("Rua das Flores", "120A", "Centro")
    assert d["endereco"] == "Rua das Flores, 120A - Apto 3 - Centro (ref.: perto da padaria)"


def test_sem_numero(cliente, produto_id):
    r = pedido(cliente, produto_id, tipo="entrega", rua="Estrada Velha", sem_numero=True, bairro="Zona Rural")
    assert r.status_code == 201 and r.get_json()["numero"] == "S/N"


@pytest.mark.parametrize("faltando", [
    dict(rua=""), dict(rua="ab"), dict(numero=""), dict(numero="<b>"), dict(bairro=""), dict(bairro="x"),
])
def test_entrega_valida_cada_campo(cliente, produto_id, faltando):
    corpo = dict(tipo="entrega", rua="Rua A", numero="10", bairro="Centro")
    corpo.update(faltando)
    assert pedido(cliente, produto_id, **corpo).status_code == 400


def test_balcao_ignora_endereco(cliente, produto_id):
    d = pedido(cliente, produto_id, rua="Rua A", numero="1", bairro="B").get_json()
    assert d["endereco"] == "" and d["rua"] == ""


def test_cliente_antigo_com_endereco_unico_ainda_funciona(cliente, produto_id):
    r = pedido(cliente, produto_id, tipo="entrega", endereco="Rua A, 10, Centro")
    assert r.status_code == 201 and r.get_json()["endereco"] == "Rua A, 10, Centro"


def test_financeiro_agrupa_por_bairro(cliente, admin, produto_id):
    from datetime import date
    c, csrf = admin
    for bairro in ("centro", "Centro", "Alto"):
        senha = pedido(cliente, produto_id, tipo="entrega", rua="Rua A", numero="1", bairro=bairro).get_json()["senha"]
        for st in ("fazendo", "pronto", "concluido"):
            c.patch(f"/admin/api/pedidos/{senha}/status", json={"status": st}, headers={"X-CSRF-Token": csrf})
    hoje = date.today().isoformat()
    por = c.get(f"/admin/api/financeiro?inicio={hoje}&fim={hoje}").get_json()["por_bairro"]
    assert por[0]["bairro"] == "Centro" and por[0]["pedidos"] == 2
