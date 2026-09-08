"""
updates.py — versao do sistema e aviso de versao nova.

A versao vem do arquivo VERSION na raiz (fonte unica; scripts/bump-version.py
propaga para os manifests do Tauri, Stream Deck, app nativo e web). O aviso de
atualizacao e' consultado UMA vez no backend, com cache de 6h, e servido no
/api/state — assim painel web, widget, app de mesa, app nativo e Stream Deck
mostram a mesma coisa sem cada um bater no GitHub.
"""

import json
import os
import re
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
RELEASES_API = "https://api.github.com/repos/eueduardocampos/claude-usage/releases/latest"
RELEASES_PAGE = "https://github.com/eueduardocampos/claude-usage/releases/latest"
CHECK_INTERVAL_S = 6 * 3600
_lock = threading.Lock()
_cache = {"ts": 0.0, "latest": None, "url": None, "error": None}


def current_version() -> str:
    try:
        with open(os.path.join(HERE, "VERSION"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "0.0.0"


def _parts(v: str):
    """'4.2.0' -> (4, 2, 0); ignora sufixos ('v4.2.0-beta' -> (4, 2, 0))."""
    nums = re.findall(r"\d+", (v or "").split("-")[0])
    return tuple(int(n) for n in nums[:3]) + (0,) * (3 - len(nums[:3]))


def is_newer(latest: str, current: str) -> bool:
    return bool(latest) and _parts(latest) > _parts(current)


def check(force=False, log=print):
    """Consulta a ultima release no GitHub (cache de 6h). Falha de rede nao e'
    erro do painel: so' fica sem aviso ate a proxima tentativa."""
    with _lock:
        if not force and time.time() - _cache["ts"] < CHECK_INTERVAL_S:
            return
        _cache["ts"] = time.time()
    try:
        req = urllib.request.Request(RELEASES_API, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "ai-usage-panel",
        })
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
        with _lock:
            _cache["latest"] = (data.get("tag_name") or "").lstrip("v") or None
            _cache["url"] = data.get("html_url") or RELEASES_PAGE
            _cache["error"] = None
    except Exception as e:
        with _lock:
            _cache["error"] = str(e)
            _cache["ts"] = time.time() - (CHECK_INTERVAL_S - 900)  # tenta de novo em ~15min
        log(f"[update] consulta indisponivel: {e}")


def state() -> dict:
    cur = current_version()
    with _lock:
        latest, url = _cache["latest"], _cache["url"]
    return {
        "current": cur,
        "latest": latest,
        "url": url or RELEASES_PAGE,
        "available": is_newer(latest, cur) if latest else False,
    }
