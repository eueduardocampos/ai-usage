# -*- coding: utf-8 -*-
"""Prova que a frase do veredito descreve a situacao (folga x aperto) e nao
assume o modelo padrao de ninguem, e que cada frase so aparece quando o que
ela afirma e verdade."""
import os, sys
sys.path.insert(0, os.getcwd())
import forecast

falhas = []
def check(cond, label):
    print(("  PASS  " if cond else "  FALHA ") + label)
    if not cond: falhas.append(label)

def w(util=None, proj=None, htr=None, window_hours=None):
    return {"utilization": util, "projected": proj, "hours_to_reset": htr,
            "window_hours": window_hours}

def msg(verdict, current, switched=None, janela="a janela da sessão"):
    return forecast.advice(verdict, current, switched or current, janela)

# --- nenhuma frase nomeia modelo especifico -------------------------------
todas = [
    msg("SEGURO", w(10, 20, 30)), msg("SEGURO", w(10, 20, 1)), msg("SEGURO", w(5, 10, 30)),
    msg("SEGURO", w(50, 60, 30)), msg("SEGURO", w(60, 70, 30)), msg("SEGURO", w(60, None, 30)),
    msg("ATENCAO", w(50, 90, 30), w(50, 96, 30)), msg("ATENCAO", w(50, 85, 20), w(50, 85, 20)),
    msg("ATENCAO", w(50, 85, 4), w(50, 90, 4)), msg("ATENCAO", w(50, 85, 4), w(50, 82, 4)),
    msg("ATENCAO", w(50, None, 4)),
    msg("RISCO", w(50, 120, 30)), msg("RISCO", w(50, 120, 8)), msg("RISCO", w(50, 60, 30), w(50, 130, 30)),
    msg("RISCO", w(50, 120, 3)), msg("RISCO", w(100, 120, 30)), msg("RISCO", w(50, None, 30)),
    msg("INDETERMINADO", w(None, None, None)), msg("INDETERMINADO", w(0, None, 4.5, 5)),
    msg("INDETERMINADO", w(12, None, 30, 168)), msg("INDETERMINADO", w(0, None, 100, 168)),
]
nomes = ("Fable", "Opus", "Sonnet", "Haiku", "Codex", "GPT")
check(all(not any(n in m for n in nomes) for m, _ in todas),
      "nenhuma frase nomeia um modelo especifico (nao assume o uso de quem le)")
check(all("—" not in m for m, _ in todas), "sem travessão")
check(all("encosta" not in m.lower() for m, _ in todas), "sem 'encosta' (vetado)")
check(all("estour" not in m.lower() or "Ou reduz" in m for m, _ in todas),
      "sem 'a cota estoura' (a janela e' o sujeito)")
check(len({i for _, i in todas}) >= 18, f"as 20 frases sao alcancaveis ({len({i for _, i in todas})} distintas nos casos testados)")

# --- SEGURO ---------------------------------------------------------------
check(msg("SEGURO", w(60, None, 30))[1] == 3, "sem projecao: frase que nao cita numero")
check(msg("SEGURO", w(10, 20, 1))[1] == 4, "reset logo ali: 'é agora'")
check(msg("SEGURO", w(5, 10, 30))[1] == 5, "projecao bem baixa: 'bem abaixo do limite'")
check(msg("SEGURO", w(50, 60, 30))[1] == 1, "folga normal")
m, i = msg("SEGURO", w(60, 72, 30))
check(i == 2 and "72%" in m, f"cita o numero real da projecao: {m}")

# --- ATENCAO --------------------------------------------------------------
check(msg("ATENCAO", w(50, 90, 30), w(50, 96, 30))[1] == 6, "projecao no limite")
check(msg("ATENCAO", w(50, 85, 20), w(50, 85, 20))[1] == 7, "janela longa: 'não no dia todo'")
check(msg("ATENCAO", w(50, 85, 4), w(50, 90, 4))[1] == 10, "perto do limite confortavel")
m, i = msg("ATENCAO", w(50, 83, 4), w(50, 83, 4))
check(i == 8 and "83%" in m, f"cita o numero: {m}")
check(msg("ATENCAO", w(50, None, 4))[1] == 9, "sem projecao: frase sem numero")

# --- RISCO ----------------------------------------------------------------
m, i = msg("RISCO", w(50, 120, 30))
check(i == 14 and "a janela da sessão" in m, f"teto antes do reset, janela nomeada: {m}")
m, i = msg("RISCO", w(50, 120, 8))
check(i == 11 and "No ritmo atual" in m, f"ritmo atual nao chega ao reset: {m}")
m, i = msg("RISCO", w(50, 60, 30), w(50, 130, 30))
check(i == 12, f"ritmo atual aguenta; quem estoura e' a troca: {m}")
m, i = msg("RISCO", w(50, 120, 3))
check(i == 15 and "3h" in m, f"pouco tempo pro reset: {m}")
check(msg("RISCO", w(100, 120, 30))[1] == 15, "ja estourada: 'comprometida'")
m, i = msg("RISCO", w(50, None, 30), None, "a semana do Fable")
check(i == 13 and "na semana do Fable" in m, f"sem projecao, janela por modelo: {m}")

# --- portugues: contracao e maiuscula -------------------------------------
check(not any(" em a " in m or " de a " in m for m, _ in todas), "sem 'em a'/'de a' (contrai para na/da)")
m, _ = msg("RISCO", w(50, 120, 3), None, "a semana do Fable")
check(m.startswith("A semana do Fable"), f"primeira letra maiuscula sem rebaixar o resto: {m}")
m, _ = msg("RISCO", w(50, 120, 30), None, "a semana do Fable")
check("teto da semana do Fable" in m, f"'de a' vira 'da': {m}")
m, _ = msg("RISCO", w(50, 60, 30), w(50, 130, 30), "a semana")
check("100% na semana" in m, f"'em a' vira 'na': {m}")

# nunca diz "no ritmo atual ... acaba antes do reset" quando o ritmo atual aguenta
m, _ = msg("RISCO", w(50, 60, 30), w(50, 130, 30))
check("No ritmo atual" not in m, "nao afirma que o ritmo atual estoura quando ele nao estoura")

# --- INDETERMINADO --------------------------------------------------------
check(msg("INDETERMINADO", w(None, None, None))[1] == 20, "sem leitura nenhuma")
check(msg("INDETERMINADO", w(0, None, 4.5, 5))[1] == 19, "janela recem-aberta")
m, i = msg("INDETERMINADO", w(12, None, 30, 168))
check(i == 18 and "12%" in m, f"tem consumo mas nao tem ritmo: {m}")
check(msg("INDETERMINADO", w(0, None, 100, 168))[1] == 17, "zerada ha um tempo: coletando amostras")

# --- nome da janela -------------------------------------------------------
check(forecast._window_phrase("five_hour") == "a janela da sessão", "five_hour")
check(forecast._window_phrase("seven_day") == "a semana", "seven_day")
check(forecast._window_phrase("seven_day_model:fable", {"seven_day_model:fable": {"label": "Fable"}})
      == "a semana do Fable", "janela por modelo usa o rotulo do servidor")
check(forecast._window_phrase("seven_day_model:fable") == "a semana do Fable", "sem rotulo: deriva do slug")
check(forecast._window_phrase(None) == "a janela", "sem janela: generico")

# --- integracao com switch_verdict ---------------------------------------
ws = {
    "five_hour": {"utilization": 92.0, "rate": 5.0, "hours_to_reset": 4.0, "window_hours": 5},
    "seven_day": {"utilization": 20.0, "rate": 0.5, "hours_to_reset": 30.0, "window_hours": 168},
}
v = forecast.switch_verdict(ws, "claude-sonnet-5", 2.0, target="opus")
check(v["tightest_window"] == "five_hour", f"janela mais apertada identificada: {v['tightest_window']}")
check(v["message_id"] in range(1, 21) and v["message"], f"{v['verdict']}: {v['message']}")
check("Opus" not in v["message"], "mensagem do veredito nao nomeia o alvo")

print()
print(("%d FALHA(S)" % len(falhas)) if falhas else "TUDO PASSOU")
sys.exit(1 if falhas else 0)
