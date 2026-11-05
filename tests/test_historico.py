import pytest
from conftest import pedido


def H(csrf):
    return {"X-CSRF-Token": csrf}


def hist(c, **q):
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    r = c.get("/admin/api/historico" + ("?" + qs if qs else ""))
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def textos(c, **q):
    return [i["texto"] for i in hist(c, **q)["itens"]]


def _produto_novo(c, csrf):
    cat = c.get("/admin/api/produtos-extra") and c.get("/api/menu").get_json()["categorias"][0]["id"]
    r = c.post("/admin/api/produtos", json={"nome": "Teste Hist", "preco": "10", "categoria_id": cat}, headers=H(csrf))
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    return r.get_json()["id"]


def test_preco_e_estoque_do_produto_ficam_registrados_com_o_autor(admin):
    c, csrf = admin
    pid = _produto_novo(c, csrf)
    r = c.put(f"/admin/api/produtos/{pid}", json={"preco": "25.50", "estoque": "7"}, headers=H(csrf))
    assert r.status_code == 200, r.get_data(as_text=True)
    h = hist(c, area="produto")["itens"][0]
    assert h["usuario"] == "admin" and "preço" in h["texto"] and "25,50" in h["texto"] and "estoque" in h["texto"]
    c.delete(f"/admin/api/produtos/{pid}", headers=H(csrf))
    assert "Excluiu" in hist(c, area="produto")["itens"][0]["texto"]


def test_mudanca_de_status_do_pedido(admin, cliente, produto_id):
    c, csrf = admin
    j = pedido(cliente, produto_id).get_json()
    c.post(f"/admin/api/pedidos/{j['senha']}/status", json={"status": "fazendo"}, headers=H(csrf))
    t = textos(c, area="pedido")[0]
    assert f"#{j['senha']}" in t and "novo → fazendo" in t


def test_sem_mudanca_nao_registra(admin):
    c, csrf = admin
    pid = _produto_novo(c, csrf)
    antes = len(hist(c, area="produto")["itens"])
    c.put(f"/admin/api/produtos/{pid}", json={}, headers=H(csrf))
    assert len(hist(c, area="produto")["itens"]) == antes
    c.delete(f"/admin/api/produtos/{pid}", headers=H(csrf))


def test_estoque_geral_cupom_e_loja(admin):
    c, csrf = admin
    iid = c.post("/admin/api/insumos", json={"nome": "Pão", "quantidade": 50}, headers=H(csrf)).get_json()["id"]
    c.put(f"/admin/api/insumos/{iid}", json={"quantidade": 80}, headers=H(csrf))
    c.post("/admin/api/cupons", json={"codigo": "TESTE5", "tipo": "percentual", "valor": 5}, headers=H(csrf))
    cfg = c.get("/admin/api/loja").get_json(); cfg["taxa_entrega"] = "7,00"
    c.put("/admin/api/loja", json=cfg, headers=H(csrf))
    assert any("50 → 80" in t for t in textos(c, area="estoque"))
    assert any("TESTE5" in t for t in textos(c, area="cupom"))
    assert any("taxa de entrega" in t and "7,00" in t for t in textos(c, area="loja"))


def test_filtro_busca_e_paginacao(admin):
    c, csrf = admin
    for i in range(7):
        c.post("/admin/api/categorias", json={"nome": f"Cat{i}", "icone": "x"}, headers=H(csrf))
    pg = hist(c, area="categoria", limite=3)
    assert len(pg["itens"]) == 3 and pg["mais"] is True
    prox = hist(c, area="categoria", limite=3, antes=pg["itens"][-1]["id"])
    assert len(prox["itens"]) == 3 and prox["itens"][0]["id"] < pg["itens"][-1]["id"]
    assert len(hist(c, busca="Cat3")["itens"]) == 1
    assert hist(c, busca="%")["itens"] == []                # curinga do LIKE é tratado como texto
    assert hist(c, busca="admin")["itens"]                   # busca também pelo autor


def test_area_invalida_e_acesso(admin, cliente):
    c, _ = admin
    assert c.get("/admin/api/historico?area=xyz").status_code == 400
    assert c.get("/admin/api/historico?antes=abc").status_code == 400
    assert cliente.get("/admin/api/historico").status_code in (302, 401, 403)


def test_limite_de_registros(con):
    import db
    for i in range(5):
        db.registrar(con, "loja", f"x{i}")
    assert con.execute("SELECT COUNT(*) FROM auditoria").fetchone()[0] == 5


def test_erro_ao_registrar_nao_derruba(con):
    import db
    db.registrar(con, None, "isto falha: area é NOT NULL")      # não levanta
