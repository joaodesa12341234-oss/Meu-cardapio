"""Servidor de produção para quem usa WINDOWS (o Gunicorn não roda no Windows).

    python servir.py

No Linux/Mac use o Gunicorn:  gunicorn wsgi:app -c gunicorn.conf.py
"""
import os

from waitress import serve

from wsgi import app

if __name__ == "__main__":
    host, porta = os.environ.get("HOST", "0.0.0.0"), int(os.environ.get("PORT", "8000"))
    print(f"Servidor de produção em http://{host}:{porta}  (Ctrl+C para parar)")
    serve(app, host=host, port=porta, threads=int(os.environ.get("THREADS", "8")))
