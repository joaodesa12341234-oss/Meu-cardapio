def test_csp_sem_inline(cliente):
    csp = cliente.get("/").headers.get("Content-Security-Policy", "")
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp


def test_paginas_nao_tem_script_nem_estilo_inline(cliente):
    html = cliente.get("/").get_data(as_text=True)
    assert " style=" not in html and "onclick=" not in html
    assert "<script>" not in html


def test_nome_com_html_nao_executa_na_comanda(cliente, admin, produto_id):
    from conftest import pedido
    c, _ = admin
    senha = pedido(cliente, produto_id, nome="<script>alert(1)</script>").get_json()["senha"]
    pagina = c.get(f"/admin/pedidos/{senha}/comanda").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in pagina


def test_whatsapp_formata_numeros():
    import whatsapp as w
    assert w.formatar_numero("+55 75 98765-4321") == "5575987654321"
    assert w.formatar_numero("75987654321") == "5575987654321"
    assert w.formatar_numero("12") is None


def test_whatsapp_status_desligado(monkeypatch):
    import whatsapp as w
    monkeypatch.setenv("WHATSAPP_ATIVO", "0")
    assert w.status_conexao()["estado"] == "desligado"
