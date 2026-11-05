"""Senhas, usuário inicial e chave secreta das sessões."""
import os
import secrets
import sys

from werkzeug.security import check_password_hash, generate_password_hash

import db
from db import ErroNegocio

SENHA_MIN = 10
SENHA_MAX = 128
SENHAS_PROIBIDAS = {"troque-esta-senha", "1234567890", "senha12345", "admin12345", "password123"}

# Hash de uma senha aleatória: usado quando o usuário não existe, para que o tempo da
# resposta seja o mesmo e ninguém consiga descobrir quais usuários existem.
_HASH_FALSO = generate_password_hash(secrets.token_hex(16))


def hash_senha(senha):
    return generate_password_hash(senha)  # scrypt, com sal aleatório


def conferir_senha(hash_guardado, senha):
    if not hash_guardado:
        check_password_hash(_HASH_FALSO, senha)
        return False
    return check_password_hash(hash_guardado, senha)


def validar_senha(senha, usuario=""):
    """Levanta ErroNegocio se a senha for fraca."""
    if len(senha) < SENHA_MIN:
        raise ErroNegocio(f"A senha precisa ter pelo menos {SENHA_MIN} caracteres.")
    if len(senha) > SENHA_MAX:
        raise ErroNegocio(f"A senha pode ter no máximo {SENHA_MAX} caracteres.")
    if senha.lower() in SENHAS_PROIBIDAS or senha.lower() == usuario.lower():
        raise ErroNegocio("Essa senha é fácil demais de adivinhar. Escolha outra.")
    if len(set(senha)) < 4:
        raise ErroNegocio("Use uma senha com mais variedade de caracteres.")


def chave_secreta(con):
    """
    Chave que assina a sessão. Em produção, EXIGE a variável de ambiente SECRET_KEY.
    Em desenvolvimento local, gera e reutiliza uma chave temporária no banco de dados.
    """
    do_ambiente = os.environ.get("SECRET_KEY", "").strip()
    if do_ambiente:
        if len(do_ambiente) < 32:
            raise RuntimeError("SECRET_KEY do ambiente precisa ter pelo menos 32 caracteres.")
        return do_ambiente

    # Notificação no terminal para ambientes sem SECRET_KEY definida
    print("[AVISO SEGURANÇA] SECRET_KEY não definida nas variáveis de ambiente. Usando chave armazenada na base local.", file=sys.stderr)
    
    con.execute(
        "INSERT OR IGNORE INTO config (chave, valor) VALUES ('secret_key', ?)",
        (secrets.token_hex(32),)
    )
    return con.execute("SELECT valor FROM config WHERE chave = 'secret_key'").fetchone()["valor"]

def criar_admin_inicial(con):
    """Na primeira execução (banco sem usuários) cria o administrador.

    Usa ADMIN_USUARIO/ADMIN_SENHA do ambiente, se definidos e fortes; caso contrário
    gera uma senha aleatória. Devolve (usuario, senha_gerada_ou_None), ou None se já
    existia algum usuário.
    """
    with db.transacao(con):
        if db.contar_usuarios(con):
            return None
        usuario = (os.environ.get("ADMIN_USUARIO") or "admin").strip().lower()
        senha = os.environ.get("ADMIN_SENHA") or ""
        gerada = None
        if not senha or senha.lower() in SENHAS_PROIBIDAS:
            senha = gerada = secrets.token_urlsafe(12)
        else:
            try:
                validar_senha(senha, usuario)
            except ErroNegocio as erro:
                raise RuntimeError(f"ADMIN_SENHA recusada: {erro.mensagem}")
        db.criar_usuario(con, usuario, hash_senha(senha), "admin")
    return usuario, gerada


def avisar_senha_gerada(usuario, senha):
    linha = "=" * 62
    print(f"\n{linha}\n  PRIMEIRO ACESSO AO PAINEL (/admin)\n"
          f"  Usuário: {usuario}\n  Senha:   {senha}\n"
          f"  Anote agora: esta senha não será mostrada de novo.\n"
          f"  Para trocar:  python manage.py trocar-senha {usuario}\n{linha}\n",
          file=sys.stderr, flush=True)
