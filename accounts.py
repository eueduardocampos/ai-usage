"""
accounts.py — varias contas no mesmo painel.

Cada conta do Claude tem o proprio token e o proprio estado de leitura (limite
de 5h, 7 dias e por modelo sao da CONTA). O consumo de tokens continua sendo
da MAQUINA: os registros do Claude Code nao dizem de qual conta veio cada uso.

A conta "principal" e' a que ja existia antes (token.json na pasta de dados);
ela continua usando o objeto Ctx do server.py como estado, entao Stream Deck,
app nativo e testes antigos seguem funcionando sem mudar nada.

O login de cada conta usa o mesmo PKCE de antes, mas com um "balcao" de logins
pendentes: o link pode ser aberto no navegador padrao ou copiado e colado em
outro perfil do Chrome. A volta cai em http://localhost:<callback_port>/callback
desta maquina e o `state` diz de qual conta era.
"""

import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse

import auth

PRINCIPAL = "principal"
CODEX = "codex"
LOGIN_TTL_S = 30 * 60
NOME_MAX = 40


class Runtime:
    """Estado de leitura de uma conta Claude (mesmos campos do Ctx)."""

    def __init__(self, token_store, aid=None):
        self.id = aid
        self.token_store = token_store
        self.auth_connected = False
        self.rate_limited = False
        self.rate_limit_hits = 0
        self.next_poll_at = 0.0
        self.last_poll = None
        self.last_error = None
        self.last_error_detail = None
        self.boot_retries = 0
        self.profile = None
        self.profile_ts = 0.0
        self.ignored_logged = False


def _limpa_nome(nome, padrao):
    nome = re.sub(r"\s+", " ", str(nome or "")).strip()[:NOME_MAX]
    return nome or padrao


class AccountManager:
    def __init__(self, data_dir, principal_token_dir, principal_runtime):
        self.data_dir = data_dir
        self.path = os.path.join(data_dir, "accounts.json")
        self._lock = threading.RLock()
        self.principal_token_dir = principal_token_dir
        self.principal_runtime = principal_runtime
        self.runtimes = {}
        self.accounts = []
        self._load()

    # --- persistencia -------------------------------------------------------

    def _defaults(self):
        return [
            {"id": PRINCIPAL, "provider": "claude", "name": "Claude 1", "app": None},
            {"id": CODEX, "provider": "codex", "name": "ChatGPT", "app": None},
        ]

    def _load(self):
        data = None
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        accs = (data or {}).get("accounts") if isinstance(data, dict) else None
        if not isinstance(accs, list) or not accs:
            accs = self._defaults()
        limpos, vistos = [], set()
        for a in accs:
            if not isinstance(a, dict) or a.get("provider") not in ("claude", "codex"):
                continue
            aid = str(a.get("id") or "")
            if not re.fullmatch(r"[a-z0-9-]{2,40}", aid) or aid in vistos:
                continue
            vistos.add(aid)
            limpos.append({"id": aid, "provider": a["provider"],
                           "name": _limpa_nome(a.get("name"), aid),
                           "app": a.get("app") if isinstance(a.get("app"), str) else None,
                           "created_at": a.get("created_at")})
        # a principal e o Codex local sempre existem (podem so' ser renomeados)
        if PRINCIPAL not in vistos:
            limpos.insert(0, self._defaults()[0])
        if CODEX not in vistos:
            limpos.append(self._defaults()[1])
        self.accounts = limpos
        for a in self.accounts:
            self._runtime_for(a)
        self.onboarded = bool((data or {}).get("onboarded")) if isinstance(data, dict) else False

    def save(self):
        with self._lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"accounts": self.accounts, "onboarded": self.onboarded},
                          f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)

    def _runtime_for(self, acc):
        if acc["provider"] != "claude":
            return None
        if acc["id"] == PRINCIPAL:
            self.runtimes[PRINCIPAL] = self.principal_runtime
            return self.principal_runtime
        if acc["id"] not in self.runtimes:
            tdir = os.path.join(self.data_dir, "accounts", acc["id"])
            store = auth.TokenStore(tdir)
            # contas extras nunca pegam o login do Claude Code desta maquina
            # (esse e' da principal); so' entram com login proprio
            store.allow_bootstrap = False
            self.runtimes[acc["id"]] = Runtime(store, acc["id"])
        return self.runtimes[acc["id"]]

    # --- consultas ----------------------------------------------------------

    def get(self, aid):
        for a in self.accounts:
            if a["id"] == aid:
                return a
        return None

    def claude_accounts(self):
        return [a for a in self.accounts if a["provider"] == "claude"]

    def runtime(self, aid):
        return self.runtimes.get(aid)

    # --- mudancas -----------------------------------------------------------

    def create(self, provider="claude", name=None):
        if provider != "claude":
            raise ValueError("por enquanto so' contas do Claude podem ser adicionadas")
        with self._lock:
            n = len(self.claude_accounts()) + 1
            aid = "c-" + secrets.token_hex(3)
            acc = {"id": aid, "provider": "claude",
                   "name": _limpa_nome(name, f"Claude {n}"), "app": None,
                   "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            # entra antes do Codex, depois das outras do Claude
            pos = max((i for i, a in enumerate(self.accounts) if a["provider"] == "claude"),
                      default=-1) + 1
            self.accounts.insert(pos, acc)
            self._runtime_for(acc)
            self.save()
            return acc

    def update(self, aid, changes):
        with self._lock:
            acc = self.get(aid)
            if not acc:
                raise KeyError(aid)
            if "name" in changes:
                acc["name"] = _limpa_nome(changes["name"], acc["name"])
            if "app" in changes:
                app = changes["app"]
                if app is not None and not (isinstance(app, str) and app_valido(app)):
                    raise ValueError("app precisa ser um aplicativo instalado")
                acc["app"] = app
            self.save()
            return acc

    def remove(self, aid):
        with self._lock:
            if aid in (PRINCIPAL, CODEX):
                raise ValueError("essa conta nao pode ser removida, so' desconectada")
            acc = self.get(aid)
            if not acc:
                raise KeyError(aid)
            self.accounts.remove(acc)
            rt = self.runtimes.pop(aid, None)
            if rt:
                rt.token_store.clear()
            self.save()

    def reorder(self, ids):
        with self._lock:
            pos = {aid: i for i, aid in enumerate(ids)}
            self.accounts.sort(key=lambda a: pos.get(a["id"], len(ids)))
            self.save()

    def disconnect(self, aid):
        rt = self.runtime(aid)
        if not rt:
            raise KeyError(aid)
        rt.token_store.clear()
        rt.auth_connected = False
        rt.last_error = "Conta desconectada."
        rt.next_poll_at = time.time() + 3600


# --- aplicativos -------------------------------------------------------------

def _apps_dirs():
    if sys.platform == "darwin":
        return ["/Applications", os.path.expanduser("~/Applications")]
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", "")
        return [os.path.join(base, "Programs"), os.environ.get("ProgramFiles", "")]
    return []


APP_PADRAO = re.compile(r"claude|chatgpt|codex|openai", re.IGNORECASE)


def list_apps():
    """Aplicativos de IA instalados (inclui copias do Parall e afins)."""
    out = []
    for d in _apps_dirs():
        try:
            nomes = sorted(os.listdir(d))
        except OSError:
            continue
        for n in nomes:
            if sys.platform == "darwin" and not n.endswith(".app"):
                continue
            if APP_PADRAO.search(n) and not re.search(r"handler|helper|uninstall", n, re.I):
                out.append({"name": n[:-4] if n.endswith(".app") else n,
                            "path": os.path.join(d, n)})
    return out


def app_valido(path):
    return any(a["path"] == path for a in list_apps())


def open_app(path):
    """Abre o app ou traz para a frente se ja estiver aberto."""
    if not app_valido(path):
        raise ValueError("aplicativo nao encontrado")
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-a", path])
    elif os.name == "nt":
        os.startfile(path)  # noqa: S606 — caminho validado contra a lista acima
    else:
        raise ValueError("abrir aplicativo nao suportado neste sistema")


# --- balcao de logins --------------------------------------------------------

class LoginBroker:
    """Varios logins pendentes ao mesmo tempo, um por conta, no mesmo
    servidor de callback (porta fixa, que e' o que o OAuth aceita)."""

    def __init__(self, port, on_success, log=print):
        self.port = port
        self.on_success = on_success
        self.log = log
        self.pending = {}   # state -> {account, verifier, redirect, ts}
        self.status = {}    # account -> {"state": "waiting"|"ok"|"error", "msg"}
        self._lock = threading.Lock()
        self._server = None

    def _ensure_server(self):
        if self._server:
            return
        import http.server
        broker = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != "/callback":
                    self.send_response(404)
                    self.end_headers()
                    return
                qs = urllib.parse.parse_qs(parsed.query)
                ok, msg = broker._finish((qs.get("state") or [None])[0],
                                         (qs.get("code") or [None])[0],
                                         (qs.get("error") or [None])[0])
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                script = ("<script>setTimeout(function(){window.close()},1200)</script>"
                          if ok else "")
                self.wfile.write(
                    ("<html><body style='font-family:-apple-system,sans-serif;padding:48px;"
                     "background:#0b0d12;color:#f2f2f2'>"
                     f"<h2>{msg}</h2><p style='opacity:.6'>Pode fechar esta aba e voltar ao painel.</p>"
                     f"{script}</body></html>").encode("utf-8"))

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def start(self, account_id, token_store):
        with self._lock:
            agora = time.time()
            for st, p in list(self.pending.items()):
                if agora - p["ts"] > LOGIN_TTL_S or p["account"] == account_id:
                    self.pending.pop(st, None)
            verifier, challenge = auth.make_pkce()
            state = secrets.token_urlsafe(24)
            redirect = f"http://localhost:{self.port}/callback"
            self.pending[state] = {"account": account_id, "verifier": verifier,
                                   "redirect": redirect, "ts": agora, "store": token_store}
            self.status[account_id] = {"state": "waiting", "msg": None, "ts": agora}
        self._ensure_server()
        return auth.build_authorize_url(redirect, challenge, state)

    def _finish(self, state, code, error):
        with self._lock:
            p = self.pending.pop(state, None) if state else None
        if not p:
            return False, "Este link de login expirou ou ja foi usado. Gere outro no painel."
        aid = p["account"]
        if error or not code:
            self.status[aid] = {"state": "error", "msg": f"Login recusado: {error}", "ts": time.time()}
            return False, "O login foi cancelado."
        try:
            new = auth.exchange_code(code, p["verifier"], p["redirect"], state=state)
            p["store"].save(new["access_token"], new["refresh_token"], new["expires_at"],
                            source="login")
        except Exception as e:  # noqa: BLE001
            self.status[aid] = {"state": "error", "msg": "Nao foi possivel concluir o login.",
                                "ts": time.time()}
            self.log(f"[login] {aid}: troca do codigo falhou: {type(e).__name__}")
            return False, "Nao foi possivel concluir o login. Tente de novo pelo painel."
        self.status[aid] = {"state": "ok", "msg": None, "ts": time.time()}
        self.log(f"[login] conta {aid} conectada")
        threading.Thread(target=self.on_success, args=(aid,), daemon=True).start()
        return True, "Conta conectada."

    def state_of(self, aid):
        return self.status.get(aid)
