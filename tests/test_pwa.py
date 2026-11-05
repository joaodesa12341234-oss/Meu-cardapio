import json


def test_manifesto_valido(cliente):
    r = cliente.get("/manifest.webmanifest")
    assert r.status_code == 200 and "manifest+json" in r.mimetype
    m = json.loads(r.data)
    assert m["display"] == "standalone" and m["start_url"] == "/" and len(m["icons"]) >= 2


def test_icones_png(cliente):
    for tam in (180, 192, 512):
        r = cliente.get(f"/icone/{tam}.png")
        assert r.status_code == 200 and r.data[:8] == b"\x89PNG\r\n\x1a\n"
    assert cliente.get("/icone/999.png").status_code == 404


def test_service_worker_na_raiz_e_nao_guarda_api(cliente):
    r = cliente.get("/sw.js")
    assert r.status_code == 200 and "javascript" in r.mimetype
    assert r.headers["Service-Worker-Allowed"] == "/" and "no-cache" in r.headers["Cache-Control"]
    corpo = r.get_data(as_text=True)
    assert '"/api/"' in corpo and '"/admin"' in corpo and '"/acompanhar"' in corpo


def test_pagina_inicial_liga_o_manifesto(cliente):
    h = cliente.get("/").get_data(as_text=True)
    assert 'rel="manifest"' in h and "pwa.js" in h and 'id="btn-instalar"' in h


def test_painel_instalavel(cliente, admin):
    m = json.loads(cliente.get("/admin/manifest.webmanifest").data)        # o navegador busca sem login
    assert m["start_url"] == "/admin" and m["scope"] == "/admin" and m["display"] == "standalone"
    c, _ = admin
    h = c.get("/admin").get_data(as_text=True)
    assert 'rel="manifest" href="/admin/manifest.webmanifest"' in h and "pwa.js" in h and 'id="btn-instalar"' in h
