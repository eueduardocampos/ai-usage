# -*- coding: utf-8 -*-
"""Prova a comparacao de versao e o formato do aviso de atualizacao."""
import os, sys
sys.path.insert(0, os.getcwd())
import updates

falhas = []
def check(cond, label):
    print(("  PASS  " if cond else "  FALHA ") + label)
    if not cond: falhas.append(label)

check(updates.is_newer("4.3.0", "4.2.0"), "4.3.0 > 4.2.0")
check(updates.is_newer("4.2.1", "4.2.0"), "4.2.1 > 4.2.0")
check(updates.is_newer("5.0.0", "4.9.9"), "5.0.0 > 4.9.9")
check(updates.is_newer("4.10.0", "4.9.0"), "4.10.0 > 4.9.0 (compara numero, nao texto)")
check(not updates.is_newer("4.2.0", "4.2.0"), "mesma versao nao e' novidade")
check(not updates.is_newer("4.1.0", "4.2.0"), "versao mais velha no GitHub nao vira aviso")
check(not updates.is_newer("", "4.2.0") and not updates.is_newer(None, "4.2.0"), "sem tag: sem aviso")
check(updates.is_newer("v4.3.0", "4.2.0"), "aceita tag com 'v'")
check(not updates.is_newer("4.2.0-beta", "4.2.0"), "sufixo nao conta como versao maior")
check(updates._parts("4.2") == (4, 2, 0), "versao com dois componentes completa com zero")

v = updates.current_version()
check(v and v[0].isdigit(), f"current_version le o arquivo VERSION ({v})")

st = updates.state()
check(set(st) == {"current", "latest", "url", "available"}, f"formato do state: {sorted(st)}")
check(st["current"] == v and isinstance(st["available"], bool), "state consistente")
check(st["url"].startswith("https://github.com/"), "url aponta pra pagina de releases")

print()
print(("%d FALHA(S)" % len(falhas)) if falhas else "TUDO PASSOU")
sys.exit(1 if falhas else 0)
