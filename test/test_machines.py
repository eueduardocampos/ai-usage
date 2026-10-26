# -*- coding: utf-8 -*-
"""Duas maquinas, uma pasta compartilhada: cada painel publica o proprio
consumo, le o da outra, e o historico soma os dois sem contar nada duas vezes."""
import datetime as dt
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import codex_usage
import machines
import store as store_mod
from pricing import row_cost

falhas = []
def check(cond, label):
    print(("  PASS  " if cond else "  FALHA ") + label)
    if not cond: falhas.append(label)

# horas cheias: m1 e m2 caem sempre na mesma hora, seja qual for o relogio
now = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
ts = lambda h, m=0: (now - dt.timedelta(hours=h) + dt.timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

def make_store(tmp, name, turns):
    st = store_mod.Store(os.path.join(tmp, name + ".db"))
    st.conn.executemany("""INSERT INTO turns (message_id, timestamp, model, input_tokens,
        output_tokens, cache_read, cache_creation) VALUES (?,?,?,?,?,?,?)""", turns)
    st.conn.commit()
    return st

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:  # sqlite segura o arquivo no Windows
    shared = os.path.join(tmp, "compartilhada")
    mac = make_store(tmp, "mac", [
        ("m1", ts(2, 10), "claude-opus-4-7", 100, 200, 1000, 50),
        ("m2", ts(2, 20), "claude-opus-4-7", 10, 20, 100, 5),   # mesma hora de m1
        ("m3", ts(30), "claude-sonnet-5", 1, 2, 3, 4),
    ])
    win = make_store(tmp, "win", [
        ("w1", ts(2), "claude-fable-5-1", 1000, 2000, 3000, 400),
    ])
    win_codex = codex_usage.CodexUsage()
    s_mac = machines.MachineSync(mac, None, shared, "Mac", log=lambda *a: None)
    s_win = machines.MachineSync(win, win_codex, shared, "Windows", log=lambda *a: None)

    mac_alone = mac.scope_summary("geral")
    s_mac.run_once(force=True)
    s_win.run_once(force=True)
    s_mac.run_once(force=True)  # agora o Mac ja enxerga o arquivo do Windows

    check(sorted(os.listdir(shared)) == ["mac.json", "windows.json"], "um arquivo por maquina")
    with open(os.path.join(shared, "mac.json"), encoding="utf-8") as f:
        payload = json.load(f)
    check(len(payload["claude"]) == 2, "Mac publica agregado por hora (m1+m2 viram uma linha)")
    check(payload["machine"] == "Mac" and payload["format"] == 1, "cabecalho do arquivo")

    g_mac, g_win = mac.scope_summary("geral"), win.scope_summary("geral")
    total = 1350 + 135 + 10 + 6400  # tokens de m1, m2, m3 e w1
    check(g_mac["total_tokens"] == total, "Mac soma os dois: %s" % g_mac["total_tokens"])
    check(g_win["total_tokens"] == total, "Windows soma os dois: %s" % g_win["total_tokens"])
    check(g_mac["total_turns"] == 4 and g_win["total_turns"] == 4, "turnos somam pela contagem da hora")
    exp_cost = (row_cost("claude-opus-4-7", 110, 220, 1100, 55) + row_cost("claude-sonnet-5", 1, 2, 3, 4)
                + row_cost("claude-fable-5-1", 1000, 2000, 3000, 400))
    check(abs(g_win["total_cost"] - exp_cost) < 1e-9, "custo agregado por hora = custo turno a turno")
    check(set(g_mac["by_machine"]) == {"Mac", "Windows"}, "by_machine com as duas")
    check(g_mac["by_machine"]["Mac"]["local"] and not g_mac["by_machine"]["Windows"]["local"], "marca a local")
    check(g_mac["by_machine"]["Mac"]["tokens"] == mac_alone["total_tokens"], "parte local = total antes da soma")
    check(mac.total_summary()["total_tokens"] == total, "/api/total tambem soma")
    check(sum(d["tokens"] for d in mac.daily_series()) == total, "serie diaria soma")

    # republicar nao duplica: o arquivo e' sempre o historico completo
    s_win.export(force=True); s_mac.run_once(force=True)
    check(mac.scope_summary("geral")["total_tokens"] == total, "reimportar nao duplica")

    # nunca devolve ao Windows o que veio dele
    check(len(mac.local_hours()) == 2, "local_hours ignora o que foi importado")

    # projecoes continuam so' locais
    check(set(mac.recent_tokph(48)) == {"claude-opus-4-7", "claude-sonnet-5"}, "queima so' local")

    # copia de conflito e arquivo de outro app sao ignorados
    with open(os.path.join(shared, "windows.sync-conflict-20261025-1.json"), "w") as f:
        json.dump(dict(payload, machine="Velho"), f)
    with open(os.path.join(shared, "outro.json"), "w") as f:
        json.dump({"app": "x"}, f)
    s_mac.run_once(force=True)
    check(set(mac.scope_summary("geral")["by_machine"]) == {"Mac", "Windows"}, "ignora conflito/arquivo alheio")

    # maquina que saiu da pasta deixa de somar
    os.remove(os.path.join(shared, "windows.json"))
    s_mac.run_once(force=True)
    check(mac.scope_summary("geral")["total_tokens"] == mac_alone["total_tokens"], "arquivo removido some da soma")
    check([m["name"] for m in s_mac.info()["machines"]] == ["Mac"], "info sem a maquina removida")

    # Codex: eventos da outra maquina entram no historico, nao na queima
    win_codex._files["x"] = (0, {"model_provider": "openai", "session_id": "s", "limits": [],
        "events": [{"timestamp": ts(1, 30).replace(".000Z", "+00:00"), "model": "gpt-5.5",
                    "input_tokens": 300000, "cached_input_tokens": 0, "output_tokens": 10,
                    "reasoning_output_tokens": 0, "total_tokens": 300010}]})
    mac_codex = codex_usage.CodexUsage()
    s_mac2 = machines.MachineSync(mac, mac_codex, shared, "Mac", log=lambda *a: None)
    s_win.export(force=True); s_mac2.run_once(force=True)
    cs = mac_codex.state()
    check(cs["history"]["geral"]["total_tokens"] == 300010, "Codex da outra maquina no historico")
    check(cs["burn_tokph"] == 0, "Codex remoto fora da queima")
    check(abs(cs["history"]["geral"]["equivalent_usd"]
              - codex_usage.equivalent_usd(win_codex.local_events()[0])) < 1e-9,
          "Codex mantem o preco por chamada (acima de 272k)")
    check(cs["history"]["geral"]["by_machine"]["Windows"]["tokens"] == 300010, "Codex by_machine")

print("\nTUDO PASSOU" if not falhas else f"\n{len(falhas)} falha(s)")
sys.exit(1 if falhas else 0)
