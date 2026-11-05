"""Carrega o arquivo .env (ao lado deste arquivo) para as variáveis de ambiente.

Assim as chaves ficam num lugar só e o sistema funciona igual com `python servir.py`,
`gunicorn`, `python manage.py` ou `python app.py`, sem precisar digitar `$env:...` toda vez.
Regras: linhas `NOME=valor`; `#` comenta; aspas ao redor do valor são removidas; valor vazio é ignorado;
uma variável que já existe no ambiente NÃO é sobrescrita (o que você definiu no terminal vence).
"""
import os
from pathlib import Path


def carregar(caminho=None):
    """Devolve quantas variáveis foram carregadas. Nunca levanta erro por causa do arquivo."""
    if os.environ.get("SEM_ENV") == "1":
        return 0
    arquivo = Path(caminho) if caminho else Path(__file__).resolve().parent / ".env"
    try:
        linhas = arquivo.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return 0
    carregadas = 0
    for linha in linhas:
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        nome, _, valor = linha.partition("=")
        nome, valor = nome.strip(), valor.strip()
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
            valor = valor[1:-1]
        if not nome.replace("_", "").isalnum() or not valor or nome in os.environ:
            continue
        os.environ[nome] = valor
        carregadas += 1
    return carregadas


carregar()
