# -*- coding: utf-8 -*-
"""Prova que errors.humanize() transforma excecoes cruas em frases legiveis,
guardando o detalhe tecnico a parte."""
import json, os, socket, ssl, sys, urllib.error
sys.path.insert(0, os.getcwd())
import errors

falhas = []
def check(cond, label):
    print(("  PASS  " if cond else "  FALHA ") + label)
    if not cond: falhas.append(label)

def http(code):
    return urllib.error.HTTPError("https://api.anthropic.com/x", code, "msg", {}, None)

m, d = errors.humanize(urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided, or not known")))
check("Sem conexão" in m and "<urlopen" not in m, f"DNS caido vira frase: {m}")
check(d.startswith("URLError:"), f"detalhe tecnico preservado: {d[:40]}")

m, _ = errors.humanize(http(401)); check("credencial" in m and "401" in m, f"401: {m}")
m, _ = errors.humanize(http(403)); check("credencial" in m, f"403: {m}")
m, _ = errors.humanize(http(429), retry_in=90); check("429" in m and "90s" in m, f"429 com espera: {m}")
m, _ = errors.humanize(http(503)); check("instável" in m and "503" in m, f"503: {m}")
m, _ = errors.humanize(http(418)); check("HTTP 418" in m, f"outro HTTP: {m}")
m, _ = errors.humanize(TimeoutError()); check("demorou" in m, f"timeout direto: {m}")
m, _ = errors.humanize(urllib.error.URLError(TimeoutError())); check("demorou" in m, f"timeout em URLError: {m}")
m, _ = errors.humanize(ConnectionRefusedError()); check("recusada" in m, f"conexao recusada: {m}")
m, _ = errors.humanize(ssl.SSLError()); check("TLS" in m, f"ssl: {m}")
m, _ = errors.humanize(json.JSONDecodeError("x", "doc", 0)); check("inesperada" in m, f"json: {m}")
m, _ = errors.humanize(urllib.error.URLError("qualquer coisa")); check("alcançar" in m, f"URLError generico: {m}")
m, d = errors.humanize(RuntimeError("boom"), retry_in=15)
check("Falha ao consultar" in m and "15s" in m, f"fallback com espera: {m}")
check(d == "RuntimeError: boom", "detalhe = Tipo: mensagem")
check(all(("—" not in errors.humanize(e)[0]) for e in (http(500), TimeoutError(), RuntimeError())),
      "frases sem travessão")

print()
print(("%d FALHA(S)" % len(falhas)) if falhas else "TUDO PASSOU")
sys.exit(1 if falhas else 0)
