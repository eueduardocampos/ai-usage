"""
server.py — monta o estado do painel e serve o dashboard em localhost.
Poller em thread separada chama a API de uso em ritmo adaptativo (mais rapido
perto do limite, mais lento com folga) e grava snapshots; o servidor le sempre o
ultimo snapshot do banco, entao a interface nunca depende da rede.
"""

import datetime as dt
import json
import math
import mimetypes
import os
import shutil
import threading
import time
import traceback
import urllib.request
from datetime import timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import accounts as accounts_mod
import auth
import codex_usage
import hwmon
import forecast
import machines
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
    # Ctx tambem e' o estado de leitura da conta PRINCIPAL do Claude (mesmos
    # campos de accounts.Runtime); as outras contas tem um Runtime proprio.
    id = accounts_mod.PRINCIPAL
    accounts = None  # accounts.AccountManager
    logins = None    # accounts.LoginBroker
    hw = None        # hwmon.HardwareMonitor
    store = None
    codex = None
    sync = None      # machines.MachineSync quando sync_dir esta configurado
    db_path = None   # onde o sqlite esta' de verdade (vai no log e no /api/health)
    token_path = None
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


def _sem_nan(o):
    """Troca NaN/Infinity por null. JSON nao tem esses valores: o json.dumps do
    Python os emite e o JSON.parse do navegador recusa a resposta INTEIRA."""
    if isinstance(o, float) and not math.isfinite(o):
        return None
    if isinstance(o, dict):
        return {k: _sem_nan(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_sem_nan(v) for v in o]
    return o


def _json(o) -> str:
    try:
        return json.dumps(o, ensure_ascii=False, allow_nan=False)
    except ValueError:
        return json.dumps(_sem_nan(o), ensure_ascii=False, allow_nan=False)


def _rejeita_constante(nome):
    raise ValueError(f"valor nao permitido em JSON: {nome}")


def json_loads_estrito(raw):
    """json.loads que recusa NaN/Infinity (entrada do /api/config)."""
    return json.loads(raw, parse_constant=_rejeita_constante)


PLANOS = ("pro", "max_5x", "max_20x", "team_standard", "team_premium", "enterprise")
# campo -> (minimo, maximo, aceita null). Horizonte do veredito entre 15 min e 24 h.
CONFIG_NUMEROS = {"intended_hours": (0.25, 24, False), "subscription_brl": (0, 1e6, True),
                  "chatgpt_subscription_brl": (0, 1e6, True), "chatgpt_extra_brl": (0, 1e6, True)}


def valida_config(body):
    """Devolve (mudancas, erro). Um valor invalido gravado no config.json
    derrubava /api/state ate alguem editar o arquivo a mao."""
    if not isinstance(body, dict):
        return None, "corpo precisa ser um objeto JSON"
    out = {}
    for k, (lo, hi, nulo) in CONFIG_NUMEROS.items():
        if k not in body:
            continue
        v = body[k]
        if v is None and nulo:
            out[k] = None
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
            return None, f"{k}: use um numero entre {lo:g} e {hi:g}"
        out[k] = float(v) if k == "intended_hours" else v
    if "layout" in body:
        v = body["layout"]
        if v is None:
            out["layout"] = None
        else:
            if not isinstance(v, dict):
                return None, "layout precisa ser um objeto"
            lay = {}
            for k in ("order", "hidden", "collapsed", "wide"):
                lst = v.get(k, [])
                if not isinstance(lst, list) or len(lst) > 40 or not all(
                        isinstance(i, str) and re.fullmatch(r"[a-z0-9_-]{1,30}", i) for i in lst):
                    return None, f"layout.{k}: lista de ids curtos"
                lay[k] = lst
            preset = v.get("preset")
            if preset is not None and not (isinstance(preset, str) and re.fullmatch(r"[a-z0-9_-]{1,30}", preset)):
                return None, "layout.preset invalido"
            lay["preset"] = preset
            out["layout"] = lay
    if "plan" in body:
        v = body["plan"]
        if v is not None and v not in PLANOS:
            return None, f"plan: use um destes valores ou null: {', '.join(PLANOS)}"
        out["plan"] = v
    return out, None


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
    if isinstance(out.get("by_machine"), dict):
        out["by_machine"] = {m: dict(d, cost_brl=_brl(d.get("cost"), fx))
                             for m, d in out["by_machine"].items()}
    return out


def _kw(rt):
    """Argumento de conta para o store. A principal nao passa nada (mantem a
    assinatura antiga, que testes e chamadas legadas usam)."""
    return {} if rt is Ctx or rt is None else {"account": rt.id}


def _claude_states(latest, hourly_profile, rt=None) -> dict:
    """Projecao + semaforo de TODAS as janelas do ultimo snapshot, inclusive
    as por modelo (seven_day_model:*) e as sem dado (viram INDETERMINADO em
    vez de sumir)."""
    states = {}
    for key, wd in ((latest or {}).get("windows") or {}).items():
        hrs = usage_api.window_hours_for(key)
        util = wd.get("utilization")
        snap_rate = Ctx.store.snapshot_rate(key, **_kw(rt)) if util is not None else None
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


def _extra_usage(latest, states, fx, cfg, rt=None):
    """Creditos extras (alem da licenca) de uma conta do Claude."""
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
            series = Ctx.store.extra_credit_series(24, **_kw(rt))
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

    return extra


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

    extra = _extra_usage(latest, states, fx, cfg)

    history = {sc: _with_brl(Ctx.store.scope_summary(sc), fx)
               for sc in ("geral", "mes", "semana", "dia")}

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
        # maquinas que somam no historico (None = recurso desligado)
        "machines": Ctx.sync.info() if Ctx.sync else None,
        "chatgpt": chatgpt,
        "plan": plan,
        "accounts": _accounts_state(fx, chatgpt) if Ctx.accounts else None,
        "update": updates.state(),
        "config": {"refresh_seconds": _poll_interval(),
                   "intended_hours": cfg.get("intended_hours"),
                   "currency": cfg.get("currency"),
                   "usd_brl": fx,
                   "subscription_brl": cfg.get("subscription_brl"),
                   "chatgpt_subscription_brl": cfg.get("chatgpt_subscription_brl"),
                   "layout": cfg.get("layout"),
                   "chatgpt_extra_brl": cfg.get("chatgpt_extra_brl") if cfg.get("chatgpt_extra_month") == dt.datetime.now().strftime("%Y-%m") else None},
    }


def _email(profile):
    acct = (profile or {}).get("account") or {}
    return acct.get("email") or acct.get("email_address")


def _account_summary(acc, fx=None, chatgpt=None, full=True):
    """Uma conta no formato unico do painel, Stream Deck e app nativo."""
    out = {"id": acc["id"], "provider": acc["provider"], "name": acc["name"],
           "app": acc.get("app"),
           "app_name": os.path.basename(acc["app"])[:-4] if (acc.get("app") or "").endswith(".app")
           else (os.path.basename(acc["app"]) if acc.get("app") else None)}
    if acc["provider"] == "claude":
        rt = Ctx.accounts.runtime(acc["id"])
        org = (rt.profile or {}).get("organization") or {}
        login = Ctx.logins.state_of(acc["id"]) if Ctx.logins else None
        has_token = bool(rt.token_store and rt.token_store.load())
        out.update({
            "connected": bool(rt.auth_connected),
            "has_token": has_token,
            "rate_limited": rt.rate_limited,
            "last_error": rt.last_error,
            "email": _email(rt.profile),
            "org_name": org.get("name"),
            "plan": detect_plan(rt.profile),
            "login": login,
            "snapshot_ts": None,
            "quotas": [],
        })
        if full:
            latest = Ctx.store.latest_state(**_kw(rt))
            states = _claude_states(latest, Ctx.store.hour_of_day_avg(), rt)
            out["snapshot_ts"] = latest["ts"] if latest else None
            out["quotas"] = _quotas(states, [], out["snapshot_ts"])
            out["extra_usage"] = _extra_usage(latest, states, fx, Ctx.cfg, rt)
    else:
        cg = chatgpt if chatgpt is not None else (Ctx.codex.state() if (full and Ctx.codex) else None)
        limits = (cg or {}).get("limits") or []
        out.update({
            "connected": bool(limits),
            "has_token": bool(limits),
            "rate_limited": False,
            "last_error": (cg or {}).get("limits_error"),
            "email": None,
            "plan": (limits[0].get("plan_type") if limits else None),
            "login": None,
            "quotas": _quotas({}, _codex_states(cg), None) if (full and cg) else [],
        })
    return out


def _accounts_state(fx=None, chatgpt=None, full=True):
    lst = [_account_summary(a, fx, chatgpt, full) for a in Ctx.accounts.accounts]
    # mesmo e-mail em duas contas = login feito no navegador errado
    vistos = {}
    for a in lst:
        if a.get("email"):
            vistos.setdefault(a["email"].lower(), []).append(a["id"])
    for a in lst:
        a["duplicate_of"] = None
        if a.get("email"):
            outros = [i for i in vistos[a["email"].lower()] if i != a["id"]]
            a["duplicate_of"] = outros[0] if outros else None
    return lst


# --- poller ------------------------------------------------------------------

def refresh_profile(log=print, rt=None):
    """Perfil da conta, cacheado por 6h — muda raramente."""
    rt = rt or Ctx
    if time.time() - rt.profile_ts < 6 * 3600:
        return
    try:
        rt.profile = usage_api.fetch_profile(rt.token_store, log=log)
        rt.profile_ts = time.time()
    except Exception as e:
        rt.profile_ts = time.time() - 5.5 * 3600  # tenta de novo em ~30min
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


def _retry_after_falha(rt=None):
    """Quanto esperar depois de uma falha que NAO e rate limit.

    Enquanto nunca houve uma leitura boa, a causa provavel e ambiente (rede ou
    disco ainda subindo depois do boot), nao credencial morta — entao tenta de
    novo rapido, dobrando ate o ritmo normal. Se a credencial estiver mesmo
    morta, converge para 5 min em vez de martelar."""
    rt = rt or Ctx
    if rt.last_poll is None:
        rt.boot_retries += 1
        return min(POLL_COLD, 15 * (2 ** (rt.boot_retries - 1)))
    return POLL_COLD


def _poll_interval(rt=None):
    """Escolhe o ritmo pelo pico de utilizacao do ultimo snapshot."""
    try:
        latest = Ctx.store.latest_state(**_kw(rt))
        janelas = (latest or {}).get("windows") or {}
        pico = max((w.get("utilization") or 0) for w in janelas.values()) if janelas else 0
    except Exception:
        pico = 0
    if pico >= 80:
        return POLL_HOT
    if pico >= 50:
        return POLL_WARM
    return POLL_COLD


def _schedule_backoff(retry_after=None, rt=None):
    """Espera crescente depois de um 429, respeitando o Retry-After do servidor."""
    rt = rt or Ctx
    rt.rate_limit_hits += 1
    espera = POLL_SECONDS * (2 ** (rt.rate_limit_hits - 1))
    if retry_after:
        espera = max(espera, float(retry_after))
    espera = min(BACKOFF_MAX_S, espera)
    rt.next_poll_at = time.time() + espera
    return espera


def poll_once(log=print, rt=None):
    """Escaneia logs e grava um snapshot da API.

    Regra central: 429 NAO e desconexao. O token segue valido; o que houve foi
    limite de requisicoes da conta. Marcar auth_connected=False aqui fazia o
    painel pedir reconexao, e reconectar gera MAIS requisicoes — o remedio
    piorava a doenca."""
    if not _poll_lock.acquire(blocking=False):
        log("[poll] ja existe uma leitura em andamento, pulando")
        return
    rt = rt or Ctx
    principal = rt is Ctx
    try:
        if principal:
            refresh_fx(log)
            updates.check(log=log)
            try:
                Ctx.store.scan(log=lambda *a: None)
            except Exception as e:
                log(f"[poll:{rt.id}] scan falhou: {e}")
        if rt.token_store is not None and rt.token_store.load() is None and not principal:
            # conta extra ainda sem login: nao ha o que ler
            rt.auth_connected = False
            rt.last_error = None
            rt.next_poll_at = time.time() + 30
            return
        refresh_profile(log, rt)
        try:
            raw = usage_api.fetch_usage(rt.token_store, log=log)
            norm = usage_api.normalize(raw)
            Ctx.store.insert_snapshot(norm, **_kw(rt))
            if not rt.ignored_logged and (norm.get("ignored_keys") or norm.get("ignored_limits")):
                # uma vez por boot: o que a API mandou e nao virou cota. Foi a
                # falta disto que escondeu a janela por modelo por semanas.
                log(f"[usage] chaves da API que nao viram cota: {norm.get('ignored_keys')}; "
                    f"limits[] ignorados: {norm.get('ignored_limits')}")
                rt.ignored_logged = True
            rt.auth_connected = True
            rt.rate_limited = False
            rt.rate_limit_hits = 0
            rt.last_error = None
            rt.last_error_detail = None
            rt.last_poll = time.time()
            rt.boot_retries = 0
            rt.next_poll_at = time.time() + _poll_interval(rt)
        except usage_api.RateLimited as e:
            # transitorio: preserva auth_connected e o ultimo snapshot bom
            espera = _schedule_backoff(e.retry_after, rt)
            rt.rate_limited = True
            rt.last_error = (
                f"Limite de requisições da conta; nova tentativa em {int(espera)}s.")
            rt.last_error_detail = f"RateLimited: retry_after={e.retry_after}"
            log(f"[poll:{rt.id}] 429 ({rt.rate_limit_hits}x), aguardando {int(espera)}s")
        except auth.TransientAuthError as e:
            espera = _schedule_backoff(rt=rt)
            rt.rate_limited = True
            rt.last_error = f"Renovação do token adiada; nova tentativa em {int(espera)}s."
            rt.last_error_detail = f"TransientAuthError: {e}"
            log(f"[poll:{rt.id}] falha transitoria ao renovar: {e}")
        except auth.AuthError as e:
            rt.auth_connected = False
            rt.rate_limited = False
            rt.last_error = "Conta desconectada: é preciso reconectar."
            rt.last_error_detail = f"AuthError: {e}"
            rt.next_poll_at = time.time() + _retry_after_falha(rt)
            log(f"[poll:{rt.id}] sem token valido: {e}")
        except Exception as e:
            # rede/servidor: nao mexe em auth_connected, so tenta mais tarde
            espera = _retry_after_falha(rt)
            rt.last_error, rt.last_error_detail = errors.humanize(e, retry_in=espera)
            rt.next_poll_at = time.time() + espera
            log(f"[poll:{rt.id}] erro: {rt.last_error_detail}")
    finally:
        _poll_lock.release()


def _runtimes():
    if not Ctx.accounts:
        return [Ctx]
    return [Ctx.accounts.runtime(a["id"]) for a in Ctx.accounts.claude_accounts()]


def poller_loop():
    while True:
        time.sleep(5)
        for rt in _runtimes():
            if rt and time.time() >= rt.next_poll_at:
                poll_once(rt=rt)


def _poll_account_now(aid):
    """Depois de um login: le a conta na hora."""
    rt = Ctx.accounts.runtime(aid) if Ctx.accounts else None
    if rt is not None:
        rt.profile_ts = 0.0
        rt.rate_limit_hits = 0
        poll_once(rt=rt)


SCAN_INTERVAL = 10  # scan incremental frequente para o total "quase em tempo real"


def scan_loop():
    while True:
        time.sleep(SCAN_INTERVAL)
        try:
            Ctx.store.scan(log=lambda *a: None)
            if Ctx.codex:
                Ctx.codex.scan()
                Ctx.codex.refresh_limits()
            if Ctx.sync:
                Ctx.sync.run_once()  # publica no maximo 1x/min; le as outras sempre
        except Exception as e:
            print(f"[scan_loop] {e}")


def start_login(aid=accounts_mod.PRINCIPAL, open_browser=True):
    """Gera o link de login da conta. Com open_browser, abre no navegador
    padrao; sem, o painel mostra o link para copiar e colar em outro perfil."""
    rt = Ctx.accounts.runtime(aid)
    if rt is None:
        raise KeyError(aid)
    url = Ctx.logins.start(aid, rt.token_store)
    if open_browser:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
    return url


def start_login_thread():
    """Compatibilidade: /auth/start (app nativo, widget antigo) = principal."""
    try:
        start_login(accounts_mod.PRINCIPAL, True)
    except Exception as e:
        msg, detail = errors.humanize(e)
        Ctx.last_error = f"Falha ao reconectar a conta: {msg}"
        Ctx.last_error_detail = f"login: {detail}"


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

    # --- quem pode falar com o painel ------------------------------------
    # O servidor so' escuta em 127.0.0.1, mas o NAVEGADOR da propria maquina
    # e' uma porta de entrada: uma pagina qualquer pode mandar POST para
    # localhost:8090 (CSRF) e, com DNS rebinding, ate' ler as respostas.
    # Host fora da lista = 403 em tudo; Origin de outro site = 403 no que
    # muda estado. Clientes sem Origin (Stream Deck, app nativo, curl) passam.

    def _hosts_permitidos(self):
        host, port = self.server.server_address[:2]
        ok = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
        if host not in ("127.0.0.1", "0.0.0.0", "::", "localhost"):
            ok.add(f"{host}:{port}")
        return ok

    def _recusa(self, escrita):
        """None se pode seguir; senao o motivo ('host' ou 'origin')."""
        permitidos = self._hosts_permitidos()
        if (self.headers.get("Host") or "").strip().lower() not in permitidos:
            return "host"
        origin = self.headers.get("Origin")
        if escrita and origin is not None:
            o = origin.strip().lower().rstrip("/")
            if not (o.startswith("http://") and o[len("http://"):] in permitidos):
                return "origin"
        return None

    def _guarda(self, escrita=False) -> bool:
        motivo = self._recusa(escrita)
        if motivo:
            self._send(403, _json({"error": "origem nao permitida", "motivo": motivo}))
            return False
        return True

    def _serve_dist(self, urlpath):
        """Serve o build do React (web/dist) com fallback SPA para index.html."""
        rel = urlpath.lstrip("/") or "index.html"
        full = os.path.normpath(os.path.join(WEB_DIST, rel))
        # protege contra path traversal: comparar prefixo sem separador deixava
        # passar qualquer pasta irma cujo nome comece com "dist"
        try:
            dentro = os.path.commonpath([WEB_DIST, full]) == os.path.normpath(WEB_DIST)
        except ValueError:
            dentro = False
        if not dentro:
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
        if not self._guarda(escrita=(p == "/auth/start")):
            return
        try:
            if p == "/api/health":
                self._send(200, _json({"app": "ai-usage", "protocol": 1,
                                       "version": updates.current_version(),
                                       "features": ["quotas", "brl", "label", "errors", "update", "machines"],
                                       "db": Ctx.db_path, "token": Ctx.token_path}))
            elif p == "/api/state":
                self._send(200, _json(build_state()))
            elif p == "/api/total":
                self._send(200, _json(_with_brl(Ctx.store.total_summary(), fx_rate())))
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
                self._send(200, _json(out))
            elif p == "/auth/start":
                start_login_thread()
                self._send(200, _json({"started": True}))
            elif p == "/api/accounts":
                self._send(200, _json({
                    "accounts": _accounts_state(fx_rate()),
                    "apps": accounts_mod.list_apps(),
                    "onboarded": Ctx.accounts.onboarded,
                }))
            elif p == "/api/usage":
                self._send(200, _json(usage_period(self._query())))
            elif p == "/api/hardware":
                self._send(200, _json(Ctx.hw.state() if Ctx.hw else {"available": False}))
            else:
                self._serve_dist(p)  # SPA React (web/dist)
        except Exception:
            # o traceback (caminhos, usuario) fica no log, nao na resposta
            print(f"[http] erro em GET {p}:\n{traceback.format_exc()}")
            self._send(500, _json({"error": "erro interno do painel; veja o log"}))

    def _query(self):
        import urllib.parse as up
        q = up.parse_qs(up.urlparse(self.path).query)
        return {k: v[0] for k, v in q.items() if v}

    CORPO_MAX = 64 * 1024

    def _corpo_json(self):
        """Le o corpo como JSON estrito. Devolve (body, erro)."""
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype not in ("application/json", ""):
            return None, (415, "envie JSON (Content-Type: application/json)")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None, (400, "Content-Length invalido")
        if length < 0 or length > self.CORPO_MAX:
            return None, (413, "corpo grande demais")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json_loads_estrito(raw or b"{}"), None
        except ValueError as e:
            return None, (400, f"JSON invalido: {e}")

    def do_POST(self):
        p = self.path.split("?")[0]
        if not self._guarda(escrita=True):
            return
        try:
            self._post(p)
        except Exception:
            print(f"[http] erro em POST {p}:\n{traceback.format_exc()}")
            self._send(500, _json({"error": "erro interno do painel; veja o log"}))

    def _post(self, p):
        if p.startswith("/api/accounts") or p == "/api/onboarded":
            self._post_accounts(p)
            return
        if p == "/api/config":
            body, erro = self._corpo_json()
            if erro:
                self._send(erro[0], _json({"error": erro[1]}))
                return
            # refresh_seconds saiu de proposito: o ritmo da API e fixo
            # (POLL_SECONDS) para nao estourar o limite da conta.
            mudancas, erro = valida_config(body)
            if erro:
                self._send(400, _json({"error": erro}))
                return
            Ctx.cfg.update(mudancas)
            if "chatgpt_extra_brl" in mudancas:
                Ctx.cfg["chatgpt_extra_month"] = dt.datetime.now().strftime("%Y-%m")
            save_config(Ctx.cfg)
            self._send(200, _json({"ok": True, "config": Ctx.cfg}))
        elif p == "/api/refresh":
            # respeita o backoff e um intervalo minimo: o botao Atualizar nao
            # pode virar um jeito de furar o rate limit da conta
            agora = time.time()
            espera = Ctx.next_poll_at - agora
            minimo = max(30, min(MANUAL_MIN_INTERVAL_S, _poll_interval() // 2))
            recente = Ctx.last_poll and (agora - Ctx.last_poll) < minimo
            if Ctx.rate_limited and espera > 0:
                self._send(200, _json({
                    "ok": False, "reason": "rate_limited",
                    "retry_in": int(espera)}))
            elif recente:
                restante = minimo - (agora - Ctx.last_poll)
                self._send(200, _json({
                    "ok": False, "reason": "muito_recente",
                    "retry_in": int(restante)}))
            else:
                poll_once()
                if Ctx.codex:
                    Ctx.codex.scan()
                    Ctx.codex.refresh_limits(force=True)
                self._send(200, _json({"ok": True}))
        else:
            self._send(404, "not found", "text/plain")


    def _post_accounts(self, p):
        body, erro = self._corpo_json()
        if erro:
            self._send(erro[0], _json({"error": erro[1]}))
            return
        if not isinstance(body, dict):
            self._send(400, _json({"error": "corpo precisa ser um objeto JSON"}))
            return
        am = Ctx.accounts
        partes = p.strip("/").split("/")  # api, accounts, <id>, <acao>
        try:
            if p == "/api/onboarded":
                am.onboarded = bool(body.get("done", True))
                am.save()
                self._send(200, _json({"ok": True}))
            elif p == "/api/accounts":
                acc = am.create(body.get("provider", "claude"), body.get("name"))
                self._send(200, _json({"ok": True, "account": _account_summary(acc, full=False)}))
            elif p == "/api/accounts/reorder":
                ids = body.get("ids")
                if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                    raise ValueError("ids precisa ser uma lista")
                am.reorder(ids)
                self._send(200, _json({"ok": True}))
            elif len(partes) == 3:
                acc = am.update(partes[2], body)
                self._send(200, _json({"ok": True, "account": _account_summary(acc, full=False)}))
            elif len(partes) == 4:
                aid, acao = partes[2], partes[3]
                acc = am.get(aid)
                if not acc:
                    raise KeyError(aid)
                if acao == "login":
                    if acc["provider"] != "claude":
                        raise ValueError("o ChatGPT usa o login do Codex desta maquina")
                    url = start_login(aid, bool(body.get("open", True)))
                    self._send(200, _json({"ok": True, "url": url}))
                elif acao == "disconnect":
                    am.disconnect(aid)
                    self._send(200, _json({"ok": True}))
                elif acao == "delete":
                    am.remove(aid)
                    self._send(200, _json({"ok": True}))
                elif acao == "open":
                    if not acc.get("app"):
                        raise ValueError("escolha no painel qual aplicativo abre esta conta")
                    accounts_mod.open_app(acc["app"])
                    self._send(200, _json({"ok": True}))
                elif acao == "refresh":
                    rt = am.runtime(aid)
                    if rt is not None and not rt.rate_limited:
                        threading.Thread(target=_poll_account_now, args=(aid,), daemon=True).start()
                    elif acc["provider"] == "codex" and Ctx.codex:
                        Ctx.codex.refresh_limits(force=True)
                    self._send(200, _json({"ok": True}))
                else:
                    self._send(404, _json({"error": "acao desconhecida"}))
            else:
                self._send(404, _json({"error": "rota desconhecida"}))
        except KeyError:
            self._send(404, _json({"error": "conta nao encontrada"}))
        except ValueError as e:
            self._send(400, _json({"error": str(e)}))


PERIODOS = ("dia", "24h", "7d", "semana", "mes", "geral")


def usage_period(q):
    """Consumo de tokens num periodo. Por padrao so' desta maquina: o consumo
    e' medido nos registros locais; as janelas de limite e' que sao da conta."""
    period = q.get("period") if q.get("period") in PERIODOS else "dia"
    local = q.get("scope", "local") != "all"
    fx = fx_rate()
    claude = _with_brl(Ctx.store.scope_summary(period, local_only=local), fx)
    codex = None
    if Ctx.codex:
        codex = Ctx.codex.period_summary(period, local_only=local)
        codex["equivalent_brl"] = _brl(codex.get("equivalent_usd"), fx)
    total_tokens = (claude.get("total_tokens") or 0) + ((codex or {}).get("total_tokens") or 0)
    total_usd = (claude.get("total_cost") or 0) + ((codex or {}).get("equivalent_usd") or 0)
    return {"period": period, "scope": "local" if local else "all",
            "machine": (Ctx.sync.name if Ctx.sync else None) or _hostname(),
            "claude": claude, "codex": codex,
            "total_tokens": total_tokens, "total_usd": total_usd,
            "total_brl": _brl(total_usd, fx), "usd_brl": fx}


def _hostname():
    import socket
    return socket.gethostname().split(".")[0]


# --- config + run ------------------------------------------------------------

DATA_DIR = os.environ.get("AI_USAGE_DATA_DIR", HERE)
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")  # recalculado em run()
DEFAULTS = {"port": 8090, "refresh_seconds": 120, "currency": "BRL",
            "credits_divisor": 100, "intended_hours": 2.0, "daily_days": 30,
            "callback_port": 54545, "open_browser": True,
            "usd_brl": None,  # None = cotacao automatica (AwesomeAPI, cache 1h)
            "subscription_brl": None,  # valor mensal da licenca (R$), p/ balanço
            "chatgpt_subscription_brl": None,  # assinatura ChatGPT; comparacao por uso da cota
            "plan": None,  # id da licenca (pro, max_5x, ...); None = usar a detectada
            # pasta compartilhada entre as maquinas da conta (Syncthing etc.);
            # None = so' esta maquina. machine_name None = nome do computador
            "sync_dir": None, "machine_name": None}


def _local_dir():
    """Pasta de dados DESTA maquina (banco, token, contas e config)."""
    if os.environ.get("AI_USAGE_DATA_DIR"):
        return DATA_DIR
    base = (os.environ.get("LOCALAPPDATA")
            or os.path.join(os.path.expanduser("~"), ".local", "share"))
    return os.path.join(base, "claude-usage")


def _config_path():
    """A config fica na pasta local, nao no clone: o clone pode estar numa
    pasta sincronizada entre maquinas, e ai uma maquina passava a usar o nome
    e a pasta de sincronizacao da outra. Na primeira subida, copia a config
    antiga do clone, descartando sync_dir que nao existe nesta maquina."""
    if os.environ.get("AI_USAGE_DATA_DIR"):
        return os.path.join(DATA_DIR, "config.json")
    d = _local_dir()
    novo = os.path.join(d, "config.json")
    legado = os.path.join(HERE, "config.json")
    if not os.path.exists(novo) and os.path.exists(legado):
        try:
            os.makedirs(d, exist_ok=True)
            with open(legado, encoding="utf-8") as f:
                old = json.load(f)
            sd = old.get("sync_dir")
            if sd and not (os.path.isabs(sd) and os.path.isdir(sd)):
                old.pop("sync_dir", None)
                old.pop("machine_name", None)
            with open(novo, "w", encoding="utf-8") as f:
                json.dump(old, f, indent=2, ensure_ascii=False)
            print(f"[painel] config copiada de {legado} para {novo}")
        except (OSError, ValueError) as e:
            print(f"[painel] nao consegui copiar a config antiga: {e}")
    return novo


def load_config():
    global CONFIG_PATH
    CONFIG_PATH = _config_path()
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


def _token_dir(db_path):
    """O token OAuth fica junto do banco (pasta local do usuario), nao na pasta
    do clone: o clone pode ser sincronizado entre maquinas e, no Windows, a
    ACL herdada da pasta deixa outras contas lerem o arquivo. Um token.json
    antigo na pasta do clone e' movido na primeira subida."""
    if os.environ.get("AI_USAGE_DATA_DIR"):
        return DATA_DIR
    novo_dir = os.path.dirname(db_path)
    novo = os.path.join(novo_dir, "token.json")
    legado = os.path.join(HERE, "token.json")
    if novo_dir != HERE and os.path.exists(legado) and not os.path.exists(novo):
        try:
            # copia + remove, nao rename: a pasta local costuma estar em outro
            # disco (clone em E:, perfil em C:) e os.replace nao cruza volumes
            shutil.copy2(legado, novo)
            auth.protege_arquivo(novo)
            os.remove(legado)
            print(f"[auth] token.json movido de {HERE} para {novo_dir}")
        except OSError as e:
            print(f"[auth] nao consegui mover o token.json para {novo_dir}: {e}")
            return HERE
    return novo_dir


def run(desktop=False, port=None, collect=True):
    Ctx.cfg = load_config()
    if desktop:
        Ctx.cfg.update(host="127.0.0.1", port=port or 8090, open_browser=False)
    Ctx.db_path = _db_path()
    Ctx.store = store_mod.Store(Ctx.db_path)
    Ctx.codex = codex_usage.CodexUsage(Ctx.db_path)
    Ctx.token_store = auth.TokenStore(_token_dir(Ctx.db_path))
    Ctx.token_path = Ctx.token_store.path
    print(f"[painel] banco em {Ctx.db_path}")
    print(f"[painel] token em {Ctx.token_path}")
    print(f"[painel] config em {CONFIG_PATH}")
    data_dir = os.path.dirname(Ctx.db_path)
    Ctx.accounts = accounts_mod.AccountManager(data_dir, os.path.dirname(Ctx.token_path), Ctx)
    Ctx.logins = accounts_mod.LoginBroker(int(Ctx.cfg.get("callback_port", 54545)),
                                          _poll_account_now)
    Ctx.hw = hwmon.HardwareMonitor(HERE)
    sync_dir = os.environ.get("AI_USAGE_SYNC_DIR") or Ctx.cfg.get("sync_dir")
    if sync_dir:
        Ctx.sync = machines.MachineSync(
            Ctx.store, Ctx.codex, sync_dir,
            os.environ.get("AI_USAGE_MACHINE") or Ctx.cfg.get("machine_name"))

    def initialize():
        Ctx.codex.scan()
        Ctx.codex.refresh_limits(force=True)
        if Ctx.sync:
            Ctx.store.scan(log=lambda *a: None)
            Ctx.sync.run_once(force=True)
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
