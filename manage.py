"""Gerenciamento de usuários do painel (rode na pasta do projeto).

    python manage.py listar
    python manage.py criar-usuario maria --papel cozinha
    python manage.py trocar-senha admin
    python manage.py desativar maria
    python manage.py ativar maria
    python manage.py desbloquear       (zera o bloqueio de tentativas de login)
    python manage.py backup            (cópia segura do banco + fotos, mesmo com o site no ar)
    python manage.py backup --manter 30   (guarda só os 30 backups mais novos; padrão 14)
    python manage.py anonimizar --dias 180   (LGPD: apaga nome/telefone/endereço de pedidos antigos)

Papéis:  admin   = tudo (cardápio, fotos, pedidos)
         cozinha = só a aba Pedidos
"""
import argparse
import getpass
import shutil
import sqlite3
import sys
from datetime import datetime

import db
import imagens
import seguranca
from db import ErroNegocio


def pedir_senha(usuario):
    senha = getpass.getpass("Nova senha (não aparece enquanto digita): ")
    if senha != getpass.getpass("Repita a senha: "):
        raise ErroNegocio("As senhas não são iguais.")
    seguranca.validar_senha(senha, usuario)
    return senha


def main():
    ap = argparse.ArgumentParser(description="Usuários do painel da lanchonete")
    sub = ap.add_subparsers(dest="comando", required=True)
    sub.add_parser("listar")
    b = sub.add_parser("backup")
    b.add_argument("--manter", type=int, default=14, help="quantos backups guardar (padrão 14)")
    an = sub.add_parser("anonimizar")
    an.add_argument("--dias", type=int, required=True, help="pedidos encerrados há mais de N dias (mínimo 30)")
    sub.add_parser("desbloquear")
    c = sub.add_parser("criar-usuario")
    c.add_argument("usuario")
    c.add_argument("--papel", choices=db.PAPEIS, default="cozinha")
    for nome in ("trocar-senha", "desativar", "ativar"):
        sub.add_parser(nome).add_argument("usuario")
    args = ap.parse_args()

    db.iniciar_banco()
    con = db.conectar()
    try:
        if args.comando == "listar":
            for u in db.listar_usuarios(con):
                print(f"{u['usuario']:<20} {u['papel']:<8} {'ativo' if u['ativo'] else 'DESATIVADO'}")
            return
        if args.comando == "desbloquear":
            print(f"Bloqueios de login removidos ({db.desbloquear_logins(con)} registros).")
            return
        if args.comando == "anonimizar":
            n = db.anonimizar_pedidos(con, args.dias)
            print(f"{n} pedido(s) anonimizado(s). Os totais e os itens foram mantidos.")
            return
        if args.comando == "backup":
            # API de backup do SQLite: cópia consistente mesmo com o site em uso
            # (copiar o arquivo .db "no braço" pode pegar o banco no meio de uma gravação).
            pasta = db.DB_PATH.parent / "backups"
            pasta.mkdir(parents=True, exist_ok=True)
            carimbo = f"{datetime.now():%Y%m%d-%H%M%S}"
            arquivo = pasta / f"cardapio-{carimbo}.db"
            copia = sqlite3.connect(arquivo)
            with copia:
                con.backup(copia)
            copia.close()
            fotos = ""
            if imagens.UPLOADS.is_dir():
                fotos = shutil.make_archive(str(pasta / f"fotos-{carimbo}"), "zip", imagens.UPLOADS)
            # mantém só os mais novos (o nome tem a data, então a ordem alfabética é a cronológica)
            for padrao in ("cardapio-*.db", "fotos-*.zip"):
                for velho in sorted(pasta.glob(padrao))[:-max(args.manter, 1)]:
                    velho.unlink(missing_ok=True)
            print(f"Backup do banco salvo em: {arquivo}")
            if fotos:
                print(f"Backup das fotos salvo em: {fotos}")
            print(f"Guardando os {args.manter} mais recentes. Copie a pasta {pasta} para outro lugar (pendrive/nuvem).")
            return
        usuario = args.usuario.strip().lower()
        if args.comando == "criar-usuario":
            db.criar_usuario(con, usuario, seguranca.hash_senha(pedir_senha(usuario)), args.papel)
            print(f"Usuário '{usuario}' criado com o papel {args.papel}.")
        elif args.comando == "trocar-senha":
            if db.obter_usuario_por_nome(con, usuario) is None:
                raise ErroNegocio("Usuário não encontrado.")
            db.trocar_senha_hash(con, usuario, seguranca.hash_senha(pedir_senha(usuario)))
            print("Senha trocada. Quem estava logado com ela precisará entrar de novo.")
        else:
            db.definir_usuario_ativo(con, usuario, args.comando == "ativar")
            print(f"Usuário '{usuario}' {'ativado' if args.comando == 'ativar' else 'desativado'}.")
    except ErroNegocio as erro:
        sys.exit(f"Erro: {erro.mensagem}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
