# -*- coding: utf-8 -*-
"""Prova que pricing.py e' a fonte unica de preco/modelo (store e forecast
importam de la) e que o Fable deixa de custar zero."""
import os, sys
sys.path.insert(0, os.getcwd())
import pricing, store, forecast

falhas = []
def check(cond, label):
    print(("  PASS  " if cond else "  FALHA ") + label)
    if not cond: falhas.append(label)

check(pricing.model_key("claude-fable-5-1") == "fable-5-1", "claude-fable-5-1 -> fable-5-1")
check(pricing.model_key("claude-fable-5") == "fable", "claude-fable-5 -> fable")
check(pricing.model_key("claude-opus-5") == "opus", "claude-opus-5 -> opus")
check(pricing.model_key("claude-sonnet-5") == "sonnet-5", "claude-sonnet-5 -> sonnet-5 (preco proprio)")
check(pricing.model_key("claude-sonnet-4-6") == "sonnet", "claude-sonnet-4-6 -> sonnet")
check(pricing.model_key("claude-haiku-4-5") == "haiku", "claude-haiku-4-5 -> haiku")
check(pricing.model_key("gpt-5.5") is None, "modelo desconhecido -> None (custo 0, nao chuta)")
check(pricing.family("claude-fable-5-1") == "fable" and pricing.family("claude-sonnet-5") == "sonnet",
      "family() tira a versao")

# fonte unica
check(store.model_key is pricing.model_key and store.PRICING is pricing.PRICING and store.row_cost is pricing.row_cost,
      "store re-exporta pricing")
check(forecast._model_key is pricing.model_key and forecast.OUTPUT_WEIGHT is pricing.OUTPUT_WEIGHT,
      "forecast importa de pricing")

# Fable custa dinheiro: 1M in + 1M out = 10 + 50 USD
c = pricing.row_cost("claude-fable-5-1", 1_000_000, 1_000_000, 0, 0)
check(abs(c - 60.0) < 1e-9, f"Fable 5.1: 1M in + 1M out = US$60 ({c})")
c5 = pricing.row_cost("claude-fable-5", 0, 0, 1_000_000, 1_000_000)
check(abs(c5 - (1.00 + 12.50)) < 1e-9, f"Fable 5: cache read 1.00 + cache write 12.50 ({c5})")
c51 = pricing.row_cost("claude-fable-5-1", 0, 0, 1_000_000, 0)
check(abs(c51 - 0.25) < 1e-9, f"Fable 5.1: leitura de cache US$0,25/1M ({c51})")
check(pricing.row_cost("claude-sonnet-5", 1_000_000, 0, 0, 0) == 2.0, "Sonnet 5 entrada US$2/1M")
check(pricing.row_cost("claude-sonnet-4-6", 1_000_000, 0, 0, 0) == 3.0, "Sonnet 4.6 entrada US$3/1M (inalterado)")
check(pricing.row_cost("modelo-misterioso", 1_000_000, 1_000_000, 0, 0) == 0.0, "desconhecido custa 0")

# store.group_cost usa a tabela nova
check(store.group_cost("code", "claude-fable-5-1", 1_000_000, 0, 0, 0, 0) == 10.0, "group_cost (code) precifica Fable")
check(store.group_cost("science", "claude-fable-5-1", 1_000_000, 0, 0, 0, 7.5) == 7.5, "group_cost (science) usa custo armazenado")

# peso de intensidade acompanha o preco de saida
check(pricing.OUTPUT_WEIGHT["fable-5-1"] == 50.0 and pricing.OUTPUT_WEIGHT["opus"] == 25.0, "OUTPUT_WEIGHT = preco de saida")
check(all(k in pricing.DISPLAY_NAME for k in pricing.PRICING), "todo modelo precificado tem nome de exibicao")

print()
print(("%d FALHA(S)" % len(falhas)) if falhas else "TUDO PASSOU")
sys.exit(1 if falhas else 0)
