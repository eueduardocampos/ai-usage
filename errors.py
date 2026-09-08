"""
errors.py — traduz excecoes de rede/API em frases que fazem sentido na tela.
Sem I/O. O detalhe tecnico (classe + mensagem original) vai num campo a
parte (last_error_detail) para depuracao; a UI mostra so' a frase.
"""

import json
import socket
import ssl
import urllib.error


def _sufixo_espera(retry_in):
    return f"; nova tentativa em {int(retry_in)}s" if retry_in else ""


def humanize(exc: BaseException, retry_in=None) -> tuple[str, str]:
    """(mensagem em português para a UI, detalhe técnico 'Tipo: texto')."""
    detail = f"{type(exc).__name__}: {exc}"
    espera = _sufixo_espera(retry_in)
    reason = getattr(exc, "reason", None)

    if isinstance(exc, urllib.error.HTTPError):
        code = exc.code
        if code in (401, 403):
            return f"A API recusou a credencial (HTTP {code}). Reconecte a conta.", detail
        if code == 429:
            return f"Limite de requisições da API (HTTP 429); aguardando{espera}.", detail
        if code >= 500:
            return f"A API da Anthropic está instável (HTTP {code}); tentando de novo{espera}.", detail
        return f"A API respondeu com erro HTTP {code}.", detail

    if isinstance(exc, (TimeoutError, socket.timeout)) or isinstance(reason, (TimeoutError, socket.timeout)):
        return f"A API demorou demais para responder (tempo esgotado){espera}.", detail
    if isinstance(exc, socket.gaierror) or isinstance(reason, socket.gaierror):
        return f"Sem conexão com a internet: não foi possível resolver o endereço da API{espera}.", detail
    if isinstance(exc, (ConnectionRefusedError, ConnectionResetError, BrokenPipeError)) \
            or isinstance(reason, (ConnectionRefusedError, ConnectionResetError, BrokenPipeError)):
        return f"A conexão com a API foi recusada ou interrompida{espera}.", detail
    if isinstance(exc, ssl.SSLError) or isinstance(reason, ssl.SSLError):
        return "Falha na conexão segura (TLS) com a API.", detail
    if isinstance(exc, json.JSONDecodeError):
        return "A API devolveu uma resposta inesperada.", detail
    if isinstance(exc, urllib.error.URLError):
        return f"Não foi possível alcançar a API{espera}.", detail
    return f"Falha ao consultar a API de uso{espera}.", detail
