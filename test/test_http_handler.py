# -*- coding: utf-8 -*-
"""O servidor HTTP so' atende o navegador da propria maquina e nao aceita
configuracao invalida: Host estranho (DNS rebinding) e Origin de outro site
(CSRF) levam 403; /api/config valida tipo e faixa; NaN nao entra nem sai;
path traversal pelo web/dist e' barrado; erro interno nao vaza traceback."""
import json
import os
import socket
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server
import store as store_mod

falhas = []
def check(cond, label):
    print(("  PASS  " if cond else "  FALHA ") + label)
    if not cond: falhas.append(label)

tmp = tempfile.mkdtemp()
server.Ctx.cfg = dict(server.DEFAULTS)
server.CONFIG_PATH = os.path.join(tmp, "config.json")
server.Ctx.store = store_mod.Store(os.path.join(tmp, "painel.db"))
server.Ctx.codex = None
server.Ctx.db_path = os.path.join(tmp, "painel.db")
# web/dist de mentira com uma pasta irma "dist-x" (o bug antigo deixava passar)
web = os.path.join(tmp, "web"); os.makedirs(os.path.join(web, "dist")); os.makedirs(os.path.join(web, "dist-x"))
open(os.path.join(web, "dist", "index.html"), "w").write("<title>painel</title>")
open(os.path.join(web, "dist-x", "segredo.txt"), "w").write("nao era para ler")
server.WEB_DIST = os.path.join(web, "dist")

httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
port = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{port}"

def req(path, method="GET", body=None, headers=None, raw_path=False):
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    data = body.encode("utf-8") if isinstance(body, str) else body
    if raw_path:
        # urllib normaliza "/../"; manda o pedido cru pelo socket
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
        s.sendall(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n".encode())
        resp = b""
        while True:
            chunk = s.recv(65536)
            if not chunk: break
            resp += chunk
        s.close()
        code = int(resp.split(b" ", 2)[1])
        return code, resp.split(b"\r\n\r\n", 1)[1].decode("utf-8", "ignore")
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")

# --- Host / Origin -----------------------------------------------------------
code, _ = req("/api/health")
check(code == 200, "GET /api/health com Host local: 200")
code, body = req("/api/health", headers={"Host": "evil.example:80"})
check(code == 403 and "host" in body, "Host de fora (rebinding): 403")
code, body = req("/api/config", "POST", '{"intended_hours": 3}', headers={"Origin": "http://evil.example"})
check(code == 403 and "origin" in body, "POST com Origin de outro site: 403")
code, _ = req("/api/config", "POST", '{"intended_hours": 3}', headers={"Origin": f"http://127.0.0.1:{port}"})
check(code == 200 and server.Ctx.cfg["intended_hours"] == 3.0, "POST com Origin do proprio painel: 200 e aplicado")
code, _ = req("/api/config", "POST", '{"intended_hours": 2}')
check(code == 200 and server.Ctx.cfg["intended_hours"] == 2.0, "POST sem Origin (Stream Deck, curl): 200")
code, body = req("/auth/start", headers={"Origin": "http://evil.example"})
check(code == 403, "GET /auth/start com Origin de outro site: 403")

# --- validacao do /api/config -------------------------------------------------
antes = dict(server.Ctx.cfg)
code, body = req("/api/config", "POST", '{"intended_hours": "x"}')
check(code == 400 and server.Ctx.cfg["intended_hours"] == 2.0, "intended_hours texto: 400 e config intacto")
code, _ = req("/api/config", "POST", '{"intended_hours": 48}')
check(code == 400, "intended_hours fora da faixa: 400")
code, _ = req("/api/config", "POST", '{"subscription_brl": NaN}')
check(code == 400, "NaN no corpo: 400")
code, _ = req("/api/config", "POST", '{"subscription_brl": true}')
check(code == 400, "booleano no lugar de numero: 400")
code, _ = req("/api/config", "POST", '{"plan": "ouro"}')
check(code == 400, "plan desconhecido: 400")
code, _ = req("/api/config", "POST", '{"plan": null, "subscription_brl": null}')
check(code == 200 and server.Ctx.cfg["plan"] is None, "null limpa plan e mensalidade: 200")
code, _ = req("/api/config", "POST", '{"intended_hours": 1}', headers={"Content-Type": "text/plain"})
check(code == 415, "Content-Type text/plain: 415")
code, _ = req("/api/config", "POST", "{" + '"a":1,' * 20000 + '"b":2}')
check(code == 413, "corpo acima de 64 KB: 413")
code, _ = req("/api/config", "POST", '{"intended_hours": 1, "refresh_seconds": 5}')
check(code == 200 and server.Ctx.cfg["refresh_seconds"] == antes["refresh_seconds"], "chave desconhecida e' ignorada")
check(json.load(open(server.CONFIG_PATH))["intended_hours"] == 1, "config.json gravado com o valor validado")

# --- NaN nunca sai -----------------------------------------------------------
check(server._json({"a": float("nan"), "b": [1, float("inf")]}) == '{"a": null, "b": [1, null]}', "_json troca NaN/Infinity por null")

# --- traversal e erro interno ------------------------------------------------
code, body = req("/../dist-x/segredo.txt", raw_path=True)
check(code != 200 or "nao era para ler" not in body, "pasta irma 'dist-x' nao e' servida")
code, body = req("/index.html")
check(code == 200 and "painel" in body, "index.html do dist continua servido")
code, body = req("/api/state")
check(code == 200, "GET /api/state responde com store vazio")
store_bom = server.Ctx.store
server.Ctx.store = None
code, body = req("/api/total")
check(code == 500 and "Traceback" not in body and "interno" in body, "erro interno: 500 sem traceback")
server.Ctx.store = store_bom

httpd.shutdown()
print("\nTUDO PASSOU" if not falhas else f"\n{len(falhas)} falha(s)")
sys.exit(1 if falhas else 0)
