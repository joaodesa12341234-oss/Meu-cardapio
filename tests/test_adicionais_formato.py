import pytest
import db


@pytest.mark.parametrize("linha,esperado", [
    ("Bacon; 3,00", ("Bacon", "3,00")),
    ("bacon: 5,00", ("bacon", "5,00")),
    ("calabresa - R$ 5,00", ("calabresa", "5,00")),
    ("frango 4,00", ("frango", "4,00")),
    ("Queijo = 2.5", ("Queijo", "2.5")),
    ("Ovo", ("Ovo", "")),
    ("Cheddar;", ("Cheddar", "")),
])
def test_separar_nome_preco(linha, esperado):
    assert db._separar_nome_preco(linha) == esperado


def test_ler_extras_varios_formatos():
    r = db._ler_extras("bacon: 5,00\ncalabresa: 5,00\nfrango 4,00\nOvo; 2")
    assert [(e["nome"], e["preco"]) for e in r] == [("bacon", 500), ("calabresa", 500), ("frango", 400), ("Ovo", 200)]


def test_cadastro_antigo_com_preco_no_nome_e_somado(admin, cliente, con):
    """Produto salvo antes da correção ('bacon: 5,00' com preço 0) passa a somar no pedido."""
    import json
    c, csrf = admin
    cat = cliente.get("/api/menu").get_json()["categorias"][0]["id"]
    pid = c.post("/admin/api/produtos", json={"nome": "X Teste", "preco": "10", "categoria_id": cat},
                 headers={"X-CSRF-Token": csrf}).get_json()["id"]
    con.execute("UPDATE produtos SET opcoes = ? WHERE id = ?",
                (json.dumps({"extras": [{"nome": "bacon: 5,00", "preco": 0}, {"nome": "frango 4,00", "preco": 0}], "sem": []}), pid))
    con.commit()
    p = [x for k in cliente.get("/api/menu").get_json()["categorias"] for x in k["produtos"] if x["id"] == pid][0]
    assert [(e["nome"], e["preco"]) for e in p["extras"]] == [("bacon", 5.0), ("frango", 4.0)] or \
           [(e["nome"], e["preco"]) for e in p["extras"]] == [("bacon", 500), ("frango", 400)]
    r = cliente.post("/api/pedido", json=dict(itens=[{"id": pid, "qtd": 1, "extras": ["bacon", "frango"], "sem": []}],
                     tipo="balcao", nome="Ana", telefone="(75) 98765-4321", pagamento="pix"))
    assert r.status_code == 201, r.get_data(as_text=True)
    assert r.get_json()["total"] == pytest.approx(19.0)
