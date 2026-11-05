import pytest
import db
from conftest import pedido


def _cab(csrf):
    return {"X-CSRF-Token": csrf}


def test_painel_exige_login(cliente):
    assert cliente.get("/admin/api/financeiro?inicio=2026-01-01&fim=2026-01-02").status_code in (401, 302, 403)
    assert cliente.get("/admin/cartaz").status_code in (302, 401, 403)
    assert cliente.get("/admin/api/custos").status_code in (401, 302, 403)


def test_escrita_sem_csrf_e_recusada(admin):
    c, _ = admin
    assert c.put("/admin/api/lanchonete", json={"nome": "X"}).status_code in (400, 403)


def test_fluxo_de_status_nao_duplica(con, cliente, produto_id):
    senha = pedido(cliente, produto_id).get_json()["senha"]
    assert db.mudar_status(con, senha, "fazendo")["alterado"] is True
    assert db.mudar_status(con, senha, "fazendo")["alterado"] is False
    for args in ((senha, "xx"), (99999, "pronto")):
        try:
            db.mudar_status(con, *args)
            assert False, "deveria recusar"
        except db.ErroNegocio:
            pass


def test_custo_nao_vaza_no_cardapio_publico(cliente, admin):
    c, csrf = admin
    cat = cliente.get("/api/menu").get_json()["categorias"][0]["id"]
    r = c.post("/admin/api/produtos", json={"categoria_id": cat, "nome": "Teste", "preco": "20", "custo": "7,50",
                                            "imagem": ""}, headers=_cab(csrf))
    assert r.status_code == 201
    assert "custo" not in cliente.get("/api/menu").get_data(as_text=True)
    assert 750 in c.get("/admin/api/custos").get_json().values()
    assert c.post("/admin/api/produtos", json={"categoria_id": cat, "nome": "Ruim", "preco": "5", "custo": "abc",
                                               "imagem": ""}, headers=_cab(csrf)).status_code == 400


def test_lucro_usa_o_custo_da_hora_da_venda(cliente, admin):
    c, csrf = admin
    cat = cliente.get("/api/menu").get_json()["categorias"][0]["id"]
    pid = c.post("/admin/api/produtos", json={"categoria_id": cat, "nome": "Lucro", "preco": "20", "custo": "8",
                                              "imagem": ""}, headers=_cab(csrf)).get_json()["id"]
    senha = pedido(cliente, pid, itens=[{"id": pid, "qtd": 2}]).get_json()["senha"]
    for st in ("fazendo", "pronto", "concluido"):
        c.patch(f"/admin/api/pedidos/{senha}/status", json={"status": st}, headers=_cab(csrf))
    c.put(f"/admin/api/produtos/{pid}", json={"custo": "15"}, headers=_cab(csrf))   # mudar depois não altera o passado
    from datetime import date
    hoje = date.today().isoformat()
    fin = c.get(f"/admin/api/financeiro?inicio={hoje}&fim={hoje}").get_json()
    assert fin["lucro"]["estimado"] == 2400            # (20,00 - 8,00) x 2
    assert fin["faturamento"] == 4000


def test_csv_nao_tem_telefone_nem_endereco(cliente, admin, produto_id):
    c, _ = admin
    pedido(cliente, produto_id, telefone="(75) 98765-4321")
    from datetime import date
    hoje = date.today().isoformat()
    corpo = c.get(f"/admin/api/financeiro.csv?inicio={hoje}&fim={hoje}").get_data(as_text=True)
    assert "98765" not in corpo and "Telefone" not in corpo


def test_cartaz_so_para_admin(admin):
    c, _ = admin
    r = c.get("/admin/cartaz")
    assert r.status_code == 200 and b"qrcode.js" in r.data


def test_xlsx_formatado_sem_dados_pessoais(cliente, admin, produto_id):
    import io
    from datetime import date
    from openpyxl import load_workbook
    c, _ = admin
    pedido(cliente, produto_id, telefone="(75) 98765-4321", nome="Zeca Secreto")
    hoje = date.today().isoformat()
    r = c.get(f"/admin/api/financeiro.xlsx?inicio={hoje}&fim={hoje}")
    assert r.status_code == 200 and r.mimetype.endswith("spreadsheetml.sheet")
    ws = load_workbook(io.BytesIO(r.data)).active
    textos = " ".join(str(c.value) for linha in ws.iter_rows() for c in linha if c.value is not None)
    assert "98765" not in textos and "Zeca" not in textos
    assert [c.value for c in ws[3]][:2] == ["Senha", "Data"]
    assert ws["J4"].value == pytest.approx(37.8) and ws["J4"].number_format.startswith('"R$"')
    assert ws.freeze_panes == "A4"
    assert [ws.cell(r, 1).value for r in range(6, 11)][0] == "RESUMO DO PERÍODO"
    assert ws["F7"].value == 0 and ws["F10"].value == 0     # nenhum concluído ainda; valores fixos, não fórmulas
    assert admin and c.get("/admin/api/financeiro.xlsx?inicio=x&fim=y").status_code == 400


def test_xlsx_so_admin(cliente):
    assert cliente.get("/admin/api/financeiro.xlsx").status_code in (302, 401, 403)
