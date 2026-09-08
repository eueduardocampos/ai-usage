# -*- coding: utf-8 -*-
"""Prova forecast.series_rate (taxa por amostragem generica, usada por Claude
e Codex) e o reconhecimento do Fable em _model_key."""
import os, sys
sys.path.insert(0, os.getcwd())
import forecast

falhas = []
def check(cond, label):
    print(("  PASS  " if cond else "  FALHA ") + label)
    if not cond: falhas.append(label)

def z(h, m=0):
    return f"2026-09-08T{h:02d}:{m:02d}:00.000Z"

# serie crescente: 10 -> 20 -> 30 em 2h = 10 %/h
r = forecast.series_rate([(z(10), 10), (z(11), 20), (z(12), 30)])
check(r is not None and abs(r - 10) < 1e-9, f"taxa de serie crescente = 10/h ({r})")

# reset no meio: so' o trecho depois da queda conta (5 -> 15 em 1h = 10/h)
r = forecast.series_rate([(z(10), 10), (z(11), 50), (z(12), 5), (z(13), 15)])
check(r is not None and abs(r - 10) < 1e-9, f"queda = reset, usa so' o trecho posterior ({r})")

# depois do reset, trecho curto demais -> None (fallback pra media da janela)
r = forecast.series_rate([(z(10), 10), (z(11), 50), (z(11, 5), 1)])
check(r is None, "trecho pos-reset com menos de 30min -> None")

# span curto -> None
check(forecast.series_rate([(z(10), 10), (z(10, 10), 12)]) is None, "span < 0.5h -> None")

# dois formatos de timestamp na mesma serie (Codex com offset, Claude com .000Z)
r = forecast.series_rate([("2026-09-08T10:00:00+00:00", 0), (z(12), 4)])
check(r is not None and abs(r - 2) < 1e-9, f"aceita ISO com offset e .000Z ({r})")

# valores nulos sao ignorados; serie vazia/unitaria -> None
check(forecast.series_rate([(z(10), None), (z(11), None)]) is None, "so' nulos -> None")
check(forecast.series_rate([]) is None and forecast.series_rate([(z(10), 1)]) is None, "vazia/unitaria -> None")

# taxa nunca negativa
check(forecast.series_rate([(z(10), 10), (z(12), 10)]) == 0.0, "sem crescimento -> 0.0")

# _model_key reconhece o Fable (antes caia em None -> 'sonnet' por fallback)
check(forecast._model_key("claude-fable-5-1") == "fable-5-1", "claude-fable-5-1 -> fable-5-1")
check(forecast._model_key("claude-fable-5") == "fable", "claude-fable-5 -> fable")
check(forecast._model_key("claude-opus-5") == "opus" and forecast._model_key("claude-sonnet-4-6") == "sonnet",
      "opus/sonnet inalterados")

# janela sem dado: INDETERMINADO sem quebrar
st = forecast.smart_project_window(None, None, 168, [])
check(st["status"] == "INDETERMINADO" and st["utilization"] is None and st["projected"] is None,
      "utilization None -> INDETERMINADO")

# veredito de troca: alvo real, janela propria do alvo, mensagens com acento
ws = {
    "five_hour": {"utilization": 10.0, "rate": 1.0, "hours_to_reset": 4.0},
    "seven_day": {"utilization": 20.0, "rate": 0.5, "hours_to_reset": 30.0},
    "seven_day_model:fable": {"utilization": 5.0, "rate": 0.2, "hours_to_reset": 30.0},
}
v = forecast.switch_verdict(ws, "claude-sonnet-5", 2.0, target="opus")
check(set(v["windows"]) == {"five_hour", "seven_day"}, "alvo Opus: nao avalia a janela do Fable")
check(v["target_label"] == "Opus", "rotulo do alvo continua no payload (a frase e' que nao nomeia)")
v = forecast.switch_verdict(ws, "claude-sonnet-5", 2.0, candidates={"opus", "fable-5-1", "sonnet-5"})
check(v["target"] == "fable-5-1" and v["target_label"] == "Fable",
      f"sem alvo fixo: prefere quem tem teto proprio ({v['target']})")
check("seven_day_model:fable" in v["windows"], "janela propria do Fable entra no veredito")
check(v["factor_estimated"] is False and abs(v["factor"] - 50.0 / 10.0) < 1e-9,
      f"fator = peso(fable)/peso(sonnet-5) = 5 ({v['factor']})")
v = forecast.switch_verdict(ws, "claude-sonnet-5", 2.0, candidates=set())
check(v["target"] == "opus", "sem candidatos: alvo Opus")
v = forecast.switch_verdict(ws, "claude-sonnet-5", 2.0, target="modelo-x")
check(v["factor_estimated"] is True, "alvo sem preco: fator estimado (peso do Opus)")
risky = {"five_hour": {"utilization": 90.0, "rate": 5.0, "hours_to_reset": 4.0, "window_hours": 5}}
v = forecast.switch_verdict(risky, "claude-sonnet-5", 2.0, target="opus")
check(v["verdict"] == "RISCO" and v["message_id"] in range(11, 16), f"RISCO: {v['message']}")
check("NAO" not in v["message"] and " pra " not in v["message"], "sem 'NAO'/'pra'")

# enum do semaforo intocado (Stream Deck e Swift comparam essas strings)
check(forecast.classify(0) == "SEGURO" and forecast.classify(85) == "ATENCAO"
      and forecast.classify(100) == "RISCO" and forecast.classify(None) == "INDETERMINADO",
      "classify: SEGURO/ATENCAO/RISCO/INDETERMINADO")

print()
print(("%d FALHA(S)" % len(falhas)) if falhas else "TUDO PASSOU")
sys.exit(1 if falhas else 0)
