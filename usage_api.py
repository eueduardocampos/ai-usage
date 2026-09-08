"""
usage_api.py — le os limites de uso ao vivo da conta via OAuth usage API.

GET https://api.anthropic.com/api/oauth/usage
Headers: Authorization: Bearer <token> | anthropic-beta: oauth-2025-04-20

A resposta traz janelas como chaves de topo (five_hour, seven_day,
seven_day_sonnet, seven_day_opus...) e um array `limits[]` onde vem, entre
outros, o teto semanal por modelo (kind "weekly_scoped", com o nome do modelo
em scope.model.display_name). E' de `limits[]` que sai a linha "Semanal ·
<modelo>" do app oficial — ela nao existe como chave de topo.
"""

from __future__ import annotations

import json
import re
import unicodedata
import urllib.request
import urllib.error

import auth

class RateLimited(Exception):
    """429 da API de uso. E transitorio e da conta inteira (todas as sessoes do
    Claude Code somam no mesmo limite) — NAO significa token invalido, entao o
    painel NUNCA deve pedir reconexao por causa disso."""

    def __init__(self, retry_after=None):
        self.retry_after = retry_after
        super().__init__("limite de requisicoes da conta")


def _retry_after(e) -> float | None:
    """Le o Retry-After da resposta, quando o servidor manda."""
    try:
        v = e.headers.get("Retry-After")
        return float(v) if v else None
    except Exception:
        return None


USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"
BETA_HEADER = "oauth-2025-04-20"

# Janelas "classicas" que a API manda como chave de topo. Sao as unicas que
# entram em /api/state.windows: os clientes ja publicados (Stream Deck) tratam
# qualquer outra chave ali como "CLAUDE SONNET". Tudo o mais vai para quotas[].
LEGACY_WINDOW_KEYS = ("five_hour", "seven_day", "seven_day_sonnet", "seven_day_opus")
WINDOW_LABELS = {
    "five_hour": "Sessão (5h)",
    "seven_day": "Semana (7d)",
    "seven_day_sonnet": "Sonnet (7d)",
    "seven_day_opus": "Opus (7d)",
}
# Chaves de topo com formato parecido com janela mas que nao sao cota de uso.
NON_WINDOW_KEYS = {"extra_usage", "limits", "spend"}
# Prefixo das janelas por modelo vindas de limits[] (ex.: seven_day_model:fable).
MODEL_WINDOW_PREFIX = "seven_day_model:"


def window_hours_for(key: str) -> int:
    """Duracao da janela em horas, pelo nome da chave."""
    return 5 if key.startswith("five_hour") else 7 * 24


def model_slug(display_name: str) -> str:
    """'Sonnet 4.6' -> 'sonnet_4_6'; 'Fable' -> 'fable'. Chave estavel por modelo."""
    s = unicodedata.normalize("NFKD", display_name or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
    return s or "modelo"


def _is_window(v) -> bool:
    return isinstance(v, dict) and ("utilization" in v or "resets_at" in v)


def _get(url, token):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "anthropic-beta": BETA_HEADER,
        "Accept": "application/json",
    }, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_usage(store: auth.TokenStore, log=print) -> dict:
    """Busca o uso ao vivo. Em 401, renova uma vez e tenta de novo."""
    token = auth.get_valid_token(store, log=log)
    try:
        raw = _get(USAGE_URL, token)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RateLimited(_retry_after(e)) from e
        if e.code == 401:
            log("[usage] 401, renovando token e tentando de novo...")
            token = auth.force_refresh(store, log=log)
            try:
                raw = _get(USAGE_URL, token)
            except urllib.error.HTTPError as e2:
                if e2.code == 429:
                    raise RateLimited(_retry_after(e2)) from e2
                raise
        else:
            raise
    return raw


def fetch_profile(store: auth.TokenStore, log=print) -> dict:
    """Perfil da conta (tipo de organizacao, tier do assento) — p/ detectar a licenca."""
    token = auth.get_valid_token(store, log=log)
    try:
        return _get(PROFILE_URL, token)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RateLimited(_retry_after(e)) from e
        if e.code == 401:
            token = auth.force_refresh(store, log=log)
            return _get(PROFILE_URL, token)
        raise


def normalize(raw: dict) -> dict:
    """Extrai as janelas num formato simples e estavel.

    windows[key] = {utilization, resets_at, label, window_hours, origin}
      - chaves de topo conhecidas (LEGACY_WINDOW_KEYS), origin "top"
      - uma por modelo vinda de limits[] (weekly_scoped), origin "limits",
        chave seven_day_model:<slug>, rotulo = display_name do servidor
    Uma janela presente com utilization 0 e' uma janela com 0% — entra.
    ignored_keys / ignored_limits registram o que a API mandou e nao virou
    cota (codinomes de experimento, kinds desconhecidos), para o log de boot
    denunciar mudanca de schema em vez de descartar em silencio.
    """
    out = {"windows": {}, "extra_usage": None, "ignored_keys": [], "ignored_limits": []}
    for key in LEGACY_WINDOW_KEYS:
        w = raw.get(key)
        if _is_window(w):
            out["windows"][key] = {
                "utilization": w.get("utilization"),
                "resets_at": w.get("resets_at"),
                "label": WINDOW_LABELS.get(key, key),
                "window_hours": window_hours_for(key),
                "origin": "top",
            }
    for key, v in raw.items():
        if key in LEGACY_WINDOW_KEYS or key in NON_WINDOW_KEYS or v is None:
            continue
        out["ignored_keys"].append(key)

    families = {k[len("seven_day_"):] for k in out["windows"] if k.startswith("seven_day_")}
    for item in raw.get("limits") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        name = ((item.get("scope") or {}).get("model") or {}).get("display_name")
        if kind != "weekly_scoped" or not name:
            out["ignored_limits"].append({"kind": kind, "group": item.get("group")})
            continue
        slug = model_slug(name)
        if any(slug.startswith(f) for f in families):
            out["ignored_limits"].append({"kind": kind, "model": name, "reason": "duplicate"})
            continue
        out["windows"][MODEL_WINDOW_PREFIX + slug] = {
            "utilization": item.get("percent"),
            "resets_at": item.get("resets_at"),
            "label": name,
            "window_hours": 7 * 24,
            "origin": "limits",
        }

    eu = raw.get("extra_usage")
    if isinstance(eu, dict):
        out["extra_usage"] = {
            "is_enabled": eu.get("is_enabled"),
            "monthly_limit": eu.get("monthly_limit"),
            "used_credits": eu.get("used_credits"),
            "utilization": eu.get("utilization"),
            "currency": eu.get("currency"),
        }
    return out


if __name__ == "__main__":
    import os
    st = auth.TokenStore(os.path.dirname(os.path.abspath(__file__)))
    raw = fetch_usage(st)
    print("=== RAW ===")
    print(json.dumps(raw, indent=2, ensure_ascii=False))
    print("=== NORMALIZADO ===")
    print(json.dumps(normalize(raw), indent=2, ensure_ascii=False))
