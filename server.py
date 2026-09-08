"""
server.py — monta o estado do painel e serve o dashboard em localhost.
Poller em thread separada chama a API de uso em ritmo adaptativo (mais rapido
perto do limite, mais lento com folga) e grava snapshots; o servidor le sempre o
ultimo snapshot do banco, entao a interface nunca depende da rede.
"""

import datetime as dt
import json
import mimetypes
import os
import shutil
import threading
import time
import traceback
import urllib.request
from datetime import timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import auth
import codex_usage
import forecast
import store as store_mod
import usage_api
import errors
import updates
import re

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_DIST = os.path.join(HERE, "web", "dist")  # build do frontend React/Konsta

LEGACY_WINDOW_KEYS = usage_api.LEGACY_WINDOW_KEYS
WINDOW_LABELS = usage_api.WINDOW_LABELS
# Rotulos das cotas do Claude em quotas[] (mesmo padrao "produto · janela"
# das do Codex). Janelas por modelo usam o nome que o servidor manda.
QUOTA_LABELS = {"five_hour": "Sessão · 5 horas", "seven_day": "Semana · 7 dias",
                "seven_day_sonnet": "Sonnet · 7 dias", "seven_day_opus": "Opus · 7 dias"}
_CODEX_SHORT = re.compile(r"^gpt-[\d.]+-codex-(.+)$", re.IGNORECASE)


class Ctx:
    store = None
    codex = None
    cfg = None
    token_store = None
    last_error = None         # frase pra UI (errors.humanize)
    last_error_detail = None  # 'Tipo: mensagem original', so' pra depuracao
    last_poll = None
    auth_connected = False
    _login_thread = None
    fx_rate = None   # cotacao USD->BRL (cache de 1h)
    fx_ts = 0.0
    profile = None   # perfil da conta (cache de 6h) — p/ detectar a licenca
    profile_ts = 0.0
    rate_limited = False   # 429 em curso (transitorio, NAO e desconexao)
    rate_limit_hits = 0    # 429s seguidos — alimenta o backoff exponencial
    next_poll_at = 0.0     # timestamp do proximo poll permitido
    boot_retries = 0       # falhas antes da PRIMEIRA leitura boa (pos-boot)
    ignored_logged = False # ja avisou (uma vez por boot) o que a API manda e nao vira cota


# Ritmo do poll da API de uso. NAO e configuravel de proposito: o limite de
# requisicoes e da CONTA INTEIRA (cada sessao aberta do Claude Code soma no
# mesmo balde), entao deixar o usuario baixar o intervalo so derruba o painel.
#
# Ele e adaptativo porque o numero so precisa estar fresco quando muda decisao.
# Perto do teto ele anda ~1 ponto/min em uso pesado, entao 5 min de defasagem
# viram 5 pontos de erro — exatamente na hora de decidir se continua. Com folga,
# a mesma defasagem nao muda nada e o que importa e poupar requisicao.
POLL_HOT = 90                # >= 80% usado: leitura quase ao vivo
POLL_WARM = 180              # 50-80%: ritmo intermediario
POLL_COLD = 300              # < 50%: economiza requisicoes
POLL_SECONDS = POLL_COLD     # ritmo de referencia / fallback
BACKOFF_MAX_S = 1800         # teto do backoff quando toma 429 (30 min)
MANUAL_MIN_INTERVAL_S = 60   # intervalo minimo entre "Atualizar" manuais

# Uma leitura por vez: sem isso o botao Atualizar dispara em paralelo com o
# poller e as duas requisicoes contam dobrado no rate limit.
_poll_lock = threading.Lock()


FX_URL = "https://economia.awesomeapi.com.br/json/last/USD-BRL"


def refresh_fx(log=print):
    """Atualiza a cotacao USD->BRL se o cache tiver mais de 1h.
    `usd_brl` no config.json, quando definido, tem prioridade (fixo)."""
    if time.time() - Ctx.fx_ts < 3600:
        return
    try:
        with urllib.request.urlopen(FX_URL, timeout=6) as r:
            data = json.loads(r.read().decode("utf-8"))
        Ctx.fx_rate = float(data["USDBRL"]["bid"])
        Ctx.fx_ts = time.time()
    except Exception as e:
        Ctx.fx_ts = time.time() - 3000  # tenta de novo em ~10min
        log(f"[fx] cotacao indisponivel: {e}")


# --- montagem do estado ------------------------------------------------------

def fx_rate():
    """Cotacao USD->BRL em vigor: fixa no config.json (`usd_brl`) ou a
    automatica. UNICO ponto de conversao do backend — o resto so' consome."""
    return Ctx.cfg.get("usd_brl") or Ctx.fx_rate


def _brl(usd, fx):
    return None if usd is None or not fx else usd * fx


def _with_brl(summary: dict, fx) -> dict:
    """Acrescenta *_brl a um resumo de custo (que continua em USD): a UI nunca
    deve multiplicar por cambio por conta propria. Sem cotacao, vem null."""
    if not summary:
        return summary
    out = dict(summary)
    for k in ("total_cost", "cost_per_hour"):
        if k in out:
            out[k + "_brl"] = _brl(out.get(k), fx)
    if isinstance(out.get("by_model"), list):
        out["by_model"] = [dict(m, cost_brl=_brl(m.get("cost"), fx)) for m in out["by_model"]]
    if isinstance(out.get("by_source"), dict):
        out["by_source"] = {s: dict(d, cost_brl=_brl(d.get("cost"), fx))
                            for s, d in out["by_source"].items()}
    return out


def _claude_states(latest, hourly_profile) -> dict:
    """Projecao + semaforo de TODAS as janelas do ultimo snapshot, inclusive
    as por modelo (seven_day_model:*) e as sem dado (viram INDETERMINADO em
    vez de sumir)."""
    states = {}
    for key, wd in ((latest or {}).get("windows") or {}).items():
        hrs = usage_api.window_hours_for(key)
        util = wd.get("utilization")
        snap_rate = Ctx.store.snapshot_rate(key) if util is not None else None
        st = forecast.smart_project_window(util, wd.get("resets_at"), hrs,
                                           hourly_profile, snap_rate=snap_rate)
        st["label"] = wd.get("label") or WINDOW_LABELS.get(key) or key
        st["window"] = key
        st["window_hours"] = hrs
        states[key] = st
    return states


def _codex_short_name(limit) -> str:
    if limit.get("id") == "codex":
        return "Codex"
    name = limit.get("name") or limit.get("id") or "Codex"
    m = _CODEX_SHORT.match(name)
    return m.group(1) if m else name


def _hours_label(hours) -> str:
    return "7 dias" if round(hours) == 168 else f"{int(round(hours))} horas"


def _codex_states(cg) -> list:
    """Um estado por (limite x primary/secondary) do Codex, com projecao e
    semaforo pelo MESMO forecast das janelas do Claude. A taxa vem da serie
    de limit_history (reset detectado por queda, porque com 0% o resets_at
    do Codex e' rolante e muda a cada poll)."""
    if not cg:
        return []
    profile = cg.get("hour_of_day") or []
    history = cg.get("limit_history") or []
    out = []
    for limit in cg.get("limits") or []:
        short = _codex_short_name(limit)
        for wk in ("primary", "secondary"):
            w = limit.get(wk)
            if not w:
                continue
            hrs = ((w.get("window_minutes") or 0) / 60) or 168
            series = []
            for h in history:
                for l in h.get("limits") or []:
                    if l.get("id") != limit.get("id"):
                        continue
                    lw = l.get(wk)
                    if lw and lw.get("used_percent") is not None:
                        series.append((h.get("ts"), lw.get("used_percent")))
            rate = forecast.series_rate(series)
            st = forecast.smart_project_window(w.get("used_percent"), w.get("resets_at"),
                                               hrs, profile, snap_rate=rate)
            st.update({
                "key": f"codex:{limit.get('id')}:{wk}",
                "label": f"{short} · {_hours_label(hrs)}",
                "product": limit.get("name"),
                "window_hours": hrs,
                "snapshot_ts": limit.get("snapshot_ts"),
            })
            out.append(st)
    return out


def _quota_item(provider, key, source_key, label, product, window_hours, st, snapshot_ts):
    return {
        "provider": provider,
        "key": key,
        "source_key": source_key,
        "label": label,
        "product": product,
        "window_hours": window_hours,
        "utilization": st.get("utilization"),
        "resets_at": st.get("resets_at"),
        "hours_to_reset": st.get("hours_to_reset"),
        "projected": st.get("projected"),
        "projected_linear": st.get("projected_linear"),
        "status": st.get("status"),
        "rate": st.get("rate"),
        "eta_100": st.get("eta_100"),
        "smart": st.get("smart"),
        "snapshot_ts": snapshot_ts,
    }


def _claude_order(key):
    # sessao, semana geral, janelas por modelo (alfabetico), demais legadas
    if key == "five_hour":
        return (0, key)
    if key == "seven_day":
        return (1, key)
    if key.startswith(usage_api.MODEL_WINDOW_PREFIX):
        return (2, key)
    return (3, key)


def _quotas(claude_states, codex_states, snapshot_ts) -> list:
    """Contrato unico Claude + Codex: mesma forma para os dois provedores."""
    items = []
    for key in sorted(claude_states, key=_claude_order):
        st = claude_states[key]
        if key.startswith(usage_api.MODEL_WINDOW_PREFIX):
            product = st.get("label")
            label = f"{product} · 7 dias"
        else:
            product = None
            label = QUOTA_LABELS.get(key) or st.get("label") or key
        items.append(_quota_item("claude", f"claude:{key}", key, label, product,
                                 st.get("window_hours"), st, snapshot_ts))
    for st in codex_states:
        items.append(_quota_item("codex", st["key"], st["key"].split(":", 1)[1],
                                 st["label"], st.get("product"), st.get("window_hours"),
                                 st, st.get("snapshot_ts")))
    return items


def build_state() -> dict:
    cfg = Ctx.cfg
    fx = fx_rate()
    latest = Ctx.store.latest_state()
    hourly_profile = Ctx.store.hour_of_day_avg()
    states = _claude_states(latest, hourly_profile)
    # /api/state.windows continua com a forma antiga (so chaves classicas com
    # valor numerico): e' o que Tauri e Stream Deck ja publicados esperam.
    windows_out = {k: st for k, st in states.items()
                   if k in LEGACY_WINDOW_KEYS and st.get("utilization") is not None}
    chatgpt = Ctx.codex.state() if Ctx.codex else None
    quotas = _quotas(states, _codex_states(chatgpt), latest["ts"] if latest else None)

    burn = Ctx.store.recent_tokph(2)
    burn_src = Ctx.store.recent_burn_by_source(2)
    dominant = max(burn, key=burn.get) if burn else None
    verdict = (forecast.switch_verdict(
        states, dominant, cfg.get("intended_hours", 2.0),
        target=cfg.get("switch_target"),
        candidates={k for k in (forecast._model_key(m) for m in burn) if k})
        if windows_out else None)

    extra = None
    if latest and latest.get("extra_usage"):
        eu = latest["extra_usage"]
        div = cfg.get("credits_divisor", 100) or 1
        extra = {
            "used": (eu["used_credits"] / div) if eu.get("used_credits") is not None else None,
            "limit": (eu["monthly_limit"] / div) if eu.get("monthly_limit") is not None else None,
            # Moeda dos creditos = a que a API informa (a conta pode ser
            # cobrada em BRL ou USD). Sem informacao, assume USD — nunca a
            # moeda de EXIBICAO do painel (cfg["currency"]), que foi o que
            # fazia o painel somar dolar com real.
            "currency": eu.get("currency") or "USD",
        }
        to_brl = (lambda v: v) if extra["currency"] == "BRL" else (lambda v: _brl(v, fx))
        extra["used_brl"] = to_brl(extra["used"])
        extra["limit_brl"] = to_brl(extra["limit"])
        # licenca x extras: mede pelo historico de used_credits dos snapshots
        try:
            series = Ctx.store.extra_credit_series(24)
        except Exception:
            series = []
        rate = spent_24h = None
        burning = False
        if len(series) >= 2:
            def _t(s):
                return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
            t_last = _t(series[-1][0])
            uc_last = series[-1][1]
            spent_24h = max(0.0, (uc_last - series[0][1]) / div)
            span_h = (t_last - _t(series[0][0])).total_seconds() / 3600
            if span_h >= 0.5:
                rate = max(0.0, (uc_last - series[0][1]) / div / span_h)
            # "queimando agora" = credito subiu entre os DOIS ultimos snapshots.
            # Comparar so o par mais recente faz a fonte voltar a "licenca" um
            # ciclo de poll apos o reset; o caso "estourado mas ocioso" e coberto
            # pelo cheque de utilizacao >= 100% logo abaixo.
            burning = uc_last - series[-2][1] > 0
        # janela estourada (>=100%) = qualquer requisicao nova ja sai dos extras
        if not burning:
            for st in states.values():
                if (st.get("utilization") or 0) >= 100:
                    burning = True
                    break
        extra.update({"rate_per_hour": rate, "spent_24h": spent_24h,
                      "rate_per_hour_brl": to_brl(rate),
                      "spent_24h_brl": to_brl(spent_24h),
                      "burning": burning})

    history = {sc: _with_brl(Ctx.store.scope_summary(sc), fx)
               for sc in ("geral", "mes", "semana", "dia")}

    semrush = {
        "balance": cfg.get("semrush_units_balance"),
        "limit": cfg.get("semrush_units_limit"),
        "updated_at": cfg.get("semrush_units_updated_at"),
    }

    org = (Ctx.profile or {}).get("organization") or {}
    plan = {
        "selected": cfg.get("plan"),
        "detected": detect_plan(Ctx.profile),
        "org_name": org.get("name"),
        "rate_limit_tier": org.get("rate_limit_tier"),
        "seat_tier": org.get("seat_tier"),
    }

    return {
        "generated_at": dt.datetime.now(timezone.utc).isoformat(),
        "snapshot_ts": latest["ts"] if latest else None,
        "auth_connected": Ctx.auth_connected,
        "rate_limited": Ctx.rate_limited,
        "retry_in": max(0, int(Ctx.next_poll_at - time.time())) if Ctx.rate_limited else 0,
        "last_error": Ctx.last_error,
        "last_error_detail": Ctx.last_error_detail,
        "windows": windows_out,
        "quotas": quotas,
        "switch": verdict,
        "burn_tokph": {k: round(v) for k, v in burn.items()},
        "burn_by_source": {m: {s: round(v) for s, v in d.items()}
                           for m, d in burn_src.items()},
        "dominant_model": dominant,
        "extra_usage": extra,
        "history": history,
        "chatgpt": chatgpt,
        "plan": plan,
        "update": updates.state(),
        "semrush": semrush,
        "config": {"refresh_seconds": _poll_interval(),
                   "intended_hours": cfg.get("intended_hours"),
                   "currency": cfg.get("currency"),
                   "usd_brl": fx,
                   "subscription_brl": cfg.get("subscription_brl"),
                   "chatgpt_subscription_brl": cfg.get("chatgpt_subscription_brl"),
                   "chatgpt_extra_brl": cfg.get("chatgpt_extra_brl") if cfg.get("chatgpt_extra_month") == dt.datetime.now().strftime("%Y-%m") else None},
    }


# --- poller ------------------------------------------------------------------

def refresh_profile(log=print):
    """Perfil da conta, cacheado por 6h — muda raramente."""
    if time.time() - Ctx.profile_ts < 6 * 3600:
        return
    try:
        Ctx.profile = usage_api.fetch_profile(Ctx.token_store, log=log)
        Ctx.profile_ts = time.time()
    except Exception as e:
        Ctx.profile_ts = time.time() - 5.5 * 3600  # tenta de novo em ~30min
        log(f"[profile] indisponivel: {e}")


def detect_plan(profile):
    """Mapeia (organization_type, rate_limit_tier) num id de licenca conhecido."""
    org = (profile or {}).get("organization") or {}
    acct = (profile or {}).get("account") or {}
    ot = (org.get("organization_type") or "").lower()
    tier = (org.get("rate_limit_tier") or "").lower()
    if "enterprise" in ot:
        return "enterprise"
    if "team" in ot:
        # assento com limites estilo Max = premium (Claude Code incluso)
        return "team_premium" if "max" in tier else "team_standard"
    if acct.get("has_claude_max") or "max" in ot or "max" in tier:
        return "max_20x" if "20x" in tier else "max_5x"
    if acct.get("has_claude_pro") or "pro" in tier:
        return "pro"
    return None


def _retry_after_falha():
    """Quanto esperar depois de uma falha que NAO e rate limit.

    Enquanto nunca houve uma leitura boa, a causa provavel e ambiente (rede ou
    disco ainda subindo depois do boot), nao credencial morta — entao tenta de
    novo rapido, dobrando ate o ritmo normal. Se a credencial estiver mesmo
    morta, converge para 5 min em vez de martelar."""
    if Ctx.last_poll is None:
        Ctx.boot_retries += 1
        return min(POLL_COLD, 15 * (2 ** (Ctx.boot_retries - 1)))
    return POLL_COLD


def _poll_interval():
    """Escolhe o ritmo pelo pico de utilizacao do ultimo snapshot."""
    try:
        latest = Ctx.store.latest_state()
        janelas = (latest or {}).get("windows") or {}
        pico = max((w.get("utilization") or 0) for w in janelas.values()) if janelas else 0
    except Exception:
        pico = 0
    if pico >= 80:
        return POLL_HOT
    if pico >= 50:
        return POLL_WARM
    return POLL_COLD


def _schedule_backoff(retry_after=None):
    """Espera crescente depois de um 429, respeitando o Retry-After do servidor."""
    Ctx.rate_limit_hits += 1
    espera = POLL_SECONDS * (2 ** (Ctx.rate_limit_hits - 1))
    if retry_after:
        espera = max(espera, float(retry_after))
    espera = min(BACKOFF_MAX_S, espera)
    Ctx.next_poll_at = time.time() + espera
    return espera


def poll_once(log=print):
    """Escaneia logs e grava um snapshot da API.

    Regra central: 429 NAO e desconexao. O token segue valido; o que houve foi
    limite de requisicoes da conta. Marcar auth_connected=False aqui fazia o
    painel pedir reconexao, e reconectar gera MAIS requisicoes — o remedio
    piorava a doenca."""
    if not _poll_lock.acquire(blocking=False):
        log("[poll] ja existe uma leitura em andamento, pulando")
        return
    try:
        refresh_fx(log)
        refresh_profile(log)
        updates.check(log=log)
        try:
            Ctx.store.scan(log=lambda *a: None)
        except Exception as e:
            log(f"[poll] scan falhou: {e}")
        try:
            raw = usage_api.fetch_usage(Ctx.token_store, log=log)
            norm = usage_api.normalize(raw)
            Ctx.store.insert_snapshot(norm)
            if not Ctx.ignored_logged and (norm.get("ignored_keys") or norm.get("ignored_limits")):
                # uma vez por boot: o que a API mandou e nao virou cota. Foi a
                # falta disto que escondeu a janela por modelo por semanas.
                log(f"[usage] chaves da API que nao viram cota: {norm.get('ignored_keys')}; "
                    f"limits[] ignorados: {norm.get('ignored_limits')}")
                Ctx.ignored_logged = True
            Ctx.auth_connected = True
            Ctx.rate_limited = False
            Ctx.rate_limit_hits = 0
            Ctx.last_error = None
            Ctx.last_error_detail = None
            Ctx.last_poll = time.time()
            Ctx.boot_retries = 0
            Ctx.next_poll_at = time.time() + _poll_interval()
        except usage_api.RateLimited as e:
            # transitorio: preserva auth_connected e o ultimo snapshot bom
            espera = _schedule_backoff(e.retry_after)
            Ctx.rate_limited = True
            Ctx.last_error = (
                f"Limite de requisições da conta; nova tentativa em {int(espera)}s.")
            Ctx.last_error_detail = f"RateLimited: retry_after={e.retry_after}"
            log(f"[poll] 429 ({Ctx.rate_limit_hits}x), aguardando {int(espera)}s")
        except auth.TransientAuthError as e:
            espera = _schedule_backoff()
            Ctx.rate_limited = True
            Ctx.last_error = f"Renovação do token adiada; nova tentativa em {int(espera)}s."
            Ctx.last_error_detail = f"TransientAuthError: {e}"
            log(f"[poll] falha transitoria ao renovar: {e}")
        except auth.AuthError as e:
            Ctx.auth_connected = False
            Ctx.rate_limited = False
            Ctx.last_error = "Conta desconectada: é preciso reconectar."
            Ctx.last_error_detail = f"AuthError: {e}"
            Ctx.next_poll_at = time.time() + _retry_after_falha()
            log(f"[poll] sem token valido: {e}")
        except Exception as e:
            # rede/servidor: nao mexe em auth_connected, so tenta mais tarde
            espera = _retry_after_falha()
            Ctx.last_error, Ctx.last_error_detail = errors.humanize(e, retry_in=espera)
            Ctx.next_poll_at = time.time() + espera
            log(f"[poll] erro: {Ctx.last_error_detail}")
    finally:
        _poll_lock.release()


def poller_loop():
    while True:
        time.sleep(5)
        if time.time() >= Ctx.next_poll_at:
            poll_once()


SCAN_INTERVAL = 10  # scan incremental frequente para o total "quase em tempo real"


def scan_loop():
    while True:
        time.sleep(SCAN_INTERVAL)
        try:
            Ctx.store.scan(log=lambda *a: None)
            if Ctx.codex:
                Ctx.codex.scan()
                Ctx.codex.refresh_limits()
        except Exception as e:
            print(f"[scan_loop] {e}")


def start_login_thread():
    if Ctx._login_thread and Ctx._login_thread.is_alive():
        return
    def _run():
        try:
            auth.run_pkce_login(Ctx.token_store,
                                callback_port=Ctx.cfg.get("callback_port", 54545))
            poll_once()
        except Exception as e:
            msg, detail = errors.humanize(e)
            Ctx.last_error = f"Falha ao reconectar a conta: {msg}"
            Ctx.last_error_detail = f"login: {detail}"
    Ctx._login_thread = threading.Thread(target=_run, daemon=True)
    Ctx._login_thread.start()


# --- HTTP handler ------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_dist(self, urlpath):
        """Serve o build do React (web/dist) com fallback SPA para index.html."""
        rel = urlpath.lstrip("/") or "index.html"
        full = os.path.normpath(os.path.join(WEB_DIST, rel))
        if not full.startswith(WEB_DIST):  # protege contra path traversal
            self._send(403, "forbidden", "text/plain")
            return
        if not os.path.isfile(full):
            full = os.path.join(WEB_DIST, "index.html")  # fallback SPA
        if not os.path.isfile(full):
            self._send(404, "Build nao encontrado. Rode: npm --prefix web run build",
                       "text/plain")
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text") or "javascript" in ctype or "json" in ctype:
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype)

    def do_GET(self):
        p = self.path.split("?")[0]
        try:
            if p == "/api/health":
                self._send(200, json.dumps({"app": "ai-usage", "protocol": 1,
                                            "version": updates.current_version(),
                                            "features": ["quotas", "brl", "label", "errors", "update"]}))
            elif p == "/api/state":
                self._send(200, json.dumps(build_state(), ensure_ascii=False))
            elif p == "/api/total":
                self._send(200, json.dumps(_with_brl(Ctx.store.total_summary(), fx_rate()),
                                           ensure_ascii=False))
            elif p == "/api/history":
                fx = fx_rate()
                out = {
                    "snapshots": Ctx.store.snapshot_history(48),
                    "daily": [dict(d, cost_brl=_brl(d.get("cost"), fx))
                              for d in Ctx.store.daily_series(Ctx.cfg.get("daily_days", 30))],
                    "hour_of_day": Ctx.store.hour_of_day_avg(),
                    "heatmap": [dict(c, avg_cost_brl=_brl(c.get("avg_cost"), fx))
                                for c in Ctx.store.heatmap_data()],
                }
                self._send(200, json.dumps(out, ensure_ascii=False))
            elif p == "/auth/start":
                start_login_thread()
                self._send(200, json.dumps({"started": True}))
            else:
                self._serve_dist(p)  # SPA React (web/dist)
        except Exception:
            self._send(500, json.dumps({"error": traceback.format_exc()}))

    def do_POST(self):
        p = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            body = {}
        if p == "/api/config":
            # refresh_seconds saiu de proposito: o ritmo da API e fixo
            # (POLL_SECONDS) para nao estourar o limite da conta.
            for key in ("chatgpt_subscription_brl", "chatgpt_extra_brl"):
                if key in body and (isinstance(body[key], bool) or not isinstance(body[key], (int, float)) or not 0 <= body[key] < float("inf")):
                    self._send(400, json.dumps({"error": "valor monetario invalido"}))
                    return
            for k in ("intended_hours", "subscription_brl", "chatgpt_subscription_brl", "chatgpt_extra_brl", "plan"):
                if k in body:
                    Ctx.cfg[k] = body[k]
            if "chatgpt_extra_brl" in body:
                Ctx.cfg["chatgpt_extra_month"] = dt.datetime.now().strftime("%Y-%m")
            save_config(Ctx.cfg)
            self._send(200, json.dumps({"ok": True, "config": Ctx.cfg}))
        elif p == "/api/semrush":
            if "balance" in body and isinstance(body["balance"], (int, float)):
                Ctx.cfg["semrush_units_balance"] = int(body["balance"])
                Ctx.cfg["semrush_units_updated_at"] = dt.datetime.now(timezone.utc).isoformat()
                save_config(Ctx.cfg)
            self._send(200, json.dumps({"ok": True}))
        elif p == "/api/refresh":
            # respeita o backoff e um intervalo minimo: o botao Atualizar nao
            # pode virar um jeito de furar o rate limit da conta
            agora = time.time()
            espera = Ctx.next_poll_at - agora
            minimo = max(30, min(MANUAL_MIN_INTERVAL_S, _poll_interval() // 2))
            recente = Ctx.last_poll and (agora - Ctx.last_poll) < minimo
            if Ctx.rate_limited and espera > 0:
                self._send(200, json.dumps({
                    "ok": False, "reason": "rate_limited",
                    "retry_in": int(espera)}))
            elif recente:
                restante = minimo - (agora - Ctx.last_poll)
                self._send(200, json.dumps({
                    "ok": False, "reason": "muito_recente",
                    "retry_in": int(restante)}))
            else:
                poll_once()
                if Ctx.codex:
                    Ctx.codex.scan()
                    Ctx.codex.refresh_limits(force=True)
                self._send(200, json.dumps({"ok": True}))
        else:
            self._send(404, "not found", "text/plain")


# --- config + run ------------------------------------------------------------

DATA_DIR = os.environ.get("AI_USAGE_DATA_DIR", HERE)
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
DEFAULTS = {"port": 8090, "refresh_seconds": 120, "currency": "BRL",
            "credits_divisor": 100, "intended_hours": 2.0, "daily_days": 30,
            "callback_port": 54545, "open_browser": True,
            "semrush_units_balance": None, "semrush_units_limit": 49190,
            "semrush_units_updated_at": None,
            "usd_brl": None,  # None = cotacao automatica (AwesomeAPI, cache 1h)
            "subscription_brl": None,  # valor mensal da licenca (R$), p/ balanço
            "chatgpt_subscription_brl": None,  # assinatura ChatGPT; comparacao por uso da cota
            "plan": None}  # id da licenca (pro, max_5x, ...); None = usar a detectada


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    cfg["refresh_seconds"] = POLL_SECONDS  # fixo; ignora valor antigo do arquivo
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


def _db_path():
    """O sqlite precisa de disco local: rodar o painel de dentro de um drive
    sincronizado (Google Drive etc.) deixa cada write segurando o lock por
    segundos e o /api/state estoura o timeout dos clientes (Stream Deck)."""
    if os.environ.get("AI_USAGE_DATA_DIR"):
        return os.path.join(DATA_DIR, "painel.db")
    base = (os.environ.get("LOCALAPPDATA")
            or os.path.join(os.path.expanduser("~"), ".local", "share"))
    d = os.path.join(base, "claude-usage")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        return os.path.join(HERE, "painel.db")
    path = os.path.join(d, "painel.db")
    legacy = os.path.join(HERE, "painel.db")
    if not os.path.exists(path) and os.path.exists(legacy):
        try:
            shutil.copy2(legacy, path)
        except OSError:
            pass
    return path


def run(desktop=False, port=None, collect=True):
    Ctx.cfg = load_config()
    if desktop:
        Ctx.cfg.update(host="127.0.0.1", port=port or 8090, open_browser=False)
    Ctx.store = store_mod.Store(_db_path())
    Ctx.codex = codex_usage.CodexUsage(_db_path())
    Ctx.token_store = auth.TokenStore(DATA_DIR)

    def initialize():
        Ctx.codex.scan()
        Ctx.codex.refresh_limits(force=True)
        poll_once()
        threading.Thread(target=poller_loop, daemon=True).start()
        threading.Thread(target=scan_loop, daemon=True).start()

    if collect:
        if desktop:
            threading.Thread(target=initialize, daemon=True).start()
        else:
            initialize()

    port = int(Ctx.cfg.get("port", 8090))
    host = Ctx.cfg.get("host", "127.0.0.1")
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://localhost:{port}"
    print(f"[painel] rodando em {url}  (refresh={Ctx.cfg['refresh_seconds']}s)")
    if Ctx.cfg.get("open_browser"):
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
    httpd.serve_forever()


if __name__ == "__main__":
    run()
