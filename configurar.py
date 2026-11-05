"""Prepara o arquivo .env sozinho: cria, gera chaves aleatórias e limpa textos que atrapalham.
Pode rodar quantas vezes quiser: nunca troca uma chave que já existe."""
import re
import secrets
import sys
from pathlib import Path

pasta = Path(__file__).resolve().parent
env = pasta / ".env"
exemplo = pasta / ".env.example"

if not env.exists():
    env.write_text(exemplo.read_text(encoding="utf-8") if exemplo.exists() else "", encoding="utf-8")
    print("Criado o arquivo .env")

linhas = env.read_text(encoding="utf-8-sig").splitlines()
valores = {}
saida = []
for linha in linhas:
    m = re.match(r"^\s*(#\s*)?([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$", linha)
    if m:
        comentada, nome, valor = bool(m.group(1)), m.group(2), m.group(3)
        # tira explicações do tipo "valor   (use quando ...)"
        valor = re.sub(r"\s{2,}\(.*\)$", "", valor).strip()
        if valor.startswith("("):
            valor = ""
        if not comentada:
            valores[nome] = valor
            linha = f"{nome}={valor}"
        elif nome == "SECRET_KEY":
            # linha de exemplo comentada: vamos preencher mais abaixo
            continue
    saida.append(linha)

def garantir(nome):
    if not valores.get(nome):
        valores[nome] = secrets.token_urlsafe(32)
        saida[:] = [l for l in saida if not re.match(rf"^\s*{nome}\s*=\s*$", l)]
        saida.append(f"{nome}={valores[nome]}")
        print(f"Gerada a chave {nome}")

garantir("EVOLUTION_API_KEY")
garantir("SECRET_KEY")

# COOKIE_SECURE=1 só funciona em https; em http o login do painel não funcionaria
if valores.get("COOKIE_SECURE") == "1" and not valores.get("PUBLIC_URL", "").startswith("https://"):
    saida[:] = [("# " + l) if re.match(r"^\s*COOKIE_SECURE\s*=", l) else l for l in saida]
    print("COOKIE_SECURE desligado (o endereço ainda não é https)")

env.write_text("\n".join(saida) + "\n", encoding="utf-8")
print("Arquivo .env pronto.")
