import pytest

import db
from conftest import pedido
from test_bairros_dono_cozinha import _Evolution, _esperar, _salvar_loja, evolution  # noqa: F401


def _H(csrf):
    return {"X-CSRF-Token": csrf}


def _prod(c, csrf, cliente, nome, **extra):
    cat = cliente.get("/api/menu").get_json()["categorias"][0]["id"]
    r = c.post("/admin/api/produtos", json=dict({"categoria_id": cat, "nome": nome, "preco": "10", "imagem": ""}, **extra), headers=_H(csrf))
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def _insumo(c, csrf, nome="Pão", qtd=10, minimo=3):
    r = c.post("/admin/api/insumos", json={"nome": nome, "quantidade": qtd, "minimo": minimo}, headers=_H(csrf))
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def _qtd(c, ins_id):
    return next(i for i in c.get("/admin/api/insumos").get_json() if i["id"] == ins_id)["quantidade"]


def _item(cliente, pid):
    return next(p for cat in cliente.get("/api/menu").get_json()["categorias"] for p in cat["produtos"] if p["id"] == pid)


def test_um_estoque_para_varios_lanches(cliente, admin):
    c, csrf = admin
    pao = _insumo(c, csrf, qtd=5)
    a = _prod(c, csrf, cliente, "Lanche A", insumo_id=pao)
    b = _prod(c, csrf, cliente, "Lanche B", insumo_id=pao)
    assert pedido(cliente, a, itens=[{"id": a, "qtd": 2}, {"id": b, "qtd": 1}]).status_code == 201
    assert _qtd(c, pao) == 2                                             # 3 pães gastos no total
    assert _item(cliente, a)["restam"] == 2 and _item(cliente, b)["restam"] == 2


def test_acaba_o_pao_e_todos_somem_do_cardapio(cliente, admin):
    c, csrf = admin
    pao = _insumo(c, csrf, qtd=2)
    a = _prod(c, csrf, cliente, "Lanche A", insumo_id=pao)
    b = _prod(c, csrf, cliente, "Lanche B", insumo_id=pao)
    sem_pao = _prod(c, csrf, cliente, "Suco")                           # não usa pão
    assert pedido(cliente, a, itens=[{"id": a, "qtd": 2}]).status_code == 201
    assert _item(cliente, a)["disponivel"] is False and _item(cliente, b)["disponivel"] is False
    assert _item(cliente, sem_pao)["disponivel"] is True
    assert pedido(cliente, b, itens=[{"id": b, "qtd": 1}]).status_code == 409
    assert pedido(cliente, sem_pao, itens=[{"id": sem_pao, "qtd": 1}]).status_code == 201


def test_pedido_maior_que_o_estoque_e_recusado_inteiro(cliente, admin):
    c, csrf = admin
    pao = _insumo(c, csrf, qtd=3)
    a = _prod(c, csrf, cliente, "Lanche A", insumo_id=pao)
    b = _prod(c, csrf, cliente, "Lanche B", insumo_id=pao)
    r = pedido(cliente, a, itens=[{"id": a, "qtd": 2}, {"id": b, "qtd": 2}])     # pediria 4 pães
    assert r.status_code == 409 and "pão" in r.get_json()["erro"].lower()
    assert _qtd(c, pao) == 3                                             # nada foi descontado


def test_consumo_por_unidade(cliente, admin):
    c, csrf = admin
    pao = _insumo(c, csrf, qtd=5)
    combo = _prod(c, csrf, cliente, "Combo duplo", insumo_id=pao, consumo="2")
    assert _item(cliente, combo)["restam"] == 2                          # 5 // 2
    assert pedido(cliente, combo, itens=[{"id": combo, "qtd": 2}]).status_code == 201
    assert _qtd(c, pao) == 1 and _item(cliente, combo)["disponivel"] is False   # sobrou 1, combo pede 2


def test_cancelar_devolve_paes_e_volta_ao_cardapio(cliente, admin):
    c, csrf = admin
    pao = _insumo(c, csrf, qtd=2)
    a = _prod(c, csrf, cliente, "Lanche A", insumo_id=pao)
    senha = pedido(cliente, a, itens=[{"id": a, "qtd": 2}]).get_json()["senha"]
    assert _item(cliente, a)["disponivel"] is False
    c.patch(f"/admin/api/pedidos/{senha}/status", json={"status": "cancelado"}, headers=_H(csrf))
    c.patch(f"/admin/api/pedidos/{senha}/status", json={"status": "cancelado"}, headers=_H(csrf))   # repetir não devolve 2x
    assert _qtd(c, pao) == 2 and _item(cliente, a)["disponivel"] is True


def test_repor_estoque_reativa(cliente, admin):
    c, csrf = admin
    pao = _insumo(c, csrf, qtd=0)
    a = _prod(c, csrf, cliente, "Lanche A", insumo_id=pao)
    assert _item(cliente, a)["disponivel"] is False
    assert c.put(f"/admin/api/insumos/{pao}", json={"quantidade": 30}, headers=_H(csrf)).status_code == 200
    assert _item(cliente, a)["disponivel"] is True


def test_vincular_categoria_inteira(cliente, admin):
    c, csrf = admin
    pao = _insumo(c, csrf, qtd=20)
    cat = cliente.get("/api/menu").get_json()["categorias"][0]
    r = c.post(f"/admin/api/insumos/{pao}/vincular", json={"categoria_id": cat["id"], "consumo": 1}, headers=_H(csrf))
    assert r.status_code == 200 and r.get_json()["produtos"] == len(cat["produtos"]) > 0
    assert next(i for i in c.get("/admin/api/insumos").get_json() if i["id"] == pao)["produtos"] == len(cat["produtos"])
    primeiro = cat["produtos"][0]["id"]
    pedido(cliente, primeiro, itens=[{"id": primeiro, "qtd": 3}])
    assert _qtd(c, pao) == 17


def test_excluir_estoque_solta_os_produtos(cliente, admin):
    c, csrf = admin
    pao = _insumo(c, csrf, qtd=0)
    a = _prod(c, csrf, cliente, "Lanche A", insumo_id=pao)
    assert _item(cliente, a)["disponivel"] is False
    assert c.delete(f"/admin/api/insumos/{pao}", headers=_H(csrf)).status_code == 200
    assert _item(cliente, a)["disponivel"] is True


def test_estoque_do_produto_e_geral_valem_juntos(cliente, admin):
    c, csrf = admin
    pao = _insumo(c, csrf, qtd=10)
    a = _prod(c, csrf, cliente, "Lanche A", insumo_id=pao, estoque="2")        # produto limita a 2
    assert _item(cliente, a)["restam"] == 2
    assert pedido(cliente, a, itens=[{"id": a, "qtd": 3}]).status_code == 409


@pytest.mark.parametrize("corpo", [{"nome": "", "quantidade": 5}, {"nome": "Pão", "quantidade": -1},
                                   {"nome": "Pão", "quantidade": "abc"}, {"nome": "Pão", "quantidade": 1.5},
                                   {"nome": "Pão", "quantidade": 5, "minimo": -2}])
def test_insumo_invalido(admin, corpo):
    c, csrf = admin
    assert c.post("/admin/api/insumos", json=corpo, headers=_H(csrf)).status_code == 400


def test_nome_repetido_e_produto_com_estoque_inexistente(cliente, admin):
    c, csrf = admin
    _insumo(c, csrf, nome="Pão")
    assert c.post("/admin/api/insumos", json={"nome": "pão", "quantidade": 1}, headers=_H(csrf)).status_code == 400
    cat = cliente.get("/api/menu").get_json()["categorias"][0]["id"]
    r = c.post("/admin/api/produtos", json={"categoria_id": cat, "nome": "X", "preco": "5", "imagem": "", "insumo_id": "naoexiste"}, headers=_H(csrf))
    assert r.status_code == 400


def test_so_admin_mexe_no_estoque_geral(cliente):
    assert cliente.post("/admin/api/insumos", json={"nome": "Pão", "quantidade": 5}).status_code in (401, 403, 302)
    assert cliente.get("/admin/api/insumos").status_code in (401, 403, 302)


def test_dono_e_avisado_quando_o_estoque_baixa(cliente, admin, evolution):   # noqa: F811
    c, csrf = admin
    _salvar_loja(c, csrf, dono_telefone="(75) 99999-0000")
    pao = _insumo(c, csrf, qtd=6, minimo=3)
    a = _prod(c, csrf, cliente, "Lanche A", insumo_id=pao)
    pedido(cliente, a, itens=[{"id": a, "qtd": 2}], telefone="(75) 98765-4321")        # 4 restam: acima do mínimo
    _esperar(evolution, 2)
    antes = len(evolution)
    pedido(cliente, a, itens=[{"id": a, "qtd": 2}], telefone="(75) 98765-4321")        # 2 restam: avisa
    pedido(cliente, a, itens=[{"id": a, "qtd": 1}], telefone="(75) 98765-4321")        # 1 resta: NÃO repete o aviso
    _esperar(evolution, antes + 4)
    avisos = [m for m in evolution if "Estoque baixo" in m["text"]]
    assert len(avisos) == 1 and "2 de Pão" in avisos[0]["text"] and avisos[0]["number"] == "5575999990000"
