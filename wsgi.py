"""Ponto de entrada para servidores de produção:  gunicorn wsgi:app -c gunicorn.conf.py"""
from app import app

__all__ = ["app"]
