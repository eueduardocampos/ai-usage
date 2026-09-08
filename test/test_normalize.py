# -*- coding: utf-8 -*-
"""Prova que usage_api.normalize() le as janelas por modelo de limits[] (a
linha "Semanal · Fable" do app oficial), mantem zero e None presentes, e
registra em vez de descartar em silencio o que a API manda e nao vira cota."""
import inspect, os, sys
sys.path.insert(0, os.getcwd())
import usage_api

falhas = []
def check(cond, label):
    print(("  PASS  " if cond else "  FALHA ") + label)
    if not cond: falhas.append(label)

RESET_5H = "2026-09-08T22:39:59.830105+00:00"
RESET_7D = "2026-09-09T22:59:59.830127+00:00"

# Fixture no formato real de GET /api/oauth/usage (schema do Claude Code),
# com os codinomes de experimento que a API tambem manda.
raw = {
    "five_hour": {"utilization": 2.0, "resets_at": RESET_5H, "used_dollars": 1, "limit_dollars": 50},
    "seven_day": {"utilization": 17.0, "resets_at": RESET_7D},
    "seven_day_sonnet": None,
    "seven_day_opus": None,
    "seven_day_oauth_apps": None,
    "seven_day_omelette": None,
    "cinder_cove": None,
    "nimbus_quill": {"utilization": 0, "resets_at": RESET_7D},
    "member_dashboard_available": False,
    "extra_usage": {"is_enabled": True, "monthly_limit": 2000, "used_credits": 0,
                    "utilization": 0, "currency": None, "weekly": {}},
    "spend": {"balance": 0, "enabled": False},
    "limits": [
        {"kind": "session", "group": "session", "percent": 10, "resets_at": RESET_5H},
        {"kind": "weekly_all", "group": "weekly", "percent": 19, "resets_at": RESET_7D},
        {"kind": "weekly_scoped", "group": "weekly", "percent": 0, "resets_at": RESET_7D,
         "scope": {"model": {"display_name": "Fable"}, "surface": None}},
        {"kind": "weekly_scoped", "group": "weekly", "percent": 12, "resets_at": RESET_7D,
         "scope": {"model": {"display_name": "Sonnet 4.6"}}},
        {"kind": "daily_mystery", "group": "daily", "percent": 1},
    ],
}
out = usage_api.normalize(raw)
w = out["windows"]

check(set(w) == {"five_hour", "seven_day", "seven_day_model:fable", "seven_day_model:sonnet_4_6"},
      f"janelas: classicas + uma por modelo de limits[] ({sorted(w)})")
f = w["seven_day_model:fable"]
check(f["utilization"] == 0, "Fable com 0% entra (zero nao e' descartado)")
check(f["label"] == "Fable", "rotulo vem do display_name do servidor")
check(f["window_hours"] == 168 and f["origin"] == "limits", "janela por modelo: 7 dias, origem limits[]")
check(f["resets_at"] == RESET_7D, "resets_at da janela por modelo preservado")
check(w["seven_day_model:sonnet_4_6"]["utilization"] == 12, "slug estavel a partir de 'Sonnet 4.6'")
check(w["five_hour"]["label"] == "Sessão (5h)" and w["seven_day"]["label"] == "Semana (7d)",
      "rotulos classicos com acento")
check(w["five_hour"]["window_hours"] == 5 and w["seven_day"]["window_hours"] == 168,
      "horas por tipo de janela")
check("nimbus_quill" not in w, "codinome de experimento nao vira cota (o app oficial tambem nao mostra)")
check(set(out["ignored_keys"]) == {"nimbus_quill", "member_dashboard_available"},
      f"chaves de topo ignoradas ficam registradas: {out['ignored_keys']}")
kinds = {i.get("kind") for i in out["ignored_limits"]}
check("daily_mystery" in kinds, "kind desconhecido em limits[] fica registrado")
check("session" in kinds and "weekly_all" in kinds,
      "session/weekly_all de limits[] nao duplicam five_hour/seven_day (so registrados)")
check(out["extra_usage"]["currency"] is None, "moeda dos creditos: None quando a API nao informa")
check(not hasattr(usage_api, "WINDOW_HOURS"), "dicionario fixo WINDOW_HOURS (com 'omelette') sumiu")

# janela presente porem sem dado: continua presente (vira INDETERMINADO no server)
o2 = usage_api.normalize({"five_hour": {"utilization": None, "resets_at": None}})
check("five_hour" in o2["windows"] and o2["windows"]["five_hour"]["utilization"] is None,
      "janela com utilization None continua presente")

# dedupe: chave de topo da mesma familia vence a de limits[]
o3 = usage_api.normalize({
    "seven_day_sonnet": {"utilization": 5, "resets_at": RESET_7D},
    "limits": [{"kind": "weekly_scoped", "percent": 5, "resets_at": RESET_7D,
                "scope": {"model": {"display_name": "Sonnet 4.6"}}}],
})
check("seven_day_model:sonnet_4_6" not in o3["windows"] and "seven_day_sonnet" in o3["windows"],
      "mesma familia em topo e em limits[]: mantem a de topo")
check(any(i.get("reason") == "duplicate" for i in o3["ignored_limits"]), "duplicata registrada")

# helpers
check(usage_api.window_hours_for("five_hour") == 5 and usage_api.window_hours_for("seven_day_model:fable") == 168,
      "window_hours_for")
check(usage_api.model_slug("Sonnet 4.6") == "sonnet_4_6" and usage_api.model_slug("Fable") == "fable"
      and usage_api.model_slug("Ópus 5") == "opus_5", "model_slug normaliza acento/espaco/ponto")

# compatibilidade com test_rate_limit.py (monkeypatch normalize = lambda raw: ...)
check(len(inspect.signature(usage_api.normalize).parameters) == 1, "normalize(raw) continua com um argumento")
check(usage_api.normalize({}) == {"windows": {}, "extra_usage": None, "ignored_keys": [], "ignored_limits": []},
      "resposta vazia nao quebra")

print()
print(("%d FALHA(S)" % len(falhas)) if falhas else "TUDO PASSOU")
sys.exit(1 if falhas else 0)
