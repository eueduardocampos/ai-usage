"""
claude_sessions.py — de qual conta e' cada sessao do Claude Code.

Os registros de uso (~/.claude/projects/*.jsonl) nao dizem a conta. Mas cada
app do Claude (o oficial e as copias do Parall, uma por conta) guarda a lista
das proprias sessoes em
    <dados do app>/claude-code-sessions/<uuid da conta>/<uuid da org>/local_*.json
e cada arquivo traz o `cliSessionId`, que e' o nome do .jsonl. Juntando os
dois, o uso de cada sessao vai para a conta (e a organizacao) certa.

Sessoes que nao aparecem em app nenhum (terminal, scripts) ficam sem conta;
o painel deixa o usuario dizer para qual conta elas contam.
"""

import glob
import json
import os
import sys
import threading
import time

CACHE_S = 60


def _app_dirs():
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        base = os.path.join(home, "Library", "Application Support")
        dirs = [os.path.join(base, "Claude")]
        dirs += glob.glob(os.path.join(base, "Parall", "*"))
        return dirs
    if os.name == "nt":
        ap = os.environ.get("APPDATA", "")
        return [os.path.join(ap, "Claude")] + glob.glob(os.path.join(ap, "Parall", "*"))
    return [os.path.join(home, ".config", "Claude")]


class SessionMap:
    def __init__(self):
        self._map = {}
        self._ts = 0.0
        self._lock = threading.Lock()

    def get(self):
        with self._lock:
            if time.time() - self._ts < CACHE_S:
                return self._map
        m = {}
        for d in _app_dirs():
            raiz = os.path.join(d, "claude-code-sessions")
            for fp in glob.glob(os.path.join(raiz, "*", "*", "local_*.json")):
                partes = fp.split(os.sep)
                acct, org = partes[-3], partes[-2]
                try:
                    with open(fp, encoding="utf-8") as f:
                        data = json.load(f)
                except (OSError, ValueError):
                    continue
                for k in ("cliSessionId", "lastAssistantCliSessionId"):
                    sid = data.get(k)
                    if isinstance(sid, str) and sid:
                        m[sid] = (acct, org)
        with self._lock:
            self._map, self._ts = m, time.time()
        return m
