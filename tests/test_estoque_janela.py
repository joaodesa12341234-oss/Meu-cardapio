from datetime import datetime

import pytest

import db
from conftest import pedido


def _criar(c, csrf, cliente, **extra):
    cat = cliente.get("/api/menu").get_json()["categorias"][0]["id"]
    corpo = {"categoria_id": cat, "nome": "Item Teste", "preco": "10", "imagem": ""}
    corpo.update(extra)
    r = c.post("/admin/api/produtos", json=corpo, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def _menu_item(cliente, pid):
    return next(p for cat in cliente.get("/api/menu").get_json()["categorias"] for p in cat["produtos"] if p["id"] == pid)



# ----------------------------------------------------------- estoque ----
def test_estoque_baixa_e_esgota(cliente, admin):
    c, csrf = admin
    pid = _criar(c, csrf, cliente, estoque="3")
    assert pedido(cliente, pid, itens=[{"id": pid, "qtd": 2}]).status_code == 201
    m = _menu_item(cliente, pid)
    assert m["restam"] == 1 and m["disponivel"] is True
    assert pedido(cliente, pid, itens=[{"id": pid, "qtd": 2}]).status_code == 409           # só resta 1
    assert pedido(cliente, pid, itens=[{"id": pid, "qtd": 1}]).status_code == 201
    m = _menu_item(cliente, pid)
    assert m["disponivel"] is False and m["restam"] == 0                                    # zerou: some sozinho
    assert pedido(cliente, pid, itens=[{"id": pid, "qtd": 1}]).status_code == 409


def test_estoque_conta_linhas_com_opcoes_juntas(cliente, admin):
    c, csrf = admin
    pid = _criar(c, csrf, cliente, estoque="2", extras_texto="Bacon; 3")
    itens = [{"id": pid, "qtd": 1, "extras": ["Bacon"]}, {"id": pid, "qtd": 2}]
    assert pedido(cliente, pid, itens=itens).status_code == 409                              # 3 > 2 no total


def test_cancelar_devolve_estoque_e_reativa(cliente, admin):
    c, csrf = admin
    pid = _criar(c, csrf, cliente, estoque="2")
    senha = pedido(cliente, pid, itens=[{"id": pid, "qtd": 2}]).get_json()["senha"]
    assert _menu_item(cliente, pid)["disponivel"] is False
    c.patch(f"/admin/api/pedidos/{senha}/status", json={"status": "cancelado"}, headers={"X-CSRF-Token": csrf})
    m = _menu_item(cliente, pid)
    assert m["disponivel"] is True and c.get("/admin/api/produtos-extra").get_json()[pid]["estoque"] == 2
    c.patch(f"/admin/api/pedidos/{senha}/status", json={"status": "cancelado"}, headers={"X-CSRF-Token": csrf})   # repetir não devolve 2x
    assert c.get("/admin/api/produtos-extra").get_json()[pid]["estoque"] == 2


def test_nao_reativa_item_zerado_sem_repor(cliente, admin):
    c, csrf = admin
    pid = _criar(c, csrf, cliente, estoque="0")
    assert _menu_item(cliente, pid)["disponivel"] is False
    r = c.patch(f"/admin/api/produtos/{pid}/disponibilidade", json={"disponivel": True}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400
    c.put(f"/admin/api/produtos/{pid}", json={"estoque": "5"}, headers={"X-CSRF-Token": csrf})      # repor reativa
    assert _menu_item(cliente, pid)["disponivel"] is True


def test_sem_estoque_informado_nao_controla(cliente, admin):
    c, csrf = admin
    pid = _criar(c, csrf, cliente)
    assert pedido(cliente, pid, itens=[{"id": pid, "qtd": 50}]).status_code == 201
    assert _menu_item(cliente, pid)["restam"] is None


@pytest.mark.parametrize("valor", ["abc", "-1", "1.5", "100000"])
def test_estoque_invalido(cliente, admin, valor):
    c, csrf = admin
    cat = cliente.get("/api/menu").get_json()["categorias"][0]["id"]
    r = c.post("/admin/api/produtos", json={"categoria_id": cat, "nome": "X", "preco": "5", "imagem": "", "estoque": valor},
               headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400


def test_estoque_nao_vaza_no_cardapio_publico(cliente, admin):
    c, csrf = admin
    pid = _criar(c, csrf, cliente, estoque="500")
    assert _menu_item(cliente, pid)["restam"] is None                      # só aparece quando restam poucas
    assert "estoque" not in cliente.get("/api/menu").get_data(as_text=True)


# ------------------------------------------------------------ horário ----
def _fora_e_dentro():
    h = datetime.now(db.FUSO).hour
    dentro = (f"{(h - 1) % 24:02d}:00", f"{(h + 2) % 24:02d}:00")          # janela que contém "agora"
    fora = (f"{(h + 3) % 24:02d}:00", f"{(h + 5) % 24:02d}:00")
    return dentro, fora


def test_produto_dentro_do_horario(cliente, admin):
    c, csrf = admin
    (ini, fim), _ = _fora_e_dentro()
    pid = _criar(c, csrf, cliente, janela_ini=ini, janela_fim=fim)
    assert _menu_item(cliente, pid)["disponivel"] is True and pedido(cliente, pid, itens=[{"id": pid, "qtd": 1}]).status_code == 201


def test_produto_fora_do_horario(cliente, admin):
    c, csrf = admin
    _, (ini, fim) = _fora_e_dentro()
    pid = _criar(c, csrf, cliente, janela_ini=ini, janela_fim=fim)
    m = _menu_item(cliente, pid)
    assert m["disponivel"] is False and m["ativo"] is True and f"{ini} às {fim}" in m["aviso"]
    assert pedido(cliente, pid, itens=[{"id": pid, "qtd": 1}]).status_code == 409


def test_janela_que_vira_a_noite():
    p = {"janela_ini": "18:00", "janela_fim": "02:00"}
    ag = lambda h, m=0: datetime(2026, 11, 3, h, m, tzinfo=db.FUSO)
    assert db.produto_no_horario(p, ag(23))[0] and db.produto_no_horario(p, ag(1, 30))[0]
    assert not db.produto_no_horario(p, ag(2)) [0] and not db.produto_no_horario(p, ag(12))[0]
    assert db.produto_no_horario({"janela_ini": "", "janela_fim": ""}, ag(3))[0]


@pytest.mark.parametrize("ini,fim", [("14:00", ""), ("", "22:00"), ("25:00", "22:00"), ("10:00", "10:00"), ("abc", "def")])
def test_janela_invalida(cliente, admin, ini, fim):
    c, csrf = admin
    cat = cliente.get("/api/menu").get_json()["categorias"][0]["id"]
    r = c.post("/admin/api/produtos", json={"categoria_id": cat, "nome": "X", "preco": "5", "imagem": "",
                                            "janela_ini": ini, "janela_fim": fim}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400
