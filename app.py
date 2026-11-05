"""Cardápio digital para lanchonete (Flask + SQLite).

Desenvolvimento:  python app.py          ->  http://localhost:5000
Painel do dono:                              http://localhost:5000/admin
Produção:         gunicorn wsgi:app -c gunicorn.conf.py   (veja o README)
"""
import ambiente  # noqa: F401  (carrega o .env antes de ler qualquer variável)
import csv
import gzip
import io
import json
import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from datetime import timedelta
from functools import wraps
from pathlib import Path

from flask import (Flask, Response, abort, g, jsonify, redirect, render_template, request,
                    send_from_directory, session, url_for)
from flask.sessions import SecureCookieSessionInterface
from werkzeug.middleware.proxy_fix import ProxyFix

import db
import imagens
import whatsapp
import seguranca
from db import ErroNegocio

BASE = Path(__file__).parent

app = Flask(__name__)
app.json.ensure_ascii = False
app.config.update(
    MAX_CONTENT_LENGTH=6 * 1024 * 1024,           # teto da requisição (só o upload chega perto)
    SESSION_COOKIE_NAME="cardapio_sessao",
    SESSION_COOKIE_HTTPONLY=True,                 # JavaScript não enxerga o cookie
    SESSION_COOKIE_SAMESITE="Lax",                # não é enviado em requisições de outros sites
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",  # use 1 quando houver HTTPS
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    SESSION_REFRESH_EACH_REQUEST=False,           # o prazo conta do login, não "desliza" com o uso
)
PEDIDO_ANTIGO_HORAS = 12   # pedido ainda na fila depois disso é considerado esquecido
SESSAO_MAX_SEGUNDOS = 12 * 3600  # prazo absoluto, conferido também no servidor


class SessaoSegura(SecureCookieSessionInterface):
    """Cookie 'Secure' automático sempre que o acesso for por HTTPS (e COOKIE_SECURE=1 força).
    Em http://localhost o cookie continua funcionando, para você poder testar."""

    def get_cookie_secure(self, app):
        return bool(app.config["SESSION_COOKIE_SECURE"] or request.is_secure)


app.session_interface = SessaoSegura()
# Atrás de Nginx/Caddy/hospedagem com HTTPS, ligue TRUST_PROXY=1: sem isso o sistema enxerga
# o IP do proxy (e não o do cliente) e não percebe que o acesso é HTTPS.
if os.environ.get("TRUST_PROXY") == "1":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# Bloqueio de tentativas de login
JANELA_LOGIN = 15 * 60
MAX_FALHAS_POR_IP = 5          # mesmo usuário, mesmo IP
MAX_FALHAS_POR_USUARIO = 50    # mesmo usuário, qualquer IP (ataque distribuído)
MAX_FALHAS_POR_IP_TOTAL = 20   # mesmo IP, tentando usuários diferentes


def _iniciar():
    db.iniciar_banco()
    con = db.conectar()
    try:
        app.secret_key = seguranca.chave_secreta(con)
        novo = seguranca.criar_admin_inicial(con)
    finally:
        con.close()
    if novo and novo[1]:
        seguranca.avisar_senha_gerada(novo[0], novo[1])


def _vigia_de_atrasos():
    """Uma vez por minuto confere se algum pedido passou do tempo limite (avisa o dono, 1 vez por pedido)."""
    while True:
        time.sleep(60)
        try:
            con = db.conectar()
            try:
                db.verificar_atrasos(con)
                db.enviar_relatorio_diario(con)
            finally:
                con.close()
        except Exception:                                  # o vigia nunca pode derrubar o servidor
            app.logger.exception("Falha ao verificar pedidos atrasados")


_iniciar()
if os.environ.get("SEM_VIGIA") != "1":
    threading.Thread(target=_vigia_de_atrasos, daemon=True, name="vigia-atrasos").start()


# ------------------------------------------------------------ infra ----
def get_db():
    if "db" not in g:
        g.db = db.conectar()
    return g.db


@app.teardown_appcontext
def _fechar_banco(_erro):
    con = g.pop("db", None)
    if con is not None:
        con.close()


@app.errorhandler(ErroNegocio)
def _erro_negocio(erro):
    return jsonify({"erro": erro.mensagem}), erro.status


@app.errorhandler(imagens.ErroImagem)
def _erro_imagem(erro):
    return jsonify({"erro": str(erro)}), 400


@app.errorhandler(413)
def _arquivo_grande(_erro):
    return jsonify({"erro": "Arquivo grande demais."}), 413


@app.errorhandler(sqlite3.IntegrityError)
def _integridade(_erro):
    app.logger.warning("Violação de integridade no banco", exc_info=True)
    return jsonify({"erro": "Dados inválidos."}), 400


@app.errorhandler(OverflowError)
@app.errorhandler(sqlite3.InterfaceError)
def _dado_invalido(_erro):  # número gigante ou tipo inesperado vindo do navegador
    return jsonify({"erro": "Dados inválidos."}), 400


def corpo_json():
    """Corpo da requisição como dict; qualquer outra coisa ([], texto, número) é recusada."""
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        raise ErroNegocio("Dados inválidos.")
    return dados


@app.errorhandler(sqlite3.OperationalError)
def _banco_ocupado(_erro):
    app.logger.exception("Erro no banco de dados")
    return jsonify({"erro": "O sistema está ocupado. Tente de novo em instantes."}), 503


@app.before_request
def _marcar_autor():
    """O histórico de alterações guarda quem fez a mudança (o usuário logado)."""
    u = usuario_atual() if session.get("uid") else None
    db.QUEM.set(u["usuario"] if u else "sistema")


@app.before_request
def _limitar_tamanho():
    if request.endpoint != "admin_upload":  # só o upload de foto pode ser grande
        request.max_content_length = 64 * 1024


@app.template_global()
def asset(nome):
    """URL de um arquivo de static/ com a data de modificação (?v=...). Quando o arquivo
    muda, a URL muda, e por isso o navegador pode guardá-lo por 1 ano."""
    try:
        versao = int((BASE / "static" / nome).stat().st_mtime)
    except OSError:
        versao = 0
    return url_for("static", filename=nome, v=versao)


# --------------------------------------------- cache, compressão, segurança ----
CSP = ("default-src 'self'; img-src 'self' data: blob:; script-src 'self'; style-src 'self'; "
       "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
       "frame-ancestors 'none'")
COMPRIMIVEIS = {"text/html", "text/css", "text/javascript", "application/javascript",
                "application/json", "image/svg+xml"}
_cache_gzip = {}  # (arquivo, etag) -> bytes comprimidos; os arquivos de static/ são poucos


@app.after_request
def _depois(resp):
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.is_secure:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000"

    caminho = request.path
    if caminho.startswith("/static/"):
        if resp.status_code in (200, 304):
            fixo = "v" in request.args or caminho.startswith("/static/uploads/")
            resp.headers["Cache-Control"] = ("public, max-age=31536000, immutable" if fixo
                                             else "public, max-age=3600")
    elif caminho.startswith("/admin") or caminho == "/api/pedido":
        resp.headers["Cache-Control"] = "no-store"
    elif caminho == "/api/menu" or resp.mimetype == "text/html":
        resp.headers["Cache-Control"] = "no-cache"

    _comprimir(resp)
    return resp


def _comprimir(resp):
    if (resp.status_code != 200 or "Content-Encoding" in resp.headers
            or resp.mimetype not in COMPRIMIVEIS
            or "gzip" not in request.headers.get("Accept-Encoding", "")):
        return
    resp.vary.add("Accept-Encoding")
    if resp.direct_passthrough:
        etag = resp.headers.get("ETag")
        chave = (request.path, etag)
        if etag and chave in _cache_gzip:
            dados = _cache_gzip[chave]
        else:
            resp.direct_passthrough = False
            dados = gzip.compress(resp.get_data(), 6, mtime=0)
            if etag and len(_cache_gzip) < 64:
                _cache_gzip[chave] = dados
    else:
        original = resp.get_data()
        if len(original) < 500:
            return
        dados = gzip.compress(original, 6, mtime=0)
    resp.direct_passthrough = False
    resp.set_data(dados)
    resp.headers["Content-Encoding"] = "gzip"
    etag = resp.headers.get("ETag")
    if etag and not etag.startswith("W/"):
        resp.headers["ETag"] = "W/" + etag


# --------------------------------------------------- autenticação ----
def _csrf_ok():
    esperado = session.get("csrf")
    recebido = request.headers.get("X-CSRF-Token") or request.form.get("csrf") or ""
    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        return False
    return bool(esperado) and hmac.compare_digest(esperado, recebido)


def _marca_senha(u):
    return hashlib.sha256(u["senha_hash"].encode()).hexdigest()[:16]


def usuario_atual():
    if "usuario" in g:
        return g.usuario
    g.usuario = None
    uid = session.get("uid")
    if uid:
        u = db.obter_usuario(get_db(), uid)
        recente = time.time() - session.get("iat", 0) < SESSAO_MAX_SEGUNDOS
        if (u and u["ativo"] and recente
                and hmac.compare_digest(session.get("sv", ""), _marca_senha(u))):
            g.usuario = u
        else:
            session.clear()
    return g.usuario


def exige_papel(*papeis, pagina=False):
    def decorador(rota):
        @wraps(rota)
        def protegida(*args, **kwargs):
            u = usuario_atual()
            if u is None:
                if pagina:
                    return redirect(url_for("login"))
                return jsonify({"erro": "Sua sessão expirou. Entre de novo."}), 401
            if u["papel"] not in papeis:
                if pagina:
                    return Response("Você não tem permissão para ver esta página.", 403)
                return jsonify({"erro": "Você não tem permissão para fazer isso."}), 403
            if request.method not in ("GET", "HEAD", "OPTIONS") and not _csrf_ok():
                return jsonify({"erro": "Sessão inválida. Recarregue a página."}), 403
            return rota(*args, **kwargs)
        return protegida
    return decorador


def exige_json(rota):
    @wraps(rota)
    def verificada(*args, **kwargs):
        if not request.is_json:
            return jsonify({"erro": "Envie os dados em JSON."}), 415
        return rota(*args, **kwargs)
    return verificada


def _pagina_login(erro=None, status=200):
    if not session.get("csrf"):
        session["csrf"] = secrets.token_urlsafe(32)
    resp = Response(render_template("login.html", nome=db.nome_lanchonete(get_db()),
                                    csrf=session["csrf"], erro=erro), status)
    return resp


@app.route("/admin/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if usuario_atual():
            return redirect(url_for("admin"))
        return _pagina_login()

    if not _csrf_ok():
        return redirect(url_for("login"))
    usuario = request.form.get("usuario", "").strip().lower()[:50]
    senha = request.form.get("senha", "")[:200]
    ip = request.remote_addr or "?"
    con = get_db()

    por_par, por_usuario, por_ip = db.contar_falhas_login(con, usuario, ip,
                                                          time.time() - JANELA_LOGIN)
    if (por_par >= MAX_FALHAS_POR_IP or por_usuario >= MAX_FALHAS_POR_USUARIO
            or por_ip >= MAX_FALHAS_POR_IP_TOTAL):
        resp = _pagina_login("Muitas tentativas erradas. Aguarde 15 minutos e tente de novo.", 429)
        resp.headers["Retry-After"] = str(JANELA_LOGIN)
        return resp

    u = db.obter_usuario_por_nome(con, usuario)
    ativo = u is not None and u["ativo"]
    if not seguranca.conferir_senha(u["senha_hash"] if ativo else None, senha):
        db.registrar_falha_login(con, usuario, ip, time.time())
        return _pagina_login("Usuário ou senha incorretos.", 401)

    db.limpar_falhas_login(con, usuario, ip)
    session.clear()
    session.permanent = True
    session["uid"] = u["id"]
    session["sv"] = _marca_senha(u)
    session["iat"] = int(time.time())
    session["csrf"] = secrets.token_urlsafe(32)
    return redirect(url_for("admin"))


@app.route("/admin/logout", methods=["POST"])
def logout():
    if usuario_atual() and not _csrf_ok():
        return redirect(url_for("admin"))
    session.clear()
    return redirect(url_for("login"))


# ------------------------------------------------------------ painel ----
@app.route("/admin")
@exige_papel("admin", "cozinha", pagina=True)
def admin():
    u = g.usuario
    return render_template("admin.html", nome=db.nome_lanchonete(get_db()), usuario=u["usuario"],
                           papel=u["papel"], pode_cardapio=u["papel"] == "admin",
                           csrf=session["csrf"])


@app.route("/admin/api/pedidos")
@exige_papel("admin", "cozinha")
def admin_listar_pedidos():
    return jsonify(db.listar_pedidos_ativos(get_db()))



@app.route("/admin/api/pedidos/<senha>/status", methods=["PATCH", "POST"])
@exige_papel("admin", "cozinha")
def api_mudar_status(senha):
    dados = corpo_json()
    novo_status = dados.get("status")
    if not isinstance(novo_status, str):
        raise ErroNegocio("Status não informado.")
    return jsonify(db.mudar_status(get_db(), senha, novo_status))


# ------------------------------------------------- loja e manutenção ----
@app.route("/admin/api/whatsapp")
@exige_papel("admin", "cozinha")
def admin_status_whatsapp():
    return jsonify(whatsapp.status_conexao())


@app.route("/admin/api/loja", methods=["GET"])
@exige_papel("admin")
def admin_obter_loja():
    return jsonify(db.config_loja_painel(get_db()))


@app.route("/admin/api/loja", methods=["PUT"])
@exige_papel("admin")
@exige_json
def admin_salvar_loja():
    db.salvar_config_loja(get_db(), corpo_json())
    return jsonify(db.config_loja_painel(get_db()))


@app.route("/admin/api/pedidos/antigos")
@exige_papel("admin")
def admin_pedidos_antigos():
    return jsonify({"horas": PEDIDO_ANTIGO_HORAS, "quantidade": db.contar_pedidos_antigos(get_db(), PEDIDO_ANTIGO_HORAS)})


@app.route("/admin/api/pedidos/encerrar-antigos", methods=["POST"])
@exige_papel("admin")
@exige_json
def admin_encerrar_antigos():
    destino = corpo_json().get("destino")
    return jsonify({"encerrados": db.encerrar_pedidos_antigos(get_db(), destino, PEDIDO_ANTIGO_HORAS)})


@app.route("/admin/cartaz")
@exige_papel("admin", pagina=True)
def admin_cartaz():
    """Cartaz imprimível com o QR code do cardápio. O endereço padrão vem de PUBLIC_URL (ou do host atual)."""
    url = os.environ.get("PUBLIC_URL", "").strip() or request.host_url
    return render_template("cartaz.html", loja=db.nome_lanchonete(get_db()), url=url)


@app.route("/admin/cartaz-mesas")
@exige_papel("admin", pagina=True)
def admin_cartaz_mesas():
    """Um cartaz com QR code para cada mesa (o QR abre o cardápio já "sentado" naquela mesa)."""
    url = os.environ.get("PUBLIC_URL", "").strip() or request.host_url
    cfg = db.obter_config_loja(get_db())
    return render_template("cartaz_mesas.html", loja=db.nome_lanchonete(get_db()), url=url, mesas=cfg["mesas"])


@app.route("/admin/cozinha")
@exige_papel("admin", "cozinha", pagina=True)
def admin_cozinha():
    """Tela cheia para a cozinha (tablet/TV): só o que precisa para preparar, sem telefone nem endereço."""
    return render_template("cozinha.html", nome=db.nome_lanchonete(get_db()), csrf=session["csrf"])


@app.route("/admin/pedidos/<int:senha>/comanda")
@exige_papel("admin", "cozinha", pagina=True)
def admin_comanda(senha):
    dados = db.obter_pedido(get_db(), senha)
    if dados is None:
        abort(404)
    return render_template("comanda.html", p=dados["pedido"], itens=dados["itens"], hora=dados["hora_local"],
                           agendado=dados["agendado_local"],
                           loja=db.nome_lanchonete(get_db()), pagamentos=db.PAGAMENTOS)


# ---------------------------------------------------------- financeiro ----
@app.route("/admin/api/financeiro")
@exige_papel("admin")
def admin_financeiro():
    inicio, fim = db.validar_periodo(request.args.get("inicio"), request.args.get("fim"))
    return jsonify(db.resumo_financeiro(get_db(), inicio, fim))


def _csv_seguro(texto):
    """Evita 'injeção de fórmula' ao abrir no Excel: texto que começa com = + - @ vira texto puro."""
    texto = str(texto)
    return "'" + texto if texto[:1] in ("=", "+", "-", "@", "\t", "\r") else texto


@app.route("/admin/api/financeiro.csv")
@exige_papel("admin")
def admin_financeiro_csv():
    inicio, fim = db.validar_periodo(request.args.get("inicio"), request.args.get("fim"))
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";")   # ponto e vírgula: o Excel em português abre certo
    escritor.writerow(["Senha", "Data", "Hora", "Tipo", "Situação", "Pagamento", "Taxa de entrega (R$)", "Total (R$)"])
    for p in db.pedidos_do_periodo(get_db(), inicio, fim):
        local = db._local(p["criado"])
        escritor.writerow([p["senha"], local.strftime("%d/%m/%Y"), local.strftime("%H:%M"),
                           _csv_seguro(p["tipo"]), _csv_seguro(p["status"]),
                           db.PAGAMENTOS.get(p["pagamento"], "—"),
                           f"{p['taxa_entrega_centavos'] / 100:.2f}".replace(".", ","),
                           f"{p['total_centavos'] / 100:.2f}".replace(".", ",")])
    resp = Response("\ufeff" + saida.getvalue(), mimetype="text/csv")
    resp.headers["Content-Disposition"] = f'attachment; filename="pedidos-{inicio}-a-{fim}.csv"'
    return resp


_ROTULO_STATUS = {"novo": "Novo", "fazendo": "Em preparo", "pronto": "Pronto", "saiu": "Saiu p/ entrega",
                  "concluido": "Concluído", "cancelado": "Cancelado"}
_ROTULO_TIPO = {"balcao": "Retirada", "entrega": "Entrega"}


@app.route("/admin/api/financeiro.xlsx")
@exige_papel("admin")
def admin_financeiro_xlsx():
    """Planilha formatada (colunas largas, valores em R$, filtros, totais) — abre direto no Excel."""
    try:
        from openpyxl import Workbook
    except ImportError:
        return jsonify({"erro": "Falta instalar o pacote openpyxl. No terminal: pip install -r requirements.txt e reinicie o cardápio."}), 501
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    inicio, fim = db.validar_periodo(request.args.get("inicio"), request.args.get("fim"))
    pedidos = db.pedidos_do_periodo(get_db(), inicio, fim)
    wb = Workbook()
    ws = wb.active
    ws.title = "Pedidos"
    ws.append([f"Pedidos de {inicio.strftime('%d/%m/%Y')} a {fim.strftime('%d/%m/%Y')}"])
    ws["A1"].font = Font(bold=True, size=14, color="991B1B")
    ws.merge_cells("A1:J1")
    cab = ["Senha", "Data", "Hora", "Tipo", "Situação", "Pagamento", "Cupom", "Desconto", "Taxa de entrega", "Total"]
    ws.append([])
    ws.append(cab)
    borda = Side(style="thin", color="CBD5E1")
    for c in ws[3]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="B91C1C")
        c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[3].height = 24
    moeda = '"R$" #,##0.00'
    for p in pedidos:
        local = db._local(p["criado"])
        ws.append([int(p["senha"]) if str(p["senha"]).isdigit() else p["senha"],
                   local.replace(tzinfo=None).date(), local.strftime("%H:%M"),
                   (f"Mesa {p['mesa']}" if p["mesa"] else _ROTULO_TIPO.get(p["tipo"], p["tipo"])), _ROTULO_STATUS.get(p["status"], p["status"]),
                   db.PAGAMENTOS.get(p["pagamento"], "—"),
                   " + ".join(x for x in (p["cupom"], "Fidelidade" if p["fidelidade_centavos"] else "") if x),
                   (p["desconto_centavos"] + p["fidelidade_centavos"]) / 100, p["taxa_entrega_centavos"] / 100, p["total_centavos"] / 100])
    primeira, ultima = 4, 3 + len(pedidos)
    cores = {"Concluído": "DCFCE7", "Cancelado": "FEE2E2"}
    for i, linha in enumerate(ws.iter_rows(min_row=primeira, max_row=ultima), 0):
        zebra = "F8FAFC" if i % 2 else "FFFFFF"
        for c in linha:
            c.border = Border(bottom=borda)
            c.fill = PatternFill("solid", fgColor=zebra)
        linha[0].alignment = linha[2].alignment = linha[1].alignment = Alignment(horizontal="center")
        linha[1].number_format = "dd/mm/yyyy"
        linha[6].alignment = Alignment(horizontal="center")
        linha[7].number_format = linha[8].number_format = linha[9].number_format = moeda
        cor = cores.get(linha[4].value)
        if cor:
            linha[4].fill = PatternFill("solid", fgColor=cor)
    for col, larg in zip("ABCDEFGHIJ", (9, 13, 8, 12, 17, 14, 14, 12, 17, 14)):
        ws.column_dimensions[col].width = larg
    ws.freeze_panes = "A4"
    if pedidos:
        ws.auto_filter.ref = f"A3:J{ultima}"
    # totais com valores fixos (não fórmulas): o Excel em "Modo Protegido" não calcula fórmulas e mostraria vazio
    conc = [p for p in pedidos if p["status"] == "concluido"]
    resumo = [("Faturamento (concluídos)", sum(p["total_centavos"] for p in conc) / 100, moeda),
              ("Descontos de cupons (concluídos)", sum(p["desconto_centavos"] + p["fidelidade_centavos"] for p in conc) / 100, moeda),
              ("Taxas de entrega (concluídos)", sum(p["taxa_entrega_centavos"] for p in conc) / 100, moeda),
              ("Pedidos concluídos", len(conc), "0"),
              ("Pedidos cancelados", sum(1 for p in pedidos if p["status"] == "cancelado"), "0")]
    r = ultima + 2
    ws.cell(r, 1, "RESUMO DO PERÍODO").font = Font(bold=True, color="991B1B")
    for k, (rotulo, valor, formato) in enumerate(resumo, 1):
        ws.cell(r + k, 1, rotulo).font = Font(bold=True)
        ws.merge_cells(start_row=r + k, start_column=1, end_row=r + k, end_column=5)
        c = ws.cell(r + k, 6, valor)
        c.number_format = formato
        c.font = Font(bold=True)
        c.alignment = Alignment(horizontal="right")
        for col in range(1, 7):
            ws.cell(r + k, col).border = Border(bottom=borda)
    ws.sheet_view.showGridLines = False
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToHeight = 0
    saida = io.BytesIO()
    wb.save(saida)
    resp = Response(saida.getvalue(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp.headers["Content-Disposition"] = f'attachment; filename="pedidos-{inicio}-a-{fim}.xlsx"'
    return resp


@app.route("/admin/api/upload", methods=["POST"])
@exige_papel("admin")
def admin_upload():
    arquivo = request.files.get("foto")
    if arquivo is None:
        raise ErroNegocio("Escolha uma foto.")
    dados = arquivo.read(imagens.TAMANHO_MAX_BYTES + 1)
    url = imagens.processar(dados)
    db.limpar_fotos_soltas(get_db())
    return jsonify({"url": url, "miniatura": imagens.miniatura_de(url)}), 201


@app.route("/admin/api/custos")
@exige_papel("admin")
def admin_custos():
    return jsonify(db.custos_dos_produtos(get_db()))


@app.route("/admin/api/insumos")
@exige_papel("admin", "cozinha")
def admin_listar_insumos():
    return jsonify(db.listar_insumos(get_db()))


@app.route("/admin/api/insumos", methods=["POST"])
@exige_papel("admin")
@exige_json
def admin_criar_insumo():
    d = corpo_json()
    return jsonify(db.criar_insumo(get_db(), d.get("nome"), d.get("quantidade", 0), d.get("minimo", 10))), 201


@app.route("/admin/api/insumos/<ins_id>", methods=["PUT"])
@exige_papel("admin")
@exige_json
def admin_editar_insumo(ins_id):
    return jsonify(db.editar_insumo(get_db(), ins_id, corpo_json()))


@app.route("/admin/api/insumos/<ins_id>", methods=["DELETE"])
@exige_papel("admin")
def admin_excluir_insumo(ins_id):
    db.excluir_insumo(get_db(), ins_id)
    return jsonify({"ok": True})


@app.route("/admin/api/insumos/<ins_id>/vincular", methods=["POST"])
@exige_papel("admin")
@exige_json
def admin_vincular_insumo(ins_id):
    d = corpo_json()
    return jsonify(db.vincular_categoria(get_db(), ins_id, d.get("categoria_id"), d.get("consumo", 1)))


@app.route("/admin/api/historico")
@exige_papel("admin")
def admin_historico():
    try:
        return jsonify(db.listar_auditoria(get_db(), request.args.get("area"), request.args.get("busca"),
                                           request.args.get("antes"), request.args.get("limite", 50)))
    except ValueError:
        raise ErroNegocio("Parâmetro inválido.")


def _link_publico(cupom=None):
    base = (os.environ.get("PUBLIC_URL", "").strip() or request.host_url).rstrip("/") + "/"
    return base + (f"?cupom={cupom}" if cupom else "")


@app.route("/admin/api/cupons")
@exige_papel("admin")
def admin_listar_cupons():
    cupons = db.listar_cupons(get_db())
    for c in cupons:
        c["link"] = _link_publico(c["codigo"])
    return jsonify(cupons)


@app.route("/admin/api/relatorio/enviar", methods=["POST"])
@exige_papel("admin")
def admin_enviar_relatorio():
    db.enviar_relatorio_diario(get_db(), forcar=True)
    return jsonify({"ok": True})


@app.route("/admin/api/sumidos")
@exige_papel("admin")
def admin_sumidos():
    try:
        dias = int(request.args.get("dias", 21))
    except ValueError:
        raise ErroNegocio("Quantidade de dias inválida.")
    return jsonify({"clientes": db.clientes_sumidos(get_db(), dias), "mensagem_padrao": db.MENSAGEM_REATIVACAO,
                    "espera_dias": db.REATIVACAO_ESPERA_DIAS, "link": _link_publico()})


@app.route("/admin/api/sumidos/enviar", methods=["POST"])
@exige_papel("admin")
@exige_json
def admin_enviar_reativacao():
    d = corpo_json()
    cod = d.get("cupom")
    if cod not in (None, ""):
        cod = db._codigo_cupom(cod)
        if not get_db().execute("SELECT 1 FROM cupons WHERE codigo = ?", (cod,)).fetchone():
            raise ErroNegocio("Cupom inexistente.")
    return jsonify(db.enviar_reativacao(get_db(), d.get("telefone"), d.get("mensagem"), _link_publico(cod or None)))


@app.route("/admin/api/cupons", methods=["POST"])
@exige_papel("admin")
@exige_json
def admin_criar_cupom():
    return jsonify(db.criar_cupom(get_db(), corpo_json())), 201


@app.route("/admin/api/cupons/<codigo>", methods=["PUT"])
@exige_papel("admin")
@exige_json
def admin_editar_cupom(codigo):
    return jsonify(db.editar_cupom(get_db(), codigo, corpo_json()))


@app.route("/admin/api/cupons/<codigo>", methods=["DELETE"])
@exige_papel("admin")
def admin_excluir_cupom(codigo):
    db.excluir_cupom(get_db(), codigo)
    return jsonify({"ok": True})


@app.route("/admin/api/avaliacoes")
@exige_papel("admin")
def admin_avaliacoes():
    inicio, fim = db.validar_periodo(request.args.get("inicio"), request.args.get("fim"))
    return jsonify(db.resumo_avaliacoes(get_db(), inicio, fim))


@app.route("/admin/api/produtos-extra")
@exige_papel("admin")
def admin_produtos_extra():
    return jsonify(db.extras_dos_produtos(get_db()))


@app.route("/admin/api/produtos", methods=["POST"])
@exige_papel("admin")
@exige_json
def admin_criar_produto():
    return jsonify(db.criar_produto(get_db(), corpo_json())), 201


@app.route("/admin/api/produtos/<prod_id>", methods=["PUT"])
@exige_papel("admin")
@exige_json
def admin_editar_produto(prod_id):
    return jsonify(db.editar_produto(get_db(), prod_id, corpo_json()))


@app.route("/admin/api/produtos/<prod_id>/disponibilidade", methods=["PATCH"])
@exige_papel("admin")
@exige_json
def admin_disponibilidade(prod_id):
    disponivel = corpo_json().get("disponivel")
    if not isinstance(disponivel, bool):
        raise ErroNegocio("Informe disponivel: true ou false.")
    return jsonify(db.definir_disponibilidade(get_db(), prod_id, disponivel))


@app.route("/admin/api/produtos/<prod_id>", methods=["DELETE"])
@exige_papel("admin")
def admin_excluir_produto(prod_id):
    db.excluir_produto(get_db(), prod_id)
    return jsonify({"ok": True})


@app.route("/admin/api/lanchonete", methods=["PUT"])
@exige_papel("admin")
@exige_json
def admin_nome_lanchonete():
    return jsonify({"nome": db.definir_nome_lanchonete(get_db(), corpo_json().get("nome"))})


@app.route("/admin/api/categorias", methods=["POST"])
@exige_papel("admin")
@exige_json
def admin_criar_categoria():
    dados = corpo_json()
    return jsonify(db.criar_categoria(get_db(), dados.get("nome"), dados.get("icone"))), 201


@app.route("/admin/api/categorias/<cat_id>", methods=["DELETE"])
@exige_papel("admin")
def admin_excluir_categoria(cat_id):
    return jsonify(db.excluir_categoria(get_db(), cat_id))


@app.route("/admin/api/insumos/<ins_id>/produtos", methods=["PUT"])
@exige_papel("admin")
@exige_json
def admin_produtos_do_insumo(ins_id):
    return jsonify(db.definir_produtos_do_insumo(get_db(), ins_id, corpo_json().get("produtos")))


# ----------------------------------------------------------- clientes ----
@app.route("/static/uploads/<nome>")
def foto_produto(nome):
    if not imagens.RE_ARQUIVO_WEBP.match(nome):
        abort(404)
    return send_from_directory(imagens.UPLOADS, nome, mimetype="image/webp")


@app.route("/")
def index():
    return render_template("index.html", nome=db.nome_lanchonete(get_db()))


@app.route("/api/menu")
def api_menu():
    resp = jsonify(db.obter_menu(get_db()))
    resp.add_etag()
    return resp.make_conditional(request)


@app.route("/api/pedido", methods=["POST"])
def novo_pedido():
    d = corpo_json()
    try:
        pedido = db.criar_pedido(get_db(), d.get("itens"), d.get("tipo"), d.get("nome"),
                                 d.get("telefone"), d.get("endereco"), d.get("observacao"),
                                 ip=request.remote_addr or "?",
                                 pagamento=d.get("pagamento"), troco_para=d.get("troco_para"),
                                 rua=d.get("rua"), numero=d.get("numero"), sem_numero=d.get("sem_numero") is True,
                                 bairro=d.get("bairro"), complemento=d.get("complemento"),
                                 referencia=d.get("referencia"),
                                 base_url=os.environ.get("PUBLIC_URL", "").strip() or request.host_url,
                                 agendado_para=d.get("agendado_para"), cupom=d.get("cupom"), mesa=d.get("mesa"))
    except ErroNegocio as erro:
        resp = jsonify({"erro": erro.mensagem})
        resp.status_code = erro.status
        if erro.status == 429:
            resp.headers["Retry-After"] = "120"
        return resp
    return jsonify(pedido), 201


_tentativas_cupom = {}     # ip -> [instantes]; freia quem tenta adivinhar códigos de cupom


def _cupom_limitado(ip):
    agora = time.time()
    recentes = [t for t in _tentativas_cupom.get(ip, []) if agora - t < 600]
    if len(recentes) >= 20:
        _tentativas_cupom[ip] = recentes
        return True
    recentes.append(agora)
    _tentativas_cupom[ip] = recentes
    if len(_tentativas_cupom) > 5000:
        _tentativas_cupom.clear()
    return False


@app.route("/api/cupom", methods=["POST"])
@exige_json
def api_cupom():
    """Mostra ao cliente o desconto de um cupom (a conta de verdade é feita ao fechar o pedido)."""
    d = corpo_json()
    if _cupom_limitado(request.remote_addr or "?"):
        resp = jsonify({"erro": "Muitas tentativas. Aguarde alguns minutos."})
        resp.status_code = 429
        return resp
    try:
        return jsonify(db.pre_visualizar_cupom(get_db(), d.get("codigo"), d.get("subtotal"), d.get("telefone")))
    except ErroNegocio as erro:
        return jsonify({"erro": erro.mensagem}), erro.status


@app.route("/api/fidelidade")
def api_fidelidade():
    """Quanto falta para o cliente (pelo telefone) ganhar o desconto de fidelidade."""
    if _cupom_limitado(request.remote_addr or "?"):
        return jsonify({"erro": "Muitas tentativas. Aguarde alguns minutos."}), 429
    resp = jsonify(db.pre_visualizar_fidelidade(get_db(), request.args.get("telefone")))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/acompanhar/<token>/avaliar", methods=["POST"])
@exige_json
def api_avaliar(token):
    d = corpo_json()
    try:
        return jsonify(db.avaliar_pedido(get_db(), token, d.get("nota"), d.get("comentario")))
    except ErroNegocio as erro:
        return jsonify({"erro": erro.mensagem}), erro.status


# ---------------------------------------------------------------- PWA ----
_ICONES = {}


def _desenhar_icone(tam):
    """Ícone do app (um hambúrguer simples) em PNG, desenhado na hora e guardado em memória."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (tam, tam), (214, 35, 0, 255))
    d = ImageDraw.Draw(img)
    u = tam / 100.0
    def caixa(x1, y1, x2, y2):
        return [x1 * u, y1 * u, x2 * u, y2 * u]
    d.pieslice(caixa(22, 20, 78, 68), 180, 360, fill=(255, 193, 79, 255))          # pão de cima
    for x in (38, 50, 62):
        d.ellipse(caixa(x - 2, 33, x + 2, 36), fill=(255, 245, 220, 255))           # gergelim
    d.rounded_rectangle(caixa(20, 46, 80, 53), radius=3.5 * u, fill=(86, 196, 79, 255))   # alface
    d.rounded_rectangle(caixa(22, 54, 78, 64), radius=5 * u, fill=(110, 58, 28, 255))     # hambúrguer
    d.rounded_rectangle(caixa(22, 66, 78, 76), radius=5 * u, fill=(255, 193, 79, 255))    # pão de baixo
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG", optimize=True)
    return buf.getvalue()


@app.route("/icone/<int:tam>.png")
def icone_app(tam):
    if tam not in (180, 192, 512):
        abort(404)
    if tam not in _ICONES:
        _ICONES[tam] = _desenhar_icone(tam)
    resp = Response(_ICONES[tam], mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.route("/manifest.webmanifest")
def manifesto():
    nome = db.nome_lanchonete(get_db())
    corpo = {"name": f"{nome} — Cardápio", "short_name": nome[:12], "description": f"Peça no {nome}",
             "start_url": "/", "scope": "/", "display": "standalone", "orientation": "portrait",
             "background_color": "#fff8e7", "theme_color": "#d62300", "lang": "pt-BR",
             "icons": [{"src": "/icone/192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
                       {"src": "/icone/512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}]}
    resp = Response(json.dumps(corpo, ensure_ascii=False), mimetype="application/manifest+json")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/admin/manifest.webmanifest")
def manifesto_painel():
    """Painel instalável como app (o navegador busca o manifesto sem login, por isso a rota é pública e sem dados sensíveis)."""
    nome = db.nome_lanchonete(get_db())
    corpo = {"name": f"Painel — {nome}", "short_name": "Painel", "description": f"Painel de pedidos do {nome}",
             "start_url": "/admin", "scope": "/admin", "display": "standalone",
             "background_color": "#fff8e7", "theme_color": "#d62300", "lang": "pt-BR",
             "icons": [{"src": "/icone/192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
                       {"src": "/icone/512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}]}
    resp = Response(json.dumps(corpo, ensure_ascii=False), mimetype="application/manifest+json")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/sw.js")
def service_worker():
    resp = send_from_directory(BASE / "static", "sw.js", mimetype="text/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@app.route("/saude")
def saude():
    get_db().execute("SELECT 1").fetchone()
    return jsonify({"ok": True})


# Rota pública para o celular do cliente consultar se o pedido está pronto
@app.route("/acompanhar/<token>")
def acompanhar_pagina(token):
    """Página pública de acompanhamento (o token vem no link enviado por WhatsApp)."""
    if db.dados_acompanhamento(get_db(), token) is None:
        abort(404)
    resp = Response(render_template("acompanhar.html", token=token, nome=db.nome_lanchonete(get_db())))
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


@app.route("/api/acompanhar/<token>")
def acompanhar_dados(token):
    dados = db.dados_acompanhamento(get_db(), token)
    if dados is None:
        return jsonify({"erro": "Pedido não encontrado."}), 404
    resp = jsonify(dados)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/pedido-cliente/<int:senha>/status", methods=["GET"])
def verificar_status_cliente(senha):
    status = db.obter_status_pedido(get_db(), senha)
    if status:
        return jsonify({"status": status})
    return jsonify({"erro": "Pedido não encontrado"}), 404


if __name__ == "__main__":
    # Só para testes locais. Em produção use gunicorn/waitress (veja o README).
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")),
            debug=os.environ.get("FLASK_DEBUG") == "1")