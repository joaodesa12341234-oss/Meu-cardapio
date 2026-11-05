"""Configuração do Gunicorn (servidor de produção para Linux/Mac).

Uso:  gunicorn wsgi:app -c gunicorn.conf.py
Tudo pode ser ajustado por variáveis de ambiente, sem editar este arquivo.
"""
import os

bind = f"{os.environ.get('HOST', '0.0.0.0')}:{os.environ.get('PORT', '8000')}"

# Poucos processos bastam: com SQLite as gravações são feitas uma por vez de qualquer jeito.
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
threads = int(os.environ.get("THREADS", "4"))
worker_class = "gthread"

timeout = 30
graceful_timeout = 30
keepalive = 5
max_requests = 2000          # recicla o processo de tempos em tempos (evita acúmulo de memória)
max_requests_jitter = 200
preload_app = True           # cria/migra o banco uma única vez, antes de abrir os processos

# Só confie em X-Forwarded-* se o Gunicorn estiver atrás do seu proxy (Nginx, Caddy...).
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")
