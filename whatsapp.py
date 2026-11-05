"""Envio de mensagens pelo WhatsApp via Evolution API.

Configuração por variáveis de ambiente (nada de chave escrita no código):

    EVOLUTION_API_KEY    chave da API (obrigatória; sem ela nenhuma mensagem é enviada)
    EVOLUTION_BASE_URL   padrão http://127.0.0.1:8080
    EVOLUTION_INSTANCE   nome da instância criada na Evolution, padrão "lanchonete"
    WHATSAPP_ATIVO       "0" desliga todos os envios (útil em testes)
"""
import logging
import os
import re
import threading

import requests

log = logging.getLogger("whatsapp")

TIMEOUT = 5


def _config():
    return {
        "base": os.environ.get("EVOLUTION_BASE_URL", "http://127.0.0.1:8080").rstrip("/"),
        "instancia": os.environ.get("EVOLUTION_INSTANCE", "lanchonete"),
        "chave": os.environ.get("EVOLUTION_API_KEY", "").strip(),
        "ativo": os.environ.get("WHATSAPP_ATIVO", "1") != "0",
    }


def formatar_numero(telefone):
    """Devolve DDI+DDD+número só com dígitos (ex.: 5575987654321) ou None se não parecer um
    celular/fixo brasileiro. Aceita com ou sem o 55, com máscara, espaços e zero na frente."""
    numeros = re.sub(r"\D", "", str(telefone or "")).lstrip("0")
    if len(numeros) in (10, 11):            # DDD + número
        return "55" + numeros
    if len(numeros) in (12, 13) and numeros.startswith("55"):   # já vem com o DDI
        return numeros
    return None


def enviar_mensagem_whatsapp(numero, texto):
    """Envia UMA mensagem. Nunca levanta exceção (roda em thread). Devolve True/False."""
    cfg = _config()
    if not cfg["ativo"]:
        return False
    if not cfg["chave"]:
        log.warning("EVOLUTION_API_KEY não definida: mensagem não enviada.")
        return False
    destino = formatar_numero(numero)
    if not destino:
        log.warning("Telefone inválido, mensagem não enviada.")
        return False

    url = f"{cfg['base']}/message/sendText/{cfg['instancia']}"
    try:
        resp = requests.post(
            url,
            json={"number": destino, "text": texto},
            headers={"Content-Type": "application/json", "apikey": cfg["chave"]},
            timeout=TIMEOUT,
        )
    except requests.RequestException as erro:
        log.warning("Falha de conexão com a Evolution API: %s", erro)
        return False
    if not resp.ok:
        log.warning("Evolution API respondeu %s: %s", resp.status_code, resp.text[:200])
        return False
    return True


def enviar_em_segundo_plano(numero, texto):
    """Dispara o envio sem travar a requisição do cliente."""
    threading.Thread(target=enviar_mensagem_whatsapp, args=(numero, texto), daemon=True).start()


# ------------------------------------------------ estado da conexão (painel) ----
import time as _time

_cache_status = {"quando": 0.0, "valor": None}
CACHE_SEGUNDOS = 20


def _estado(estado, ok, mensagem):
    return {"estado": estado, "ok": ok, "mensagem": mensagem}


def status_conexao(forcar=False):
    """Pergunta à Evolution se o WhatsApp está conectado. Nunca levanta exceção.
    Resultado fica 20 s em memória, para o painel poder perguntar com frequência sem sobrecarregar."""
    cfg = _config()
    if not cfg["ativo"]:
        return _estado("desligado", False, "Avisos por WhatsApp estão desligados (WHATSAPP_ATIVO=0).")
    if not cfg["chave"]:
        return _estado("sem_chave", False, "Chave da Evolution não configurada (EVOLUTION_API_KEY). Nenhum aviso está sendo enviado.")
    agora = _time.monotonic()
    if not forcar and _cache_status["valor"] and agora - _cache_status["quando"] < CACHE_SEGUNDOS:
        return _cache_status["valor"]

    url = f"{cfg['base']}/instance/connectionState/{cfg['instancia']}"
    try:
        resp = requests.get(url, headers={"apikey": cfg["chave"]}, timeout=3)
    except requests.RequestException:
        resultado = _estado("fora_do_ar", False, "A ponte do WhatsApp não está respondendo. Abra o iniciar_ponte_whatsapp.bat (ou ligue o Docker, se usa a Evolution).")
    else:
        if resp.status_code in (401, 403):
            resultado = _estado("chave_invalida", False, "A Evolution recusou a chave. Confira se EVOLUTION_API_KEY é a mesma usada pela ponte/Evolution.")
        elif resp.status_code == 404:
            resultado = _estado("sem_instancia", False, f"A instância \"{cfg['instancia']}\" não existe na Evolution.")
        elif not resp.ok:
            resultado = _estado("erro", False, f"A Evolution respondeu com erro {resp.status_code}.")
        else:
            try:
                dados = resp.json()
                estado = (dados.get("instance") or {}).get("state") or dados.get("state") or ""
            except ValueError:
                estado = ""
            if estado == "open":
                resultado = _estado("conectado", True, "WhatsApp conectado.")
            else:
                resultado = _estado("desconectado", False, "O WhatsApp está desconectado da Evolution. Leia o QR code de novo no painel da Evolution.")
    _cache_status.update(quando=agora, valor=resultado)
    return resultado
