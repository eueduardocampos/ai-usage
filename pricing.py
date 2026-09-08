"""
pricing.py — tabela de preco da API (USD por 1M tokens) e reconhecimento de
modelo. Sem I/O. E' a UNICA fonte disso: store.py (custo dos turnos) e
forecast.py (peso de intensidade no veredito de troca) importam daqui.

Precos da referencia oficial da Anthropic (junho/2026). Convencao da tabela
da API: leitura de cache = 10% da entrada, criacao de cache = 125% da
entrada — exceto o Fable 5.1, cuja leitura de cache e' US$0,25/1M.
"""

PRICING = {
    "fable-5-1": {"in": 10.00, "out": 50.00, "cr": 0.25, "cc": 12.50},
    "fable":     {"in": 10.00, "out": 50.00, "cr": 1.00, "cc": 12.50},
    "opus":      {"in": 5.00,  "out": 25.00, "cr": 0.50, "cc": 6.25},
    "sonnet-5":  {"in": 2.00,  "out": 10.00, "cr": 0.20, "cc": 2.50},
    "sonnet":    {"in": 3.00,  "out": 15.00, "cr": 0.30, "cc": 3.75},
    "haiku":     {"in": 1.00,  "out": 5.00,  "cr": 0.10, "cc": 1.25},
}

# (trecho procurado no id do modelo, chave em PRICING) — a ordem importa:
# os mais especificos ("sonnet-5", "fable-5-1") vem antes da familia.
MODEL_FAMILIES = (
    ("fable-5-1", "fable-5-1"),
    ("sonnet-5", "sonnet-5"),
    ("fable", "fable"),
    ("opus", "opus"),
    ("sonnet", "sonnet"),
    ("haiku", "haiku"),
)

# peso aproximado de "intensidade" por modelo = preco de saida (USD/1M)
OUTPUT_WEIGHT = {key: p["out"] for key, p in PRICING.items()}

# nome da familia para exibicao ("Pode trocar para Fable...")
DISPLAY_NAME = {
    "fable-5-1": "Fable", "fable": "Fable", "opus": "Opus",
    "sonnet-5": "Sonnet", "sonnet": "Sonnet", "haiku": "Haiku",
}


def model_key(model: str):
    """'claude-fable-5-1' -> 'fable-5-1'; 'claude-sonnet-4-6' -> 'sonnet';
    desconhecido -> None (custo 0, nunca chuta preco)."""
    m = (model or "").lower()
    for needle, key in MODEL_FAMILIES:
        if needle in m:
            return key
    return None


def family(model: str):
    """Familia sem versao: 'claude-fable-5-1' -> 'fable', 'claude-sonnet-5' -> 'sonnet'."""
    key = model_key(model)
    return key.split("-", 1)[0] if key else None


def row_cost(model, i, o, cr, cc) -> float:
    p = PRICING.get(model_key(model))
    if not p:
        return 0.0
    return (i * p["in"] + o * p["out"] + cr * p["cr"] + cc * p["cc"]) / 1_000_000
