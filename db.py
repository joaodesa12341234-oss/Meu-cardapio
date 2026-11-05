"""Módulo de base de dados para o Cardápio Digital (SQLite)."""
import ambiente  # noqa: F401  (carrega o .env antes de ler qualquer variável)
import contextvars
import json
import re
import secrets
import unicodedata
import os
from datetime import date, datetime, timedelta, timezone
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    FUSO = ZoneInfo(os.environ.get("TZ_LOJA", "America/Sao_Paulo"))
except Exception:   # sem o pacote tzdata (Windows): usa UTC-3 fixo, que é o horário do Brasil hoje
    FUSO = timezone(timedelta(hours=-3))

import imagens
import whatsapp

BASE = Path(__file__).parent
DB_PATH = Path(os.environ.get("DATABASE_PATH") or BASE / "dados" / "cardapio.db")

PAPEIS = ("admin", "cozinha")
STATUS_VALIDOS = ("novo", "fazendo", "pronto", "saiu", "concluido", "cancelado")

# Limites de entrada
MAX_ITENS = 40
MAX_QTD = 50
MAX_NOME = 80
MAX_ENDERECO = 200
MAX_OBS = 300
MAX_RUA, MAX_NUMERO, MAX_BAIRRO, MAX_COMPL, MAX_REF = 80, 10, 60, 60, 100
RE_NUMERO = re.compile(r"^[0-9A-Za-zÀ-ÿ][0-9A-Za-zÀ-ÿ/ .\-]{0,9}$")
MAX_PRECO = 10_000.00

PAGAMENTOS = {"pix": "Pix", "cartao": "Cartão", "dinheiro": "Dinheiro"}   # pagos na retirada/entrega
DIAS_SEMANA = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
RE_HORA = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

# Limite de pedidos por IP (o README documenta 60 a cada 10 min)
PEDIDOS_JANELA = 10 * 60
PEDIDOS_MAX = int(os.environ.get("MAX_PEDIDOS_POR_10MIN", "60"))

# Guarda de login: quantos segundos de histórico de falhas manter
RETENCAO_FALHAS = 24 * 3600


class ErroNegocio(Exception):
    def __init__(self, mensagem, status=400):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.status = status


def agora_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def conectar():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 10000")
    con.execute("PRAGMA journal_mode = WAL")      # leitores não bloqueiam o gravador
    con.execute("PRAGMA synchronous = NORMAL")    # seguro com WAL e bem mais rápido
    return con


@contextmanager
def transacao(con):
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise


def iniciar_banco():
    con = conectar()
    try:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS config (
                chave TEXT PRIMARY KEY,
                valor TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK (length(usuario) BETWEEN 3 AND 50),
                senha_hash TEXT NOT NULL,
                papel TEXT NOT NULL CHECK (papel IN ('admin','cozinha')),
                ativo INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1)),
                criado TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reativacoes (
                telefone TEXT PRIMARY KEY,
                quando TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS auditoria (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quando TEXT NOT NULL,
                usuario TEXT NOT NULL DEFAULT 'sistema',
                area TEXT NOT NULL,
                texto TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_auditoria_area ON auditoria (area, id);
            CREATE TABLE IF NOT EXISTS limite_pedidos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip TEXT NOT NULL,
                quando REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_limite_pedidos ON limite_pedidos (ip, quando);
            CREATE TABLE IF NOT EXISTS login_falhas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario TEXT NOT NULL,
                ip TEXT NOT NULL,
                quando REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_login_falhas ON login_falhas (quando);
            CREATE TABLE IF NOT EXISTS categorias (
                id TEXT PRIMARY KEY,
                nome TEXT NOT NULL CHECK (length(nome) BETWEEN 1 AND 30),
                icone TEXT NOT NULL DEFAULT '🍔',
                ordem INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS insumos (
                id TEXT PRIMARY KEY,
                nome TEXT NOT NULL CHECK (length(nome) BETWEEN 1 AND 30),
                quantidade INTEGER NOT NULL DEFAULT 0 CHECK (quantidade >= 0),
                minimo INTEGER NOT NULL DEFAULT 10 CHECK (minimo >= 0),
                avisado INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS cupons (
                codigo TEXT PRIMARY KEY CHECK (length(codigo) BETWEEN 3 AND 20),
                tipo TEXT NOT NULL CHECK (tipo IN ('percentual','valor')),
                valor INTEGER NOT NULL CHECK (valor > 0),
                minimo_centavos INTEGER NOT NULL DEFAULT 0,
                valido_ate TEXT NOT NULL DEFAULT '',
                limite INTEGER,
                usos INTEGER NOT NULL DEFAULT 0,
                uma_vez INTEGER NOT NULL DEFAULT 1,
                primeira_compra INTEGER NOT NULL DEFAULT 0,
                ativo INTEGER NOT NULL DEFAULT 1,
                criado TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS produtos (
                id TEXT PRIMARY KEY,
                categoria_id TEXT NOT NULL REFERENCES categorias(id) ON DELETE RESTRICT,
                nome TEXT NOT NULL CHECK (length(nome) BETWEEN 1 AND 60),
                descricao TEXT NOT NULL DEFAULT '',
                preco_centavos INTEGER NOT NULL CHECK (preco_centavos BETWEEN 0 AND 1000000),
                emoji TEXT NOT NULL DEFAULT '🍔',
                imagem TEXT NOT NULL DEFAULT '',
                miniatura TEXT NOT NULL DEFAULT '',
                disponivel INTEGER NOT NULL DEFAULT 1 CHECK (disponivel IN (0, 1)),
                ordem INTEGER NOT NULL DEFAULT 0,
                custo_centavos INTEGER CHECK (custo_centavos IS NULL OR custo_centavos BETWEEN 0 AND 1000000),
                opcoes TEXT NOT NULL DEFAULT '',
                estoque INTEGER CHECK (estoque IS NULL OR estoque >= 0),
                janela_ini TEXT NOT NULL DEFAULT '',
                janela_fim TEXT NOT NULL DEFAULT '',
                insumo_id TEXT NOT NULL DEFAULT '',
                consumo INTEGER NOT NULL DEFAULT 1 CHECK (consumo BETWEEN 1 AND 50)
            );
            CREATE TABLE IF NOT EXISTS pedidos (
                senha INTEGER PRIMARY KEY AUTOINCREMENT,
                criado TEXT NOT NULL,
                atualizado TEXT,
                status TEXT NOT NULL DEFAULT 'novo' CHECK (status IN ('novo','fazendo','pronto','saiu','concluido','cancelado')),
                tipo TEXT NOT NULL DEFAULT 'balcao' CHECK (tipo IN ('balcao','entrega')),
                nome TEXT NOT NULL DEFAULT '',
                telefone TEXT NOT NULL DEFAULT '',
                endereco TEXT NOT NULL DEFAULT '',
                observacao TEXT NOT NULL DEFAULT '',
                pagamento TEXT NOT NULL DEFAULT '',
                troco_para_centavos INTEGER NOT NULL DEFAULT 0,
                taxa_entrega_centavos INTEGER NOT NULL DEFAULT 0,
                rua TEXT NOT NULL DEFAULT '',
                numero TEXT NOT NULL DEFAULT '',
                bairro TEXT NOT NULL DEFAULT '',
                complemento TEXT NOT NULL DEFAULT '',
                referencia TEXT NOT NULL DEFAULT '',
                mesa TEXT NOT NULL DEFAULT '',
                token TEXT NOT NULL DEFAULT '',
                atraso_avisado INTEGER NOT NULL DEFAULT 0,
                agendado_para TEXT NOT NULL DEFAULT '',
                liberar_em TEXT NOT NULL DEFAULT '',
                total_centavos INTEGER NOT NULL CHECK (total_centavos >= 0),
                CHECK (tipo = 'balcao' OR (nome <> '' AND telefone <> '' AND endereco <> ''))
            );
            CREATE INDEX IF NOT EXISTS idx_pedidos_status ON pedidos (status);
            CREATE TABLE IF NOT EXISTS pedido_itens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pedido_senha INTEGER NOT NULL REFERENCES pedidos(senha) ON DELETE CASCADE,
                produto_id TEXT NOT NULL,
                nome TEXT NOT NULL,
                qtd INTEGER NOT NULL CHECK (qtd >= 1),
                preco_centavos INTEGER NOT NULL CHECK (preco_centavos >= 0),
                custo_centavos INTEGER,
                opcoes TEXT NOT NULL DEFAULT '',
                insumo_id TEXT NOT NULL DEFAULT '',
                insumo_qtd INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_itens_pedido ON pedido_itens (pedido_senha);
        """)
        con.commit()
        _migrar_colunas(con)
        _carregar_cardapio_inicial(con)
    finally:
        con.close()


def _migrar_colunas(con):
    """Bancos criados em versões antigas ganham as colunas novas de pedidos."""
    existentes = {r["name"] for r in con.execute("PRAGMA table_info(pedidos)")}
    for nome, ddl in (("pagamento", "TEXT NOT NULL DEFAULT ''"),
                      ("troco_para_centavos", "INTEGER NOT NULL DEFAULT 0"),
                      ("taxa_entrega_centavos", "INTEGER NOT NULL DEFAULT 0"),
                      ("rua", "TEXT NOT NULL DEFAULT ''"), ("numero", "TEXT NOT NULL DEFAULT ''"),
                      ("bairro", "TEXT NOT NULL DEFAULT ''"), ("complemento", "TEXT NOT NULL DEFAULT ''"),
                      ("referencia", "TEXT NOT NULL DEFAULT ''"),
                      ("token", "TEXT NOT NULL DEFAULT ''"), ("atraso_avisado", "INTEGER NOT NULL DEFAULT 0"),
                      ("agendado_para", "TEXT NOT NULL DEFAULT ''"), ("liberar_em", "TEXT NOT NULL DEFAULT ''")):
        if nome not in existentes:
            con.execute(f"ALTER TABLE pedidos ADD COLUMN {nome} {ddl}")
    for tabela in ("produtos", "pedido_itens"):
        if "opcoes" not in {r["name"] for r in con.execute(f"PRAGMA table_info({tabela})")}:
            con.execute(f"ALTER TABLE {tabela} ADD COLUMN opcoes TEXT NOT NULL DEFAULT ''")
    for nome, ddl in (("cupom", "TEXT NOT NULL DEFAULT ''"), ("desconto_centavos", "INTEGER NOT NULL DEFAULT 0"),
                      ("nota", "INTEGER"), ("comentario", "TEXT NOT NULL DEFAULT ''"),
                      ("avaliado_em", "TEXT NOT NULL DEFAULT ''"), ("pronto_em", "TEXT NOT NULL DEFAULT ''"),
                      ("mesa", "TEXT NOT NULL DEFAULT ''"), ("fidelidade_centavos", "INTEGER NOT NULL DEFAULT 0")):
        if nome not in existentes:
            con.execute(f"ALTER TABLE pedidos ADD COLUMN {nome} {ddl}")
    existentes_p = {r["name"] for r in con.execute("PRAGMA table_info(produtos)")}
    for nome, ddl in (("estoque", "INTEGER"), ("janela_ini", "TEXT NOT NULL DEFAULT ''"),
                      ("janela_fim", "TEXT NOT NULL DEFAULT ''"), ("insumo_id", "TEXT NOT NULL DEFAULT ''"),
                      ("consumo", "INTEGER NOT NULL DEFAULT 1")):
        if nome not in existentes_p:
            con.execute(f"ALTER TABLE produtos ADD COLUMN {nome} {ddl}")
    existentes_i = {r["name"] for r in con.execute("PRAGMA table_info(pedido_itens)")}
    for nome, ddl in (("insumo_id", "TEXT NOT NULL DEFAULT ''"), ("insumo_qtd", "INTEGER NOT NULL DEFAULT 0")):
        if nome not in existentes_i:
            con.execute(f"ALTER TABLE pedido_itens ADD COLUMN {nome} {ddl}")
    con.execute("CREATE INDEX IF NOT EXISTS idx_pedidos_token ON pedidos (token)")
    for tabela in ("produtos", "pedido_itens"):   # custo: NULL = "não informado"
        if "custo_centavos" not in {r["name"] for r in con.execute(f"PRAGMA table_info({tabela})")}:
            con.execute(f"ALTER TABLE {tabela} ADD COLUMN custo_centavos INTEGER")
    con.commit()


def _carregar_cardapio_inicial(con):
    """Banco sem categorias: importa o menu.json antigo (se existir) ou o seed_menu.json.
    Os arquivos antigos são renomeados para .migrado; nada é apagado."""
    if con.execute("SELECT COUNT(*) FROM categorias").fetchone()[0]:
        return
    antigo = BASE / "menu.json"
    origem = antigo if antigo.is_file() else BASE / "seed_menu.json"
    if not origem.is_file():
        return
    try:
        menu = json.loads(origem.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return

    with transacao(con):
        nome = (menu.get("lanchonete") or "").strip()
        if nome:
            con.execute("INSERT OR IGNORE INTO config (chave, valor) VALUES ('nome_lanchonete', ?)",
                        (nome[:60],))
        for i, cat in enumerate(menu.get("categorias", [])):
            con.execute("INSERT OR IGNORE INTO categorias (id, nome, icone, ordem) VALUES (?, ?, ?, ?)",
                        (str(cat["id"])[:40], str(cat["nome"])[:30], cat.get("icone") or "🍔", i))
            for j, p in enumerate(cat.get("produtos", [])):
                url = p.get("imagem") or ""
                if url and not imagens.RE_IMAGEM.match(url):
                    url = imagens.converter_legada(url)   # foto de versão antiga -> WebP
                mini = imagens.miniatura_de(url) if url else ""
                con.execute(
                    """INSERT OR IGNORE INTO produtos
                       (id, categoria_id, nome, descricao, preco_centavos, emoji, imagem, miniatura, disponivel, ordem)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (str(p["id"])[:40], str(cat["id"])[:40], str(p["nome"])[:60],
                     str(p.get("descricao") or "")[:300], int(round(float(p["preco"]) * 100)),
                     p.get("emoji") or "🍔", url, mini, 0 if p.get("esgotado") else 1, j))
    if origem == antigo:
        try:
            origem.rename(origem.with_name(origem.name + ".migrado"))
        except OSError:
            pass


# ----------------------------------------------------------- Configurações ----

def nome_lanchonete(con):
    row = con.execute("SELECT valor FROM config WHERE chave = 'nome_lanchonete'").fetchone()
    return row["valor"] if row else "Minha Lanchonete"


def definir_nome_lanchonete(con, nome):
    nome = (nome or "").strip()[:60] or "Minha Lanchonete"
    with transacao(con):
        con.execute(
            "INSERT INTO config (chave, valor) VALUES ('nome_lanchonete', ?) "
            "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
            (nome,))
    return nome


# ------------------------------------------------------------- Usuários ----

def contar_usuarios(con):
    return con.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0]


def criar_usuario(con, usuario, senha_hash, papel="admin"):
    if papel not in PAPEIS:
        raise ErroNegocio("Papel inválido.")
    try:
        cur = con.execute(
            "INSERT INTO usuarios (usuario, senha_hash, papel, ativo, criado) VALUES (?, ?, ?, 1, ?)",
            (usuario.lower(), senha_hash, papel, agora_iso()))
        con.commit()
    except sqlite3.IntegrityError:
        con.rollback()
        raise ErroNegocio("Usuário inválido ou já existente (use de 3 a 50 caracteres).")
    return cur.lastrowid


def obter_usuario(con, uid):
    return con.execute("SELECT * FROM usuarios WHERE id = ?", (uid,)).fetchone()


def obter_usuario_por_nome(con, usuario):
    return con.execute("SELECT * FROM usuarios WHERE usuario = ?", (usuario.lower(),)).fetchone()


def listar_usuarios(con):
    return con.execute("SELECT usuario, papel, ativo FROM usuarios ORDER BY usuario").fetchall()


def trocar_senha_hash(con, usuario, nova_senha_hash):
    cur = con.execute("UPDATE usuarios SET senha_hash = ? WHERE usuario = ?",
                      (nova_senha_hash, usuario.lower()))
    con.commit()
    if cur.rowcount == 0:
        raise ErroNegocio("Usuário não encontrado.", 404)


def definir_usuario_ativo(con, usuario, ativo):
    cur = con.execute("UPDATE usuarios SET ativo = ? WHERE usuario = ?",
                      (1 if ativo else 0, usuario.lower()))
    con.commit()
    if cur.rowcount == 0:
        raise ErroNegocio("Usuário não encontrado.", 404)


# ------------------------------------ Falhas de Login (Tabela login_falhas) ----

def contar_falhas_login(con, usuario, ip, desde):
    usuario = usuario.lower()
    p_par = con.execute(
        "SELECT COUNT(*) FROM login_falhas WHERE usuario = ? AND ip = ? AND quando >= ?",
        (usuario, ip, desde)).fetchone()[0]
    p_user = con.execute(
        "SELECT COUNT(*) FROM login_falhas WHERE usuario = ? AND quando >= ?",
        (usuario, desde)).fetchone()[0]
    p_ip = con.execute(
        "SELECT COUNT(*) FROM login_falhas WHERE ip = ? AND quando >= ?",
        (ip, desde)).fetchone()[0]
    return p_par, p_user, p_ip


def registrar_falha_login(con, usuario, ip, timestamp):
    con.execute("DELETE FROM login_falhas WHERE quando < ?", (timestamp - RETENCAO_FALHAS,))
    con.execute("INSERT INTO login_falhas (usuario, ip, quando) VALUES (?, ?, ?)",
                (usuario.lower(), ip, timestamp))
    con.commit()


def limpar_falhas_login(con, usuario, ip):
    """Login correto: zera só as falhas deste usuário a partir deste IP."""
    con.execute("DELETE FROM login_falhas WHERE usuario = ? AND ip = ?", (usuario.lower(), ip))
    con.commit()


def desbloquear_logins(con):
    """Apaga todas as falhas de login (usado por: python manage.py desbloquear)."""
    n = con.execute("DELETE FROM login_falhas").rowcount
    con.commit()
    return n


# ----------------------------------------------------------------- Menu ----

MAX_OPCOES = 15


_RE_NOME_PRECO = re.compile(r"^(?P<nome>.*?)[\s:=\-–—]*(?:R\$\s*)?(?P<valor>\d+(?:[.,]\d{1,2})?)\s*$", re.I)


def _separar_nome_preco(linha):
    """'Bacon; 3,00' | 'Bacon: 3,00' | 'Bacon - R$ 3,00' | 'Bacon 3' -> ('Bacon', '3,00'). Sem valor: ('Bacon', '')."""
    if ";" in linha:
        nome, _, valor = linha.partition(";")
        return " ".join(nome.split()), valor.strip()
    m = _RE_NOME_PRECO.match(linha.strip())
    if m and m.group("nome").strip():
        return " ".join(m.group("nome").split()), m.group("valor")
    return " ".join(linha.split()), ""


def _ler_extras(texto):
    """Painel: uma linha por adicional, 'Bacon; 3,00' (sem valor = grátis)."""
    if texto is None:
        return []
    if not isinstance(texto, str):
        raise ErroNegocio("Lista de adicionais inválida.")
    extras, vistos = [], set()
    for n, linha in enumerate(texto.splitlines(), 1):
        linha = linha.strip()
        if not linha:
            continue
        nome, valor = _separar_nome_preco(linha)
        if not (2 <= len(nome) <= 40):
            raise ErroNegocio(f"Adicional inválido na linha {n} (2 a 40 letras).")
        if _norm(nome) in vistos:
            raise ErroNegocio(f"O adicional \"{nome}\" está repetido.")
        vistos.add(_norm(nome))
        preco = _valor_centavos(valor.strip(), f"preço de {nome}", maximo=200) if valor.strip() else 0
        extras.append({"nome": nome, "preco": preco})
    if len(extras) > MAX_OPCOES:
        raise ErroNegocio(f"No máximo {MAX_OPCOES} adicionais por produto.")
    return extras


def _ler_sem(texto):
    """Painel: uma linha por ingrediente que o cliente pode pedir para tirar ('cebola')."""
    if texto is None:
        return []
    if not isinstance(texto, str):
        raise ErroNegocio("Lista de retiradas inválida.")
    nomes, vistos = [], set()
    for n, linha in enumerate(texto.splitlines(), 1):
        nome = " ".join(linha.split())
        if not nome:
            continue
        if not (2 <= len(nome) <= 30):
            raise ErroNegocio(f"Ingrediente inválido na linha {n} (2 a 30 letras).")
        if _norm(nome) in vistos:
            raise ErroNegocio(f"\"{nome}\" está repetido.")
        vistos.add(_norm(nome))
        nomes.append(nome)
    if len(nomes) > MAX_OPCOES:
        raise ErroNegocio(f"No máximo {MAX_OPCOES} retiradas por produto.")
    return nomes


def opcoes_do_produto(row):
    """{'extras': [{'nome','preco'(centavos)}], 'sem': [nomes]} de um produto (nunca levanta erro)."""
    try:
        d = json.loads(row["opcoes"] or "{}")
        extras = []
        for e in d.get("extras", []):
            nome, preco = e["nome"], int(e["preco"])
            if preco == 0:   # cadastro antigo digitado como "Bacon: 5,00": separa o preço do nome
                n2, v2 = _separar_nome_preco(nome)
                if v2 and n2:
                    nome, preco = n2, _valor_centavos(v2, "preço", maximo=200)
            extras.append({"nome": nome, "preco": preco})
        sem = [str(x) for x in d.get("sem", [])]
        return {"extras": extras, "sem": sem}
    except (ValueError, TypeError, KeyError, AttributeError):
        return {"extras": [], "sem": []}


def _opcoes_json(extras, sem):
    return json.dumps({"extras": extras, "sem": sem}, ensure_ascii=False) if (extras or sem) else ""


def texto_opcoes(extras, sem):
    """'+ Bacon, + Queijo, sem cebola' (aparece no painel, comanda e cozinha)."""
    return ", ".join([f"+ {e}" for e in extras] + [f"sem {x}" for x in sem])


def _inteiro(valor, campo, minimo=0, maximo=99999):
    if isinstance(valor, bool) or valor is None or (isinstance(valor, str) and not valor.strip()):
        raise ErroNegocio(f"Informe {campo} (número inteiro).")
    try:
        n = float(valor.replace(",", ".")) if isinstance(valor, str) else float(valor)
    except ValueError:
        raise ErroNegocio(f"Informe {campo} (número inteiro).")
    if n != int(n) or not (minimo <= n <= maximo):
        raise ErroNegocio(f"{campo[0].upper() + campo[1:]} deve ser um número inteiro entre {minimo} e {maximo}.")
    return int(n)


# ------------------------------------------------- Histórico de alterações ----
QUEM = contextvars.ContextVar("quem_alterou", default="sistema")   # o app.py preenche com o usuário logado
AREAS_AUDITORIA = {"pedido": "Pedidos", "produto": "Produtos", "categoria": "Categorias", "estoque": "Estoque geral",
                   "cupom": "Cupons", "loja": "Loja", "cliente": "Clientes"}
MAX_AUDITORIA = 20000


def registrar(con, area, texto):
    """Anota quem mudou o quê. Nunca derruba a operação principal: erro aqui é só ignorado."""
    try:
        con.execute("INSERT INTO auditoria (quando, usuario, area, texto) VALUES (?, ?, ?, ?)",
                    (agora_iso(), QUEM.get(), area, str(texto)[:400]))
        con.execute("DELETE FROM auditoria WHERE id <= (SELECT MAX(id) FROM auditoria) - ?", (MAX_AUDITORIA,))
        con.commit()
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass


def listar_auditoria(con, area=None, busca=None, antes=None, limite=50):
    """Mais recentes primeiro. `antes` = id do último item já mostrado (paginação)."""
    if area not in (None, "") and area not in AREAS_AUDITORIA:
        raise ErroNegocio("Área inválida.")
    limite = max(1, min(int(limite or 50), 200))
    sql, args = "SELECT * FROM auditoria WHERE 1=1", []
    if area:
        sql += " AND area = ?"; args.append(area)
    if busca:
        sql += " AND (texto LIKE ? ESCAPE '\\' OR usuario LIKE ? ESCAPE '\\')"
        t = "%" + str(busca)[:60].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        args += [t, t]
    if antes:
        sql += " AND id < ?"; args.append(int(antes))
    sql += " ORDER BY id DESC LIMIT ?"; args.append(limite + 1)
    linhas = con.execute(sql, args).fetchall()
    mais = len(linhas) > limite
    return {"itens": [{"id": r["id"], "quando": r["quando"], "usuario": r["usuario"], "area": r["area"],
                       "area_nome": AREAS_AUDITORIA.get(r["area"], r["area"]), "texto": r["texto"]} for r in linhas[:limite]],
            "mais": mais}


def listar_insumos(con):
    """Estoque geral (ex.: pães) com quantos produtos usam cada um."""
    usos = {r["insumo_id"]: r["n"] for r in con.execute(
        "SELECT insumo_id, COUNT(*) AS n FROM produtos WHERE insumo_id <> '' GROUP BY insumo_id")}
    return [{"id": r["id"], "nome": r["nome"], "quantidade": r["quantidade"], "minimo": r["minimo"],
             "baixo": r["quantidade"] <= r["minimo"], "produtos": usos.get(r["id"], 0)}
            for r in con.execute("SELECT * FROM insumos ORDER BY nome COLLATE NOCASE")]


def criar_insumo(con, nome, quantidade, minimo=10):
    nome = " ".join(nome.split()) if isinstance(nome, str) else ""
    if not (1 <= len(nome) <= 30):
        raise ErroNegocio("Informe o nome do estoque (ex.: Pão), até 30 letras.")
    if con.execute("SELECT 1 FROM insumos WHERE lower(nome) = lower(?)", (nome,)).fetchone():
        raise ErroNegocio(f"Já existe um estoque chamado \"{nome}\".")
    qtd = _inteiro(quantidade, "a quantidade")
    mini = _inteiro(minimo, "o aviso de estoque baixo")
    ins_id = uuid.uuid4().hex[:8]
    with transacao(con):
        con.execute("INSERT INTO insumos (id, nome, quantidade, minimo, avisado) VALUES (?, ?, ?, ?, ?)",
                    (ins_id, nome, qtd, mini, 1 if qtd <= mini else 0))
    registrar(con, "estoque", f"Criou o estoque \"{nome}\" com {qtd} unidades (aviso em {mini})")
    return {"id": ins_id}


def editar_insumo(con, ins_id, dados):
    atual = con.execute("SELECT * FROM insumos WHERE id = ?", (ins_id,)).fetchone()
    if not atual:
        raise ErroNegocio("Estoque não encontrado.", 404)
    nome = " ".join(dados["nome"].split()) if isinstance(dados.get("nome"), str) else atual["nome"]
    if not (1 <= len(nome) <= 30):
        raise ErroNegocio("Informe o nome do estoque, até 30 letras.")
    if con.execute("SELECT 1 FROM insumos WHERE lower(nome) = lower(?) AND id <> ?", (nome, ins_id)).fetchone():
        raise ErroNegocio(f"Já existe um estoque chamado \"{nome}\".")
    qtd = _inteiro(dados["quantidade"], "a quantidade") if "quantidade" in dados else atual["quantidade"]
    mini = _inteiro(dados["minimo"], "o aviso de estoque baixo") if "minimo" in dados else atual["minimo"]
    with transacao(con):
        con.execute("UPDATE insumos SET nome = ?, quantidade = ?, minimo = ?, avisado = ? WHERE id = ?",
                    (nome, qtd, mini, 1 if qtd <= mini else 0, ins_id))
    mud = []
    if nome != atual["nome"]: mud.append(f"nome \"{atual['nome']}\" → \"{nome}\"")
    if qtd != atual["quantidade"]: mud.append(f"quantidade {atual['quantidade']} → {qtd}")
    if mini != atual["minimo"]: mud.append(f"aviso {atual['minimo']} → {mini}")
    if mud:
        registrar(con, "estoque", f"Estoque \"{atual['nome']}\": " + "; ".join(mud))
    return {"ok": True}


def excluir_insumo(con, ins_id):
    atual = con.execute("SELECT nome FROM insumos WHERE id = ?", (ins_id,)).fetchone()
    if not atual:
        raise ErroNegocio("Estoque não encontrado.", 404)
    with transacao(con):
        con.execute("UPDATE produtos SET insumo_id = '' WHERE insumo_id = ?", (ins_id,))
        con.execute("DELETE FROM insumos WHERE id = ?", (ins_id,))
    registrar(con, "estoque", f"Excluiu o estoque \"{atual['nome']}\"")


def vincular_categoria(con, ins_id, categoria_id, consumo=1):
    """Liga todos os produtos de uma categoria a este estoque (ex.: todos os Lanches gastam 1 pão)."""
    if not con.execute("SELECT 1 FROM insumos WHERE id = ?", (ins_id,)).fetchone():
        raise ErroNegocio("Estoque não encontrado.", 404)
    if not isinstance(categoria_id, str) or not con.execute("SELECT 1 FROM categorias WHERE id = ?", (categoria_id,)).fetchone():
        raise ErroNegocio("Categoria inválida.")
    consumo = _inteiro(consumo, "o consumo por unidade", 1, 50)
    with transacao(con):
        n = con.execute("UPDATE produtos SET insumo_id = ?, consumo = ? WHERE categoria_id = ?",
                        (ins_id, consumo, categoria_id)).rowcount
    registrar(con, "estoque", f"Ligou {n} produto(s) da categoria ao estoque (gasto de {consumo} por unidade)")
    return {"produtos": n}


def definir_produtos_do_insumo(con, ins_id, escolhidos):
    """Define exatamente quais produtos gastam deste estoque: [{"id":..., "consumo":N}, ...].
    Quem não está na lista deixa de usá-lo (só os que usavam ESTE estoque)."""
    if not con.execute("SELECT 1 FROM insumos WHERE id = ?", (ins_id,)).fetchone():
        raise ErroNegocio("Estoque não encontrado.", 404)
    if not isinstance(escolhidos, list) or len(escolhidos) > 500:
        raise ErroNegocio("Lista de produtos inválida.")
    novos = {}
    for e in escolhidos:
        if not isinstance(e, dict) or not isinstance(e.get("id"), str):
            raise ErroNegocio("Lista de produtos inválida.")
        novos[e["id"]] = _inteiro(e.get("consumo", 1), "o consumo por unidade", 1, 50)
    existentes = {r["id"] for r in con.execute("SELECT id FROM produtos")}
    if not set(novos) <= existentes:
        raise ErroNegocio("Há produto inexistente na lista.")
    with transacao(con):
        con.execute("UPDATE produtos SET insumo_id = '', consumo = 1 WHERE insumo_id = ?", (ins_id,))
        for pid, consumo in novos.items():
            con.execute("UPDATE produtos SET insumo_id = ?, consumo = ? WHERE id = ?", (ins_id, consumo, pid))
    registrar(con, "estoque", f"Escolheu {len(novos)} produto(s) que gastam deste estoque")
    return {"produtos": len(novos)}


def _estoque_geral_basta(insumos, p, qtd=1):
    """O estoque geral do produto comporta `qtd` unidades? (produto sem estoque geral: sempre sim)"""
    if not p["insumo_id"]:
        return True
    ins = insumos.get(p["insumo_id"])
    return ins is not None and ins["quantidade"] >= p["consumo"] * qtd


def _avisar_estoque_baixo(con, ids):
    """Avisa o dono (uma vez) quando um estoque geral chega no mínimo ou zera."""
    if not ids:
        return
    cfg = obter_config_loja(con)
    for ins_id in ids:
        ins = con.execute("SELECT * FROM insumos WHERE id = ?", (ins_id,)).fetchone()
        if not ins or ins["quantidade"] > ins["minimo"] or ins["avisado"]:
            continue
        with transacao(con):
            marcou = con.execute("UPDATE insumos SET avisado = 1 WHERE id = ? AND avisado = 0", (ins_id,)).rowcount
        if marcou and cfg["dono_telefone"]:
            texto = (f"🚫 *Acabou: {ins['nome']}!* Os itens que usam {ins['nome']} saíram do cardápio até você repor."
                     if ins["quantidade"] == 0 else
                     f"⚠️ *Estoque baixo:* restam só {ins['quantidade']} de {ins['nome']}.")
            whatsapp.enviar_em_segundo_plano(cfg["dono_telefone"], texto)


def _estoque_valor(valor):
    """Estoque opcional: vazio = sem controle (None); número inteiro de 0 a 99999."""
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return None
    if isinstance(valor, bool):
        raise ErroNegocio("Estoque inválido.")
    try:
        n = float(str(valor).replace(",", ".")) if isinstance(valor, str) else float(valor)
    except ValueError:
        raise ErroNegocio("Informe o estoque como número inteiro (ou deixe em branco).")
    if n != int(n) or not (0 <= n <= 99999):
        raise ErroNegocio("O estoque deve ser um número inteiro entre 0 e 99999.")
    return int(n)


def _vinculo_insumo(con, insumo_id, consumo):
    """Valida a ligação do produto com o estoque geral. Sem estoque geral: ('', 1)."""
    if insumo_id in (None, ""):
        return "", 1
    if not isinstance(insumo_id, str) or not con.execute("SELECT 1 FROM insumos WHERE id = ?", (insumo_id,)).fetchone():
        raise ErroNegocio("Estoque geral inválido.")
    consumo = 1 if consumo in (None, "") else _inteiro(consumo, "a quantidade gasta por unidade", 1, 50)
    return insumo_id, consumo


def _janela_valida(ini, fim):
    ini = ini.strip() if isinstance(ini, str) else ""
    fim = fim.strip() if isinstance(fim, str) else ""
    if not ini and not fim:
        return "", ""
    if not (RE_HORA.match(ini) and RE_HORA.match(fim)):
        raise ErroNegocio("Preencha os dois horários (ex.: 14:00 e 22:00) ou deixe os dois em branco.")
    if ini == fim:
        raise ErroNegocio("O horário de início e o de fim não podem ser iguais.")
    return ini, fim


def produto_no_horario(p, agora=None):
    """(disponível agora?, aviso). Janela que vira a noite (ex.: 18:00 às 02:00) funciona."""
    ini, fim = p["janela_ini"], p["janela_fim"]
    if not ini or not fim:
        return True, ""
    agora = agora or datetime.now(FUSO)
    m = agora.hour * 60 + agora.minute
    a, f = _minutos(ini), _minutos(fim)
    dentro = (a <= m < f) if a < f else (m >= a or m < f)
    return dentro, "" if dentro else f"Disponível das {ini} às {fim}"


def obter_menu(con):
    cats = con.execute("SELECT * FROM categorias ORDER BY ordem, nome").fetchall()
    prods = con.execute("SELECT * FROM produtos ORDER BY ordem, nome").fetchall()
    insumos = {r["id"]: r for r in con.execute("SELECT * FROM insumos")}
    por_cat = {}
    for p in prods:
        sobra = None            # quantas unidades ainda dá para vender (menor limite entre estoque do produto e geral)
        if p["estoque"] is not None:
            sobra = p["estoque"]
        if p["insumo_id"] and p["insumo_id"] in insumos:
            pelo_geral = insumos[p["insumo_id"]]["quantidade"] // p["consumo"]
            sobra = pelo_geral if sobra is None else min(sobra, pelo_geral)
        por_cat.setdefault(p["categoria_id"], []).append({
            "id": p["id"],
            "nome": p["nome"],
            "descricao": p["descricao"] or "",
            "preco": (p["preco_centavos"] or 0) / 100.0,
            "imagem": p["imagem"] or "",
            "miniatura": p["miniatura"] or "",
            "emoji": p["emoji"] or "🍔",
            "disponivel": bool(p["disponivel"]) and produto_no_horario(p)[0] and _estoque_geral_basta(insumos, p),
            "ativo": bool(p["disponivel"]),                       # chave manual do dono (o painel usa esta)
            "aviso": produto_no_horario(p)[1],
            "restam": sobra if sobra is not None and sobra <= 5 else None,
            "extras": [{"nome": e["nome"], "preco": e["preco"] / 100.0} for e in opcoes_do_produto(p)["extras"]],
            "sem": opcoes_do_produto(p)["sem"],
        })
    return {
        "lanchonete": nome_lanchonete(con),
        "loja": estado_loja(con),
        "categorias": [
            {"id": c["id"], "nome": c["nome"], "icone": c["icone"], "produtos": por_cat.get(c["id"], [])}
            for c in cats
        ],
    }


# -------------------------------------------------------------- Pedidos ----

def pedido_permitido(con, ip, timestamp):
    """Só CONSULTA se o IP ainda pode pedir. O registro é feito em criar_pedido."""
    desde = timestamp - PEDIDOS_JANELA
    con.execute("DELETE FROM limite_pedidos WHERE quando < ?", (desde,))
    qtd = con.execute("SELECT COUNT(*) FROM limite_pedidos WHERE ip = ? AND quando >= ?",
                      (ip, desde)).fetchone()[0]
    return qtd < PEDIDOS_MAX


def _texto(valor, maximo, campo):
    if valor is None:
        return ""
    if not isinstance(valor, str):
        raise ErroNegocio(f"Campo inválido: {campo}.")
    valor = " ".join(valor.split()) if campo != "observação" else valor.strip()
    if len(valor) > maximo:
        raise ErroNegocio(f"O campo {campo} passa de {maximo} caracteres.")
    return valor


def montar_endereco(rua, numero, bairro, complemento="", referencia=""):
    """Texto único do endereço (usado no mapa, na comanda e em pedidos antigos)."""
    texto = f"{rua}, {numero}"
    if complemento:
        texto += f" - {complemento}"
    texto += f" - {bairro}"
    if referencia:
        texto += f" (ref.: {referencia})"
    return texto


def _validar_endereco(rua, numero, sem_numero, bairro, complemento, referencia):
    """Valida cada parte do endereço e devolve (rua, numero, bairro, complemento, referencia)."""
    def arrumar(t):   # "rua das flores" / "CENTRO" -> "Rua Das Flores" / "Centro"
        return t.title() if t and (t.islower() or t.isupper()) else t
    rua = arrumar(_texto(rua, MAX_RUA, "rua"))
    bairro = arrumar(_texto(bairro, MAX_BAIRRO, "bairro"))
    complemento = _texto(complemento, MAX_COMPL, "complemento")
    referencia = _texto(referencia, MAX_REF, "ponto de referência")
    numero = "S/N" if sem_numero is True else _texto(numero, MAX_NUMERO, "número").upper()
    if len(rua) < 3:
        raise ErroNegocio("Informe a rua (ou avenida) da entrega.")
    if not numero:
        raise ErroNegocio("Informe o número da casa (ou marque \"sem número\").")
    if numero != "S/N" and not RE_NUMERO.match(numero):
        raise ErroNegocio("O número do endereço é inválido (ex.: 120, 45A).")
    if len(bairro) < 2:
        raise ErroNegocio("Informe o bairro da entrega.")
    return rua, numero, bairro, complemento, referencia


def _avisar_dono(cfg, senha, tipo, nome, total_centavos, pagamento, bairro, itens, agendado="", mesa=""):
    """Mensagem para o WhatsApp do dono quando entra pedido (se ele cadastrou o número)."""
    if not cfg.get("dono_telefone"):
        return
    resumo = ", ".join(f"{i['qtd']}x {i['nome']}" + (f" ({i['opcoes']})" if i["opcoes"] else "") for i in itens[:6]) \
        + (" …" if len(itens) > 6 else "")
    onde = f"🛵 Entrega ({bairro})" if tipo == "entrega" else (f"🪑 Mesa {mesa}" if mesa else "🏪 Retirada no balcão")
    whatsapp.enviar_em_segundo_plano(
        cfg["dono_telefone"],
        f"🔔 *Novo pedido #{senha}*" + (f" 📅 agendado para {agendado}" if agendado else "") + f"\n{onde}\n{nome}\n{resumo}\n"
        f"*Total:* {_brl(total_centavos)} ({PAGAMENTOS[pagamento]})")


# ------------------------------------------------------------- Cupons ----

def _codigo_cupom(codigo):
    codigo = "".join(str(codigo or "").split()).upper() if isinstance(codigo, (str, int)) and not isinstance(codigo, bool) else ""
    if not re.fullmatch(r"[A-Z0-9_-]{3,20}", codigo):
        raise ErroNegocio("O código do cupom deve ter de 3 a 20 letras ou números (sem espaços nem acentos).")
    return codigo


def _desconto_do_cupom(c, subtotal):
    bruto = subtotal * c["valor"] // 100 if c["tipo"] == "percentual" else c["valor"]
    return max(0, min(bruto, subtotal))


def validar_cupom(con, codigo, subtotal, telefone=None):
    """Confere o cupom para um pedido de `subtotal` centavos. Devolve (codigo, desconto_centavos)
    ou levanta ErroNegocio com a razão (mensagem pensada para o cliente)."""
    try:
        codigo = _codigo_cupom(codigo)
    except ErroNegocio:
        raise ErroNegocio("Cupom inválido.")
    c = con.execute("SELECT * FROM cupons WHERE codigo = ?", (codigo,)).fetchone()
    if not c or not c["ativo"]:
        raise ErroNegocio("Cupom inválido.")
    if c["valido_ate"] and datetime.now(FUSO).date().isoformat() > c["valido_ate"]:
        raise ErroNegocio("Este cupom já venceu.")
    if c["limite"] is not None and c["usos"] >= c["limite"]:
        raise ErroNegocio("Este cupom esgotou.")
    if subtotal < c["minimo_centavos"]:
        raise ErroNegocio(f"Este cupom vale para pedidos a partir de {_brl(c['minimo_centavos'])}.")
    if telefone and (c["uma_vez"] or c["primeira_compra"]):
        if c["uma_vez"] and con.execute("SELECT 1 FROM pedidos WHERE cupom = ? AND telefone = ? AND status <> 'cancelado'",
                                        (codigo, telefone)).fetchone():
            raise ErroNegocio("Você já usou este cupom.")
        if c["primeira_compra"] and con.execute("SELECT 1 FROM pedidos WHERE telefone = ? AND status <> 'cancelado'",
                                                (telefone,)).fetchone():
            raise ErroNegocio("Este cupom vale só para a primeira compra.")
    desconto = _desconto_do_cupom(c, subtotal)
    if desconto <= 0:
        raise ErroNegocio("Este cupom não dá desconto neste pedido.")
    return codigo, desconto


def pre_visualizar_cupom(con, codigo, subtotal_reais, telefone=None):
    """Mostra ao cliente quanto o cupom abate (a conta de verdade é refeita ao fechar o pedido)."""
    try:
        subtotal = int(round(float(subtotal_reais) * 100))
    except (TypeError, ValueError):
        raise ErroNegocio("Valor do pedido inválido.")
    if subtotal <= 0 or subtotal > 10_000_000:
        raise ErroNegocio("Valor do pedido inválido.")
    tel = None
    if isinstance(telefone, str) and telefone.strip():
        fmt = whatsapp.formatar_numero(telefone)
        tel = fmt[2:] if fmt else None
    cod, desc = validar_cupom(con, codigo, subtotal, tel)
    return {"codigo": cod, "desconto": desc / 100.0}


def _cupom_para_painel(c):
    return {"codigo": c["codigo"], "tipo": c["tipo"],
            "valor": c["valor"] if c["tipo"] == "percentual" else c["valor"] / 100.0,
            "minimo": c["minimo_centavos"] / 100.0, "valido_ate": c["valido_ate"],
            "limite": c["limite"], "usos": c["usos"], "uma_vez": bool(c["uma_vez"]),
            "primeira_compra": bool(c["primeira_compra"]), "ativo": bool(c["ativo"]),
            "vencido": bool(c["valido_ate"]) and datetime.now(FUSO).date().isoformat() > c["valido_ate"]}


def listar_cupons(con):
    return [_cupom_para_painel(c) for c in con.execute("SELECT * FROM cupons ORDER BY criado DESC, codigo")]


def _dados_cupom(dados, existente=None):
    tipo = dados.get("tipo", existente["tipo"] if existente else None)
    if tipo not in ("percentual", "valor"):
        raise ErroNegocio("Escolha o tipo do desconto (porcentagem ou valor em reais).")
    bruto = dados.get("valor", None if existente is None else (existente["valor"] if tipo == "percentual" else existente["valor"] / 100.0))
    if tipo == "percentual":
        valor = _inteiro(bruto, "a porcentagem", 1, 100)
    else:
        valor = _valor_centavos(bruto if isinstance(bruto, (int, float, str)) and bruto != "" else None, "desconto", maximo=1000)
        if valor <= 0:
            raise ErroNegocio("O desconto em reais deve ser maior que zero.")
    if "minimo" in dados or existente is None:
        bruto_min = dados.get("minimo")
        minimo = _valor_centavos(0 if bruto_min in (None, "") else bruto_min, "pedido mínimo do cupom", maximo=10_000)
    else:
        minimo = existente["minimo_centavos"]
    validade = dados.get("valido_ate", existente["valido_ate"] if existente else "")
    if validade in (None, ""):
        validade = ""
    else:
        try:
            validade = date.fromisoformat(str(validade)).isoformat()
        except ValueError:
            raise ErroNegocio("Data de validade inválida.")
    limite = dados.get("limite", existente["limite"] if existente else None)
    limite = None if limite in (None, "") else _inteiro(limite, "o limite de usos", 1, 1_000_000)
    return {"tipo": tipo, "valor": valor, "minimo_centavos": minimo, "valido_ate": validade, "limite": limite,
            "uma_vez": 1 if dados.get("uma_vez", bool(existente["uma_vez"]) if existente else True) else 0,
            "primeira_compra": 1 if dados.get("primeira_compra", bool(existente["primeira_compra"]) if existente else False) else 0}


def criar_cupom(con, dados):
    codigo = _codigo_cupom(dados.get("codigo"))
    if con.execute("SELECT 1 FROM cupons WHERE codigo = ?", (codigo,)).fetchone():
        raise ErroNegocio(f"Já existe o cupom {codigo}.")
    d = _dados_cupom(dados)
    with transacao(con):
        con.execute("INSERT INTO cupons (codigo, tipo, valor, minimo_centavos, valido_ate, limite, uma_vez, primeira_compra, criado) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (codigo, d["tipo"], d["valor"], d["minimo_centavos"], d["valido_ate"], d["limite"],
                     d["uma_vez"], d["primeira_compra"], agora_iso()))
    registrar(con, "cupom", f"Criou o cupom {codigo} ({d['valor']}% de desconto)" if d["tipo"] == "percentual"
              else f"Criou o cupom {codigo} ({_brl(d['valor'])} de desconto)")
    return {"codigo": codigo}


def editar_cupom(con, codigo, dados):
    codigo = _codigo_cupom(codigo)
    atual = con.execute("SELECT * FROM cupons WHERE codigo = ?", (codigo,)).fetchone()
    if not atual:
        raise ErroNegocio("Cupom não encontrado.", 404)
    d = _dados_cupom(dados, atual)
    ativo = (1 if dados["ativo"] else 0) if "ativo" in dados else atual["ativo"]
    with transacao(con):
        con.execute("UPDATE cupons SET tipo=?, valor=?, minimo_centavos=?, valido_ate=?, limite=?, uma_vez=?, "
                    "primeira_compra=?, ativo=? WHERE codigo=?",
                    (d["tipo"], d["valor"], d["minimo_centavos"], d["valido_ate"], d["limite"], d["uma_vez"],
                     d["primeira_compra"], ativo, codigo))
    registrar(con, "cupom", f"Cupom {codigo}: " + ("ativado" if ativo and not atual["ativo"] else "desativado" if not ativo and atual["ativo"] else "regras alteradas"))
    return {"ok": True}


def excluir_cupom(con, codigo):
    codigo = _codigo_cupom(codigo)
    with transacao(con):
        if con.execute("DELETE FROM cupons WHERE codigo = ?", (codigo,)).rowcount == 0:
            raise ErroNegocio("Cupom não encontrado.", 404)
    registrar(con, "cupom", f"Excluiu o cupom {codigo}")


# ------------------------------------------------------- Fidelidade ----

def status_fidelidade(con, telefone, cfg=None):
    """Quanto falta para o cliente (pelo telefone) ganhar o desconto da fidelidade, e se ele já ganhou."""
    cfg = cfg or obter_config_loja(con)
    n = cfg["fidelidade_pedidos"]
    base = {"ativo": bool(n), "elegivel": False, "falta": 0, "pedidos": n, "percentual": cfg["fidelidade_percentual"]}
    if not n or not telefone:
        return base
    concl = con.execute("SELECT COUNT(*) FROM pedidos WHERE telefone = ? AND status = 'concluido'", (telefone,)).fetchone()[0]
    usados = con.execute("SELECT COUNT(*) FROM pedidos WHERE telefone = ? AND fidelidade_centavos > 0 AND status <> 'cancelado'",
                         (telefone,)).fetchone()[0]
    progresso = max(0, concl - usados * n)
    base["elegivel"] = progresso >= n
    base["falta"] = max(0, n - progresso)
    return base


def pre_visualizar_fidelidade(con, telefone):
    fmt = whatsapp.formatar_numero(telefone) if isinstance(telefone, str) and telefone.strip() else None
    return status_fidelidade(con, fmt[2:] if fmt else None)


# --------------------------------------------------- Relatório diário ----

def texto_relatorio(con, dia):
    """Resumo do dia (data local) para o WhatsApp do dono."""
    r = resumo_financeiro(con, dia, dia)
    av = resumo_avaliacoes(con, dia, dia)
    de, ate = _intervalo_utc(dia, dia)
    desc = con.execute("SELECT COALESCE(SUM(desconto_centavos + fidelidade_centavos), 0) FROM pedidos "
                       "WHERE status = 'concluido' AND criado >= ? AND criado < ?", (de, ate)).fetchone()[0]
    linhas = [f"📊 *Resumo do dia {dia.strftime('%d/%m')}* — {nome_lanchonete(con)}", "",
              f"💰 Faturamento: *{_brl(r['faturamento'])}* em {r['concluidos']} pedido(s) concluído(s)"]
    if r["concluidos"]:
        linhas.append(f"🧾 Ticket médio: {_brl(r['ticket_medio'])}")
    if r["lucro"]["receita_com_custo"]:
        linhas.append(f"📈 Lucro estimado: {_brl(r['lucro']['estimado'])}")
    if desc:
        linhas.append(f"🏷 Descontos dados (cupons e fidelidade): {_brl(desc)}")
    if r["mais_vendidos"]:
        linhas += ["", "🏆 *Mais vendidos*"] + [f"{i}. {t['qtd']}× {t['nome']}" for i, t in enumerate(r["mais_vendidos"][:3], 1)]
    linhas.append("")
    if av["total"]:
        linhas.append(f"⭐ Nota média: *{str(av['media']).replace('.', ',')}* ({av['total']} avaliação(ões))")
    else:
        linhas.append("⭐ Nenhuma avaliação hoje")
    if r["cancelados"]["pedidos"]:
        linhas.append(f"❌ Cancelados: {r['cancelados']['pedidos']} ({_brl(r['cancelados']['total'])})")
    if r["em_andamento"]["pedidos"]:
        linhas.append(f"⏳ Ainda em andamento: {r['em_andamento']['pedidos']}")
    return "\n".join(linhas)


def enviar_relatorio_diario(con, agora=None, forcar=False):
    """Manda o resumo do dia uma única vez, a partir do horário configurado. `forcar` = botão "enviar agora"."""
    cfg = obter_config_loja(con)
    if not cfg["dono_telefone"] or (not forcar and not cfg["relatorio_hora"]):
        if forcar:
            raise ErroNegocio("Cadastre o WhatsApp do dono na aba Loja para receber o relatório.")
        return False
    local = agora or datetime.now(FUSO)
    hoje = local.date()
    if not forcar:
        if local.strftime("%H:%M") < cfg["relatorio_hora"]:
            return False
        with transacao(con):       # "reserva" o envio do dia: só um processo/instante consegue
            if con.execute("INSERT INTO config (chave, valor) VALUES ('relatorio_enviado', ?) "
                           "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor WHERE valor <> excluded.valor",
                           (hoje.isoformat(),)).rowcount == 0:
                return False
    whatsapp.enviar_em_segundo_plano(cfg["dono_telefone"], texto_relatorio(con, hoje))
    return True


# ------------------------------------------------- Clientes sumidos ----

REATIVACAO_ESPERA_DIAS = 14
MENSAGEM_REATIVACAO = ("Olá, {nome}! 😊 Faz um tempinho que você não pede no {loja}. "
                       "Que tal matar a saudade? Peça aqui: {link}")


def clientes_sumidos(con, dias=21, limite=50):
    """Clientes que já compraram, mas não pedem há `dias` dias (mais fiéis primeiro)."""
    dias = max(7, min(int(dias), 365))
    corte = (datetime.now(timezone.utc) - timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M:%SZ")
    linhas = con.execute(
        """SELECT telefone, (SELECT nome FROM pedidos x WHERE x.telefone = p.telefone ORDER BY senha DESC LIMIT 1) AS nome,
                  MAX(criado) AS ultimo, SUM(status = 'concluido') AS pedidos, SUM(CASE WHEN status = 'concluido' THEN total_centavos END) AS total
           FROM pedidos p WHERE status <> 'cancelado' AND telefone NOT IN ('', '[removido]')
           GROUP BY telefone HAVING MAX(criado) < ? AND SUM(status = 'concluido') >= 1
           ORDER BY pedidos DESC, ultimo DESC LIMIT ?""", (corte, max(1, min(int(limite), 200)))).fetchall()
    contatados = {r["telefone"]: r["quando"] for r in con.execute("SELECT telefone, quando FROM reativacoes")}
    hoje = datetime.now(timezone.utc)
    saida = []
    for r in linhas:
        ultimo = datetime.strptime(r["ultimo"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        quando = contatados.get(r["telefone"])
        espera = False
        if quando:
            espera = (hoje - datetime.strptime(quando, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).days < REATIVACAO_ESPERA_DIAS
        saida.append({"telefone": r["telefone"], "nome": r["nome"] or "", "pedidos": r["pedidos"],
                      "total": (r["total"] or 0) / 100.0, "dias_sem_pedir": (hoje - ultimo).days,
                      "contatado_em": quando or "", "em_espera": espera})
    return saida


def enviar_reativacao(con, telefone, mensagem, link):
    """Manda uma mensagem de WhatsApp para UM cliente sumido (não repete antes de 14 dias)."""
    if not isinstance(telefone, str) or not telefone:
        raise ErroNegocio("Cliente inválido.")
    ped = con.execute("SELECT nome FROM pedidos WHERE telefone = ? AND status = 'concluido' ORDER BY senha DESC LIMIT 1",
                      (telefone,)).fetchone()
    if not ped or telefone == "[removido]":
        raise ErroNegocio("Cliente não encontrado.", 404)
    mensagem = " ".join(str(mensagem or "").split()) if isinstance(mensagem, str) else ""
    if not mensagem:
        mensagem = MENSAGEM_REATIVACAO
    if len(mensagem) > 500:
        raise ErroNegocio("A mensagem passa de 500 caracteres.")
    feita = con.execute("SELECT quando FROM reativacoes WHERE telefone = ?", (telefone,)).fetchone()
    if feita and (datetime.now(timezone.utc) - datetime.strptime(feita["quando"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).days < REATIVACAO_ESPERA_DIAS:
        raise ErroNegocio(f"Este cliente já recebeu uma mensagem há menos de {REATIVACAO_ESPERA_DIAS} dias.", 409)
    primeiro = (ped["nome"] or "").split(" ")[0] or "tudo bem"
    texto = mensagem.replace("{nome}", primeiro).replace("{loja}", nome_lanchonete(con)).replace("{link}", link)
    with transacao(con):
        con.execute("INSERT INTO reativacoes (telefone, quando) VALUES (?, ?) "
                    "ON CONFLICT(telefone) DO UPDATE SET quando = excluded.quando", (telefone, agora_iso()))
    whatsapp.enviar_em_segundo_plano(telefone, texto)
    registrar(con, "cliente", f"Enviou mensagem de reativação para {primeiro} ({telefone})")
    return {"ok": True}


# --------------------------------------------------------- Avaliações ----

def avaliar_pedido(con, token, nota, comentario=None):
    """O cliente dá de 1 a 5 estrelas ao pedido concluído, uma única vez (pelo link de acompanhamento)."""
    if not isinstance(token, str) or not (8 <= len(token) <= 40):
        raise ErroNegocio("Pedido não encontrado.", 404)
    if isinstance(nota, bool) or not isinstance(nota, int) or not (1 <= nota <= 5):
        raise ErroNegocio("Escolha de 1 a 5 estrelas.")
    comentario = " ".join(comentario.split()) if isinstance(comentario, str) else ""
    if len(comentario) > 300:
        raise ErroNegocio("O comentário passa de 300 caracteres.")
    p = con.execute("SELECT senha, status, nota FROM pedidos WHERE token = ?", (token,)).fetchone()
    if not p:
        raise ErroNegocio("Pedido não encontrado.", 404)
    if p["status"] != "concluido":
        raise ErroNegocio("Você poderá avaliar depois que o pedido for concluído.")
    with transacao(con):
        cur = con.execute("UPDATE pedidos SET nota = ?, comentario = ?, avaliado_em = ? "
                          "WHERE senha = ? AND nota IS NULL", (nota, comentario, agora_iso(), p["senha"]))
    if cur.rowcount == 0:
        raise ErroNegocio("Este pedido já foi avaliado. Obrigado!", 409)
    if nota <= 2:
        _avisar_nota_baixa(con, p["senha"], nota, comentario)
    return {"ok": True}


def _avisar_nota_baixa(con, senha, nota, comentario):
    """WhatsApp para o dono quando alguém dá 1 ou 2 estrelas (para ele poder reparar o problema)."""
    cfg = obter_config_loja(con)
    if not cfg.get("dono_telefone"):
        return
    p = con.execute("SELECT nome, telefone FROM pedidos WHERE senha = ?", (senha,)).fetchone()
    whatsapp.enviar_em_segundo_plano(
        cfg["dono_telefone"],
        f"⚠️ *Nota baixa: {nota} {'estrela' if nota == 1 else 'estrelas'}*\nPedido #{senha} — {p['nome']} ({p['telefone']})\n"
        + (f"“{comentario}”\n" if comentario else "Sem comentário.\n") + "Vale chamar o cliente para entender e resolver. 🙏")


def resumo_avaliacoes(con, inicio, fim):
    """Média, distribuição e últimos comentários do período (datas locais, inclusive)."""
    de, ate = _intervalo_utc(inicio, fim)
    linhas = con.execute("SELECT senha, nome, nota, comentario, avaliado_em FROM pedidos "
                         "WHERE nota IS NOT NULL AND criado >= ? AND criado < ? ORDER BY avaliado_em DESC, senha DESC",
                         (de, ate)).fetchall()
    dist = {n: 0 for n in range(1, 6)}
    for r in linhas:
        dist[r["nota"]] += 1
    total = len(linhas)
    return {"total": total, "media": round(sum(r["nota"] for r in linhas) / total, 2) if total else None,
            "distribuicao": dist,
            "ultimas": [{"senha": r["senha"], "nome": (r["nome"] or "").split(" ")[0], "nota": r["nota"],
                         "comentario": r["comentario"], "quando": r["avaliado_em"]} for r in linhas[:30]]}


def criar_pedido(con, itens, tipo, nome, telefone, endereco, observacao, ip=None,
                 pagamento=None, troco_para=None, rua=None, numero=None, sem_numero=False,
                 bairro=None, complemento=None, referencia=None, base_url="", agendado_para=None, cupom=None, mesa=None):
    agora_ts = time.time()

    if ip and not pedido_permitido(con, ip, agora_ts):
        raise ErroNegocio("Muitos pedidos em pouco tempo. Aguarde alguns minutos.", status=429)

    cfg = obter_config_loja(con)
    agendado_local = None
    if agendado_para not in (None, ""):
        if not cfg["agendamento"]:
            raise ErroNegocio("Pedidos agendados não estão disponíveis no momento.")
        if not isinstance(agendado_para, str) or agendado_para not in {h["valor"] for h in horarios_agendamento(con)}:
            raise ErroNegocio("Esse horário não está mais disponível. Escolha outro.", status=409)
        agendado_local = datetime.strptime(agendado_para, "%Y-%m-%dT%H:%M").replace(tzinfo=FUSO)
    else:
        aberta, aviso = loja_aberta(con)
        if not aberta:
            raise ErroNegocio(aviso or "Estamos fechados no momento.", status=409)

    if not isinstance(itens, list) or not itens:
        raise ErroNegocio("Selecione pelo menos um item.")
    if len(itens) > MAX_ITENS:
        raise ErroNegocio("Itens demais no pedido.")
    if tipo not in ("balcao", "entrega"):
        raise ErroNegocio("Tipo de pedido inválido.")
    if pagamento not in PAGAMENTOS:
        raise ErroNegocio("Escolha a forma de pagamento.")
    mesa = "" if mesa in (None, "") else mesa
    if mesa != "":
        if not cfg["mesas"]:
            raise ErroNegocio("Pedidos por mesa não estão ativos.")
        if isinstance(mesa, bool) or not str(mesa).isdigit() or not (1 <= int(mesa) <= cfg["mesas"]):
            raise ErroNegocio("Mesa inválida. Escaneie o QR code da sua mesa de novo.")
        if tipo != "balcao" or agendado_local:
            raise ErroNegocio("Pedido de mesa não pode ser entrega nem agendado.")
        mesa = str(int(mesa))

    nome = _texto(nome, MAX_NOME, "nome")
    observacao = _texto(observacao, MAX_OBS, "observação")
    if tipo == "entrega" and not (isinstance(rua, str) and rua.strip()) and endereco:
        # compatibilidade: cliente antigo (página em cache) que ainda manda o endereço num campo só
        endereco = _texto(endereco, MAX_ENDERECO, "endereço")
        rua = numero = bairro = complemento = referencia = ""
    elif tipo == "entrega":
        rua, numero, bairro, complemento, referencia = _validar_endereco(
            rua, numero, sem_numero, bairro, complemento, referencia)
        endereco = montar_endereco(rua, numero, bairro, complemento, referencia)
    else:
        endereco = rua = numero = bairro = complemento = referencia = ""
    telefone_fmt = whatsapp.formatar_numero(_texto(telefone, 30, "telefone"))
    if not nome:
        raise ErroNegocio("Informe o seu nome.")
    if not telefone_fmt:
        raise ErroNegocio("Informe um telefone válido com DDD (WhatsApp).")
    if tipo == "entrega" and not endereco:
        raise ErroNegocio("Para entrega, informe o endereço.")
    telefone = telefone_fmt[2:]   # guarda DDD + número, sem o 55

    # Soma quantidades de linhas iguais (mesmo produto e mesmas opções) e confere cada uma
    def nomes_da_lista(valor, campo):
        if valor is None:
            return ()
        if not isinstance(valor, list) or len(valor) > MAX_OPCOES or not all(isinstance(x, str) for x in valor):
            raise ErroNegocio(f"Opções inválidas ({campo}).")
        if len(set(valor)) != len(valor):
            raise ErroNegocio(f"Opção repetida ({campo}).")
        return tuple(sorted(valor))

    quantidades = {}
    for item in itens:
        if not isinstance(item, dict):
            raise ErroNegocio("Item inválido.")
        prod_id, qtd = item.get("id"), item.get("qtd")
        if not isinstance(prod_id, str) or isinstance(qtd, bool) or not isinstance(qtd, int):
            raise ErroNegocio("Item inválido.")
        if qtd < 1 or qtd > MAX_QTD:
            raise ErroNegocio(f"A quantidade de cada item deve ficar entre 1 e {MAX_QTD}.")
        chave = (prod_id, nomes_da_lista(item.get("extras"), "adicionais"), nomes_da_lista(item.get("sem"), "retiradas"))
        quantidades[chave] = quantidades.get(chave, 0) + qtd

    subtotal = 0
    usado = {}
    usado_geral = {}      # estoque geral (ex.: pães) que o pedido inteiro vai gastar
    itens_processados = []
    for (prod_id, extras_pedidos, sem_pedidos), qtd in quantidades.items():
        if qtd > MAX_QTD:
            raise ErroNegocio(f"A quantidade de cada item deve ficar entre 1 e {MAX_QTD}.")
        p = con.execute("SELECT id, nome, preco_centavos, disponivel, opcoes, estoque, janela_ini, janela_fim, "
                        "insumo_id, consumo FROM produtos WHERE id = ?", (prod_id,)).fetchone()
        if not p:
            raise ErroNegocio("Produto indisponível.")
        if not p["disponivel"]:
            raise ErroNegocio(f"O item '{p['nome']}' acabou de esgotar.", status=409)
        no_horario, aviso_horario = produto_no_horario(p)
        if not no_horario:
            raise ErroNegocio(f"O item '{p['nome']}' não está disponível agora. {aviso_horario}.", status=409)
        if p["insumo_id"]:
            ins = con.execute("SELECT nome, quantidade FROM insumos WHERE id = ?", (p["insumo_id"],)).fetchone()
            gasto_antes = usado_geral.get(p["insumo_id"], 0)
            if ins is not None and ins["quantidade"] < gasto_antes + p["consumo"] * qtd:
                sobrou = max(ins["quantidade"] - gasto_antes, 0)
                if ins["quantidade"] < p["consumo"]:
                    raise ErroNegocio(f"O item '{p['nome']}' acabou de esgotar.", status=409)
                raise ErroNegocio(f"Não temos {ins['nome'].lower()} suficiente para esse pedido: "
                                  f"dá para fazer só {sobrou // p['consumo']} de '{p['nome']}'.", status=409)
            if ins is not None:
                usado_geral[p["insumo_id"]] = gasto_antes + p["consumo"] * qtd
        usado[prod_id] = usado.get(prod_id, 0) + qtd
        if p["estoque"] is not None and usado[prod_id] > p["estoque"]:
            restam = p["estoque"]
            raise ErroNegocio(f"Só temos {restam} unidade(s) de '{p['nome']}'." if restam else f"O item '{p['nome']}' acabou de esgotar.",
                              status=409)
        oferta = opcoes_do_produto(p)
        precos_extras = {e["nome"]: e["preco"] for e in oferta["extras"]}
        if any(x not in precos_extras for x in extras_pedidos) or any(x not in oferta["sem"] for x in sem_pedidos):
            raise ErroNegocio(f"O item '{p['nome']}' mudou. Atualize a página e escolha de novo.", status=409)
        unitario = p["preco_centavos"] + sum(precos_extras[x] for x in extras_pedidos)
        sub_item = unitario * qtd
        subtotal += sub_item
        itens_processados.append({
            "id": p["id"], "nome": p["nome"], "qtd": qtd,
            "extras": list(extras_pedidos), "sem": list(sem_pedidos),
            "opcoes": texto_opcoes(extras_pedidos, sem_pedidos),
            "preco": unitario / 100.0, "preco_centavos": unitario,
            "subtotal": sub_item / 100.0,
            "insumo_id": p["insumo_id"] if p["insumo_id"] in usado_geral else "",
            "insumo_qtd": p["consumo"] * qtd if p["insumo_id"] in usado_geral else 0,
        })

    if subtotal < cfg["pedido_minimo"]:
        raise ErroNegocio(f"O pedido mínimo é {_brl(cfg['pedido_minimo'])}.")
    taxa = cfg["taxa_entrega"] if tipo == "entrega" else 0
    if tipo == "entrega" and cfg["bairros_modo"] == "lista":
        if not bairro:
            raise ErroNegocio("Atualize a página e escolha o bairro da entrega na lista.")
        achado = achar_bairro(cfg, bairro)
        if not achado:
            raise ErroNegocio(f"Não entregamos no bairro \"{bairro}\".")
        taxa = achado["taxa"]
        bairro = achado["nome"]
        endereco = montar_endereco(rua, numero, bairro, complemento, referencia)
    cupom_codigo, desconto = "", 0
    if cupom not in (None, ""):
        cupom_codigo, desconto = validar_cupom(con, cupom, subtotal, telefone)
    fid_desconto = 0
    fid = status_fidelidade(con, telefone, cfg)
    if fid["elegivel"]:
        fid_desconto = max(0, (subtotal - desconto) * cfg["fidelidade_percentual"] // 100)
    total_centavos = subtotal - desconto - fid_desconto + taxa

    troco_centavos = 0
    if pagamento == "dinheiro" and troco_para not in (None, "", 0):
        troco_centavos = _valor_centavos(troco_para, "valor para troco", maximo=10_000)
        if troco_centavos < total_centavos:
            raise ErroNegocio(f"O valor para troco precisa ser pelo menos o total ({_brl(total_centavos)}).")

    hora = agora_iso()
    agendado_utc, liberar_em = "", hora
    if agendado_local:
        agendado_utc = agendado_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        liberar = (agendado_local - timedelta(minutes=cfg["agend_liberar"])).astimezone(timezone.utc)
        liberar_em = max(hora, liberar.strftime("%Y-%m-%dT%H:%M:%SZ"))
    token = secrets.token_urlsafe(9)      # link de acompanhamento: impossível de adivinhar
    with transacao(con):
        if ip:
            con.execute("INSERT INTO limite_pedidos (ip, quando) VALUES (?, ?)", (ip, agora_ts))
        senha = con.execute(
            """INSERT INTO pedidos (criado, atualizado, status, tipo, nome, telefone, endereco, observacao,
                                    pagamento, troco_para_centavos, taxa_entrega_centavos, total_centavos,
                                    rua, numero, bairro, complemento, referencia, token, agendado_para, liberar_em,
                                    cupom, desconto_centavos, mesa, fidelidade_centavos)
               VALUES (?, ?, 'novo', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (hora, hora, tipo, nome, telefone, endereco, observacao,
             pagamento, troco_centavos, taxa, total_centavos,
             rua, numero, bairro, complemento, referencia, token, agendado_utc, liberar_em,
             cupom_codigo, desconto, mesa, fid_desconto)).lastrowid
        if cupom_codigo:      # conta o uso de forma atômica (dois pedidos ao mesmo tempo não passam do limite)
            if con.execute("UPDATE cupons SET usos = usos + 1 WHERE codigo = ? AND ativo = 1 "
                           "AND (limite IS NULL OR usos < limite)", (cupom_codigo,)).rowcount == 0:
                raise ErroNegocio("Este cupom acabou de esgotar.", status=409)
        for prod_id, qtd_total in usado.items():     # baixa de estoque, atômica (dois clientes ao mesmo tempo não furam)
            cur = con.execute(
                "UPDATE produtos SET estoque = estoque - ?, "
                "disponivel = CASE WHEN estoque - ? <= 0 THEN 0 ELSE disponivel END "
                "WHERE id = ? AND estoque IS NOT NULL AND estoque >= ?", (qtd_total, qtd_total, prod_id, qtd_total))
            if cur.rowcount == 0 and con.execute("SELECT estoque FROM produtos WHERE id = ?", (prod_id,)).fetchone()["estoque"] is not None:
                raise ErroNegocio("Um dos itens acabou de esgotar. Atualize o pedido.", status=409)
        for ins_id, gasto in usado_geral.items():     # baixa do estoque geral, também atômica
            if con.execute("UPDATE insumos SET quantidade = quantidade - ? WHERE id = ? AND quantidade >= ?",
                           (gasto, ins_id, gasto)).rowcount == 0:
                raise ErroNegocio("Um dos itens acabou de esgotar. Atualize o pedido.", status=409)
        con.executemany(
            """INSERT INTO pedido_itens (pedido_senha, produto_id, nome, qtd, preco_centavos, opcoes, custo_centavos,
                                         insumo_id, insumo_qtd)
               VALUES (?, ?, ?, ?, ?, ?, (SELECT custo_centavos FROM produtos WHERE id = ?), ?, ?)""",
            [(senha, i["id"], i["nome"], i["qtd"], i["preco_centavos"], i["opcoes"], i["id"],
              i["insumo_id"], i["insumo_qtd"]) for i in itens_processados])
    _avisar_estoque_baixo(con, list(usado_geral))

    whatsapp.enviar_em_segundo_plano(
        telefone,
        f"Olá, {nome}! 🍔\n\nO seu pedido *#{senha}* foi recebido com sucesso!\n"
        + (f"*Cupom {cupom_codigo}:* -{_brl(desconto)}\n" if desconto else "")
        + (f"*Fidelidade 🎁:* -{_brl(fid_desconto)}\n" if fid_desconto else "")
        + f"*Total:* {_brl(total_centavos)} ({PAGAMENTOS[pagamento]})\n"
        + (f"*Agendado para:* {_rotulo_agendamento(agendado_local)}\n" if agendado_local else "")
        + "\nAvisaremos por aqui quando houver novidades."
        + (f"\n\nAcompanhe o seu pedido: {base_url.rstrip('/')}/acompanhar/{token}" if base_url else ""))

    _avisar_dono(cfg, senha, tipo, nome, total_centavos, pagamento, bairro, itens_processados,
                 _rotulo_agendamento(agendado_local) if agendado_local else "", mesa)

    return {
        "id": senha, "senha": senha, "tipo": tipo, "status": "novo", "nome": nome,
        "telefone": telefone, "endereco": endereco, "observacao": observacao,
        "rua": rua, "numero": numero, "bairro": bairro, "complemento": complemento, "referencia": referencia,
        "mesa": mesa, "pagamento": pagamento, "troco_para": troco_centavos / 100.0,
        "taxa_entrega": taxa / 100.0, "subtotal": subtotal / 100.0,
        "cupom": cupom_codigo, "desconto": desconto / 100.0, "fidelidade": fid_desconto / 100.0,
        "total": total_centavos / 100.0, "itens": itens_processados, "hora": hora,
        "token": token, "acompanhar": f"/acompanhar/{token}",
        "agendado_para": agendado_utc, "agendado_rotulo": _rotulo_agendamento(agendado_local) if agendado_local else "",
    }


def obter_status_pedido(con, senha):
    row = con.execute("SELECT status FROM pedidos WHERE senha = ?", (senha,)).fetchone()
    return row["status"] if row else None


def listar_pedidos_ativos(con):
    pedidos = con.execute(
        "SELECT * FROM pedidos WHERE status IN ('novo', 'fazendo', 'pronto', 'saiu') ORDER BY senha ASC"
    ).fetchall()
    if not pedidos:
        return []
    marcas = ",".join("?" * len(pedidos))
    itens_por_pedido = {}
    for i in con.execute(
            f"SELECT pedido_senha, produto_id AS id, nome, qtd, preco_centavos, opcoes "
            f"FROM pedido_itens WHERE pedido_senha IN ({marcas}) ORDER BY id",
            [p["senha"] for p in pedidos]):
        itens_por_pedido.setdefault(i["pedido_senha"], []).append(
            {"id": i["id"], "nome": i["nome"], "qtd": i["qtd"], "opcoes": i["opcoes"],
             "preco": i["preco_centavos"] / 100.0})
    limite = obter_config_loja(con)["atraso_minutos"]
    agora = datetime.now(timezone.utc)
    def inicio_contagem(p):
        return p["liberar_em"] or p["criado"]

    def minutos(p):
        return max(0, int((agora - datetime.strptime(inicio_contagem(p), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds() // 60))
    return [{
        "id": p["senha"], "senha": p["senha"], "tipo": p["tipo"], "status": p["status"],
        "nome": p["nome"] or "", "telefone": p["telefone"] or "", "endereco": p["endereco"] or "",
        "rua": p["rua"], "numero": p["numero"], "bairro": p["bairro"], "mesa": p["mesa"],
        "complemento": p["complemento"], "referencia": p["referencia"],
        "observacao": p["observacao"] or "", "total": (p["total_centavos"] or 0) / 100.0,
        "pagamento": p["pagamento"], "troco_para": p["troco_para_centavos"] / 100.0,
        "taxa_entrega": p["taxa_entrega_centavos"] / 100.0,
        "cupom": p["cupom"], "desconto": p["desconto_centavos"] / 100.0, "fidelidade": p["fidelidade_centavos"] / 100.0,
        "hora": p["criado"], "itens": itens_por_pedido.get(p["senha"], []),
        "minutos": minutos(p),
        "agendado_para": p["agendado_para"],
        "liberado": p["status"] != "novo" or inicio_contagem(p) <= agora.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "atrasado": bool(limite) and p["status"] in ("novo", "fazendo") and minutos(p) >= limite,
    } for p in pedidos]


def _mensagem_status(status, nome, tipo, senha, mesa=""):
    n = str(senha).zfill(3)
    if status == "fazendo":
        return f"Olá, {nome}! 👨‍🍳 O seu pedido #{n} já começou a ser preparado!"
    if status == "pronto":
        if mesa:
            return f"Olá, {nome}! ✅ O seu pedido #{n} está PRONTO e já vai para a Mesa {mesa}!"
        if tipo == "balcao":
            return f"Olá, {nome}! ✅ O seu pedido #{n} está PRONTO! Pode vir retirar no balcão."
        return f"Olá, {nome}! ✅ O seu pedido #{n} está pronto e logo sai para entrega."
    if status == "saiu":
        return f"Olá, {nome}! 🛵 O seu pedido #{n} saiu para entrega!"
    return ""


def _ajustar_estoque(con, senha, sinal):
    """sinal=+1 devolve ao estoque (pedido cancelado); sinal=-1 tira de novo (pedido reaberto).
    O uso do cupom também volta (ou é contado de novo)."""
    cup = con.execute("SELECT cupom FROM pedidos WHERE senha = ?", (senha,)).fetchone()
    if cup and cup["cupom"]:
        con.execute("UPDATE cupons SET usos = MAX(usos + ?, 0) WHERE codigo = ?", (-sinal, cup["cupom"]))
    for g in con.execute("SELECT insumo_id, SUM(insumo_qtd) AS qtd FROM pedido_itens "
                         "WHERE pedido_senha = ? AND insumo_id <> '' GROUP BY insumo_id", (senha,)).fetchall():
        if sinal > 0:      # voltou acima do mínimo: libera um novo aviso de estoque baixo no futuro
            con.execute("UPDATE insumos SET quantidade = quantidade + ?, "
                        "avisado = CASE WHEN quantidade + ? > minimo THEN 0 ELSE avisado END WHERE id = ?",
                        (g["qtd"], g["qtd"], g["insumo_id"]))
        else:
            con.execute("UPDATE insumos SET quantidade = MAX(quantidade - ?, 0) WHERE id = ?", (g["qtd"], g["insumo_id"]))
    for it in con.execute("SELECT produto_id, SUM(qtd) AS qtd FROM pedido_itens WHERE pedido_senha = ? GROUP BY produto_id",
                          (senha,)).fetchall():
        if sinal > 0:
            con.execute(
                "UPDATE produtos SET estoque = estoque + ?, "
                "disponivel = CASE WHEN estoque = 0 AND disponivel = 0 THEN 1 ELSE disponivel END "
                "WHERE id = ? AND estoque IS NOT NULL", (it["qtd"], it["produto_id"]))
        else:
            con.execute(
                "UPDATE produtos SET estoque = MAX(estoque - ?, 0), "
                "disponivel = CASE WHEN estoque - ? <= 0 THEN 0 ELSE disponivel END "
                "WHERE id = ? AND estoque IS NOT NULL", (it["qtd"], it["qtd"], it["produto_id"]))


def mudar_status(con, senha, novo_status):
    """Muda o status e avisa o cliente. O UPDATE condicional (status <> novo) garante que, mesmo
    com cliques ou requisições simultâneas, só UMA delas muda o pedido e dispara a mensagem."""
    if novo_status not in STATUS_VALIDOS:
        raise ErroNegocio("Status inválido.")
    try:
        senha = int(senha)
    except (TypeError, ValueError):
        raise ErroNegocio("Pedido não encontrado.", 404)

    pedido = con.execute("SELECT nome, telefone, tipo, status, mesa FROM pedidos WHERE senha = ?", (senha,)).fetchone()
    if not pedido:
        raise ErroNegocio("Pedido não encontrado.", 404)

    with transacao(con):
        agora = agora_iso()
        # começar um pedido agendado antes da hora "libera" ele agora (e o contador de atraso parte daí)
        cur = con.execute(
            "UPDATE pedidos SET status = ?, atualizado = ?, "
            "liberar_em = CASE WHEN ? IN ('fazendo','pronto','saiu','concluido') AND liberar_em > ? THEN ? ELSE liberar_em END, "
            "pronto_em = CASE WHEN ? IN ('pronto','saiu','concluido') AND pronto_em = '' THEN ? ELSE pronto_em END "
            "WHERE senha = ? AND status <> ?",
            (novo_status, agora, novo_status, agora, agora, novo_status, agora, senha, novo_status))
        mudou = cur.rowcount == 1
        if mudou and novo_status == "cancelado":
            _ajustar_estoque(con, senha, +1)
        elif mudou and pedido["status"] == "cancelado":
            _ajustar_estoque(con, senha, -1)

    if mudou:
        registrar(con, "pedido", f"Pedido #{senha}: {pedido['status']} → {novo_status}")
        texto = _mensagem_status(novo_status, pedido["nome"], pedido["tipo"], senha, pedido["mesa"])
        if texto and pedido["telefone"]:
            whatsapp.enviar_em_segundo_plano(pedido["telefone"], texto)
    return {"ok": True, "status": novo_status, "alterado": mudou}


# ------------------------------------------------------------- Gestão ----

def _preco_centavos(valor):
    try:
        preco = float(valor)
    except (TypeError, ValueError):
        raise ErroNegocio("Informe um preço válido.")
    if not (0 < preco <= MAX_PRECO) or preco != preco:
        raise ErroNegocio(f"O preço deve ficar entre 0,01 e {MAX_PRECO:.2f}.")
    return int(round(preco * 100))


def _custo_centavos(valor):
    """Custo de produção. Vazio = não informado (None); 0 é válido (ex.: item sem custo)."""
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return None
    try:
        custo = float(str(valor).replace(",", ".")) if not isinstance(valor, (int, float)) or isinstance(valor, bool) else float(valor)
    except (TypeError, ValueError):
        raise ErroNegocio("Informe um custo válido (ou deixe em branco).")
    if isinstance(valor, bool) or custo != custo or not (0 <= custo <= MAX_PRECO):
        raise ErroNegocio(f"O custo deve ficar entre 0 e {MAX_PRECO:.2f}.")
    return int(round(custo * 100))


def extras_dos_produtos(con):
    """{id: {estoque, janela_ini, janela_fim}} — dados de gestão que só o painel recebe."""
    return {r["id"]: {"estoque": r["estoque"], "janela_ini": r["janela_ini"], "janela_fim": r["janela_fim"],
                      "insumo_id": r["insumo_id"], "consumo": r["consumo"]}
            for r in con.execute("SELECT id, estoque, janela_ini, janela_fim, insumo_id, consumo FROM produtos")}


def custos_dos_produtos(con):
    """{id_do_produto: custo em centavos ou None}. Só o painel (admin) vê isso; o cardápio público não."""
    return {r["id"]: r["custo_centavos"] for r in con.execute("SELECT id, custo_centavos FROM produtos")}


def _foto(dados):
    """Valida a foto enviada pelo painel (só URLs geradas por imagens.processar)."""
    url = dados.get("imagem") or ""
    if not url:
        return "", ""
    if not imagens.RE_IMAGEM.match(url) or not imagens.existe(url):
        raise ErroNegocio("Foto inválida. Envie a foto de novo.")
    return url, imagens.miniatura_de(url)


def criar_categoria(con, nome, icone):
    nome = (nome or "").strip() if isinstance(nome, str) or nome is None else ""
    icone = ((icone or "🍔").strip() if isinstance(icone, str) or icone is None else "🍔")[:8] or "🍔"
    if not nome:
        raise ErroNegocio("Informe o nome da categoria.")
    if len(nome) > 30:
        raise ErroNegocio("O nome da categoria passa de 30 caracteres.")
    cat_id = uuid.uuid4().hex[:8]
    ordem = con.execute("SELECT COALESCE(MAX(ordem), -1) + 1 FROM categorias").fetchone()[0]
    with transacao(con):
        con.execute("INSERT INTO categorias (id, nome, icone, ordem) VALUES (?, ?, ?, ?)",
                    (cat_id, nome, icone, ordem))
    registrar(con, "categoria", f"Criou a categoria \"{nome}\"")
    return {"id": cat_id, "nome": nome, "icone": icone}


def excluir_categoria(con, cat_id):
    """Exclui a categoria e os produtos dela (pedidos antigos continuam, pois guardam cópia dos itens)."""
    if not con.execute("SELECT 1 FROM categorias WHERE id = ?", (cat_id,)).fetchone():
        raise ErroNegocio("Categoria não encontrada.", 404)
    imgs = [r["imagem"] for r in con.execute("SELECT imagem FROM produtos WHERE categoria_id = ?", (cat_id,))]
    nome_cat = con.execute("SELECT nome FROM categorias WHERE id = ?", (cat_id,)).fetchone()["nome"]
    with transacao(con):
        n = con.execute("DELETE FROM produtos WHERE categoria_id = ?", (cat_id,)).rowcount
        con.execute("DELETE FROM categorias WHERE id = ?", (cat_id,))
    registrar(con, "categoria", f"Excluiu a categoria \"{nome_cat}\" e {n} produto(s)")
    for i in imgs:
        imagens.apagar(i)
    return {"produtos_excluidos": n}


def criar_produto(con, dados):
    nome = dados.get("nome")
    nome = nome.strip() if isinstance(nome, str) else ""
    cat_id = dados.get("categoria_id")
    if not nome or len(nome) > 60:
        raise ErroNegocio("Informe o nome do produto (até 60 caracteres).")
    if not isinstance(cat_id, str) or not con.execute(
            "SELECT 1 FROM categorias WHERE id = ?", (cat_id,)).fetchone():
        raise ErroNegocio("Categoria inválida.")
    preco_centavos = _preco_centavos(dados.get("preco"))
    descricao = dados.get("descricao") if isinstance(dados.get("descricao"), str) else ""
    emoji = dados.get("emoji") if isinstance(dados.get("emoji"), str) and dados.get("emoji").strip() else "🍔"
    if len(descricao) > 300:
        raise ErroNegocio("A descrição passa de 300 caracteres.")
    custo = _custo_centavos(dados.get("custo"))
    opcoes = _opcoes_json(_ler_extras(dados.get("extras_texto")), _ler_sem(dados.get("sem_texto")))
    estoque = _estoque_valor(dados.get("estoque"))
    janela_ini, janela_fim = _janela_valida(dados.get("janela_ini"), dados.get("janela_fim"))
    insumo_id, consumo = _vinculo_insumo(con, dados.get("insumo_id"), dados.get("consumo"))
    imagem, miniatura = _foto(dados)
    prod_id = uuid.uuid4().hex[:8]
    with transacao(con):
        ordem = con.execute("SELECT COALESCE(MAX(ordem), -1) + 1 FROM produtos WHERE categoria_id = ?",
                            (cat_id,)).fetchone()[0]
        con.execute(
            """INSERT INTO produtos (id, categoria_id, nome, descricao, preco_centavos, emoji, imagem, miniatura, disponivel, ordem, custo_centavos, opcoes,
                                     estoque, janela_ini, janela_fim, insumo_id, consumo)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (prod_id, cat_id, nome, descricao.strip(), preco_centavos, emoji.strip()[:8], imagem, miniatura,
             0 if estoque == 0 else 1, ordem, custo, opcoes, estoque, janela_ini, janela_fim, insumo_id, consumo))
    registrar(con, "produto", f"Criou o produto \"{nome}\" por {_brl(preco_centavos)}")
    return {"id": prod_id, "nome": nome, "preco": preco_centavos / 100.0}


def editar_produto(con, prod_id, dados):
    atual = con.execute("SELECT * FROM produtos WHERE id = ?", (prod_id,)).fetchone()
    if not atual:
        raise ErroNegocio("Produto não encontrado.", 404)

    nome = dados.get("nome", atual["nome"])
    nome = nome.strip() if isinstance(nome, str) else ""
    if not nome or len(nome) > 60:
        raise ErroNegocio("Informe o nome do produto (até 60 caracteres).")
    descricao = dados.get("descricao", atual["descricao"])
    if not isinstance(descricao, str) or len(descricao) > 300:
        raise ErroNegocio("Descrição inválida (até 300 caracteres).")
    preco_centavos = _preco_centavos(dados["preco"]) if "preco" in dados else atual["preco_centavos"]
    emoji = dados.get("emoji", atual["emoji"])
    emoji = emoji.strip()[:8] if isinstance(emoji, str) and emoji.strip() else atual["emoji"]

    cat_id = dados.get("categoria_id", atual["categoria_id"])
    if not isinstance(cat_id, str) or not con.execute(
            "SELECT 1 FROM categorias WHERE id = ?", (cat_id,)).fetchone():
        raise ErroNegocio("Categoria inválida.")

    custo = _custo_centavos(dados["custo"]) if "custo" in dados else atual["custo_centavos"]
    if "extras_texto" in dados or "sem_texto" in dados:
        antigas = opcoes_do_produto(atual)
        extras = _ler_extras(dados["extras_texto"]) if "extras_texto" in dados else antigas["extras"]
        sem = _ler_sem(dados["sem_texto"]) if "sem_texto" in dados else antigas["sem"]
        opcoes = _opcoes_json(extras, sem)
    else:
        opcoes = atual["opcoes"]
    estoque = _estoque_valor(dados["estoque"]) if "estoque" in dados else atual["estoque"]
    if "janela_ini" in dados or "janela_fim" in dados:
        janela_ini, janela_fim = _janela_valida(dados.get("janela_ini", atual["janela_ini"]),
                                                dados.get("janela_fim", atual["janela_fim"]))
    else:
        janela_ini, janela_fim = atual["janela_ini"], atual["janela_fim"]
    if "insumo_id" in dados or "consumo" in dados:
        insumo_id, consumo = _vinculo_insumo(con, dados.get("insumo_id", atual["insumo_id"]),
                                             dados.get("consumo", atual["consumo"]))
    else:
        insumo_id, consumo = atual["insumo_id"], atual["consumo"]
    disponivel = atual["disponivel"]
    if estoque is not None and estoque <= 0:
        disponivel = 0
    elif estoque and atual["estoque"] is not None and atual["estoque"] <= 0:
        disponivel = 1                                        # repôs o estoque de um item que tinha zerado
    imagem, miniatura = atual["imagem"], atual["miniatura"]
    if "imagem" in dados:
        imagem, miniatura = _foto(dados)

    with transacao(con):
        con.execute(
            """UPDATE produtos SET nome = ?, descricao = ?, preco_centavos = ?, emoji = ?,
                      categoria_id = ?, imagem = ?, miniatura = ?, custo_centavos = ?, opcoes = ?,
                      estoque = ?, janela_ini = ?, janela_fim = ?, disponivel = ?, insumo_id = ?, consumo = ?
               WHERE id = ?""",
            (nome, descricao.strip(), preco_centavos, emoji, cat_id, imagem, miniatura, custo, opcoes,
             estoque, janela_ini, janela_fim, disponivel, insumo_id, consumo, prod_id))
    if atual["imagem"] and atual["imagem"] != imagem:
        imagens.apagar(atual["imagem"])
    mud = []
    if nome != atual["nome"]: mud.append(f"nome \"{atual['nome']}\" → \"{nome}\"")
    if preco_centavos != atual["preco_centavos"]: mud.append(f"preço {_brl(atual['preco_centavos'])} → {_brl(preco_centavos)}")
    if custo != atual["custo_centavos"]:
        fm = lambda v: "sem custo" if v is None else _brl(v)
        mud.append(f"custo {fm(atual['custo_centavos'])} → {fm(custo)}")
    if estoque != atual["estoque"]:
        fe = lambda v: "sem controle" if v is None else str(v)
        mud.append(f"estoque {fe(atual['estoque'])} → {fe(estoque)}")
    if disponivel != atual["disponivel"]: mud.append("ficou disponível" if disponivel else "ficou indisponível")
    if cat_id != atual["categoria_id"]: mud.append("mudou de categoria")
    if opcoes != atual["opcoes"]: mud.append("adicionais/retiradas")
    if descricao.strip() != atual["descricao"]: mud.append("descrição")
    if (janela_ini, janela_fim) != (atual["janela_ini"], atual["janela_fim"]): mud.append("horário de venda")
    if (insumo_id, consumo) != (atual["insumo_id"], atual["consumo"]): mud.append("estoque geral")
    if imagem != atual["imagem"]: mud.append("foto")
    if mud:
        registrar(con, "produto", f"Produto \"{atual['nome']}\": " + "; ".join(mud))
    return {"ok": True}


def definir_disponibilidade(con, prod_id, disponivel):
    atual = con.execute("SELECT estoque FROM produtos WHERE id = ?", (prod_id,)).fetchone()
    if atual and disponivel and atual["estoque"] is not None and atual["estoque"] <= 0:
        raise ErroNegocio("Este produto está com estoque zerado. Informe o novo estoque ao editá-lo para reativar.")
    with transacao(con):
        cur = con.execute("UPDATE produtos SET disponivel = ? WHERE id = ?",
                          (1 if disponivel else 0, prod_id))
    if cur.rowcount == 0:
        raise ErroNegocio("Produto não encontrado.", 404)
    nm = con.execute("SELECT nome FROM produtos WHERE id = ?", (prod_id,)).fetchone()
    registrar(con, "produto", f"Produto \"{nm['nome']}\": {'ativado' if disponivel else 'desativado'} no cardápio")
    return {"id": prod_id, "disponivel": disponivel}


def excluir_produto(con, prod_id):
    row = con.execute("SELECT imagem, nome FROM produtos WHERE id = ?", (prod_id,)).fetchone()
    if not row:
        raise ErroNegocio("Produto não encontrado.", 404)
    with transacao(con):
        con.execute("DELETE FROM produtos WHERE id = ?", (prod_id,))
    registrar(con, "produto", f"Excluiu o produto \"{row['nome']}\"")
    imagens.apagar(row["imagem"])


def limpar_fotos_soltas(con):
    """Apaga fotos enviadas que nenhum produto usa (com mais de 1 hora)."""
    em_uso = [r["imagem"] for r in con.execute("SELECT imagem FROM produtos WHERE imagem <> ''")]
    imagens.limpar_orfas(em_uso)


# ----------------------------------------------------------- Financeiro ----
MAX_DIAS_RELATORIO = 366


def _intervalo_utc(inicio, fim):
    """Datas locais [inicio, fim] (inclusive) -> strings ISO em UTC [de, ate) para filtrar `criado`."""
    de = datetime.combine(inicio, datetime.min.time(), FUSO).astimezone(timezone.utc)
    ate = datetime.combine(fim + timedelta(days=1), datetime.min.time(), FUSO).astimezone(timezone.utc)
    f = "%Y-%m-%dT%H:%M:%SZ"
    return de.strftime(f), ate.strftime(f)


def _parse_data(texto, campo):
    try:
        return date.fromisoformat(texto)
    except (TypeError, ValueError):
        raise ErroNegocio(f"Data inválida: {campo}.")


def validar_periodo(inicio, fim):
    hoje = datetime.now(FUSO).date()
    ini = _parse_data(inicio, "início") if inicio else hoje
    fi = _parse_data(fim, "fim") if fim else ini
    if fi < ini:
        raise ErroNegocio("A data final não pode ser anterior à inicial.")
    if (fi - ini).days + 1 > MAX_DIAS_RELATORIO:
        raise ErroNegocio(f"Escolha um período de no máximo {MAX_DIAS_RELATORIO} dias.")
    return ini, fi


def _local(iso_utc):
    return datetime.strptime(iso_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(FUSO)


def _faturamento(con, inicio, fim):
    de, ate = _intervalo_utc(inicio, fim)
    return con.execute(
        "SELECT COALESCE(SUM(total_centavos), 0) FROM pedidos WHERE status = 'concluido' AND criado >= ? AND criado < ?",
        (de, ate)).fetchone()[0]


def resumo_financeiro(con, inicio, fim):
    """Números do período (datas locais, inclusive). Valores em centavos.
    Faturamento = pedidos CONCLUÍDOS; cancelados e em andamento aparecem à parte."""
    de, ate = _intervalo_utc(inicio, fim)
    linhas = con.execute(
        "SELECT criado, status, tipo, total_centavos, pagamento, taxa_entrega_centavos, bairro FROM pedidos WHERE criado >= ? AND criado < ?",
        (de, ate)).fetchall()

    por_pagamento = {k: {"pedidos": 0, "total": 0} for k in list(PAGAMENTOS) + [""]}
    taxas = 0
    dias = {}
    d = inicio
    while d <= fim:
        dias[d.isoformat()] = {"data": d.isoformat(), "pedidos": 0, "total": 0}
        d += timedelta(days=1)
    horas = [0] * 24
    bairros = {}
    por_tipo = {"balcao": {"pedidos": 0, "total": 0}, "entrega": {"pedidos": 0, "total": 0}}
    conc = {"pedidos": 0, "total": 0}
    canc = {"pedidos": 0, "total": 0}
    andamento = {"pedidos": 0, "total": 0}

    for r in linhas:
        valor = r["total_centavos"]
        if r["status"] == "concluido":
            local = _local(r["criado"])
            conc["pedidos"] += 1; conc["total"] += valor
            por_tipo[r["tipo"]]["pedidos"] += 1; por_tipo[r["tipo"]]["total"] += valor
            dia = dias.get(local.date().isoformat())
            if dia:
                dia["pedidos"] += 1; dia["total"] += valor
            horas[local.hour] += 1
            pg = por_pagamento[r["pagamento"] if r["pagamento"] in PAGAMENTOS else ""]
            pg["pedidos"] += 1; pg["total"] += valor
            taxas += r["taxa_entrega_centavos"]
            if r["tipo"] == "entrega":
                chave = (r["bairro"] or "").strip().title() or "Não informado"
                b = bairros.setdefault(chave, {"bairro": chave, "pedidos": 0, "total": 0})
                b["pedidos"] += 1; b["total"] += valor
        elif r["status"] == "cancelado":
            canc["pedidos"] += 1; canc["total"] += valor
        else:
            andamento["pedidos"] += 1; andamento["total"] += valor

    # lucro estimado dos produtos (sem a taxa de entrega); só conta itens com custo informado
    top = con.execute(
        """SELECT i.nome AS nome, SUM(i.qtd) AS qtd, SUM(i.qtd * i.preco_centavos) AS total,
                  SUM(CASE WHEN i.custo_centavos IS NOT NULL THEN i.qtd * (i.preco_centavos - i.custo_centavos) END) AS lucro,
                  SUM(CASE WHEN i.custo_centavos IS NULL THEN 1 ELSE 0 END) AS sem_custo
           FROM pedido_itens i JOIN pedidos p ON p.senha = i.pedido_senha
           WHERE p.status = 'concluido' AND p.criado >= ? AND p.criado < ?
           GROUP BY i.produto_id, i.nome ORDER BY qtd DESC, total DESC LIMIT 10""",
        (de, ate)).fetchall()
    lc = con.execute(
        """SELECT COALESCE(SUM(i.qtd * i.preco_centavos), 0) AS receita,
                  COALESCE(SUM(CASE WHEN i.custo_centavos IS NOT NULL THEN i.qtd * i.preco_centavos END), 0) AS receita_com_custo,
                  COALESCE(SUM(CASE WHEN i.custo_centavos IS NOT NULL THEN i.qtd * (i.preco_centavos - i.custo_centavos) END), 0) AS lucro
           FROM pedido_itens i JOIN pedidos p ON p.senha = i.pedido_senha
           WHERE p.status = 'concluido' AND p.criado >= ? AND p.criado < ?""", (de, ate)).fetchone()
    lucro = {
        "estimado": lc["lucro"], "receita_itens": lc["receita"], "receita_com_custo": lc["receita_com_custo"],
        "margem": round(lc["lucro"] * 100 / lc["receita_com_custo"], 1) if lc["receita_com_custo"] else None,
        "cobertura": round(lc["receita_com_custo"] * 100 / lc["receita"]) if lc["receita"] else 0,
    }

    # período anterior, de mesmo tamanho, para a comparação
    n = (fim - inicio).days + 1
    ant_fim = inicio - timedelta(days=1)
    ant_ini = ant_fim - timedelta(days=n - 1)
    anterior = _faturamento(con, ant_ini, ant_fim)

    return {
        "inicio": inicio.isoformat(), "fim": fim.isoformat(),
        "faturamento": conc["total"], "concluidos": conc["pedidos"],
        "ticket_medio": round(conc["total"] / conc["pedidos"]) if conc["pedidos"] else 0,
        "cancelados": canc, "em_andamento": andamento,
        "por_tipo": por_tipo, "por_pagamento": por_pagamento, "taxas_entrega": taxas,
        "por_dia": list(dias.values()),
        "por_hora": horas,
        "por_bairro": sorted(bairros.values(), key=lambda b: (-b["pedidos"], -b["total"]))[:10],
        "mais_vendidos": [{"nome": t["nome"], "qtd": t["qtd"], "total": t["total"],
                           "lucro": t["lucro"], "custo_completo": t["sem_custo"] == 0} for t in top],
        "lucro": lucro,
        "periodo_anterior": {"inicio": ant_ini.isoformat(), "fim": ant_fim.isoformat(), "faturamento": anterior},
    }


def pedidos_do_periodo(con, inicio, fim):
    """Linhas para exportar (sem telefone nem endereço: dados pessoais ficam fora da planilha)."""
    de, ate = _intervalo_utc(inicio, fim)
    return con.execute(
        "SELECT senha, criado, status, tipo, total_centavos, pagamento, taxa_entrega_centavos, cupom, desconto_centavos, mesa, fidelidade_centavos FROM pedidos WHERE criado >= ? AND criado < ? ORDER BY senha",
        (de, ate)).fetchall()


# ------------------------------------------------- Loja: horário, taxa, mínimo ----

def _brl(centavos):
    return f"R$ {centavos / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _valor_centavos(valor, campo, maximo=1000):
    """Aceita 12, 12.5, '12,50' ou '12.50' (reais) e devolve centavos; recusa negativo e lixo."""
    if isinstance(valor, bool) or valor is None:
        raise ErroNegocio(f"Valor inválido: {campo}.")
    try:
        numero = float(valor.strip().replace(",", ".")) if isinstance(valor, str) else float(valor)
    except ValueError:
        raise ErroNegocio(f"Valor inválido: {campo}.")
    if not (0 <= numero <= maximo) or numero != numero:
        raise ErroNegocio(f"O valor de {campo} deve ficar entre 0 e {maximo}.")
    return int(round(numero * 100))


def _config_padrao():
    return {
        "modo": "sempre",      # sempre aberto | segue o horário | fechado
        "dias": [{"aberto": True, "abre": "18:00", "fecha": "23:00"} for _ in range(7)],
        "taxa_entrega": 0,
        "pedido_minimo": 0,
        "bairros_modo": "livre",   # livre = qualquer bairro, taxa única | lista = só os bairros cadastrados
        "bairros": [],             # [{"nome": "Centro", "taxa": 500}] (taxa em centavos)
        "dono_telefone": "",       # WhatsApp do dono (DDD+número) para o alerta de pedido novo
        "atraso_minutos": 30,      # pedido novo/fazendo há mais que isso é "atrasado" (0 = desligado)
        "relatorio_hora": "",      # "21:30" = manda o resumo do dia ao WhatsApp do dono nesse horário ("" = não manda)
        "fidelidade_pedidos": 0,   # a cada N pedidos concluídos o cliente ganha um desconto (0 = desligado)
        "fidelidade_percentual": 10,   # tamanho desse desconto (% sobre os lanches)
        "mesas": 0,                # quantidade de mesas (0 = sem pedidos por mesa)
        "preparo_minutos": 20,     # tempo médio de preparo mostrado ao cliente (0 = não mostrar)
        "agendamento": False,      # aceita pedidos para um horário futuro
        "agend_minimo": 30,        # antecedência mínima (min) entre o pedido e o horário escolhido
        "agend_liberar": 40,       # o pedido agendado aparece na cozinha X min antes do horário
    }


def _norm(texto):
    """Compara bairros sem diferença de maiúscula, acento ou espaço duplo."""
    sem_acento = unicodedata.normalize("NFD", texto or "")
    sem_acento = "".join(c for c in sem_acento if unicodedata.category(c) != "Mn")
    return " ".join(sem_acento.casefold().split())


def _ler_bairros(texto, taxa_padrao):
    """Texto do painel, uma linha por bairro: 'Centro; 5,00' (sem valor = taxa padrão)."""
    if not isinstance(texto, str):
        raise ErroNegocio("Lista de bairros inválida.")
    bairros, vistos = [], set()
    for n, linha in enumerate(texto.splitlines(), 1):
        linha = linha.strip()
        if not linha:
            continue
        nome, _, valor = linha.partition(";")
        nome = " ".join(nome.split())
        if not (2 <= len(nome) <= MAX_BAIRRO):
            raise ErroNegocio(f"Bairro inválido na linha {n} (2 a {MAX_BAIRRO} letras).")
        if _norm(nome) in vistos:
            raise ErroNegocio(f"O bairro \"{nome}\" está repetido.")
        vistos.add(_norm(nome))
        taxa = _valor_centavos(valor.strip(), f"taxa de {nome}") if valor.strip() else taxa_padrao
        bairros.append({"nome": nome, "taxa": taxa})
    if len(bairros) > 100:
        raise ErroNegocio("No máximo 100 bairros.")
    return bairros


def achar_bairro(cfg, nome):
    """Bairro cadastrado que corresponde ao digitado (ou None)."""
    alvo = _norm(nome)
    return next((b for b in cfg["bairros"] if _norm(b["nome"]) == alvo), None)


def obter_config_loja(con):
    padrao = _config_padrao()
    row = con.execute("SELECT valor FROM config WHERE chave = 'loja'").fetchone()
    if not row:
        return padrao
    try:
        salvo = json.loads(row["valor"])
        padrao.update({k: salvo[k] for k in padrao if k in salvo})
    except (ValueError, TypeError):
        pass
    return padrao


def salvar_config_loja(con, dados):
    modo = dados.get("modo")
    if modo not in ("sempre", "horario", "fechado"):
        raise ErroNegocio("Modo de funcionamento inválido.")
    dias_in = dados.get("dias")
    if not isinstance(dias_in, list) or len(dias_in) != 7:
        raise ErroNegocio("Informe os 7 dias da semana.")
    dias = []
    for i, d in enumerate(dias_in):
        if not isinstance(d, dict):
            raise ErroNegocio("Horário inválido.")
        aberto = d.get("aberto") is True
        abre, fecha = d.get("abre", ""), d.get("fecha", "")
        if aberto:
            if not (isinstance(abre, str) and isinstance(fecha, str)
                    and RE_HORA.match(abre) and RE_HORA.match(fecha)):
                raise ErroNegocio(f"Horário inválido na {DIAS_SEMANA[i]}. Use o formato 18:00.")
            if abre == fecha:
                raise ErroNegocio(f"Na {DIAS_SEMANA[i]}, abrir e fechar não podem ser no mesmo horário.")
        dias.append({"aberto": aberto, "abre": abre or "18:00", "fecha": fecha or "23:00"})
    if modo == "horario" and not any(d["aberto"] for d in dias):
        raise ErroNegocio("Marque pelo menos um dia como aberto, ou escolha outro modo.")
    taxa = _valor_centavos(dados.get("taxa_entrega", 0), "taxa de entrega")
    bairros_modo = dados.get("bairros_modo", "livre")
    if bairros_modo not in ("livre", "lista"):
        raise ErroNegocio("Modo de bairros inválido.")
    bairros = _ler_bairros(dados.get("bairros_texto", ""), taxa)
    if bairros_modo == "lista" and not bairros:
        raise ErroNegocio("Cadastre pelo menos um bairro, ou escolha \"qualquer bairro\".")
    dono = re.sub(r"\D", "", str(dados.get("dono_telefone") or ""))
    if dono:
        dono_fmt = whatsapp.formatar_numero(dono)
        if not dono_fmt:
            raise ErroNegocio("O WhatsApp do dono precisa ter DDD (ex.: 75 98765-4321).")
        dono = dono_fmt[2:]
    atraso = dados.get("atraso_minutos", 30)
    try:
        atraso = int(str(atraso).strip() or 0)
    except ValueError:
        raise ErroNegocio("O tempo de atraso precisa ser um número de minutos.")
    if not (0 <= atraso <= 240):
        raise ErroNegocio("O tempo de atraso deve ficar entre 0 (desligado) e 240 minutos.")
    def minutos_cfg(chave, padrao, maximo, nome):
        try:
            v = int(str(dados.get(chave, padrao)).strip() or padrao)
        except ValueError:
            raise ErroNegocio(f"{nome} precisa ser um número de minutos.")
        if not (0 <= v <= maximo):
            raise ErroNegocio(f"{nome} deve ficar entre 0 e {maximo} minutos.")
        return v
    preparo = minutos_cfg("preparo_minutos", 20, 180, "O tempo de preparo")
    hora_rel = str(dados.get("relatorio_hora") or "").strip()
    if hora_rel and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", hora_rel):
        raise ErroNegocio("O horário do relatório diário deve ser no formato 21:30.")
    if hora_rel and not dono:
        raise ErroNegocio("Para receber o relatório diário, informe o WhatsApp do dono.")
    fid_n = minutos_cfg("fidelidade_pedidos", 0, 50, "A quantidade de pedidos da fidelidade")
    fid_pct = minutos_cfg("fidelidade_percentual", 10, 50, "O desconto da fidelidade")
    if fid_n and fid_pct < 1:
        raise ErroNegocio("O desconto da fidelidade deve ser de pelo menos 1%.")
    try:
        mesas = int(str(dados.get("mesas", 0)).strip() or 0)
    except ValueError:
        raise ErroNegocio("A quantidade de mesas precisa ser um número.")
    if not (0 <= mesas <= 100):
        raise ErroNegocio("A quantidade de mesas deve ficar entre 0 (desligado) e 100.")
    agendamento = dados.get("agendamento") is True
    agend_minimo = minutos_cfg("agend_minimo", 30, 720, "A antecedência mínima do agendamento")
    agend_liberar = minutos_cfg("agend_liberar", 40, 240, "O tempo de liberação para a cozinha")
    cfg = {"modo": modo, "dias": dias, "taxa_entrega": taxa, "atraso_minutos": atraso,
           "preparo_minutos": preparo, "mesas": mesas, "relatorio_hora": hora_rel,
           "fidelidade_pedidos": fid_n, "fidelidade_percentual": fid_pct, "agendamento": agendamento, "agend_minimo": agend_minimo, "agend_liberar": agend_liberar,
           "pedido_minimo": _valor_centavos(dados.get("pedido_minimo", 0), "pedido mínimo"),
           "bairros_modo": bairros_modo, "bairros": bairros, "dono_telefone": dono}
    antes = obter_config_loja(con)
    with transacao(con):
        con.execute("INSERT INTO config (chave, valor) VALUES ('loja', ?) "
                    "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
                    (json.dumps(cfg, ensure_ascii=False),))
    rotulos = {"modo": "funcionamento", "dias": "horários", "taxa_entrega": "taxa de entrega", "pedido_minimo": "pedido mínimo",
               "bairros_modo": "modo de bairros", "bairros": "bairros", "dono_telefone": "WhatsApp do dono",
               "atraso_minutos": "tempo de atraso", "relatorio_hora": "horário do relatório", "fidelidade_pedidos": "fidelidade (pedidos)",
               "fidelidade_percentual": "fidelidade (%)", "preparo_minutos": "tempo de preparo", "mesas": "mesas",
               "agendamento": "agendamento", "agend_minimo": "antecedência do agendamento", "agend_liberar": "liberação p/ cozinha"}
    mud = []
    for k, rot in rotulos.items():
        if antes.get(k) != cfg.get(k):
            if k in ("taxa_entrega", "pedido_minimo"):
                mud.append(f"{rot} {_brl(antes[k])} → {_brl(cfg[k])}")
            elif k in ("dias", "bairros", "dono_telefone"):
                mud.append(rot)
            else:
                mud.append(f"{rot} {antes.get(k)} → {cfg.get(k)}")
    if mud:
        registrar(con, "loja", "Configuração da loja: " + "; ".join(mud))
    return cfg


def _minutos(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def loja_aberta(con, agora=None):
    """(aberta, mensagem). Janelas que viram a noite (ex.: 18:00 às 02:00) são tratadas."""
    cfg = obter_config_loja(con)
    if cfg["modo"] == "fechado":
        return False, "Estamos fechados no momento."
    if cfg["modo"] == "sempre":
        return True, ""
    agora = agora or datetime.now(FUSO)
    agora_min = agora.hour * 60 + agora.minute
    dia = agora.weekday()
    hoje, ontem = cfg["dias"][dia], cfg["dias"][(dia - 1) % 7]

    if ontem["aberto"] and _minutos(ontem["fecha"]) < _minutos(ontem["abre"]) \
            and agora_min < _minutos(ontem["fecha"]):
        return True, ""                                   # ainda é a madrugada do expediente de ontem
    if hoje["aberto"]:
        a, f = _minutos(hoje["abre"]), _minutos(hoje["fecha"])
        if (a < f and a <= agora_min < f) or (f < a and agora_min >= a):
            return True, ""
        if agora_min < a:
            return False, f"Estamos fechados. Abrimos hoje às {hoje['abre']}."
    for passo in range(1, 8):
        prox = cfg["dias"][(dia + passo) % 7]
        if prox["aberto"]:
            quando = "amanhã" if passo == 1 else f"na {DIAS_SEMANA[(dia + passo) % 7]}"
            return False, f"Estamos fechados. Abrimos {quando} às {prox['abre']}."
    return False, "Estamos fechados no momento."


_DIAS_CURTOS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]


def horarios_agendamento(con, agora=None):
    """Horários que o cliente pode escolher (de 30 em 30 min, nas próximas 48 h, só com a loja aberta).
    Devolve [{'valor': '2026-11-03T20:00' (hora local), 'rotulo': 'Hoje 20:00'}]. Gerado SEMPRE no servidor:
    é ele quem confere, na hora do pedido, se o horário enviado está nesta lista."""
    cfg = obter_config_loja(con)
    if not cfg["agendamento"]:
        return []
    agora = (agora or datetime.now(FUSO)).replace(second=0, microsecond=0)
    inicio = agora + timedelta(minutes=cfg["agend_minimo"])
    inicio += timedelta(minutes=(30 - inicio.minute % 30) % 30)       # arredonda para :00 ou :30
    hoje = agora.date()
    saida = []
    for i in range(96):
        h = inicio + timedelta(minutes=30 * i)
        if h > agora + timedelta(hours=48):
            break
        if not loja_aberta(con, h)[0]:
            continue
        dia = "Hoje" if h.date() == hoje else "Amanhã" if h.date() == hoje + timedelta(days=1) \
            else f"{_DIAS_CURTOS[h.weekday()]} {h:%d/%m}"
        saida.append({"valor": h.strftime("%Y-%m-%dT%H:%M"), "rotulo": f"{dia} {h:%H:%M}"})
    return saida


def _rotulo_agendamento(local):
    hoje = datetime.now(FUSO).date()
    dia = "hoje" if local.date() == hoje else "amanhã" if local.date() == hoje + timedelta(days=1) \
        else f"{_DIAS_CURTOS[local.weekday()]} {local:%d/%m}"
    return f"{dia} às {local:%H:%M}"


def estado_loja(con):
    """O que o cardápio do cliente precisa saber (público: sem dados internos)."""
    cfg = obter_config_loja(con)
    aberta, aviso = loja_aberta(con)
    return {"aberta": aberta, "aviso": aviso,
            "taxa_entrega": cfg["taxa_entrega"] / 100.0, "pedido_minimo": cfg["pedido_minimo"] / 100.0,
            "bairros_modo": cfg["bairros_modo"],
            "bairros": [{"nome": b["nome"], "taxa": b["taxa"] / 100.0} for b in cfg["bairros"]],
            "agendamento": {"ativo": cfg["agendamento"], "horarios": horarios_agendamento(con)},
            "preparo": estimativa_preparo(con), "mesas": cfg["mesas"],
            "fidelidade": {"ativo": bool(cfg["fidelidade_pedidos"]), "pedidos": cfg["fidelidade_pedidos"],
                           "percentual": cfg["fidelidade_percentual"]},
            "pagamentos": [{"id": k, "nome": v} for k, v in PAGAMENTOS.items()]}


def config_loja_painel(con):
    """Configuração completa para a tela do administrador (valores em reais)."""
    cfg = obter_config_loja(con)
    aberta, aviso = loja_aberta(con)
    return {"modo": cfg["modo"], "dias": cfg["dias"], "dias_nomes": DIAS_SEMANA,
            "taxa_entrega": cfg["taxa_entrega"] / 100.0, "pedido_minimo": cfg["pedido_minimo"] / 100.0,
            "bairros_modo": cfg["bairros_modo"],
            "bairros_texto": "\n".join(f"{b['nome']}; {b['taxa'] / 100:.2f}".replace(".", ",") for b in cfg["bairros"]),
            "dono_telefone": cfg["dono_telefone"], "atraso_minutos": cfg["atraso_minutos"],
            "preparo_minutos": cfg["preparo_minutos"], "mesas": cfg["mesas"], "relatorio_hora": cfg["relatorio_hora"],
            "fidelidade_pedidos": cfg["fidelidade_pedidos"], "fidelidade_percentual": cfg["fidelidade_percentual"],
            "agendamento": cfg["agendamento"], "agend_minimo": cfg["agend_minimo"], "agend_liberar": cfg["agend_liberar"],
            "aberta_agora": aberta, "aviso": aviso}


# ------------------------------------------------ Manutenção de pedidos e dados ----

_BASE_TEMPO = "COALESCE(NULLIF(liberar_em, ''), criado)"   # quando o pedido passou a valer para a cozinha


def contar_pedidos_antigos(con, horas=12):
    corte = (datetime.now(timezone.utc) - timedelta(hours=horas)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return con.execute(
        f"SELECT COUNT(*) FROM pedidos WHERE status IN ('novo','fazendo','pronto','saiu') AND {_BASE_TEMPO} < ?",
        (corte,)).fetchone()[0]


def encerrar_pedidos_antigos(con, destino, horas=12):
    """Fecha pedidos esquecidos na fila. NÃO avisa os clientes (são pedidos velhos)."""
    if destino not in ("concluido", "cancelado"):
        raise ErroNegocio("Destino inválido.")
    if not isinstance(horas, int) or isinstance(horas, bool) or not (1 <= horas <= 24 * 30):
        raise ErroNegocio("Prazo inválido.")
    corte = (datetime.now(timezone.utc) - timedelta(hours=horas)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with transacao(con):
        alvo = [r["senha"] for r in con.execute(
            f"SELECT senha FROM pedidos WHERE status IN ('novo','fazendo','pronto','saiu') AND {_BASE_TEMPO} < ?", (corte,))]
        for senha in alvo:
            con.execute("UPDATE pedidos SET status = ?, atualizado = ? WHERE senha = ?", (destino, agora_iso(), senha))
            if destino == "cancelado":
                _ajustar_estoque(con, senha, +1)
    if alvo:
        registrar(con, "pedido", f"Encerrou {len(alvo)} pedido(s) antigo(s) como {destino}")
    return len(alvo)


def obter_pedido(con, senha):
    """Pedido com itens (para a comanda impressa)."""
    p = con.execute("SELECT * FROM pedidos WHERE senha = ?", (senha,)).fetchone()
    if not p:
        return None
    itens = con.execute(
        "SELECT nome, qtd, preco_centavos, opcoes FROM pedido_itens WHERE pedido_senha = ? ORDER BY id", (senha,)).fetchall()
    return {"pedido": p, "itens": itens, "hora_local": _local(p["criado"]),
            "agendado_local": _local(p["agendado_para"]) if p["agendado_para"] else None}


ETAPAS = [("novo", "Pedido recebido"), ("fazendo", "Em preparo"), ("pronto", "Pronto"),
          ("saiu", "Saiu para entrega"), ("concluido", "Entregue")]


def _arredondar_5(minutos):
    return int(min(180, max(5, -(-minutos // 5) * 5)))


def estimativa_preparo(con):
    """Tempo estimado para um pedido feito agora: média real dos últimos pedidos (ou o tempo informado
    pela loja enquanto há poucos dados) + 2 min por pedido já na fila. {'ativo', 'minutos', 'fila'}."""
    cfg = obter_config_loja(con)
    base = cfg["preparo_minutos"]
    if not base:
        return {"ativo": False, "minutos": 0, "fila": 0}
    desde = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
    duracoes = []
    for r in con.execute(f"SELECT {_BASE_TEMPO} AS ini, pronto_em FROM pedidos WHERE pronto_em <> '' AND criado >= ? "
                         "ORDER BY senha DESC LIMIT 40", (desde,)):
        try:
            m = (datetime.strptime(r["pronto_em"], "%Y-%m-%dT%H:%M:%SZ") - datetime.strptime(r["ini"], "%Y-%m-%dT%H:%M:%SZ")).total_seconds() / 60
        except ValueError:
            continue
        if 1 <= m <= 180:
            duracoes.append(m)
    tempo = sum(duracoes) / len(duracoes) if len(duracoes) >= 5 else base
    agora = agora_iso()
    fila = con.execute(f"SELECT COUNT(*) FROM pedidos WHERE status IN ('novo','fazendo') AND {_BASE_TEMPO} <= ?",
                       (agora,)).fetchone()[0]
    return {"ativo": True, "minutos": _arredondar_5(tempo + max(0, fila - 1) * 2), "fila": fila}


def _previsao_do_pedido(con, p):
    """Minutos que faltam para o pedido ficar pronto (só enquanto está novo ou fazendo e já liberado)."""
    if p["status"] not in ("novo", "fazendo"):
        return None
    est = estimativa_preparo(con)
    if not est["ativo"]:
        return None
    inicio = p["liberar_em"] or p["criado"]
    agora = agora_iso()
    if inicio > agora:
        return None
    decorrido = (datetime.strptime(agora, "%Y-%m-%dT%H:%M:%SZ") - datetime.strptime(inicio, "%Y-%m-%dT%H:%M:%SZ")).total_seconds() / 60
    antes = con.execute(f"SELECT COUNT(*) FROM pedidos WHERE status IN ('novo','fazendo') AND senha < ? AND {_BASE_TEMPO} <= ?",
                        (p["senha"], agora)).fetchone()[0]
    base = est["minutos"] - max(0, est["fila"] - 1) * 2 + antes * 2
    return _arredondar_5(base - decorrido)


def dados_acompanhamento(con, token):
    """Dados PÚBLICOS do pedido para a página de acompanhamento: sem nome, telefone nem endereço."""
    if not isinstance(token, str) or not (8 <= len(token) <= 40):
        return None
    p = con.execute("SELECT * FROM pedidos WHERE token = ?", (token,)).fetchone()
    if not p:
        return None
    itens = con.execute("SELECT nome, qtd, opcoes FROM pedido_itens WHERE pedido_senha = ? ORDER BY id",
                        (p["senha"],)).fetchall()
    etapas = [(k, v) for k, v in ETAPAS if p["tipo"] == "entrega" or k != "saiu"]
    if p["tipo"] != "entrega":
        etapas = [(k, "Retirado" if k == "concluido" else v) for k, v in etapas]
    return {
        "loja": nome_lanchonete(con), "senha": p["senha"], "tipo": p["tipo"], "mesa": p["mesa"], "status": p["status"],
        "total": p["total_centavos"] / 100.0, "hora": p["criado"], "agendado_para": p["agendado_para"],
        "desconto": p["desconto_centavos"] / 100.0, "cupom": p["cupom"], "fidelidade": p["fidelidade_centavos"] / 100.0,
        "nota": p["nota"], "comentario": p["comentario"],
        "pode_avaliar": p["status"] == "concluido" and p["nota"] is None,
        "previsao_min": _previsao_do_pedido(con, p),
        "etapas": [{"id": k, "nome": v} for k, v in etapas],
        "itens": [{"nome": i["nome"], "qtd": i["qtd"], "opcoes": i["opcoes"]} for i in itens],
    }


def verificar_atrasos(con, agora=None):
    """Avisa o dono (uma única vez por pedido) quando um pedido passa do tempo limite sem andar.
    Só olha pedidos das últimas 6 horas, para não mexer em pedidos esquecidos. Devolve quantos avisou."""
    cfg = obter_config_loja(con)
    limite = cfg["atraso_minutos"]
    if not limite:
        return 0
    agora = agora or datetime.now(timezone.utc)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    ate = (agora - timedelta(minutes=limite)).strftime(fmt)
    desde = (agora - timedelta(hours=6)).strftime(fmt)
    avisados = 0
    for p in con.execute(
            f"SELECT senha, status, tipo, {_BASE_TEMPO} AS base FROM pedidos WHERE status IN ('novo','fazendo') "
            f"AND atraso_avisado = 0 AND {_BASE_TEMPO} <= ? AND {_BASE_TEMPO} >= ?", (ate, desde)).fetchall():
        with transacao(con):   # só quem conseguir marcar envia (vários processos não duplicam o aviso)
            marcou = con.execute("UPDATE pedidos SET atraso_avisado = 1 WHERE senha = ? AND atraso_avisado = 0",
                                 (p["senha"],)).rowcount
        if not marcou:
            continue
        avisados += 1
        if cfg["dono_telefone"]:
            minutos = int((agora - datetime.strptime(p["base"], fmt).replace(tzinfo=timezone.utc)).total_seconds() // 60)
            etapa = "ainda não começou" if p["status"] == "novo" else "ainda está em preparo"
            whatsapp.enviar_em_segundo_plano(
                cfg["dono_telefone"],
                f"⏰ *Pedido #{p['senha']} atrasado*\nFaz {minutos} min e {etapa}.")
    return avisados


def anonimizar_pedidos(con, dias):
    """LGPD: remove nome, telefone, endereço e observação de pedidos ENCERRADOS com mais de N dias.
    Os totais e itens ficam (o financeiro continua certo)."""
    if dias < 30:
        raise ErroNegocio("Por segurança, o prazo mínimo é de 30 dias.")
    corte = (datetime.now(timezone.utc) - timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with transacao(con):
        return con.execute(
            """UPDATE pedidos SET nome = '[removido]', telefone = '[removido]',
                      endereco = CASE WHEN tipo = 'entrega' THEN '[removido]' ELSE '' END,
                      rua = '', numero = '', complemento = '', referencia = '',
                      bairro = CASE WHEN tipo = 'entrega' THEN bairro ELSE '' END, observacao = ''
               WHERE status IN ('concluido','cancelado') AND criado < ? AND nome <> '[removido]'""",
            (corte,)).rowcount
