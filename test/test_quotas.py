# -*- coding: utf-8 -*-
"""Prova o contrato unificado quotas[] do /api/state: Claude (inclusive a
janela por modelo e a sem dado) e Codex (3 medicoes) na mesma forma, e que
`windows` continua so' com as chaves classicas (Stream Deck ja publicado)."""
import datetime as dt
import os, sys, time
sys.path.insert(0, os.getcwd())
import server

falhas = []
def check(cond, label):
    print(("  PASS  " if cond else "  FALHA ") + label)
    if not cond: falhas.append(label)

NOW = dt.datetime.now(dt.timezone.utc)
def future(h):
    return (NOW + dt.timedelta(hours=h)).isoformat()
NOW_ISO = NOW.isoformat()
SNAP_TS = NOW.strftime("%Y-%m-%dT%H:%M:%S.000Z")

class FakeStore:
    def latest_state(self):
        return {"ts": SNAP_TS, "windows": {
            "five_hour": {"utilization": 6.0, "resets_at": future(4), "label": "Sessão (5h)"},
            "seven_day": {"utilization": 18.0, "resets_at": future(28), "label": "Semana (7d)"},
            "seven_day_model:fable": {"utilization": 0, "resets_at": future(28), "label": "Fable"},
            "seven_day_opus": {"utilization": None, "resets_at": None, "label": None},
        }, "extra_usage": {"used_credits": 0, "monthly_limit": 2000, "currency": None}}
    def hour_of_day_avg(self): return []
    def snapshot_rate(self, window, *a, **k): return 1.0
    def recent_tokph(self, hours): return {"claude-fable-5-1": 1000.0}
    def recent_burn_by_source(self, hours): return {}
    def extra_credit_series(self, hours): return []
    def scope_summary(self, sc):
        return {"scope": sc, "total_tokens": 0, "total_cost": 10.0, "cost_per_hour": 2.0,
                "total_turns": 0,
                "by_model": [{"model": "claude-fable-5-1", "tokens": 1, "cost": 4.0}],
                "by_source": {"code": {"tokens": 1, "cost": 4.0, "turns": 1}}}

class FakeCodex:
    def state(self):
        return {"available": True, "generated_at": NOW_ISO, "limits": [
            {"id": "codex", "name": "Codex", "plan_type": "prolite",
             "primary": {"used_percent": 0, "window_minutes": 10080.0, "resets_at": future(160)},
             "secondary": None, "credits": None, "snapshot_ts": NOW_ISO},
            {"id": "codex_bengalfox", "name": "GPT-5.3-Codex-Spark", "plan_type": "prolite",
             "primary": {"used_percent": 0, "window_minutes": 300.0, "resets_at": future(5)},
             "secondary": {"used_percent": 40, "window_minutes": 10080.0, "resets_at": future(160)},
             "credits": None, "snapshot_ts": NOW_ISO},
        ], "limit_history": [
            {"ts": (NOW - dt.timedelta(hours=2)).isoformat(), "limits": [
                {"id": "codex_bengalfox", "secondary": {"used_percent": 20}}]},
            {"ts": NOW_ISO, "limits": [{"id": "codex_bengalfox", "secondary": {"used_percent": 40}}]},
        ], "hour_of_day": [], "burn_tokph": 0.0, "burn_by_model": {}, "history": {},
            "limits_error": None}

server.Ctx.cfg = {"intended_hours": 2.0, "credits_divisor": 100, "currency": "BRL"}
server.Ctx.store = FakeStore()
server.Ctx.codex = FakeCodex()
server.Ctx.profile = None
server.Ctx.fx_rate = 5.0
server.Ctx.fx_ts = time.time()
server.Ctx.profile_ts = time.time()

s = server.build_state()
q = s["quotas"]
keys = [i["key"] for i in q]
check(keys == ["claude:five_hour", "claude:seven_day", "claude:seven_day_model:fable",
               "claude:seven_day_opus", "codex:codex:primary",
               "codex:codex_bengalfox:primary", "codex:codex_bengalfox:secondary"],
      f"ordem e chaves de quotas[]: {keys}")
by = {i["key"]: i for i in q}
check(by["claude:five_hour"]["label"] == "Sessão · 5 horas" and by["claude:seven_day"]["label"] == "Semana · 7 dias",
      "rotulos do Claude no padrao 'produto · janela'")
fab = by["claude:seven_day_model:fable"]
check(fab["utilization"] == 0 and fab["status"] == "SEGURO", "Fable 0% presente e SEGURO")
check(fab["label"] == "Fable · 7 dias" and fab["product"] == "Fable"
      and fab["source_key"] == "seven_day_model:fable" and fab["window_hours"] == 168,
      "Fable: rotulo, product, source_key, 7 dias")
opus = by["claude:seven_day_opus"]
check(opus["utilization"] is None and opus["status"] == "INDETERMINADO" and opus["projected"] is None,
      "janela sem dado entra em quotas[] como INDETERMINADO")
check(by["codex:codex:primary"]["label"] == "Codex · 7 dias", "Codex · 7 dias")
check(by["codex:codex_bengalfox:primary"]["label"] == "Spark · 5 horas"
      and by["codex:codex_bengalfox:secondary"]["label"] == "Spark · 7 dias",
      "GPT-5.3-Codex-Spark vira 'Spark' + janela")
sec = by["codex:codex_bengalfox:secondary"]
check(sec["rate"] is not None and abs(sec["rate"] - 10) < 1e-6,
      f"Codex ganha taxa pela serie de limit_history (20->40 em 2h = 10/h): {sec['rate']}")
check(sec["projected"] is not None and sec["projected"] > 40 and sec["status"] in ("SEGURO", "ATENCAO", "RISCO"),
      f"Codex ganha projecao e semaforo pelo mesmo forecast ({sec['projected']}, {sec['status']})")
check(all(i["product"] for i in q if i["provider"] == "codex"), "product do Codex = nome do limite")
check(all(i["status"] in ("SEGURO", "ATENCAO", "RISCO", "INDETERMINADO") for i in q),
      "enum de status intocado (sem acento)")
check(all(i["snapshot_ts"] for i in q), "snapshot_ts por item (Claude e Codex tem frescor diferente)")

# compatibilidade: windows so' com as chaves classicas e valor numerico
check(set(s["windows"]) == {"five_hour", "seven_day"},
      f"windows continua so' com five_hour/seven_day ({sorted(s['windows'])})")
check(s["windows"]["five_hour"]["label"] == "Sessão (5h)", "windows[*].label com acento")
check("window" in s["windows"]["five_hour"] and s["windows"]["five_hour"]["status"] in ("SEGURO", "ATENCAO", "RISCO"),
      "windows[*] mantem os campos antigos")
check(s["chatgpt"]["limits"][1]["secondary"]["used_percent"] == 40, "chatgpt.limits intocado")
check(s["switch"] is not None and s["switch"]["verdict"] in ("SEGURO", "ATENCAO", "RISCO", "INDETERMINADO"),
      "switch continua sendo calculado")

# moeda: creditos em USD, conversao num ponto so' (fx = 5.0)
eu = s["extra_usage"]
check(eu["currency"] == "USD", "extra_usage.currency = USD (a API nao manda; os creditos sao em dolar)")
check(eu["limit"] == 20.0 and eu["limit_brl"] == 100.0, f"limit 20 USD -> 100 BRL ({eu['limit']}, {eu['limit_brl']})")
check(eu["used_brl"] == 0.0, "used_brl presente")
h = s["history"]["dia"]
check(h["total_cost"] == 10.0 and h["total_cost_brl"] == 50.0, "history.total_cost continua USD; total_cost_brl convertido")
check(h["cost_per_hour_brl"] == 10.0, "cost_per_hour_brl")
check(h["by_model"][0]["cost"] == 4.0 and h["by_model"][0]["cost_brl"] == 20.0, "by_model[*].cost_brl")
check(h["by_source"]["code"]["cost_brl"] == 20.0, "by_source[*].cost_brl")
check(s["config"]["usd_brl"] == 5.0, "config.usd_brl vem do mesmo fx_rate()")
# conta cobrada em BRL: a API informa a moeda e NAO se converte de novo
server.Ctx.store.latest_state = (lambda orig=server.Ctx.store.latest_state: (lambda: dict(
    orig(), extra_usage={"used_credits": 500, "monthly_limit": 2000, "currency": "BRL"})))()
s_brl = server.build_state()
check(s_brl["extra_usage"]["currency"] == "BRL" and s_brl["extra_usage"]["limit_brl"] == 20.0
      and s_brl["extra_usage"]["used_brl"] == 5.0,
      "creditos em BRL: limit_brl = limit (sem multiplicar pelo cambio)")
server.Ctx.store = FakeStore()
# sem cotacao: *_brl vem null, nunca 0
server.Ctx.fx_rate = None
s_nofx = server.build_state()
check(s_nofx["history"]["dia"]["total_cost_brl"] is None and s_nofx["extra_usage"]["limit_brl"] is None,
      "sem cotacao: *_brl = null (nao zero)")
server.Ctx.fx_rate = 5.0

# sem snapshot nenhum (conta recem-conectada): quotas so' com Codex, windows vazio
server.Ctx.store.latest_state = lambda: None
s2 = server.build_state()
check(s2["windows"] == {} and all(i["provider"] == "codex" for i in s2["quotas"]) and len(s2["quotas"]) == 3,
      "sem snapshot: windows vazio, quotas so' Codex")

print()
print(("%d FALHA(S)" % len(falhas)) if falhas else "TUDO PASSOU")
sys.exit(1 if falhas else 0)
