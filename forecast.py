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

from pricing import OUTPUT_WEIGHT, model_key as _model_key  # fonte unica (pricing.py)

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


def switch_verdict(windows_state, dominant_model, intended_hours, target="opus"):
    """Estima o impacto de trabalhar `intended_hours` no modelo `target`
    sobre as janelas que ele consome (5h e 7d geral)."""
    cur_key = _model_key(dominant_model) or "sonnet"
    factor = OUTPUT_WEIGHT.get(target, 25.0) / OUTPUT_WEIGHT.get(cur_key, 15.0)
    results = {}
    worst = "SEGURO"
    for win in ("five_hour", "seven_day"):
        st = windows_state.get(win)
        if not st or st.get("utilization") is None:
            continue
        rate = st.get("rate") or 0.0
        htr = st.get("hours_to_reset") or 0.0
        hrs = min(intended_hours, htr) if htr else intended_hours
        proj = st["utilization"] + rate * factor * hrs
        cls = classify(min(proj, 999.0))
        results[win] = {"projected": min(proj, 999.0), "status": cls,
                        "hours_to_reset": htr}
        if _ORDER[cls] > _ORDER[worst]:
            worst = cls

    msg = {
        "SEGURO": f"Pode trocar pra {target.title()} com folga.",
        "ATENCAO": f"Da pra trocar pra {target.title()}, mas acompanhe de perto.",
        "RISCO": f"NAO troque pra {target.title()} agora: deve estourar o limite "
                 f"antes do reset.",
        "INDETERMINADO": "Ainda sem dados suficientes para decidir (colete algumas amostras).",
    }[worst]
    return {"verdict": worst, "message": msg, "factor": round(factor, 2),
            "dominant_model": dominant_model, "target": target,
            "intended_hours": intended_hours, "windows": results}


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
