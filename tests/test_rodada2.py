import pytest
from datetime import datetime, timedelta
import db
from conftest import pedido


def H(csrf):
    return {"X-CSRF-Token": csrf}


def cfg(admin, **m):
    c, csrf = admin
    x = c.get("/admin/api/loja").get_json()
    x.update(m)
    r = c.put("/admin/api/loja", json=x, headers=H(csrf))
    assert r.status_code == 200, r.get_data(as_text=True)


def concluir(admin, senha):
    c, csrf = admin
    assert c.post(f"/admin/api/pedidos/{senha}/status", json={"status": "concluido"}, headers=H(csrf)).status_code == 200


@pytest.fixture
def envios(monkeypatch):
    enviados = []
    monkeypatch.setattr(db.whatsapp, "enviar_em_segundo_plano", lambda numero, texto: enviados.append((numero, texto)))
    return enviados


# ------------------------------------------------------------ relatório ----
def test_relatorio_exige_dono_e_formato(admin):
    c, csrf = admin
    x = c.get("/admin/api/loja").get_json()
    x["relatorio_hora"] = "9h"
    assert c.put("/admin/api/loja", json=x, headers=H(csrf)).status_code == 400
    x["relatorio_hora"] = "21:30"; x["dono_telefone"] = ""
    assert c.put("/admin/api/loja", json=x, headers=H(csrf)).status_code == 400


def test_relatorio_conteudo_e_envio_unico(admin, cliente, produto_id, con, envios):
    cfg(admin, dono_telefone="(75) 99999-0000", relatorio_hora="00:00")
    j = pedido(cliente, produto_id).get_json()
    concluir(admin, j["senha"])
    cliente.post(f"/api/acompanhar/{j['token']}/avaliar", json={"nota": 5})
    envios.clear()
    agora = datetime.now(db.FUSO)
    assert db.enviar_relatorio_diario(con, agora=agora) is True
    assert db.enviar_relatorio_diario(con, agora=agora) is False          # só uma vez por dia
    texto = envios[-1][1]
    assert "Resumo do dia" in texto and "37,80" in texto and "2× X-Burger" in texto and "5,0" in texto
    assert db.enviar_relatorio_diario(con, agora=agora + timedelta(days=1)) is True   # no dia seguinte manda de novo


def test_relatorio_nao_envia_antes_da_hora_nem_desligado(admin, con, envios):
    cfg(admin, dono_telefone="(75) 99999-0000", relatorio_hora="23:59")
    cedo = datetime.now(db.FUSO).replace(hour=8, minute=0)
    assert db.enviar_relatorio_diario(con, agora=cedo) is False
    cfg(admin, relatorio_hora="")
    assert db.enviar_relatorio_diario(con, agora=cedo.replace(hour=23, minute=59)) is False


def test_relatorio_agora_botao(admin, envios):
    c, csrf = admin
    assert c.post("/admin/api/relatorio/enviar", headers=H(csrf)).status_code == 400     # sem WhatsApp do dono
    cfg(admin, dono_telefone="(75) 99999-0000")
    assert c.post("/admin/api/relatorio/enviar", headers=H(csrf)).status_code == 200
    assert "Resumo do dia" in envios[-1][1]


# ----------------------------------------------------------- fidelidade ----
def test_fidelidade_desconto_depois_de_n_pedidos(admin, cliente, produto_id):
    cfg(admin, fidelidade_pedidos=2, fidelidade_percentual=10)
    tel = "(75) 98111-2222"
    for _ in range(2):
        r = pedido(cliente, produto_id, telefone=tel)
        assert r.get_json()["fidelidade"] == 0
        concluir(admin, r.get_json()["senha"])
    r = pedido(cliente, produto_id, telefone=tel)                     # 3º pedido: ganhou
    j = r.get_json()
    assert j["fidelidade"] == pytest.approx(3.78) and j["total"] == pytest.approx(34.02)
    concluir(admin, j["senha"])
    r4 = pedido(cliente, produto_id, telefone=tel).get_json()         # recomeça a contagem
    assert r4["fidelidade"] == 0


def test_fidelidade_empilha_com_cupom(admin, cliente, produto_id):
    c, csrf = admin
    c.post("/admin/api/cupons", json={"codigo": "DEZ", "tipo": "percentual", "valor": 10}, headers=H(csrf))
    cfg(admin, fidelidade_pedidos=1, fidelidade_percentual=10)
    tel = "(75) 98222-3333"
    concluir(admin, pedido(cliente, produto_id, telefone=tel).get_json()["senha"])
    j = pedido(cliente, produto_id, telefone=tel, cupom="DEZ").get_json()
    # 37,80 - 10% (3,78) = 34,02 ; fidelidade 10% de 34,02 = 3,40
    assert j["desconto"] == pytest.approx(3.78) and j["fidelidade"] == pytest.approx(3.40) and j["total"] == pytest.approx(30.62)


def test_fidelidade_cancelar_devolve_o_premio(admin, cliente, produto_id):
    c, csrf = admin
    cfg(admin, fidelidade_pedidos=1, fidelidade_percentual=20)
    tel = "(75) 98333-4444"
    concluir(admin, pedido(cliente, produto_id, telefone=tel).get_json()["senha"])
    usado = pedido(cliente, produto_id, telefone=tel).get_json()
    assert usado["fidelidade"] > 0
    c.post(f"/admin/api/pedidos/{usado['senha']}/status", json={"status": "cancelado"}, headers=H(csrf))
    assert pedido(cliente, produto_id, telefone=tel).get_json()["fidelidade"] > 0


def test_fidelidade_previa_e_desligada(admin, cliente, produto_id):
    import app as aplicacao
    aplicacao._tentativas_cupom.clear()
    assert cliente.get("/api/fidelidade?telefone=75981115555").get_json()["ativo"] is False
    cfg(admin, fidelidade_pedidos=2, fidelidade_percentual=15)
    d = cliente.get("/api/fidelidade?telefone=75981115555").get_json()
    assert d["ativo"] is True and d["falta"] == 2 and d["elegivel"] is False and d["percentual"] == 15
    assert cliente.get("/api/menu").get_json()["loja"]["fidelidade"]["pedidos"] == 2
    aplicacao._tentativas_cupom.clear()


def test_fidelidade_config_invalida(admin):
    c, csrf = admin
    x = c.get("/admin/api/loja").get_json()
    for chave, v in (("fidelidade_pedidos", 51), ("fidelidade_percentual", 99), ("fidelidade_pedidos", "x")):
        y = dict(x); y[chave] = v
        assert c.put("/admin/api/loja", json=y, headers=H(csrf)).status_code == 400


# ------------------------------------------------------ nota baixa / link ----
def test_alerta_de_nota_baixa(admin, cliente, produto_id, envios):
    cfg(admin, dono_telefone="(75) 99999-0000")
    j = pedido(cliente, produto_id, nome="Zeca").get_json()
    concluir(admin, j["senha"])
    envios.clear()
    cliente.post(f"/api/acompanhar/{j['token']}/avaliar", json={"nota": 2, "comentario": "demorou"})
    assert len(envios) == 1 and "Nota baixa" in envios[0][1] and "demorou" in envios[0][1] and "Zeca" in envios[0][1]
    j2 = pedido(cliente, produto_id, telefone="(75) 98999-1111").get_json()
    concluir(admin, j2["senha"])
    envios.clear()
    cliente.post(f"/api/acompanhar/{j2['token']}/avaliar", json={"nota": 4})
    assert envios == []


def test_cupom_traz_link_para_divulgar(admin):
    c, csrf = admin
    c.post("/admin/api/cupons", json={"codigo": "LINK10", "tipo": "percentual", "valor": 10}, headers=H(csrf))
    assert c.get("/admin/api/cupons").get_json()[0]["link"].endswith("/?cupom=LINK10")


# -------------------------------------------------------------- sumidos ----
def test_clientes_sumidos_e_envio(admin, cliente, produto_id, con, envios):
    c, csrf = admin
    j = pedido(cliente, produto_id, nome="Maria Souza", telefone="(75) 98444-5555").get_json()
    concluir(admin, j["senha"])
    pedido(cliente, produto_id, nome="Recente", telefone="(75) 98666-7777")      # pedido de agora: não é "sumido"
    velho = (datetime.utcnow() - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
    con.execute("UPDATE pedidos SET criado = ?, liberar_em = '' WHERE senha = ?", (velho, j["senha"])); con.commit()
    r = c.get("/admin/api/sumidos?dias=21").get_json()
    assert [x["nome"] for x in r["clientes"]] == ["Maria Souza"] and r["clientes"][0]["dias_sem_pedir"] >= 39
    tel = r["clientes"][0]["telefone"]
    envios.clear()
    ok = c.post("/admin/api/sumidos/enviar", json={"telefone": tel, "mensagem": "Oi {nome}! Volte: {link}"}, headers=H(csrf))
    assert ok.status_code == 200 and "Oi Maria!" in envios[0][1] and "http" in envios[0][1]
    again = c.post("/admin/api/sumidos/enviar", json={"telefone": tel}, headers=H(csrf))
    assert again.status_code == 409                                             # não repete antes de 14 dias
    assert c.get("/admin/api/sumidos").get_json()["clientes"][0]["em_espera"] is True
    assert c.post("/admin/api/sumidos/enviar", json={"telefone": "999"}, headers=H(csrf)).status_code == 404
    assert c.post("/admin/api/sumidos/enviar", json={"telefone": tel, "cupom": "NAOEXISTE"}, headers=H(csrf)).status_code == 400


def test_novas_rotas_so_admin(cliente):
    for rota in ("/admin/api/sumidos",):
        assert cliente.get(rota).status_code in (302, 401, 403)
    assert cliente.post("/admin/api/relatorio/enviar").status_code in (302, 401, 403)
