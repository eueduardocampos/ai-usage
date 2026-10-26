"""
store.py — banco sqlite proprio do painel + scan dos logs locais + agregacoes.

Tabelas:
  turns(message_id PK, timestamp, model, input, output, cache_read, cache_creation,
        source, cost)                            -> source: 'code' | 'science'
  processed_files(path PK, mtime, lines)         -> scan incremental
  api_snapshots(ts, window, utilization, resets_at, used_credits, monthly_limit, currency)
  remote_hours(machine, hour, model, source, ..., cost, turns)
                                                 -> consumo das OUTRAS maquinas,
                                                    ja agregado por hora (machines.py)

Fontes:
  code    -> ~/.claude/projects/**/*.jsonl (transcricoes do Claude Code)
  science -> ~/.claude-science/orgs/*/operon-cli.db, tabela frames (Claude Science).
             Um frame MUDA enquanto o agente roda (tokens sobem), entao e upsert
             por id, nao insert-once como os .jsonl. O custo vem pronto do
             proprio Science (total_cost) — a tabela PRICING daqui nao conhece
             todos os modelos que ele usa.

Timestamps dos logs sao ISO8601 UTC ("...Z"). Os limites de escopo (dia/semana/mes)
sao calculados em horario LOCAL e convertidos para UTC para comparar como string.
"""

import datetime as dt
import functools
import glob
import json
import os
import sqlite3
import threading

HOME = os.path.expanduser("~")
PROJECTS_DIR = os.path.join(HOME, ".claude", "projects")
# bancos do Claude Science (um por org; hoje uma, amanha podem ser mais)
SCIENCE_DB_GLOB = os.path.join(HOME, ".claude-science", "orgs", "*", "operon-cli.db")

# Tabela de preco e reconhecimento de modelo vivem em pricing.py (fonte
# unica, compartilhada com forecast.py). Re-exportados aqui por compatibilidade.
from pricing import PRICING, model_key, row_cost  # noqa: F401


def group_cost(source, model, i, o, cr, cc, stored) -> float:
    """Custo de um grupo (model, source). Science ja traz o custo calculado
    pelo proprio app; para o Claude Code calculamos pela tabela PRICING."""
    if source == "science":
        return stored or 0.0
    return row_cost(model, i, o, cr, cc)


# Turnos locais + horas importadas das outras maquinas, com a mesma forma.
# `n` e' quantos turnos a linha representa (1 no local, a contagem da hora no
# remoto) e `machine` e' NULL no local. So' as agregacoes de HISTORICO usam
# isto; ritmo de queima e perfil por hora (projecoes) continuam so' locais.
ALL_TURNS = """(
    SELECT timestamp, model, COALESCE(source,'code') source, input_tokens,
           output_tokens, cache_read, cache_creation, cost, 1 AS n, NULL AS machine
    FROM turns
    UNION ALL
    SELECT hour || ':00:00.000Z', model, source, input_tokens, output_tokens,
           cache_read, cache_creation, cost, turns, machine
    FROM remote_hours)"""


# --- conexao / schema --------------------------------------------------------

def _locked(fn):
    """Serializa o acesso a unica conexao sqlite entre as threads (poller,
    scan_loop e handlers HTTP rodam em paralelo)."""
    @functools.wraps(fn)
    def wrapper(self, *a, **k):
        with self._lock:
            return fn(self, *a, **k)
    return wrapper


class Store:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.local_machine = None  # nome desta maquina no by_machine (machines.py)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        c = self.conn
        c.executescript("""
        CREATE TABLE IF NOT EXISTS turns (
            message_id TEXT PRIMARY KEY,
            timestamp TEXT,
            model TEXT,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cache_read INTEGER DEFAULT 0,
            cache_creation INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_turns_ts ON turns(timestamp);
        CREATE TABLE IF NOT EXISTS processed_files (
            path TEXT PRIMARY KEY, mtime REAL, lines INTEGER
        );
        CREATE TABLE IF NOT EXISTS api_snapshots (
            ts TEXT, window TEXT, utilization REAL, resets_at TEXT,
            used_credits REAL, monthly_limit REAL, currency TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_snap ON api_snapshots(window, ts);
        CREATE TABLE IF NOT EXISTS remote_hours (
            machine TEXT, hour TEXT, model TEXT, source TEXT,
            input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
            cache_read INTEGER DEFAULT 0, cache_creation INTEGER DEFAULT 0,
            cost REAL, turns INTEGER DEFAULT 0,
            PRIMARY KEY (machine, hour, model, source)
        );
        """)
        # migracao: bancos antigos nao tem source/cost
        cols = {r[1] for r in c.execute("PRAGMA table_info(turns)")}
        if "source" not in cols:
            c.execute("ALTER TABLE turns ADD COLUMN source TEXT DEFAULT 'code'")
        if "cost" not in cols:
            c.execute("ALTER TABLE turns ADD COLUMN cost REAL")
        # migracao: rotulo da janela vindo do servidor (janelas por modelo)
        snap_cols = {r[1] for r in c.execute("PRAGMA table_info(api_snapshots)")}
        if "label" not in snap_cols:
            c.execute("ALTER TABLE api_snapshots ADD COLUMN label TEXT")
        c.commit()

    # --- scan dos logs -------------------------------------------------------

    @_locked
    def scan(self, log=lambda *a: None) -> dict:
        files = glob.glob(os.path.join(PROJECTS_DIR, "**", "*.jsonl"), recursive=True)
        new_turns = 0
        new_files = 0
        cur = self.conn.cursor()
        seen = {r["path"]: (r["mtime"], r["lines"])
                for r in cur.execute("SELECT path, mtime, lines FROM processed_files")}
        for fp in files:
            try:
                mtime = os.path.getmtime(fp)
            except OSError:
                continue
            if fp in seen and abs(seen[fp][0] - mtime) < 1e-6:
                continue  # inalterado
            rows, lines = self._parse_file(fp)
            for r in rows:
                cur.execute("""INSERT OR IGNORE INTO turns
                    (message_id, timestamp, model, input_tokens, output_tokens,
                     cache_read, cache_creation) VALUES (?,?,?,?,?,?,?)""", r)
                new_turns += cur.rowcount
            cur.execute("""INSERT INTO processed_files(path, mtime, lines)
                VALUES(?,?,?) ON CONFLICT(path) DO UPDATE SET mtime=?, lines=?""",
                (fp, mtime, lines, mtime, lines))
            if fp not in seen:
                new_files += 1
        science = self._scan_science(cur)
        self.conn.commit()
        log(f"[scan] arquivos novos/alterados processados, +{new_turns} turnos, "
            f"~{science} frames do science")
        return {"new_turns": new_turns, "new_files": new_files, "files": len(files),
                "science_frames": science}

    def _scan_science(self, cur) -> int:
        """Upsert dos frames do Claude Science na tabela turns (source='science').

        Incremental por updated_at, com margem de 60s: um frame pode ser lido
        de novo enquanto muda, e o upsert por id garante que nao duplica.
        As colunas aux_* ficam DE FORA de proposito — sao roll-up do pai e
        somariam duas vezes com as linhas dos frames filhos."""
        total = 0
        for dbp in glob.glob(SCIENCE_DB_GLOB):
            key = "science:" + dbp
            row = cur.execute("SELECT mtime FROM processed_files WHERE path=?",
                              (key,)).fetchone()
            since = row["mtime"] if row else 0
            try:
                src = sqlite3.connect("file:%s?mode=ro" % dbp, uri=True)
                src.row_factory = sqlite3.Row
                frames = src.execute("""SELECT id, model, updated_at,
                    COALESCE(input_tokens,0) i, COALESCE(output_tokens,0) o,
                    COALESCE(cache_read_tokens,0) cr,
                    COALESCE(cache_write_tokens,0) cc,
                    COALESCE(total_cost,0) cost
                    FROM frames WHERE model IS NOT NULL AND updated_at > ?""",
                    (max(0, since - 60_000),)).fetchall()
                src.close()
            except sqlite3.Error:
                continue  # banco ocupado/ausente: tenta no proximo scan
            mx = since
            for f in frames:
                if (f["i"] + f["o"] + f["cr"] + f["cc"]) == 0:
                    continue
                ts = dt.datetime.utcfromtimestamp(
                    f["updated_at"] / 1000).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                cur.execute("""INSERT INTO turns
                    (message_id, timestamp, model, input_tokens, output_tokens,
                     cache_read, cache_creation, source, cost)
                    VALUES (?,?,?,?,?,?,?,'science',?)
                    ON CONFLICT(message_id) DO UPDATE SET
                        timestamp=excluded.timestamp,
                        input_tokens=excluded.input_tokens,
                        output_tokens=excluded.output_tokens,
                        cache_read=excluded.cache_read,
                        cache_creation=excluded.cache_creation,
                        cost=excluded.cost""",
                    ("science:" + f["id"], ts, f["model"],
                     f["i"], f["o"], f["cr"], f["cc"], f["cost"]))
                total += 1
                mx = max(mx, f["updated_at"])
            cur.execute("""INSERT INTO processed_files(path, mtime, lines)
                VALUES(?,?,0) ON CONFLICT(path) DO UPDATE SET mtime=?""",
                (key, mx, mx))
        return total

    @staticmethod
    def _parse_file(fp):
        rows = []
        n = 0
        with open(fp, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                n += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("type") != "assistant":
                    continue
                msg = r.get("message", {}) or {}
                u = msg.get("usage") or r.get("usage") or {}
                mid = msg.get("id")
                if not mid:
                    continue
                i = u.get("input_tokens") or 0
                o = u.get("output_tokens") or 0
                cr = u.get("cache_read_input_tokens") or 0
                cc = u.get("cache_creation_input_tokens") or 0
                if (i + o + cr + cc) == 0:
                    continue
                rows.append((mid, r.get("timestamp"), msg.get("model"),
                             i, o, cr, cc))
        return rows, n

    # --- consumo entre maquinas (machines.py) --------------------------------

    @_locked
    def local_hours(self):
        """Consumo LOCAL agregado por (hora UTC, modelo, origem) — o que esta
        maquina publica para as outras. Por hora basta: o custo e' linear nos
        tokens (ou ja vem somado, no Science) e os escopos dia/semana/mes
        comecam em hora cheia. Nunca inclui remote_hours, para uma maquina nao
        devolver a outra o consumo dela."""
        cur = self.conn.cursor()
        rows = cur.execute("""SELECT substr(timestamp,1,13) h, model,
            COALESCE(source,'code') src,
            SUM(input_tokens) i, SUM(output_tokens) o,
            SUM(cache_read) cr, SUM(cache_creation) cc,
            SUM(cost) cost, COUNT(*) n
            FROM turns WHERE timestamp IS NOT NULL
            GROUP BY h, model, src ORDER BY h""").fetchall()
        return [[r["h"], r["model"], r["src"], r["i"] or 0, r["o"] or 0,
                 r["cr"] or 0, r["cc"] or 0, r["cost"], r["n"]] for r in rows]

    @_locked
    def replace_remote(self, machine: str, hours):
        """Troca todo o consumo importado de `machine` pelo do arquivo atual
        (o arquivo e' sempre o historico completo daquela maquina)."""
        cur = self.conn.cursor()
        cur.execute("DELETE FROM remote_hours WHERE machine=?", (machine,))
        cur.executemany("""INSERT OR REPLACE INTO remote_hours
            (machine, hour, model, source, input_tokens, output_tokens,
             cache_read, cache_creation, cost, turns)
            VALUES (?,?,?,?,?,?,?,?,?,?)""", [(machine, *h) for h in hours])
        self.conn.commit()

    @_locked
    def drop_remote_except(self, machines):
        """Esquece maquinas cujo arquivo sumiu da pasta compartilhada."""
        keep = list(machines)
        marks = ",".join("?" * len(keep))
        sql = "DELETE FROM remote_hours"
        if keep:
            sql += f" WHERE machine NOT IN ({marks})"
        self.conn.execute(sql, keep)
        self.conn.commit()

    # --- limites de escopo (LOCAL -> UTC ISO) --------------------------------

    @staticmethod
    def _utc_bound(local_dtobj) -> str:
        # converte um datetime local ingenuo para string ISO UTC com Z
        ts = local_dtobj.timestamp()
        return dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def _scope_lower_bound(self, scope: str):
        now = dt.datetime.now()
        if scope == "dia":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif scope == "semana":
            monday = now - dt.timedelta(days=now.weekday())
            start = monday.replace(hour=0, minute=0, second=0, microsecond=0)
        elif scope == "mes":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        else:  # geral
            return None
        return self._utc_bound(start)

    # --- agregacoes ----------------------------------------------------------

    @_locked
    def scope_summary(self, scope: str) -> dict:
        """Total + media por hora ativa, no escopo dado."""
        lb = self._scope_lower_bound(scope)
        where, params = ("WHERE timestamp >= ?", [lb]) if lb else ("", [])
        cur = self.conn.cursor()
        rows = cur.execute(f"""SELECT model, source src, machine,
            SUM(input_tokens) i, SUM(output_tokens) o,
            SUM(cache_read) cr, SUM(cache_creation) cc,
            SUM(COALESCE(cost,0)) stored, SUM(n) turns
            FROM {ALL_TURNS} {where} GROUP BY model, src, machine""", params).fetchall()
        tot_tokens = tot_cost = tot_turns = 0
        bm, by_source, by_machine = {}, {}, {}
        for r in rows:
            i, o, cr, cc = r["i"] or 0, r["o"] or 0, r["cr"] or 0, r["cc"] or 0
            cost = group_cost(r["src"], r["model"], i, o, cr, cc, r["stored"])
            tokens = i + o + cr + cc
            tot_tokens += tokens
            tot_cost += cost
            tot_turns += r["turns"]
            e = bm.setdefault(r["model"], {
                "model": r["model"], "tokens": 0, "cost": 0.0, "input": 0,
                "output": 0, "cache_read": 0, "cache_creation": 0, "turns": 0,
                "sources": {}})
            e["tokens"] += tokens; e["cost"] += cost; e["input"] += i
            e["output"] += o; e["cache_read"] += cr; e["cache_creation"] += cc
            e["turns"] += r["turns"]
            e["sources"][r["src"]] = e["sources"].get(r["src"], 0) + tokens
            s = by_source.setdefault(r["src"], {"tokens": 0, "cost": 0.0, "turns": 0})
            s["tokens"] += tokens; s["cost"] += cost; s["turns"] += r["turns"]
            m = by_machine.setdefault(r["machine"] or self.local_machine or "local", {
                "tokens": 0, "cost": 0.0, "turns": 0, "local": r["machine"] is None})
            m["tokens"] += tokens; m["cost"] += cost; m["turns"] += r["turns"]
        by_model = list(bm.values())
        active_hours = cur.execute(
            f"SELECT COUNT(DISTINCT substr(timestamp,1,13)) h FROM {ALL_TURNS} {where}",
            params).fetchone()["h"] or 0
        by_model.sort(key=lambda x: x["tokens"], reverse=True)
        return {
            "scope": scope,
            "total_tokens": tot_tokens,
            "total_cost": tot_cost,
            "total_turns": tot_turns,
            "active_hours": active_hours,
            "tokens_per_hour": (tot_tokens / active_hours) if active_hours else 0,
            "cost_per_hour": (tot_cost / active_hours) if active_hours else 0,
            "by_model": by_model,
            "by_source": by_source,
            "by_machine": by_machine,
        }

    @_locked
    def total_summary(self) -> dict:
        """Total absoluto da vida toda (rapido, so leitura)."""
        cur = self.conn.cursor()
        rows = cur.execute(f"""SELECT model, source src,
            SUM(input_tokens) i, SUM(output_tokens) o,
            SUM(cache_read) cr, SUM(cache_creation) cc,
            SUM(COALESCE(cost,0)) stored, SUM(n) t
            FROM {ALL_TURNS} GROUP BY model, src""").fetchall()
        tokens = cost = turns = 0
        t_in = t_out = t_cr = t_cc = 0
        for r in rows:
            i, o, cr, cc = r["i"] or 0, r["o"] or 0, r["cr"] or 0, r["cc"] or 0
            tokens += i + o + cr + cc
            cost += group_cost(r["src"], r["model"], i, o, cr, cc, r["stored"])
            turns += r["t"]
            t_in += i; t_out += o; t_cr += cr; t_cc += cc
        return {
            "total_tokens": tokens,      # processados: input+output+cache (volume real)
            "total_cost": cost,
            "total_turns": turns,
            "input_tokens": t_in,
            "output_tokens": t_out,
            "cache_read": t_cr,
            "cache_creation": t_cc,
        }

    @_locked
    def daily_series(self, days: int = 30):
        cur = self.conn.cursor()
        rows = cur.execute(f"""SELECT strftime('%Y-%m-%d', timestamp, 'localtime') d,
            model, source src,
            SUM(input_tokens) i, SUM(output_tokens) o,
            SUM(cache_read) cr, SUM(cache_creation) cc, SUM(COALESCE(cost,0)) stored
            FROM {ALL_TURNS} GROUP BY d, model, src ORDER BY d""").fetchall()
        out = {}
        for r in rows:
            d = r["d"]
            if not d:
                continue
            tokens = (r["i"] or 0)+(r["o"] or 0)+(r["cr"] or 0)+(r["cc"] or 0)
            cost = group_cost(r["src"], r["model"], r["i"] or 0, r["o"] or 0,
                              r["cr"] or 0, r["cc"] or 0, r["stored"])
            e = out.setdefault(d, {"day": d, "tokens": 0, "cost": 0.0})
            e["tokens"] += tokens
            e["cost"] += cost
        series = sorted(out.values(), key=lambda x: x["day"])
        return series[-days:]

    @_locked
    def hour_of_day_avg(self):
        """Media de tokens por hora-do-dia (local), para grafico de perfil."""
        cur = self.conn.cursor()
        rows = cur.execute("""SELECT
            CAST(strftime('%H', timestamp, 'localtime') AS INTEGER) hh,
            SUM(input_tokens+output_tokens+cache_read+cache_creation) tokens,
            COUNT(DISTINCT strftime('%Y-%m-%d', timestamp, 'localtime')) days
            FROM turns GROUP BY hh ORDER BY hh""").fetchall()
        return [{"hour": r["hh"], "avg_tokens": (r["tokens"] or 0)/r["days"]
                 if r["days"] else 0} for r in rows if r["hh"] is not None]

    @_locked
    def recent_tokph(self, hours: float = 2.0):
        """Tokens por hora por modelo nas ultimas `hours` horas (burn rate)."""
        lb = dt.datetime.utcnow() - dt.timedelta(hours=hours)
        lb_s = lb.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        cur = self.conn.cursor()
        rows = cur.execute("""SELECT model,
            SUM(input_tokens+output_tokens+cache_read+cache_creation) tokens
            FROM turns WHERE timestamp >= ? GROUP BY model""", [lb_s]).fetchall()
        return {r["model"]: (r["tokens"] or 0)/hours for r in rows}

    @_locked
    def recent_burn_by_source(self, hours: float = 2.0):
        """Queima recente por modelo x origem: {model: {source: tok/h}}.
        Mesma janela do recent_tokph, mas separando Claude Code de Science."""
        lb = dt.datetime.utcnow() - dt.timedelta(hours=hours)
        lb_s = lb.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        cur = self.conn.cursor()
        rows = cur.execute("""SELECT model, COALESCE(source,'code') src,
            SUM(input_tokens+output_tokens+cache_read+cache_creation) tokens
            FROM turns WHERE timestamp >= ? GROUP BY model, src""",
            [lb_s]).fetchall()
        out = {}
        for r in rows:
            out.setdefault(r["model"], {})[r["src"]] = (r["tokens"] or 0)/hours
        return out

    # --- snapshots da API ----------------------------------------------------

    @_locked
    def insert_snapshot(self, normalized: dict):
        ts = dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
        eu = normalized.get("extra_usage") or {}
        cur = self.conn.cursor()
        for win, data in (normalized.get("windows") or {}).items():
            cur.execute("""INSERT INTO api_snapshots
                (ts, window, utilization, resets_at, used_credits, monthly_limit, currency, label)
                VALUES (?,?,?,?,?,?,?,?)""",
                (ts, win, data.get("utilization"), data.get("resets_at"),
                 eu.get("used_credits"), eu.get("monthly_limit"), eu.get("currency"),
                 data.get("label")))
        self.conn.commit()

    @_locked
    def extra_credit_series(self, hours: int = 24):
        """Serie (ts, used_credits) dos snapshots — mede o ritmo de gasto do
        excedente (tokens extras alem da licenca)."""
        lb = (dt.datetime.utcnow() - dt.timedelta(hours=hours)).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z")
        cur = self.conn.cursor()
        rows = cur.execute(
            """SELECT ts, MAX(used_credits) AS uc FROM api_snapshots
               WHERE ts >= ? AND used_credits IS NOT NULL
               GROUP BY ts ORDER BY ts""", (lb,)).fetchall()
        return [(r["ts"], r["uc"]) for r in rows]

    @_locked
    def latest_state(self):
        """Ultimo snapshot gravado (todas as janelas no mesmo ts)."""
        cur = self.conn.cursor()
        row = cur.execute("SELECT MAX(ts) m FROM api_snapshots").fetchone()
        if not row or not row["m"]:
            return None
        ts = row["m"]
        rows = cur.execute("SELECT * FROM api_snapshots WHERE ts=?", [ts]).fetchall()
        windows, extra = {}, None
        for r in rows:
            windows[r["window"]] = {"utilization": r["utilization"],
                                    "resets_at": r["resets_at"],
                                    "label": r["label"]}
            if extra is None:
                extra = {"used_credits": r["used_credits"],
                         "monthly_limit": r["monthly_limit"],
                         "currency": r["currency"]}
        return {"ts": ts, "windows": windows, "extra_usage": extra}

    def _series(self, window: str, lookback: int):
        cur = self.conn.cursor()
        rows = cur.execute("""SELECT ts, utilization FROM api_snapshots
            WHERE window = ? AND utilization IS NOT NULL
            ORDER BY ts DESC LIMIT ?""", (window, lookback)).fetchall()
        return [(r["ts"], r["utilization"]) for r in reversed(rows)]

    @_locked
    def snapshot_series(self, window: str, lookback: int = 60):
        """Serie [(ts, utilization)] da janela, do mais antigo ao mais novo."""
        return self._series(window, lookback)

    @_locked
    def snapshot_rate(self, window: str, min_span_h: float = 0.5,
                      lookback: int = 60):
        """%/hora medido por amostragem (ver forecast.series_rate): so retorna
        se as amostras cobrirem pelo menos `min_span_h` horas, e uma queda de
        valor (reset de janela) corta o trecho anterior."""
        from forecast import series_rate
        return series_rate(self._series(window, lookback), min_span_h)

    @_locked
    def heatmap_data(self):
        """Media de tokens por (dia-da-semana, hora-do-dia) para heatmap.
        dow: 0=Dom 1=Seg ... 6=Sab. Retorna lista de {dow, hour, avg_tokens}."""
        cur = self.conn.cursor()
        rows = cur.execute(f"""SELECT timestamp, model, source src,
            input_tokens i, output_tokens o, cache_read cr, cache_creation cc,
            COALESCE(cost,0) stored,
            strftime('%Y-%m-%d',timestamp,'localtime') day,
            CAST(strftime('%w',timestamp,'localtime') AS INTEGER) dow,
            CAST(strftime('%H',timestamp,'localtime') AS INTEGER) hour
            FROM {ALL_TURNS}""").fetchall()
        cells = {}
        for r in rows:
            if r['dow'] is None or r['hour'] is None:
                continue
            cell = cells.setdefault((r['dow'],r['hour']),
                {'tokens':0, 'cost':0, 'days':set(), 'models':set()})
            i,o,cr,cc = (r[k] or 0 for k in ('i','o','cr','cc'))
            cell['tokens'] += i+o+cr+cc
            cell['cost'] += group_cost(r['src'],r['model'],i,o,cr,cc,r['stored'])
            cell['days'].add(r['day'])
            cell['models'].add(r['model'])
        return [{'dow':dow,'hour':hour,'avg_tokens':c['tokens']/len(c['days']),
                 'avg_cost':c['cost']/len(c['days']), 'models':sorted(c['models'])}
                for (dow,hour),c in sorted(cells.items())]

    @_locked
    def snapshot_history(self, hours: int = 48):
        lb = (dt.datetime.utcnow() - dt.timedelta(hours=hours)).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z")
        cur = self.conn.cursor()
        rows = cur.execute("""SELECT ts, window, utilization FROM api_snapshots
            WHERE ts >= ? AND utilization IS NOT NULL ORDER BY ts""", [lb]).fetchall()
        return [dict(r) for r in rows]
