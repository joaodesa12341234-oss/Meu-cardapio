import pytest
from conftest import pedido


def H(csrf):
    return {"X-CSRF-Token": csrf}


def cupom(admin, **kw):
    c, csrf = admin
    corpo = dict(codigo="PRIMEIRA10", tipo="percentual", valor=10)
    corpo.update(kw)
    r = c.post("/admin/api/cupons", json=corpo, headers=H(csrf))
    assert r.status_code == 201, r.get_data(as_text=True)
    return corpo["codigo"]


def total(r):
    return r.get_json()["total"]


# ------------------------------------------------------------ cupons ----
def test_cupom_percentual_desconta_do_subtotal_nao_da_taxa(cliente, admin, produto_id):
    cupom(admin)
    r = pedido(cliente, produto_id, cupom="primeira10")           # 2 x 18,90 = 37,80 ; 10% = 3,78
    assert r.status_code == 201, r.get_data(as_text=True)
    j = r.get_json()
    assert j["cupom"] == "PRIMEIRA10" and j["desconto"] == pytest.approx(3.78) and j["total"] == pytest.approx(34.02)


def test_cupom_valor_fixo_e_nunca_passa_do_subtotal(cliente, admin, produto_id):
    cupom(admin, codigo="GRANDE", tipo="valor", valor="100")
    r = pedido(cliente, produto_id, cupom="GRANDE")
    assert r.get_json()["total"] == pytest.approx(0) and r.get_json()["desconto"] == pytest.approx(37.8)


def test_cupom_nao_desconta_taxa_de_entrega(cliente, admin, produto_id):
    c, csrf = admin
    cupom(admin, codigo="DEZ", tipo="valor", valor="5")
    cfg = c.get("/admin/api/loja", headers=H(csrf)).get_json()
    cfg["taxa_entrega"] = "6,00"
    assert c.put("/admin/api/loja", json=cfg, headers=H(csrf)).status_code == 200
    r = pedido(cliente, produto_id, cupom="DEZ", tipo="entrega", rua="Rua A", numero="10", bairro="Centro")
    assert r.status_code == 201, r.get_data(as_text=True)
    assert r.get_json()["total"] == pytest.approx(37.8 - 5 + 6)


def test_cupom_invalido_recusa_o_pedido(cliente, produto_id):
    r = pedido(cliente, produto_id, cupom="NAOEXISTE")
    assert r.status_code == 400 and "Cupom" in r.get_json()["erro"]


def test_cupom_minimo_e_validade(cliente, admin, produto_id):
    cupom(admin, codigo="MIN50", minimo="50")
    assert pedido(cliente, produto_id, cupom="MIN50").status_code == 400
    cupom(admin, codigo="VELHO", valido_ate="2020-01-01")
    r = pedido(cliente, produto_id, cupom="VELHO")
    assert r.status_code == 400 and "venceu" in r.get_json()["erro"]


def test_cupom_limite_de_usos_e_devolucao_ao_cancelar(cliente, admin, produto_id):
    c, csrf = admin
    cupom(admin, codigo="UNICO", limite=1, uma_vez=False)
    r1 = pedido(cliente, produto_id, cupom="UNICO", telefone="(75) 98765-4321")
    assert r1.status_code == 201
    r2 = pedido(cliente, produto_id, cupom="UNICO", telefone="(75) 98888-1111")
    assert r2.status_code == 400 and "esgot" in r2.get_json()["erro"]
    c.post(f"/admin/api/pedidos/{r1.get_json()['senha']}/status", json={"status": "cancelado"}, headers=H(csrf))
    r3 = pedido(cliente, produto_id, cupom="UNICO", telefone="(75) 98888-1111")
    assert r3.status_code == 201, r3.get_data(as_text=True)


def test_cupom_uma_vez_por_telefone_e_primeira_compra(cliente, admin, produto_id):
    cupom(admin, codigo="UMA")
    assert pedido(cliente, produto_id, cupom="UMA", telefone="(75) 98765-4321").status_code == 201
    r = pedido(cliente, produto_id, cupom="UMA", telefone="(75) 98765-4321")
    assert r.status_code == 400 and "já usou" in r.get_json()["erro"]
    cupom(admin, codigo="NOVATO", primeira_compra=True, uma_vez=False)
    r = pedido(cliente, produto_id, cupom="NOVATO", telefone="(75) 98765-4321")       # já comprou
    assert r.status_code == 400 and "primeira compra" in r.get_json()["erro"]
    assert pedido(cliente, produto_id, cupom="NOVATO", telefone="(75) 97777-2222").status_code == 201


def test_cupom_desativado_e_excluido(cliente, admin, produto_id):
    c, csrf = admin
    cupom(admin, codigo="OFF")
    assert c.put("/admin/api/cupons/OFF", json={"ativo": False}, headers=H(csrf)).status_code == 200
    assert pedido(cliente, produto_id, cupom="OFF").status_code == 400
    assert c.delete("/admin/api/cupons/OFF", headers=H(csrf)).status_code == 200
    assert c.delete("/admin/api/cupons/OFF", headers=H(csrf)).status_code == 404


def test_cupom_validacoes_do_cadastro(admin):
    c, csrf = admin
    for corpo in ({"codigo": "x", "tipo": "percentual", "valor": 10},
                  {"codigo": "ABC DEF!", "tipo": "percentual", "valor": 10},
                  {"codigo": "ABC", "tipo": "percentual", "valor": 150},
                  {"codigo": "ABC", "tipo": "valor", "valor": 0},
                  {"codigo": "ABC", "tipo": "outro", "valor": 5},
                  {"codigo": "ABC", "tipo": "percentual", "valor": 5, "valido_ate": "ontem"}):
        assert c.post("/admin/api/cupons", json=corpo, headers=H(csrf)).status_code == 400, corpo
    cupom(admin, codigo="ABC")
    assert c.post("/admin/api/cupons", json={"codigo": "abc", "tipo": "percentual", "valor": 5}, headers=H(csrf)).status_code == 400


def test_cupom_previa_publica_e_limite_de_tentativas(cliente, admin):
    import app as aplicacao
    aplicacao._tentativas_cupom.clear()
    cupom(admin, codigo="PREVIA", tipo="valor", valor="4")
    r = cliente.post("/api/cupom", json={"codigo": "previa", "subtotal": 20})
    assert r.status_code == 200 and r.get_json()["desconto"] == pytest.approx(4)
    assert cliente.post("/api/cupom", json={"codigo": "zzz", "subtotal": 20}).status_code == 400
    for _ in range(25):
        r = cliente.post("/api/cupom", json={"codigo": "zzz", "subtotal": 20})
    assert r.status_code == 429
    aplicacao._tentativas_cupom.clear()


def test_cupons_so_admin(cliente):
    assert cliente.get("/admin/api/cupons").status_code in (302, 401, 403)
    assert cliente.post("/admin/api/cupons", json={}).status_code in (302, 401, 403)


# -------------------------------------------------------- avaliações ----
def test_avaliar_so_depois_de_concluido_e_uma_vez(cliente, admin, produto_id):
    c, csrf = admin
    j = pedido(cliente, produto_id).get_json()
    tok = j["token"]
    r = cliente.post(f"/api/acompanhar/{tok}/avaliar", json={"nota": 5})
    assert r.status_code == 400 and "concluído" in r.get_json()["erro"]
    assert cliente.get(f"/api/acompanhar/{tok}").get_json()["pode_avaliar"] is False
    c.post(f"/admin/api/pedidos/{j['senha']}/status", json={"status": "concluido"}, headers=H(csrf))
    assert cliente.get(f"/api/acompanhar/{tok}").get_json()["pode_avaliar"] is True
    r = cliente.post(f"/api/acompanhar/{tok}/avaliar", json={"nota": 4, "comentario": "  Muito   bom <b>x</b> "})
    assert r.status_code == 200
    d = cliente.get(f"/api/acompanhar/{tok}").get_json()
    assert d["nota"] == 4 and d["comentario"] == "Muito bom <b>x</b>" and d["pode_avaliar"] is False
    assert cliente.post(f"/api/acompanhar/{tok}/avaliar", json={"nota": 1}).status_code == 409


@pytest.mark.parametrize("nota", [0, 6, "5", None, True, 3.5])
def test_avaliar_nota_invalida(cliente, admin, produto_id, nota):
    c, csrf = admin
    j = pedido(cliente, produto_id).get_json()
    c.post(f"/admin/api/pedidos/{j['senha']}/status", json={"status": "concluido"}, headers=H(csrf))
    assert cliente.post(f"/api/acompanhar/{j['token']}/avaliar", json={"nota": nota}).status_code == 400


def test_avaliar_token_inexistente_e_comentario_longo(cliente, admin, produto_id):
    assert cliente.post("/api/acompanhar/naoexiste123/avaliar", json={"nota": 5}).status_code == 404
    c, csrf = admin
    j = pedido(cliente, produto_id).get_json()
    c.post(f"/admin/api/pedidos/{j['senha']}/status", json={"status": "concluido"}, headers=H(csrf))
    assert cliente.post(f"/api/acompanhar/{j['token']}/avaliar", json={"nota": 5, "comentario": "x" * 301}).status_code == 400


def test_resumo_de_avaliacoes_no_painel(cliente, admin, produto_id):
    from datetime import date
    c, csrf = admin
    for nota, nome in ((5, "Ana Maria"), (3, "Beto")):
        j = pedido(cliente, produto_id, nome=nome, telefone="(75) 9%d765-4321" % nota).get_json()
        c.post(f"/admin/api/pedidos/{j['senha']}/status", json={"status": "concluido"}, headers=H(csrf))
        cliente.post(f"/api/acompanhar/{j['token']}/avaliar", json={"nota": nota, "comentario": "ok"})
    h = date.today().isoformat()
    r = c.get(f"/admin/api/avaliacoes?inicio={h}&fim={h}").get_json()
    assert r["total"] == 2 and r["media"] == 4.0 and r["distribuicao"]["5"] == 1 and r["distribuicao"]["3"] == 1
    assert {u["nome"] for u in r["ultimas"]} == {"Ana", "Beto"}


def test_avaliacoes_so_admin(cliente):
    assert cliente.get("/admin/api/avaliacoes").status_code in (302, 401, 403)
