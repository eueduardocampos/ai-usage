"""
machines.py — soma o consumo de varias maquinas da mesma conta.

O consumo de tokens vem dos logs LOCAIS (~/.claude/projects, ~/.codex/sessions),
entao cada maquina so' enxerga o proprio uso. As janelas de 5h/semanal ja sao
da conta e nao passam por aqui.

Como funciona: cada painel grava, numa pasta que as maquinas compartilham
(Syncthing, Dropbox, OneDrive...), um arquivo `<maquina>.json` com o proprio
historico, e le os arquivos das outras. Cada maquina so' escreve o seu arquivo,
entao nao ha conflito de escrita. O banco sqlite continua local: banco em pasta
sincronizada corrompe.

Formato do arquivo (format 1):
  {"app": "ai-usage", "format": 1, "machine": "Mac", "updated_at": ISO,
   "claude": [[hora UTC "YYYY-MM-DDTHH", modelo, origem, input, output,
               cache_read, cache_creation, custo_guardado|null, turnos], ...],
   "codex":  [[timestamp, modelo, input, cached_input, output, reasoning], ...]}
"""

import datetime as dt
import glob
import hashlib
import json
import math
import os
import re
import socket

FORMAT = 1
EXPORT_INTERVAL_S = 60  # o scan local roda a cada 10s; publicar a cada 1 min basta
ARQUIVO_MAX = 20 * 1024 * 1024  # um ano de uso intenso cabe em poucos MB
_HORA = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}$")


def _rejeita_constante(nome):
    raise ValueError(f"valor nao permitido: {nome}")


def _inteiro(v):
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 2**53


def _numero_ou_nulo(v):
    return v is None or (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v))


def _texto(v, max_len=200):
    return isinstance(v, str) and 0 < len(v) <= max_len


def linha_claude_valida(h):
    """[hora UTC, modelo, origem, input, output, cache_read, cache_creation, custo|null, turnos]"""
    return (isinstance(h, list) and len(h) == 9 and isinstance(h[0], str) and _HORA.match(h[0])
            and _texto(h[1]) and _texto(h[2], 40) and all(_inteiro(x) for x in h[3:7])
            and _numero_ou_nulo(h[7]) and _inteiro(h[8]))


def evento_codex_valido(e):
    """[timestamp, modelo, input, cached_input, output, reasoning]"""
    return (isinstance(e, list) and len(e) == 6 and _texto(e[0], 40) and _texto(e[1])
            and all(_inteiro(x) for x in e[2:6]))


def ler_arquivo(path):
    """JSON estrito (sem NaN/Infinity), com teto de tamanho. O arquivo vem de
    OUTRA maquina: nada nele e' confiavel ate' ser validado linha a linha.
    Devolve (dados, descartadas) ou levanta ValueError/OSError."""
    if os.path.getsize(path) > ARQUIVO_MAX:
        raise ValueError(f"arquivo maior que {ARQUIVO_MAX // 1024 // 1024} MB")
    with open(path, encoding="utf-8") as f:
        data = json.load(f, parse_constant=_rejeita_constante)
    if not isinstance(data, dict):
        raise ValueError("nao e' um objeto JSON")
    claude = data.get("claude")
    codex = data.get("codex")
    if claude is None:
        claude = []
    if codex is None:
        codex = []
    if not isinstance(claude, list) or not isinstance(codex, list):
        raise ValueError("'claude' e 'codex' precisam ser listas")
    bons_c = [h for h in claude if linha_claude_valida(h)]
    bons_x = [e for e in codex if evento_codex_valido(e)]
    descartadas = (len(claude) - len(bons_c)) + (len(codex) - len(bons_x))
    data["claude"], data["codex"] = bons_c, bons_x
    return data, descartadas


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", (name or "").lower()).strip("-") or "maquina"


def default_machine_name() -> str:
    return socket.gethostname().split(".")[0] or "maquina"


class MachineSync:
    def __init__(self, store, codex, sync_dir, machine_name=None, log=print):
        self.store = store
        self.codex = codex
        self.dir = os.path.expanduser(sync_dir)
        self.name = machine_name or default_machine_name()
        self.file = os.path.join(self.dir, slug(self.name) + ".json")
        self.log = log
        self._last_digest = None
        self._last_export_ts = 0.0
        self._updated_at = None
        self._seen = {}      # caminho -> mtime ja importado
        self._remote = {}    # nome -> {"updated_at", "file"}
        self.error = None
        store.local_machine = self.name
        if codex:
            codex.local_machine = self.name

    # --- publicar ------------------------------------------------------------

    def export(self, force=False):
        now = dt.datetime.now(dt.timezone.utc).timestamp()
        if not force and now - self._last_export_ts < EXPORT_INTERVAL_S:
            return False
        self._last_export_ts = now
        claude = self.store.local_hours()
        codex = [[e["timestamp"], e["model"], e["input_tokens"], e["cached_input_tokens"],
                  e["output_tokens"], e["reasoning_output_tokens"]]
                 for e in (self.codex.local_events() if self.codex else [])]
        body = json.dumps([claude, codex], separators=(",", ":"))
        digest = hashlib.sha1(body.encode("utf-8")).hexdigest()
        if digest == self._last_digest and os.path.exists(self.file):
            return False  # nada mudou: nao reescreve (nao gera trafego no sync)
        self._updated_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        payload = {"app": "ai-usage", "format": FORMAT, "machine": self.name,
                   "updated_at": self._updated_at, "claude": claude, "codex": codex}
        os.makedirs(self.dir, exist_ok=True)
        tmp = self.file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, separators=(",", ":"), ensure_ascii=False)
        os.replace(tmp, self.file)  # troca atomica: quem le nunca pega meio arquivo
        self._last_digest = digest
        return True

    # --- ler as outras -------------------------------------------------------

    def _candidates(self):
        for path in glob.glob(os.path.join(self.dir, "*.json")):
            base = os.path.basename(path)
            # copia de conflito do Syncthing ("x.sync-conflict-...json") nao e'
            # outra maquina: e' versao velha do mesmo arquivo
            if ".sync-conflict-" in base or os.path.normcase(path) == os.path.normcase(self.file):
                continue
            yield path

    def import_others(self):
        present = set()
        codex_events = []
        changed = False
        for path in self._candidates():
            try:
                mtime = os.path.getmtime(path)
                if self._seen.get(path, (None,))[0] == mtime:
                    present.add(self._seen[path][1])
                    codex_events.extend(self._seen[path][2])
                    continue
                data, descartadas = ler_arquivo(path)
                if descartadas:
                    self.log(f"[machines] {os.path.basename(path)}: {descartadas} linha(s) fora do formato, ignoradas")
            except (OSError, ValueError) as e:
                # leitura falhou agora (arquivo chegando?): fica com a ultima boa
                self.log(f"[machines] ignorando {path}: {e}")
                if path in self._seen:
                    present.add(self._seen[path][1])
                    codex_events.extend(self._seen[path][2])
                continue
            if data.get("app") != "ai-usage" or data.get("format") != FORMAT:
                continue
            name = data.get("machine") or os.path.splitext(os.path.basename(path))[0]
            if slug(name) == slug(self.name):
                self.log(f"[machines] {path} usa o mesmo nome desta maquina ({name}); "
                         "ajuste machine_name no config.json de uma das duas")
                continue
            if not _texto(name, 80):
                self.log(f"[machines] ignorando {path}: nome de maquina invalido")
                continue
            self.store.replace_remote(name, data["claude"])  # ja validado em ler_arquivo
            events = [{"timestamp": ts, "model": m, "input_tokens": i,
                       "cached_input_tokens": c, "output_tokens": o,
                       "reasoning_output_tokens": r, "total_tokens": i + o,
                       "machine": name}
                      for ts, m, i, c, o, r in data["codex"]]
            self._seen[path] = (mtime, name, events)
            self._remote[name] = {"updated_at": data.get("updated_at"), "file": path}
            present.add(name)
            codex_events.extend(events)
            changed = True
        gone = set(self._remote) - present
        if gone or changed:
            self.store.drop_remote_except(present)
            for name in gone:
                self._remote.pop(name, None)
            self._seen = {p: v for p, v in self._seen.items() if v[1] in present}
        if self.codex:
            self.codex.remote_events = codex_events

    def run_once(self, force=False):
        try:
            self.export(force=force)
            self.import_others()
            self.error = None
        except Exception as e:  # pasta fora do ar nao pode derrubar o painel
            self.error = f"{type(e).__name__}: {e}"
            self.log(f"[machines] {self.error}")

    def info(self) -> dict:
        machines = [{"name": self.name, "local": True, "updated_at": self._updated_at}]
        machines += [{"name": n, "local": False, "updated_at": r["updated_at"]}
                     for n, r in sorted(self._remote.items())]
        return {"enabled": True, "dir": self.dir, "machines": machines, "error": self.error}
