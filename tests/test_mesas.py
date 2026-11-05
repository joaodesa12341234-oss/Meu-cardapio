import pytest
from conftest import pedido


def H(csrf):
    return {"X-CSRF-Token": csrf}


def mesas(admin, n):
    c, csrf = admin
    cfg = c.get("/admin/api/loja").get_json()
    cfg["mesas"] = n
    assert c.put("/admin/api/loja", json=cfg, headers=H(csrf)).status_code == 200


def test_mesa_desligada_recusa(cliente, produto_id):
    r = pedido(cliente, produto_id, mesa="3")
    assert r.status_code == 400 and "mesa" in r.get_json()["erro"].lower()


def test_pedido_de_mesa(cliente, admin, produto_id):
    mesas(admin, 10)
    assert cliente.get("/api/menu").get_json()["loja"]["mesas"] == 10
    r = pedido(cliente, produto_id, mesa="7")
    assert r.status_code == 201, r.get_data(as_text=True)
    j = r.get_json()
    assert j["mesa"] == "7" and j["taxa_entrega"] == 0 and j["tipo"] == "balcao"
    c, _ = admin
    ativos = c.get("/admin/api/pedidos").get_json()
    assert ativos[0]["mesa"] == "7"
    assert cliente.get(f"/api/acompanhar/{j['token']}").get_json()["mesa"] == "7"
    assert "MESA 7" in c.get(f"/admin/pedidos/{j['senha']}/comanda").get_data(as_text=True)


@pytest.mark.parametrize("mesa", ["0", "11", "-1", "abc", "1.5", True, "<b>"])
def test_mesa_invalida(cliente, admin, produto_id, mesa):
    mesas(admin, 10)
    assert pedido(cliente, produto_id, mesa=mesa).status_code == 400


def test_mesa_nao_combina_com_entrega(cliente, admin, produto_id):
    mesas(admin, 10)
    r = pedido(cliente, produto_id, mesa="2", tipo="entrega", rua="Rua A", numero="1", bairro="Centro")
    assert r.status_code == 400


def test_quantidade_de_mesas_valida(admin):
    c, csrf = admin
    cfg = c.get("/admin/api/loja").get_json()
    for v in (-1, 101, "x"):
        cfg["mesas"] = v
        assert c.put("/admin/api/loja", json=cfg, headers=H(csrf)).status_code == 400


def test_cartaz_das_mesas(admin):
    c, _ = admin
    mesas(admin, 3)
    r = c.get("/admin/cartaz-mesas")
    assert r.status_code == 200 and b'data-mesas="3"' in r.data and b"cartaz-mesas.js" in r.data


def test_cartaz_das_mesas_so_admin(cliente):
    assert cliente.get("/admin/cartaz-mesas").status_code in (302, 401, 403)
