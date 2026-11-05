from datetime import datetime, timedelta, timezone

import pytest

import db
from conftest import pedido
from test_bairros_dono_cozinha import _salvar_loja


def _ligar(c, csrf, **extra):
    return _salvar_loja(c, csrf, agendamento=True, agend_minimo="30", agend_liberar="40", **extra)


def _horarios(cliente):
    return cliente.get("/api/menu").get_json()["loja"]["agendamento"]["horarios"]


def test_desligado_por_padrao(cliente, produto_id):
    ag = cliente.get("/api/menu").get_json()["loja"]["agendamento"]
    assert ag == {"ativo": False, "horarios": []}
    assert pedido(cliente, produto_id, agendado_para="2030-01-01T20:00").status_code == 400


def test_horarios_respeitam_antecedencia_e_passo(cliente, admin):
    c, csrf = admin
    _ligar(c, csrf)
    h = _horarios(cliente)
    assert h and all(x["valor"][-2:] in ("00", "30") for x in h)
    primeiro = datetime.strptime(h[0]["valor"], "%Y-%m-%dT%H:%M").replace(tzinfo=db.FUSO)
    assert primeiro >= datetime.now(db.FUSO) + timedelta(minutes=29)
    assert datetime.strptime(h[-1]["valor"], "%Y-%m-%dT%H:%M").replace(tzinfo=db.FUSO) <= datetime.now(db.FUSO) + timedelta(hours=48)


def test_agendar_cria_pedido_futuro_e_so_aparece_perto(cliente, admin, produto_id):
    c, csrf = admin
    _ligar(c, csrf)
    alvo = _horarios(cliente)[-1]                      # o mais distante (≈48 h)
    r = pedido(cliente, produto_id, agendado_para=alvo["valor"])
    assert r.status_code == 201, r.get_json()
    d = r.get_json()
    assert d["agendado_rotulo"] and d["agendado_para"].endswith("Z")
    ativo = next(p for p in c.get("/admin/api/pedidos").get_json() if p["senha"] == d["senha"])
    assert ativo["liberado"] is False and ativo["atrasado"] is False and ativo["agendado_para"] == d["agendado_para"]
    assert cliente.get(f"/api/acompanhar/{d['token']}").get_json()["agendado_para"] == d["agendado_para"]


def test_horario_inventado_e_recusado(cliente, admin, produto_id):
    c, csrf = admin
    _ligar(c, csrf)
    for ruim in ("2030-01-01T20:00", "abc", "2026-11-03T03:07", 123):
        r = pedido(cliente, produto_id, agendado_para=ruim)
        assert r.status_code in (400, 409), ruim


def test_agendar_com_loja_fechada_agora(cliente, admin, produto_id):
    c, csrf = admin
    cfg = c.get("/admin/api/loja").get_json()
    dias = [{"aberto": True, "abre": "00:00", "fecha": "23:59"} for _ in range(7)]
    # fechado AGORA (manual) não gera horários; em modo horário, com expediente que não inclui agora, gera para depois
    agora = datetime.now(db.FUSO)
    abre = (agora + timedelta(hours=3)).strftime("%H:%M")
    fecha = (agora + timedelta(hours=5)).strftime("%H:%M")
    dias = [{"aberto": True, "abre": abre, "fecha": fecha} for _ in range(7)]
    r = c.put("/admin/api/loja", json={"modo": "horario", "dias": dias, "taxa_entrega": "0", "pedido_minimo": "0",
                                       "agendamento": True, "agend_minimo": "30", "agend_liberar": "40"},
              headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    assert pedido(cliente, produto_id).status_code == 409                         # agora: fechado
    h = _horarios(cliente)
    assert h, "deveria oferecer horários dentro do expediente"
    assert pedido(cliente, produto_id, agendado_para=h[0]["valor"]).status_code == 201


def test_modo_fechado_manual_nao_oferece_horarios(cliente, admin):
    c, csrf = admin
    _ligar(c, csrf, modo="fechado")
    assert _horarios(cliente) == []


def test_pedido_agendado_proximo_ja_vem_liberado(cliente, admin, con, produto_id):
    c, csrf = admin
    _ligar(c, csrf)
    d = pedido(cliente, produto_id, agendado_para=_horarios(cliente)[0]["valor"]).get_json()   # ≈30-60 min
    alvo = datetime.strptime(d["agendado_para"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    liberar = (alvo - timedelta(minutes=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
    con.execute("UPDATE pedidos SET liberar_em = ? WHERE senha = ?", (min(liberar, "2000-01-01T00:00:00Z"), d["senha"])); con.commit()
    ativo = next(p for p in c.get("/admin/api/pedidos").get_json() if p["senha"] == d["senha"])
    assert ativo["liberado"] is True


def test_agendado_futuro_nao_e_pedido_esquecido(cliente, admin, con, produto_id):
    c, csrf = admin
    _ligar(c, csrf)
    d = pedido(cliente, produto_id, agendado_para=_horarios(cliente)[-1]["valor"]).get_json()
    # mesmo "criado" há 20 h, o pedido só vale a partir do horário de liberação -> não é "esquecido"
    antigo = (datetime.now(timezone.utc) - timedelta(hours=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    con.execute("UPDATE pedidos SET criado = ? WHERE senha = ?", (antigo, d["senha"])); con.commit()
    assert db.contar_pedidos_antigos(con, 12) == 0
    assert db.verificar_atrasos(con) == 0


def test_comanda_mostra_agendamento(cliente, admin, produto_id):
    c, csrf = admin
    _ligar(c, csrf)
    d = pedido(cliente, produto_id, agendado_para=_horarios(cliente)[0]["valor"]).get_json()
    assert b"AGENDADO PARA" in c.get(f"/admin/pedidos/{d['senha']}/comanda").data


@pytest.mark.parametrize("campo,valor", [("agend_minimo", "abc"), ("agend_minimo", "-5"), ("agend_minimo", "9999"),
                                         ("agend_liberar", "500")])
def test_config_de_agendamento_invalida(admin, campo, valor):
    c, csrf = admin
    assert _salvar_loja(c, csrf, agendamento=True, **{campo: valor}).status_code == 400


def test_comecar_antes_da_hora_libera_o_pedido(cliente, admin, produto_id):
    c, csrf = admin
    _ligar(c, csrf)
    d = pedido(cliente, produto_id, agendado_para=_horarios(cliente)[-1]["valor"]).get_json()
    c.patch(f"/admin/api/pedidos/{d['senha']}/status", json={"status": "fazendo"}, headers={"X-CSRF-Token": csrf})
    ativo = next(p for p in c.get("/admin/api/pedidos").get_json() if p["senha"] == d["senha"])
    assert ativo["status"] == "fazendo" and ativo["liberado"] is True and ativo["minutos"] <= 1
