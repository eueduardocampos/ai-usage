"""
auth.py — gerencia o token OAuth do painel, de forma independente do Claude Desktop.

Arvore de decisao em get_valid_token():
  1. token proprio valido (margem de 120s)              -> usa
  2. renova via refresh_token do token proprio          -> salva e usa
  3. bootstrap: renova usando o refreshToken do          -> salva e usa
     ~/.claude/.credentials.json (primeira vez)
  4. login PKCE ("Conectar conta")                       -> salva e usa

NUNCA loga o valor do token (usa mask()).
NUNCA reescreve ~/.claude/.credentials.json.
"""

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
import urllib.request
import urllib.error

# --- Constantes OAuth do Claude Code (algumas a CONFIRMAR no gate) -----------
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"  # client_id publico do Claude Code
TOKEN_URL = "https://api.anthropic.com/v1/oauth/token"  # confirmado no gate (console.* da 429)
AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
DEFAULT_SCOPES = ("user:file_upload user:inference user:mcp_servers "
                  "user:profile user:sessions:claude_code")

HOME = os.path.expanduser("~")
CLAUDE_CREDS_PATH = os.path.join(HOME, ".claude", ".credentials.json")

EXPIRY_MARGIN_S = 120  # renova com 2 min de folga


# --- utilidades --------------------------------------------------------------

def mask(token: str) -> str:
    """Mascara o token para log: prefixo + tamanho, nunca o valor."""
    if not token:
        return "<vazio>"
    return f"{token[:14]}...len={len(token)}"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def protege_arquivo(path: str):
    """So' o dono le o arquivo. No POSIX, chmod 600. No Windows, chmod nao faz
    nada: e' preciso tirar a heranca da ACL e dar acesso so' ao usuario atual,
    senao qualquer conta local da maquina le o token."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    if os.name != "nt":
        return
    usuario = os.environ.get("USERNAME")
    if not usuario:
        return
    try:
        import subprocess
        subprocess.run(["icacls", path, "/inheritance:r", "/grant:r", f"{usuario}:F"],
                       check=False, capture_output=True, timeout=15,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        pass


def _write_secure_json(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)
    protege_arquivo(path)


# --- store proprio do token --------------------------------------------------

class TokenStore:
    def __init__(self, token_dir: str):
        self.path = os.path.join(token_dir, "token.json")

    def load(self):
        return _read_json(self.path)

    def save(self, access_token, refresh_token, expires_at_ms, source):
        _write_secure_json(self.path, {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at_ms,
            "obtained_at": _now_ms(),
            "source": source,
        })

    def clear(self):
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass


# --- chamadas OAuth ----------------------------------------------------------

def _post_token(payload: dict) -> dict:
    """POST no endpoint de token. Levanta urllib.error.HTTPError em falha."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8")
        except Exception:
            body = ""
        # repassa o codigo, anexando o corpo (sem segredos) para diagnostico
        raise urllib.error.HTTPError(e.url, e.code,
                                     f"{e.reason} | corpo: {body}",
                                     e.headers, None)


def _normalize_token_response(data: dict) -> dict:
    """Converte a resposta OAuth no formato do nosso store."""
    expires_in = data.get("expires_in")
    if expires_in is not None:
        expires_at_ms = _now_ms() + int(expires_in) * 1000
    else:
        # alguns retornos trazem expires_at em segundos; fallback conservador
        expires_at_ms = _now_ms() + 3600 * 1000
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),  # pode nao rotacionar
        "expires_at": expires_at_ms,
    }


def refresh_with(refresh_token: str) -> dict:
    """Renova usando um refresh_token. Retorna dict normalizado ou levanta."""
    data = _post_token({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": CLIENT_ID,
    })
    return _normalize_token_response(data)


def _claude_file_refresh_token():
    creds = _read_json(CLAUDE_CREDS_PATH)
    if not creds:
        return None
    return (creds.get("claudeAiOauth") or {}).get("refreshToken")


# --- PKCE login ("Conectar conta") -------------------------------------------

def make_pkce():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


def build_authorize_url(redirect_uri: str, code_challenge: str, state: str,
                        scopes: str = DEFAULT_SCOPES) -> str:
    params = {
        "code": "true",
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)


def exchange_code(code: str, code_verifier: str, redirect_uri: str,
                  state: str = None) -> dict:
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": CLIENT_ID,
        "code_verifier": code_verifier,
    }
    if state:
        payload["state"] = state  # o exchange do Claude Code exige o state
    return _normalize_token_response(_post_token(payload))


# --- orquestracao ------------------------------------------------------------

class TransientAuthError(Exception):
    """Falha TEMPORARIA ao renovar o token (429 ou 5xx).

    Separada da AuthError de proposito: AuthError significa "precisa de login
    novo"; esta aqui significa "tenta de novo daqui a pouco". Confundir as duas
    faz o painel pedir reconexao no meio de um rate limit — e reconectar gera
    mais requisicoes, que e exatamente o que nao se deve fazer nessa hora."""


class AuthError(Exception):
    pass


def get_valid_token(store: TokenStore, allow_bootstrap=True, log=print) -> str:
    """Retorna um access_token valido seguindo a arvore de decisao.
    Levanta AuthError se precisar de login interativo (passo 4)."""
    own = store.load()

    # 1. token proprio ainda valido?
    if own and own.get("access_token"):
        if own.get("expires_at", 0) - _now_ms() > EXPIRY_MARGIN_S * 1000:
            return own["access_token"]
        # 2. expirando: tenta renovar com o refresh proprio
        if own.get("refresh_token"):
            try:
                new = refresh_with(own["refresh_token"])
                store.save(new["access_token"],
                           new["refresh_token"] or own["refresh_token"],
                           new["expires_at"], source="refresh")
                log(f"[auth] token renovado (proprio): {mask(new['access_token'])}")
                return new["access_token"]
            except urllib.error.HTTPError as e:
                log(f"[auth] refresh proprio falhou: HTTP {e.code}")
                if e.code == 429 or e.code >= 500:
                    raise TransientAuthError(f"HTTP {e.code} ao renovar") from e

    # 3. bootstrap a partir do arquivo do Claude
    if allow_bootstrap:
        file_rt = _claude_file_refresh_token()
        if file_rt:
            try:
                new = refresh_with(file_rt)
                store.save(new["access_token"], new["refresh_token"] or file_rt,
                           new["expires_at"], source="bootstrap")
                log(f"[auth] token obtido via bootstrap do .credentials.json: "
                    f"{mask(new['access_token'])}")
                return new["access_token"]
            except urllib.error.HTTPError as e:
                log(f"[auth] bootstrap falhou: HTTP {e.code} "
                    f"(refresh token do arquivo provavelmente morto)")
                if e.code == 429 or e.code >= 500:
                    raise TransientAuthError(f"HTTP {e.code} no bootstrap") from e

    # 4. precisa de login interativo
    raise AuthError("Sem token valido. Necessario login PKCE (Conectar conta).")


def run_pkce_login(store: TokenStore, callback_port: int = 54545,
                   open_browser: bool = True, timeout_s: int = 300,
                   log=print) -> str:
    """Login OAuth PKCE com captura automatica via localhost.
    Abre o navegador, espera o callback, troca o code por tokens e salva."""
    import http.server
    import threading
    import webbrowser

    verifier, challenge = make_pkce()
    state = secrets.token_urlsafe(24)
    redirect_uri = f"http://localhost:{callback_port}/callback"
    url = build_authorize_url(redirect_uri, challenge, state)

    captured = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):  # silencia logs do http.server
            pass

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            qs = urllib.parse.parse_qs(parsed.query)
            captured["code"] = (qs.get("code") or [None])[0]
            captured["state"] = (qs.get("state") or [None])[0]
            captured["error"] = (qs.get("error") or [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            ok = bool(captured.get("code"))
            msg = ("Conta conectada. Esta aba vai fechar sozinha…"
                   if ok else
                   f"Falha no login: {captured.get('error')}")
            # Tenta fechar a aba sozinho. Funciona quando a aba foi aberta
            # direto no callback (sem historico); se o navegador bloquear,
            # troca o texto de volta para o aviso manual.
            script = ("<script>setTimeout(function(){window.close();"
                      "setTimeout(function(){document.getElementById('m').textContent="
                      "'Conta conectada. Pode fechar esta aba e voltar ao painel.';},400);"
                      "},800);</script>" if ok else "")
            self.wfile.write(
                f"<html><body style='font-family:sans-serif;padding:40px'>"
                f"<h2 id='m'>{msg}</h2>{script}</body></html>".encode("utf-8"))
            done.set()

    server = http.server.HTTPServer(("127.0.0.1", callback_port), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    log("[auth] abrindo o navegador para autorizar...")
    log(f"[auth] se nao abrir, acesse manualmente:\n{url}")
    if open_browser:
        webbrowser.open(url)

    ok = done.wait(timeout=timeout_s)
    server.shutdown()
    if not ok:
        raise AuthError("Tempo esgotado esperando a autorizacao no navegador.")
    if captured.get("error") or not captured.get("code"):
        raise AuthError(f"Login negado/erro: {captured.get('error')}")
    if captured.get("state") != state:
        raise AuthError("State invalido (possivel CSRF). Login abortado.")

    new = exchange_code(captured["code"], verifier, redirect_uri, state=state)
    store.save(new["access_token"], new["refresh_token"], new["expires_at"],
               source="login")
    log(f"[auth] login concluido: {mask(new['access_token'])}")
    return new["access_token"]


def force_refresh(store: TokenStore, log=print) -> str:
    """Forca uma renovacao (usado apos 401 ao vivo). Levanta AuthError."""
    own = store.load()
    if own and own.get("refresh_token"):
        try:
            new = refresh_with(own["refresh_token"])
            store.save(new["access_token"], new["refresh_token"] or own["refresh_token"],
                       new["expires_at"], source="refresh")
            return new["access_token"]
        except urllib.error.HTTPError as e:
            log(f"[auth] force_refresh falhou: HTTP {e.code}")
    return get_valid_token(store, allow_bootstrap=True, log=log)


if __name__ == "__main__":
    import sys
    tdir = os.path.dirname(os.path.abspath(__file__))
    st = TokenStore(tdir)

    if len(sys.argv) > 1 and sys.argv[1] == "login":
        try:
            run_pkce_login(st)
            own = st.load()
            print("LOGIN OK. expira:",
                  time.strftime("%Y-%m-%d %H:%M",
                                time.localtime(own["expires_at"] / 1000)))
        except Exception as e:
            print("LOGIN FALHOU ->", type(e).__name__, e)
        sys.exit(0)

    # Gate de teste: tenta obter um token sem login interativo.
    try:
        tok = get_valid_token(st)
        own = st.load()
        exp = own.get("expires_at", 0)
        print("RESULTADO: token OK")
        print("  ", mask(tok))
        print("   fonte:", own.get("source"))
        print("   expira:", time.strftime("%Y-%m-%d %H:%M:%S",
                                           time.localtime(exp / 1000)))
    except AuthError as e:
        print("RESULTADO: precisa login PKCE ->", e)
    except Exception as e:
        print("RESULTADO: erro inesperado ->", type(e).__name__, e)
