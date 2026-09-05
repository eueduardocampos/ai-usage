"""Leitura local do consumo da assinatura ChatGPT usado pelo Codex.

Nao usa a API paga da OpenAI. Os tokens e os snapshots de limite vem dos
eventos ``token_count`` gravados pelo Codex Desktop/CLI em ~/.codex/sessions.
Sessoes importadas do Claude sao reconhecidas pelo registro de importacao; o
contador sintetico criado na importacao e ignorado, mas uso novo ao retomar uma
dessas sessoes no Codex continua sendo contado.
"""

import datetime as dt
import glob
import json
import os
import threading
import time
import urllib.error
import urllib.request


HOME = os.path.expanduser("~")
SESSIONS_GLOB = os.path.join(HOME, ".codex", "sessions", "**", "*.jsonl")
IMPORTS_PATH = os.path.join(HOME, ".codex", "external_agent_session_imports.json")
AUTH_PATH = os.path.join(HOME, ".codex", "auth.json")
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
LIMIT_REFRESH_SECONDS = 300


def _parse_iso(value):
    try:
        return dt.datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _reset_iso(epoch):
    if not epoch:
        return None
    try:
        return dt.datetime.fromtimestamp(float(epoch), dt.timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


class CodexUsage:
    def __init__(self):
        self._lock = threading.RLock()
        self._files = {}
        self._imported_ids = set()
        self._imports_mtime = None
        self._direct_limits = None
        self._direct_snapshot_ts = None
        self._last_limit_fetch = 0.0
        self._api_error = None

    def _load_imports(self):
        try:
            mtime = os.path.getmtime(IMPORTS_PATH)
        except OSError:
            self._imported_ids = set()
            self._imports_mtime = None
            return
        if mtime == self._imports_mtime:
            return
        try:
            with open(IMPORTS_PATH, encoding="utf-8") as f:
                data = json.load(f)
            self._imported_ids = {
                r.get("imported_thread_id") for r in data.get("records", [])
                if r.get("imported_thread_id")
            }
            self._imports_mtime = mtime
        except (OSError, ValueError):
            pass

    @staticmethod
    def _parse_file(path):
        session_id = None
        model_provider = None
        current_model = None
        events = []
        limits = []
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except (TypeError, ValueError):
                        continue
                    kind = row.get("type")
                    payload = row.get("payload") or {}
                    if kind == "session_meta":
                        session_id = payload.get("session_id") or payload.get("id")
                        model_provider = payload.get("model_provider")
                    elif kind == "turn_context":
                        current_model = payload.get("model") or current_model
                    elif kind == "event_msg" and payload.get("type") == "token_count":
                        info = payload.get("info") or {}
                        usage = info.get("last_token_usage") or {}
                        input_tokens = int(usage.get("input_tokens") or 0)
                        output_tokens = int(usage.get("output_tokens") or 0)
                        cached = int(usage.get("cached_input_tokens") or 0)
                        reasoning = int(usage.get("reasoning_output_tokens") or 0)
                        # O contador inicial de uma sessao importada traz apenas
                        # total_tokens; os componentes zerados denunciam que nao
                        # houve uma chamada OpenAI naquele evento.
                        if input_tokens or output_tokens:
                            events.append({
                                "timestamp": row.get("timestamp"),
                                "model": current_model or "codex",
                                "input_tokens": input_tokens,
                                "cached_input_tokens": cached,
                                "output_tokens": output_tokens,
                                "reasoning_output_tokens": reasoning,
                                "total_tokens": input_tokens + output_tokens,
                            })
                        rate = payload.get("rate_limits")
                        if isinstance(rate, dict) and rate.get("limit_id"):
                            limits.append((row.get("timestamp"), rate))
        except OSError:
            pass
        return {
            "session_id": session_id,
            "model_provider": model_provider,
            "events": events,
            "limits": limits,
        }

    def scan(self):
        with self._lock:
            self._load_imports()
            paths = set(glob.glob(SESSIONS_GLOB, recursive=True))
            for path in paths:
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    continue
                old = self._files.get(path)
                if old and old[0] == mtime:
                    continue
                self._files[path] = (mtime, self._parse_file(path))
            for path in set(self._files) - paths:
                del self._files[path]

    def refresh_limits(self, force=False):
        """Consulta os limites da assinatura ChatGPT, nunca a API comercial.

        Reusa o OAuth mantido pelo Codex e apenas le auth.json a cada consulta;
        nao grava, renova nem persiste os tokens em outro lugar.
        """
        if not force and time.time() - self._last_limit_fetch < LIMIT_REFRESH_SECONDS:
            return
        self._last_limit_fetch = time.time()
        try:
            with open(AUTH_PATH, encoding="utf-8") as f:
                auth = json.load(f)
            tokens = auth.get("tokens") or {}
            access = tokens.get("access_token")
            account = tokens.get("account_id")
            if not access or not account or auth.get("auth_mode") != "chatgpt":
                raise ValueError("sessao ChatGPT do Codex nao encontrada")
            req = urllib.request.Request(USAGE_URL, headers={
                "Authorization": "Bearer " + access,
                "ChatGPT-Account-ID": account,
                "User-Agent": "claude-usage-panel/1.0",
            })
            with urllib.request.urlopen(req, timeout=12) as response:
                raw = json.loads(response.read().decode("utf-8"))
            limits = []
            stamp = dt.datetime.now(dt.timezone.utc).isoformat()

            def normalized(limit_id, name, rate, credits=None):
                def window(data):
                    if not data:
                        return None
                    return {
                        "used_percent": data.get("used_percent"),
                        "window_minutes": (data.get("limit_window_seconds") or 0) / 60,
                        "resets_at": _reset_iso(data.get("reset_at")),
                    }
                return {
                    "id": limit_id,
                    "name": name,
                    "plan_type": raw.get("plan_type"),
                    "primary": window((rate or {}).get("primary_window")),
                    "secondary": window((rate or {}).get("secondary_window")),
                    "credits": credits,
                    "snapshot_ts": stamp,
                }

            limits.append(normalized("codex", "Codex", raw.get("rate_limit"), raw.get("credits")))
            for extra in raw.get("additional_rate_limits") or []:
                limits.append(normalized(
                    extra.get("metered_feature") or extra.get("limit_name"),
                    extra.get("limit_name"), extra.get("rate_limit")))
            with self._lock:
                self._direct_limits = limits
                self._direct_snapshot_ts = stamp
                self._api_error = None
        except (OSError, ValueError, urllib.error.URLError) as exc:
            with self._lock:
                self._api_error = str(exc)

    @staticmethod
    def _scope_start(scope):
        now = dt.datetime.now().astimezone()
        if scope == "dia":
            return now.replace(hour=0, minute=0, second=0, microsecond=0)
        if scope == "semana":
            return (now - dt.timedelta(days=now.weekday())).replace(
                hour=0, minute=0, second=0, microsecond=0)
        if scope == "mes":
            return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return None

    @staticmethod
    def _summarize(events, start=None):
        selected = []
        for event in events:
            when = _parse_iso(event.get("timestamp"))
            if not when or (start and when < start):
                continue
            selected.append(event)
        by_model = {}
        out = {
            "total_tokens": 0,
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_output_tokens": 0,
            "calls": len(selected),
            "by_model": [],
        }
        for event in selected:
            for key in ("total_tokens", "input_tokens", "cached_input_tokens",
                        "output_tokens", "reasoning_output_tokens"):
                out[key] += event[key]
            model = event["model"]
            by_model[model] = by_model.get(model, 0) + event["total_tokens"]
        out["by_model"] = [
            {"model": model, "tokens": tokens}
            for model, tokens in sorted(by_model.items(), key=lambda p: p[1], reverse=True)
        ]
        return out

    @staticmethod
    def _daily_series(events, days=30):
        totals = {}
        for event in events:
            when = _parse_iso(event.get("timestamp"))
            if not when:
                continue
            day = when.astimezone().strftime("%Y-%m-%d")
            totals[day] = totals.get(day, 0) + event["total_tokens"]
        return [{"day": day, "tokens": totals[day]} for day in sorted(totals)[-days:]]

    @staticmethod
    def _heatmap(events):
        cells = {}
        active_days = {}
        for event in events:
            when = _parse_iso(event.get("timestamp"))
            if not when:
                continue
            local = when.astimezone()
            # Python: segunda=0; o frontend usa domingo=0.
            dow = (local.weekday() + 1) % 7
            key = (dow, local.hour)
            cells[key] = cells.get(key, 0) + event["total_tokens"]
            active_days.setdefault(key, set()).add(local.date())
        return [
            {"dow": dow, "hour": hour,
             "avg_tokens": tokens / len(active_days[(dow, hour)])}
            for (dow, hour), tokens in sorted(cells.items())
        ]

    def state(self):
        with self._lock:
            records = [entry[1] for entry in self._files.values()]
            events = []
            latest_limits = {}
            native_sessions = set()
            resumed_imports = set()
            for record in records:
                if record.get("model_provider") != "openai":
                    continue
                sid = record.get("session_id")
                if record["events"]:
                    events.extend(record["events"])
                    if sid in self._imported_ids:
                        resumed_imports.add(sid)
                    elif sid:
                        native_sessions.add(sid)
                for timestamp, rate in record["limits"]:
                    key = rate.get("limit_id")
                    previous = latest_limits.get(key)
                    if not previous or (timestamp or "") > (previous[0] or ""):
                        latest_limits[key] = (timestamp, rate)

            limits = []
            for timestamp, rate in sorted(latest_limits.values(), key=lambda p: p[0] or "", reverse=True):
                def window(data):
                    if not data:
                        return None
                    return {
                        "used_percent": data.get("used_percent"),
                        "window_minutes": data.get("window_minutes"),
                        "resets_at": _reset_iso(data.get("resets_at")),
                    }
                limits.append({
                    "id": rate.get("limit_id"),
                    "name": rate.get("limit_name"),
                    "plan_type": rate.get("plan_type"),
                    "primary": window(rate.get("primary")),
                    "secondary": window(rate.get("secondary")),
                    "credits": rate.get("credits"),
                    "snapshot_ts": timestamp,
                })

            if self._direct_limits is not None:
                limits = self._direct_limits
            now = dt.datetime.now(dt.timezone.utc)
            recent = self._summarize(events, now - dt.timedelta(hours=2))
            return {
                "available": bool(records),
                "generated_at": now.isoformat(),
                "limits": limits,
                "history": {
                    "dia": self._summarize(events, self._scope_start("dia")),
                    "semana": self._summarize(events, self._scope_start("semana")),
                    "mes": self._summarize(events, self._scope_start("mes")),
                    "geral": self._summarize(events),
                },
                "burn_tokph": recent["total_tokens"] / 2,
                "daily": self._daily_series(events),
                "heatmap": self._heatmap(events),
                "native_sessions": len(native_sessions),
                "resumed_imported_sessions": len(resumed_imports),
                "ignored_imported_sessions": max(0, len(self._imported_ids) - len(resumed_imports)),
                "limits_source": "chatgpt" if self._direct_limits is not None else "logs",
                "limits_refresh_seconds": LIMIT_REFRESH_SECONDS,
                "limits_error": self._api_error,
            }
