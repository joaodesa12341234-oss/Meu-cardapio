from datetime import datetime, timedelta, timezone

import pytest

import db
from conftest import pedido
from test_bairros_dono_cozinha import _Evolution, _esperar, _salvar_loja, evolution  # noqa: F401


def _produto_com_opcoes(c, csrf, cliente):
    cat = cliente.get("/api/menu").get_json()["categorias"][0]["id"]
    r = c.post("/admin/api/produtos", json={
        "categoria_id": cat, "nome": "Burger Top", "preco": "20", "imagem": "",
        "extras_texto": "Bacon; 3,00\nQueijo extra; 2,50\nMolho verde", "sem_texto": "Cebola\nTomate"},
        headers={"X-CSRF-Token": csrf})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


# ------------------------------------------------------------ opções ----
def test_opcoes_aparecem_no_cardapio_publico(cliente, admin):
    c, csrf = admin
    pid = _produto_com_opcoes(c, csrf, cliente)
    p = next(p for cat in cliente.get("/api/menu").get_json()["categorias"] for p in cat["produtos"] if p["id"] == pid)
    assert [e["nome"] for e in p["extras"]] == ["Bacon", "Queijo extra", "Molho verde"]
    assert p["extras"][0]["preco"] == 3.0 and p["extras"][2]["preco"] == 0 and p["sem"] == ["Cebola", "Tomate"]


def test_pedido_com_opcoes_soma_extras(cliente, admin):
    c, csrf = admin
    pid = _produto_com_opcoes(c, csrf, cliente)
    r = pedido(cliente, pid, itens=[{"id": pid, "qtd": 2, "extras": ["Bacon", "Queijo extra"], "sem": ["Cebola"]}])
    assert r.status_code == 201
    d = r.get_json()
    assert d["total"] == pytest.approx(2 * (20 + 3 + 2.5))
    assert d["itens"][0]["opcoes"] == "+ Bacon, + Queijo extra, sem Cebola"
    ativo = next(p for p in c.get("/admin/api/pedidos").get_json() if p["senha"] == d["senha"])
    assert ativo["itens"][0]["opcoes"] == "+ Bacon, + Queijo extra, sem Cebola"


def test_linhas_iguais_somam_e_diferentes_ficam_separadas(cliente, admin):
    c, csrf = admin
    pid = _produto_com_opcoes(c, csrf, cliente)
    itens = [{"id": pid, "qtd": 1, "extras": ["Bacon"]}, {"id": pid, "qtd": 2, "extras": ["Bacon"]},
             {"id": pid, "qtd": 1}]
    d = pedido(cliente, pid, itens=itens).get_json()
    assert sorted((i["qtd"], i["opcoes"]) for i in d["itens"]) == [(1, ""), (3, "+ Bacon")]


@pytest.mark.parametrize("ruim", [
    dict(extras=["Camarão"]), dict(sem=["Alface"]), dict(extras=["Bacon", "Bacon"]),
    dict(extras="Bacon"), dict(extras=[1]), dict(sem=["Bacon"]),
])
def test_opcao_inexistente_ou_invalida_e_recusada(cliente, admin, ruim):
    c, csrf = admin
    pid = _produto_com_opcoes(c, csrf, cliente)
    r = pedido(cliente, pid, itens=[dict(id=pid, qtd=1, **ruim)])
    assert r.status_code in (400, 409)


def test_cliente_nao_consegue_extra_gratis_de_outro_produto(cliente, admin, produto_id):
    c, csrf = admin
    _produto_com_opcoes(c, csrf, cliente)
    assert pedido(cliente, produto_id, itens=[{"id": produto_id, "qtd": 1, "extras": ["Bacon"]}]).status_code == 409


def test_editar_produto_preserva_ou_troca_opcoes(cliente, admin):
    c, csrf = admin
    pid = _produto_com_opcoes(c, csrf, cliente)
    h = {"X-CSRF-Token": csrf}
    c.put(f"/admin/api/produtos/{pid}", json={"nome": "Burger Top 2"}, headers=h)            # não mexe nas opções
    p = next(p for cat in cliente.get("/api/menu").get_json()["categorias"] for p in cat["produtos"] if p["id"] == pid)
    assert len(p["extras"]) == 3 and p["sem"] == ["Cebola", "Tomate"]
    c.put(f"/admin/api/produtos/{pid}", json={"extras_texto": "", "sem_texto": "Picles"}, headers=h)
    p = next(p for cat in cliente.get("/api/menu").get_json()["categorias"] for p in cat["produtos"] if p["id"] == pid)
    assert p["extras"] == [] and p["sem"] == ["Picles"]


@pytest.mark.parametrize("campo,valor", [("extras_texto", "A; 3"), ("extras_texto", "Bacon; abc"), ("extras_texto", "Bacon\nbacon"),
                                         ("sem_texto", "x"), ("sem_texto", "Cebola\ncebola")])
def test_opcoes_invalidas_no_cadastro(cliente, admin, campo, valor):
    c, csrf = admin
    cat = cliente.get("/api/menu").get_json()["categorias"][0]["id"]
    r = c.post("/admin/api/produtos", json={"categoria_id": cat, "nome": "X", "preco": "5", "imagem": "", campo: valor},
               headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400


# ------------------------------------------------ acompanhamento ----
def test_link_de_acompanhamento(cliente, admin, produto_id):
    c, csrf = admin
    d = pedido(cliente, produto_id, nome="Fulana Secreta", telefone="(75) 98765-4321",
               observacao="sem gelo").get_json()
    token = d["token"]
    assert d["acompanhar"] == f"/acompanhar/{token}" and len(token) >= 12
    pagina = cliente.get(d["acompanhar"])
    assert pagina.status_code == 200 and b"acompanhar.js" in pagina.data and b"Fulana" not in pagina.data
    dados = cliente.get(f"/api/acompanhar/{token}").get_json()
    texto = str(dados)
    assert dados["status"] == "novo" and dados["senha"] == d["senha"]
    assert "Fulana" not in texto and "98765" not in texto and "sem gelo" not in texto      # nenhum dado pessoal
    c.patch(f"/admin/api/pedidos/{d['senha']}/status", json={"status": "fazendo"}, headers={"X-CSRF-Token": csrf})
    assert cliente.get(f"/api/acompanhar/{token}").get_json()["status"] == "fazendo"


@pytest.mark.parametrize("token", ["x", "naoexiste123456", "a" * 12, "../etc/passwd", "%00"])
def test_token_invalido_da_404(cliente, token):
    assert cliente.get(f"/api/acompanhar/{token}").status_code == 404
    assert cliente.get(f"/acompanhar/{token}").status_code == 404


def test_pedidos_antigos_sem_token_nao_abrem(cliente, con, produto_id):
    senha = pedido(cliente, produto_id).get_json()["senha"]
    con.execute("UPDATE pedidos SET token = '' WHERE senha = ?", (senha,)); con.commit()
    assert cliente.get("/api/acompanhar/").status_code == 404


def test_etapas_de_balcao_nao_tem_saiu(cliente, produto_id):
    d = pedido(cliente, produto_id).get_json()
    ids = [e["id"] for e in cliente.get(f"/api/acompanhar/{d['token']}").get_json()["etapas"]]
    assert "saiu" not in ids and ids[0] == "novo"


def test_whatsapp_leva_o_link(cliente, produto_id, evolution):   # noqa: F811
    d = pedido(cliente, produto_id, telefone="(75) 98765-4321").get_json()
    _esperar(evolution, 1)
    assert f"/acompanhar/{d['token']}" in evolution[0]["text"]


# --------------------------------------------------------- atraso ----
def _envelhecer(con, senha, minutos):
    antigo = (datetime.now(timezone.utc) - timedelta(minutes=minutos)).strftime("%Y-%m-%dT%H:%M:%SZ")
    con.execute("UPDATE pedidos SET criado = ?, liberar_em = ? WHERE senha = ?", (antigo, antigo, senha)); con.commit()


def test_pedido_atrasado_fica_marcado(cliente, admin, con, produto_id):
    c, _ = admin
    velho = pedido(cliente, produto_id).get_json()["senha"]
    novo = pedido(cliente, produto_id).get_json()["senha"]
    _envelhecer(con, velho, 45)
    por_senha = {p["senha"]: p for p in c.get("/admin/api/pedidos").get_json()}
    assert por_senha[velho]["atrasado"] is True and por_senha[velho]["minutos"] >= 45
    assert por_senha[novo]["atrasado"] is False


def test_atraso_desligado(cliente, admin, con, produto_id):
    c, csrf = admin
    assert _salvar_loja(c, csrf, atraso_minutos="0").status_code == 200
    senha = pedido(cliente, produto_id).get_json()["senha"]
    _envelhecer(con, senha, 300)
    assert c.get("/admin/api/pedidos").get_json()[0]["atrasado"] is False
    assert db.verificar_atrasos(con) == 0


def test_atraso_invalido(admin):
    c, csrf = admin
    for v in ("abc", "-1", "999"):
        assert _salvar_loja(c, csrf, atraso_minutos=v).status_code == 400


def test_dono_e_avisado_uma_unica_vez(cliente, admin, con, produto_id, evolution):   # noqa: F811
    c, csrf = admin
    _salvar_loja(c, csrf, dono_telefone="(75) 99999-0000", atraso_minutos="30")
    senha = pedido(cliente, produto_id).get_json()["senha"]
    _esperar(evolution, 2)                       # confirmação ao cliente + alerta de pedido novo ao dono
    antes = len(evolution)
    _envelhecer(con, senha, 40)
    assert db.verificar_atrasos(con) == 1
    assert db.verificar_atrasos(con) == 0        # 2ª rodada não repete
    _esperar(evolution, antes + 1)
    atrasos = [m for m in evolution if "atrasado" in m["text"]]
    assert len(atrasos) == 1 and atrasos[0]["number"] == "5575999990000" and f"#{senha}" in atrasos[0]["text"]


def test_pedido_esquecido_ha_muito_tempo_nao_dispara(cliente, con, produto_id):
    senha = pedido(cliente, produto_id).get_json()["senha"]
    _envelhecer(con, senha, 60 * 24)
    assert db.verificar_atrasos(con) == 0


def test_pedido_ja_pronto_nao_e_atrasado(cliente, admin, con, produto_id):
    c, csrf = admin
    senha = pedido(cliente, produto_id).get_json()["senha"]
    for st in ("fazendo", "pronto"):
        c.patch(f"/admin/api/pedidos/{senha}/status", json={"status": st}, headers={"X-CSRF-Token": csrf})
    _envelhecer(con, senha, 90)
    assert db.verificar_atrasos(con) == 0
    assert next(p for p in c.get("/admin/api/pedidos").get_json() if p["senha"] == senha)["atrasado"] is False
