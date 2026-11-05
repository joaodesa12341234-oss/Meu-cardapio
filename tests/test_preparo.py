import pytest
import db
from conftest import pedido


def H(csrf):
    return {"X-CSRF-Token": csrf}


def config(admin, **mudancas):
    c, csrf = admin
    cfg = c.get("/admin/api/loja").get_json()
    cfg.update(mudancas)
    r = c.put("/admin/api/loja", json=cfg, headers=H(csrf))
    assert r.status_code == 200, r.get_data(as_text=True)


def test_cardapio_mostra_tempo_padrao(cliente):
    p = cliente.get("/api/menu").get_json()["loja"]["preparo"]
    assert p["ativo"] is True and p["minutos"] == 20


def test_zero_esconde(cliente, admin):
    config(admin, preparo_minutos=0)
    assert cliente.get("/api/menu").get_json()["loja"]["preparo"]["ativo"] is False


def test_fila_aumenta_a_estimativa(cliente, admin, produto_id):
    config(admin, preparo_minutos=20)
    antes = cliente.get("/api/menu").get_json()["loja"]["preparo"]["minutos"]
    for i in range(6):
        assert pedido(cliente, produto_id, telefone="(75) 9%04d-0000" % (1000 + i)).status_code == 201
    depois = cliente.get("/api/menu").get_json()["loja"]["preparo"]
    assert depois["fila"] == 6 and depois["minutos"] > antes and depois["minutos"] % 5 == 0


def test_aprende_com_o_tempo_real(cliente, admin, produto_id, con):
    config(admin, preparo_minutos=60)
    c, csrf = admin
    for i in range(6):
        j = pedido(cliente, produto_id, telefone="(75) 9%04d-1111" % (2000 + i)).get_json()
        c.post(f"/admin/api/pedidos/{j['senha']}/status", json={"status": "pronto"}, headers=H(csrf))
    con.execute("UPDATE pedidos SET liberar_em = criado")
    # cada pedido levou ~0 min (pronto na hora) -> fora da faixa válida (>= 1 min): continua o valor da loja
    assert cliente.get("/api/menu").get_json()["loja"]["preparo"]["minutos"] == 60
    # com preparo de 12 min cada, a média real (12) vale mais que o número da loja
    con.execute("UPDATE pedidos SET liberar_em = '2000-01-01T00:00:00Z' WHERE 0")
    con.execute("UPDATE pedidos SET pronto_em = strftime('%Y-%m-%dT%H:%M:%SZ', datetime(replace(substr(criado,1,19),'T',' '), '+12 minutes')), "
                "status = 'concluido'")
    con.commit()
    assert cliente.get("/api/menu").get_json()["loja"]["preparo"]["minutos"] == 15


def test_acompanhamento_tem_previsao_so_enquanto_prepara(cliente, admin, produto_id):
    c, csrf = admin
    j = pedido(cliente, produto_id).get_json()
    d = cliente.get(f"/api/acompanhar/{j['token']}").get_json()
    assert d["previsao_min"] and d["previsao_min"] % 5 == 0
    c.post(f"/admin/api/pedidos/{j['senha']}/status", json={"status": "pronto"}, headers=H(csrf))
    assert cliente.get(f"/api/acompanhar/{j['token']}").get_json()["previsao_min"] is None


def test_config_preparo_invalido(admin):
    c, csrf = admin
    cfg = c.get("/admin/api/loja").get_json()
    cfg["preparo_minutos"] = 999
    assert c.put("/admin/api/loja", json=cfg, headers=H(csrf)).status_code == 400
