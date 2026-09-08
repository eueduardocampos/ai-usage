"""
forecast.py — funcoes puras de projecao, semaforo e veredito de troca de modelo.
Sem I/O. Recebe os dados ja lidos (uso ao vivo + taxas) e devolve dicts.

Projecao por janela:
  - taxa preferida: %/hora medida por amostragem (snap_rate)
  - fallback: taxa implicita = utilization / horas_decorridas (= util/elapsed_frac no reset)
Semaforo: <80 SEGURO | 80-100 ATENCAO | >=100 RISCO | sem dados INDETERMINADO

Projecao inteligente (smart_project_window):
  Usa o perfil historico de uso por hora do dia para ponderar cada hora futura.
  Horas de madrugada (peso ~0) contribuem muito menos que horas de pico (peso 1.0).
  Retorna tambem projected_linear (projecao simples) para comparacao na UI.

Veredito de troca: a conta nao expoe bucket de Opus, entao o Opus pesa sobre a
sessao (5h) e o semanal geral (7d). Estima-se o burn do Opus aplicando um fator
de intensidade (peso de preco do Opus / peso do modelo dominante atual).
"""

import datetime as dt
from datetime import timezone

from pricing import DISPLAY_NAME, OUTPUT_WEIGHT, model_key as _model_key  # fonte unica (pricing.py)

_ORDER = {"SEGURO": 0, "ATENCAO": 1, "RISCO": 2, "INDETERMINADO": 0}


def parse_iso(s):
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def series_rate(points, min_span_h=0.5):
    """%/hora medido por amostragem numa serie [(ts_iso, valor)], do mais antigo
    ao mais novo. Uma queda de valor e' reset de janela: so' o trecho depois da
    ultima queda conta. Devolve None se o trecho cobre menos de `min_span_h`
    horas (ruido de janela curta extrapola valores absurdos). Aceita ts no
    formato ".000Z" (snapshots do Claude) e ISO com offset (Codex)."""
    pts = []
    for ts, v in points or []:
        t = parse_iso(ts)
        if t is not None and v is not None:
            pts.append((t, float(v)))
    if len(pts) < 2:
        return None
    i = len(pts) - 1
    while i > 0 and pts[i - 1][1] <= pts[i][1]:
        i -= 1
    seg = pts[i:]
    if len(seg) < 2:
        return None
    span_h = (seg[-1][0] - seg[0][0]).total_seconds() / 3600
    if span_h < min_span_h:
        return None
    return max(0.0, (seg[-1][1] - seg[0][1]) / span_h)


def classify(projected):
    if projected is None:
        return "INDETERMINADO"
    if projected < 80:
        return "SEGURO"
    if projected < 100:
        return "ATENCAO"
    return "RISCO"


def project_window(util, resets_at, window_hours, snap_rate=None, now=None):
    now = now or dt.datetime.now(timezone.utc)
    reset = parse_iso(resets_at)
    out = {"utilization": util, "resets_at": resets_at, "hours_to_reset": None,
           "projected": None, "status": "INDETERMINADO", "rate": None,
           "eta_100": None}
    if util is None or reset is None:
        return out
    start = reset - dt.timedelta(hours=window_hours)
    elapsed_h = max((now - start).total_seconds() / 3600, 0.0)
    htr = max((reset - now).total_seconds() / 3600, 0.0)
    out["hours_to_reset"] = htr

    elapsed_frac = elapsed_h / window_hours if window_hours else 0
    measured = snap_rate if (snap_rate is not None and snap_rate >= 0) else None
    if measured is not None:
        rate = measured                       # taxa medida (span suficiente)
        projected = util + rate * htr
    elif elapsed_frac >= 0.10 and elapsed_h > 0:
        rate = util / elapsed_h               # media desde a abertura da janela
        projected = util + rate * htr         # == util / elapsed_frac
    else:
        rate = None                           # cedo demais para projetar taxa
        projected = util                      # assume uso atual como minimo conservador
    out["rate"] = rate
    if projected is not None:
        projected = min(projected, 999.0)
    out["projected"] = projected
    out["status"] = classify(projected)

    if rate and rate > 0 and util is not None and util < 100:
        h100 = (100 - util) / rate
        out["eta_100"] = (now + dt.timedelta(hours=h100)).isoformat()
    return out


MODEL_WINDOW_PREFIX = "seven_day_model:"


def _family(key):
    return (key or "").split("-", 1)[0]


def _window_family(window_key):
    # "seven_day_model:fable" -> "fable"; "seven_day_model:sonnet_4_6" -> "sonnet"
    return window_key[len(MODEL_WINDOW_PREFIX):].split("_", 1)[0]


def _weight(key):
    return OUTPUT_WEIGHT.get(key) or OUTPUT_WEIGHT.get(_family(key))


def switch_verdict(windows_state, dominant_model, intended_hours, target=None, candidates=None):
    """Estima o impacto de trabalhar `intended_hours` no modelo `target`
    sobre as janelas que ele consome: sessao (5h), semana geral (7d) e, se
    existir, o teto semanal proprio do modelo alvo (seven_day_model:*).

    `target`: chave de pricing (ex. "opus", "fable-5-1"). Se None, escolhe
    entre `candidates` (modelos vistos no ritmo recente) o de outra familia
    com teto proprio presente e maior peso; sem candidatos, "opus".
    """
    cur_key = _model_key(dominant_model) or "sonnet"
    cur_fam = _family(cur_key)
    model_windows = [k for k in windows_state if k.startswith(MODEL_WINDOW_PREFIX)]

    if not target:
        cands = {c for c in (candidates or ()) if c and _family(c) != cur_fam}
        if cands:
            def score(c):
                own = any(_window_family(k) == _family(c) for k in model_windows)
                return (own, _weight(c) or 0.0, c)
            target = max(cands, key=score)
        else:
            target = "opus"
    t_fam = _family(target)

    factor_estimated = False
    w_t = _weight(target)
    if w_t is None:
        w_t, factor_estimated = OUTPUT_WEIGHT["opus"], True
    w_c = _weight(cur_key) or OUTPUT_WEIGHT["sonnet"]
    factor = w_t / w_c

    keys = ["five_hour", "seven_day"] + [k for k in model_windows if _window_family(k) == t_fam]
    general_rate = (windows_state.get("seven_day") or {}).get("rate") or 0.0
    results = {}
    worst = "SEGURO"
    for win in keys:
        st = windows_state.get(win)
        if not st or st.get("utilization") is None:
            continue
        htr = st.get("hours_to_reset") or 0.0
        hrs = min(intended_hours, htr) if htr else intended_hours
        if win.startswith(MODEL_WINDOW_PREFIX):
            # o teto proprio do alvo ja mede o consumo DO alvo: taxa propria
            # quando ha; senao estima a partir do geral pelo fator
            own_rate = st.get("rate")
            proj = st["utilization"] + (own_rate if own_rate else general_rate * factor) * hrs
        else:
            proj = st["utilization"] + (st.get("rate") or 0.0) * factor * hrs
        proj = min(proj, 999.0)
        cls = classify(proj)
        results[win] = {"projected": proj, "status": cls, "hours_to_reset": htr,
                        "utilization": st["utilization"]}
        if _ORDER[cls] > _ORDER[worst]:
            worst = cls

    display = DISPLAY_NAME.get(target) or DISPLAY_NAME.get(t_fam) or target.title()
    tight = _tightest(results)
    msg, msg_id = advice(worst, windows_state.get(tight) if tight else None,
                         results.get(tight), _window_phrase(tight, windows_state))
    return {"verdict": worst, "message": msg, "message_id": msg_id,
            "tightest_window": tight,
            "factor": round(factor, 2), "factor_estimated": factor_estimated,
            "dominant_model": dominant_model, "target": target, "target_label": display,
            "intended_hours": intended_hours, "windows": results}


def _tightest(results):
    """A janela que manda no veredito: pior status, desempate pela projecao."""
    if not results:
        return None
    return max(results, key=lambda k: (_ORDER[results[k]["status"]],
                                       results[k].get("projected") or 0.0))


WINDOW_PHRASE = {
    "five_hour": "a janela da sessão",
    "seven_day": "a semana",
    "seven_day_sonnet": "a semana do Sonnet",
    "seven_day_opus": "a semana do Opus",
}


def _window_phrase(key, windows_state=None):
    """Nome da janela dentro da frase ("a janela da sessão", "a semana do
    Fable"). Sem chave conhecida, cai num generico que sempre cabe."""
    if not key:
        return "a janela"
    if key in WINDOW_PHRASE:
        return WINDOW_PHRASE[key]
    if key.startswith(MODEL_WINDOW_PREFIX):
        label = ((windows_state or {}).get(key) or {}).get("label")
        nome = label or _window_family(key).title()
        return f"a semana do {nome}"
    return "a janela"


def _contrai(prep, janela):
    """"em" + "a janela da sessão" -> "na janela da sessão" (e "de" -> "da").
    Todas as formas de WINDOW_PHRASE comecam com "a "."""
    if janela.startswith("a "):
        return {"em": "n", "de": "d"}[prep] + janela
    return f"{prep} {janela}"


def _maiuscula(texto):
    # .capitalize() rebaixaria o resto ("a semana do Fable" -> "...do fable")
    return texto[:1].upper() + texto[1:]


def _pct(v):
    return f"{v:.0f}%" if v is not None else "?"


def _horas(h):
    if h is None:
        return "pouco tempo"
    if h < 1:
        return "menos de 1h"
    if h < 24:
        return f"{int(round(h))}h"
    return f"{int(round(h / 24))} dias"


def advice(verdict, current, switched, janela="a janela"):
    """Escolhe a frase do veredito. Nao sorteia: cada frase so aparece quando
    o que ela afirma e verdade.

    `current`  = estado da janela no ritmo de agora (utilization, projected,
                 hours_to_reset), usado pelas frases que dizem "no ritmo atual".
    `switched` = a mesma janela projetada num modelo mais pesado, que e o que
                 define o veredito.

    As frases falam de "modelo mais pesado/leve" de proposito: qual modelo e o
    padrao de cada um varia, entao nomear um so' assume um uso que pode nao ser
    o de quem esta lendo.
    """
    cur = (current or {}).get("projected")
    util = (current or {}).get("utilization")
    htr = (current or {}).get("hours_to_reset")
    if htr is None:
        htr = (switched or {}).get("hours_to_reset")
    sw = (switched or {}).get("projected")
    janela_hrs = (current or {}).get("window_hours")
    decorrido = (janela_hrs - htr) if (janela_hrs is not None and htr is not None) else None

    if verdict == "SEGURO":
        if cur is None:
            return "Cota tranquila: a escolha do modelo não é o gargalo agora.", 3
        if htr is not None and htr <= 2:
            return "Você tem folga. Se quiser mais qualidade por resposta, é agora.", 4
        if cur < 40:
            return "A projeção fica bem abaixo do limite: modelo pesado é seguro nesta janela.", 5
        if cur < 65:
            return "Sobra margem até o reset: dá para usar um modelo mais pesado sem apertar a cota.", 1
        return f"No ritmo atual a janela fecha em {_pct(cur)}. Cabe testar um modelo mais caro por token.", 2

    if verdict == "ATENCAO":
        if cur is None:
            return "Ainda cabe, mas sem sobra: use o modelo pesado só onde ele muda o resultado.", 9
        if sw is not None and sw >= 95:
            return "A projeção fecha no limite: dá para subir de modelo, mas de olho no contador.", 6
        if htr is not None and htr >= 12:
            return "Margem curta. Um modelo mais pesado cabe em tarefa pontual, não no dia todo.", 7
        if sw is not None and sw >= 88:
            return "Está no limite do confortável. Se subir de modelo, diminua o volume.", 10
        return f"No ritmo atual você chega a {_pct(cur)} no reset. Vale reservar o modelo caro para o que importa.", 8

    if verdict == "RISCO":
        if cur is None:
            return f"Sem margem para modelo mais caro {_contrai('em', janela)}.", 13
        # ja estourada ou perto do reset: o que resta e' esperar a virada
        if (util is not None and util >= 100) or (htr is not None and htr <= 6):
            return (f"{_maiuscula(janela)} comprometida: guarde o modelo pesado "
                    f"para depois do reset, em {_horas(htr)}."), 15
        if cur >= 100:
            if htr is not None and htr >= 12:
                return (f"Você chega ao teto {_contrai('de', janela)} antes do reset. "
                        f"Ou reduz o ritmo, ou desce de modelo."), 14
            return f"No ritmo atual, {janela} acaba antes do reset. Um modelo mais leve segura até lá.", 11
        # o ritmo de agora aguenta; quem estoura e' a troca por um modelo pesado
        return (f"A projeção passa de 100% {_contrai('em', janela)}: "
                f"subir de modelo agora antecipa o bloqueio."), 12

    if util is None:
        return "Sem leitura suficiente por enquanto. O painel prefere não recomendar no escuro.", 20
    if decorrido is not None and decorrido < 1:
        return "A janela acabou de abrir: sem ritmo medido, qualquer projeção seria chute.", 19
    if util > 0:
        return f"Sem dados para recomendar modelo ainda. O consumo atual está em {_pct(util)}.", 18
    if decorrido is not None and decorrido >= 1:
        return "Coletando amostras: a projeção aparece após cerca de meia hora de uso registrado.", 17
    return "Ainda sem histórico suficiente para projetar. Volte em alguns minutos.", 16


def _build_weight_map(hourly_profile):
    """Normaliza perfil historico para {hora: peso} com pico = 1.0.
    Horas sem dados ficam com 0.0 (sem uso historico = sem projecao de uso).
    """
    if not hourly_profile:
        return {}
    m = {p["hour"]: p["avg_tokens"] for p in hourly_profile}
    peak = max(m.values()) or 1
    return {h: v / peak for h, v in m.items()}


def smart_project_window(util, resets_at, window_hours, hourly_profile,
                         snap_rate=None, now=None):
    """Projecao ponderada pelo perfil historico de uso por hora do dia.

    Para cada hora futura ate o reset, aplica o peso historico daquela
    hora do dia (ex.: madrugada ~ 0, pico de manha ~ 1.0). A taxa base
    (snap_rate ou media da janela) e a taxa na hora atual de trabalho;
    as demais horas escalam proporcionalmente.

    Retorna o mesmo dict de project_window mais:
      projected_linear  — projecao linear simples, para referencia na UI
      smart             — True quando o perfil foi aplicado
    """
    now = now or dt.datetime.now(timezone.utc)
    now_local = dt.datetime.now()  # horario local para lookup hora-do-dia
    reset = parse_iso(resets_at)
    out = {"utilization": util, "resets_at": resets_at, "hours_to_reset": None,
           "projected": None, "projected_linear": None,
           "status": "INDETERMINADO", "rate": None, "eta_100": None,
           "smart": False}
    if util is None or reset is None:
        return out

    start = reset - dt.timedelta(hours=window_hours)
    elapsed_h = max((now - start).total_seconds() / 3600, 0.0)
    htr = max((reset - now).total_seconds() / 3600, 0.0)
    out["hours_to_reset"] = htr

    elapsed_frac = elapsed_h / window_hours if window_hours else 0
    measured = snap_rate if (snap_rate is not None and snap_rate >= 0) else None
    if measured is not None:
        rate = measured
    elif elapsed_frac >= 0.10 and elapsed_h > 0:
        rate = util / elapsed_h
    else:
        rate = None
    out["rate"] = rate

    # Cedo demais para calcular taxa: usa util atual como projecao minima
    if rate is None:
        out["projected"] = util
        out["projected_linear"] = util
        out["status"] = classify(util)
        out["smart"] = False
        return out

    # Projecao linear (referencia)
    if rate is not None:
        out["projected_linear"] = min(util + rate * htr, 999.0)

    weights = _build_weight_map(hourly_profile)

    if rate is not None and weights:
        # Soma de horas futuras ponderadas pelo perfil historico
        steps = int(htr) + 2
        weighted_remaining = 0.0
        for i in range(steps):
            fraction = min(1.0, htr - i)
            if fraction <= 0:
                break
            future_local = now_local + dt.timedelta(hours=i)
            w = weights.get(future_local.hour, 0.0)
            weighted_remaining += w * fraction

        smart_proj = util + rate * weighted_remaining
        out["projected"] = min(smart_proj, 999.0)
        out["smart"] = True

        # ETA para 100% via perfil (busca hora em que acumula o necessario)
        if rate > 0 and util < 100:
            needed_weighted = (100 - util) / rate
            cumulative = 0.0
            for i in range(steps):
                fraction = min(1.0, htr - i)
                if fraction <= 0:
                    break
                future_local = now_local + dt.timedelta(hours=i)
                w = weights.get(future_local.hour, 0.0)
                prev = cumulative
                cumulative += w * fraction
                if cumulative >= needed_weighted:
                    # interpola dentro do intervalo
                    leftover = needed_weighted - prev
                    offset = i + (leftover / (w * fraction + 1e-9)) * fraction if w > 0 else i
                    out["eta_100"] = (now + dt.timedelta(hours=offset)).isoformat()
                    break
    elif rate is not None:
        # Sem perfil historico: cai para projecao linear
        out["projected"] = out["projected_linear"]
        if rate > 0 and util < 100:
            h100 = (100 - util) / rate
            out["eta_100"] = (now + dt.timedelta(hours=h100)).isoformat()

    out["status"] = classify(out["projected"])
    return out


if __name__ == "__main__":
    # cenarios de teste
    now = dt.datetime(2026, 6, 13, 22, 0, tzinfo=timezone.utc)
    print("--- sessao 55%, reset em 3h30 (sem snapshot) ---")
    s = project_window(55.0, "2026-06-14T01:30:00+00:00", 5, now=now)
    print(s["status"], "projecao=", round(s["projected"], 1),
          "taxa=", round(s["rate"], 1), "htr=", round(s["hours_to_reset"], 2))
    print("--- semana 15%, reset em 6h ---")
    w = project_window(15.0, "2026-06-14T04:00:00+00:00", 7 * 24, now=now)
    print(w["status"], "projecao=", round(w["projected"], 2))
    print("--- veredito trocar pra opus (dominante sonnet, 2h) ---")
    print(switch_verdict({"five_hour": s, "seven_day": w},
                         "claude-sonnet-4-6", 2.0))
    print("--- cenario RISCO: sessao 70% com 1h decorrida, reset em 4h ---")
    r = project_window(70.0, "2026-06-14T02:00:00+00:00", 5, now=now)
    print(r["status"], "projecao=", round(r["projected"], 1), "eta100=", r["eta_100"])
