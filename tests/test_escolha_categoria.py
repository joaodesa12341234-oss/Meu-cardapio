from conftest import pedido


def _insumo(admin, nome="Pão", qtd=10):
    c, csrf = admin
    r = c.post("/admin/api/insumos", json={"nome": nome, "quantidade": qtd}, headers={"X-CSRF-Token": csrf})
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    return r.get_json()["id"]


def _produtos(con):
    return [r["id"] for r in con.execute("SELECT id FROM produtos ORDER BY ordem LIMIT 3")]


def test_escolher_produtos_um_a_um(admin, con):
    c, csrf = admin; h = {"X-CSRF-Token": csrf}
    iid = _insumo(admin); p = _produtos(con)
    r = c.put(f"/admin/api/insumos/{iid}/produtos", json={"produtos": [{"id": p[0], "consumo": 2}, {"id": p[1]}]}, headers=h)
    assert r.status_code == 200 and r.get_json()["produtos"] == 2
    rows = {x["id"]: (x["insumo_id"], x["consumo"]) for x in con.execute("SELECT id, insumo_id, consumo FROM produtos")}
    assert rows[p[0]] == (iid, 2) and rows[p[1]] == (iid, 1) and rows[p[2]][0] == ""
    # nova escolha desliga quem saiu
    c.put(f"/admin/api/insumos/{iid}/produtos", json={"produtos": [{"id": p[2], "consumo": 3}]}, headers=h)
    rows = {x["id"]: x["insumo_id"] for x in con.execute("SELECT id, insumo_id FROM produtos")}
    assert rows[p[2]] == iid and rows[p[0]] == "" and rows[p[1]] == ""


def test_escolha_invalida(admin, con):
    c, csrf = admin; h = {"X-CSRF-Token": csrf}
    iid = _insumo(admin)
    assert c.put(f"/admin/api/insumos/{iid}/produtos", json={"produtos": [{"id": "nao-existe"}]}, headers=h).status_code == 400
    assert c.put(f"/admin/api/insumos/{iid}/produtos", json={"produtos": "x"}, headers=h).status_code == 400
    assert c.put(f"/admin/api/insumos/{iid}/produtos", json={"produtos": [{"id": _produtos(con)[0], "consumo": 0}]}, headers=h).status_code == 400
    assert c.put("/admin/api/insumos/xxx/produtos", json={"produtos": []}, headers=h).status_code == 404


def test_escolha_so_admin(cliente):
    assert cliente.put("/admin/api/insumos/x/produtos", json={"produtos": []}).status_code in (302, 401, 403)


def test_excluir_categoria_com_produtos(admin, con):
    c, csrf = admin; h = {"X-CSRF-Token": csrf}
    nova = c.post("/admin/api/categorias", json={"nome": "Teste", "icone": "🍕"}, headers=h).get_json()["id"]
    r = c.post("/admin/api/produtos", json={"nome": "Pizza", "preco": "10", "categoria_id": nova}, headers=h)
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    r = c.delete(f"/admin/api/categorias/{nova}", headers=h)
    assert r.status_code == 200 and r.get_json()["produtos_excluidos"] == 1
    assert not con.execute("SELECT 1 FROM categorias WHERE id = ?", (nova,)).fetchone()
    assert not con.execute("SELECT 1 FROM produtos WHERE categoria_id = ?", (nova,)).fetchone()
    assert c.delete(f"/admin/api/categorias/{nova}", headers=h).status_code == 404


def test_excluir_categoria_mantem_pedidos_antigos(admin, cliente, con):
    c, csrf = admin; h = {"X-CSRF-Token": csrf}
    cat = c.post("/admin/api/categorias", json={"nome": "Temp", "icone": "🍕"}, headers=h).get_json()["id"]
    pid = c.post("/admin/api/produtos", json={"nome": "Pizza", "preco": "10", "categoria_id": cat}, headers=h).get_json()["id"]
    assert pedido(cliente, pid).status_code == 201
    antes = con.execute("SELECT COUNT(*) FROM pedido_itens").fetchone()[0]
    assert c.delete(f"/admin/api/categorias/{cat}", headers=h).status_code == 200
    assert con.execute("SELECT COUNT(*) FROM pedido_itens").fetchone()[0] == antes >= 1


def test_categoria_excluir_so_admin(cliente):
    assert cliente.delete("/admin/api/categorias/x").status_code in (302, 401, 403)
