# -*- coding: utf-8 -*-
"""Prova que um painel.db antigo (api_snapshots sem a coluna label) migra ao
abrir, que linhas antigas continuam legiveis, e que snapshot_series /
snapshot_rate mantem o comportamento (span curto -> None, queda -> None)."""
import os, sqlite3, sys, tempfile
sys.path.insert(0, os.getcwd())
import store as store_mod

falhas = []
def check(cond, label):
    print(("  PASS  " if cond else "  FALHA ") + label)
    if not cond: falhas.append(label)

tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "painel.db")
con = sqlite3.connect(path)
con.executescript("""
CREATE TABLE api_snapshots (ts TEXT, window TEXT, utilization REAL, resets_at TEXT,
                            used_credits REAL, monthly_limit REAL, currency TEXT);
CREATE INDEX idx_snap ON api_snapshots(window, ts);
""")
old = [
    ("2026-09-08T10:00:00.000Z", "five_hour", 1.0, "2026-09-08T15:00:00+00:00", 0, 2000, None),
    ("2026-09-08T10:00:00.000Z", "seven_day", 10.0, "2026-09-09T15:00:00+00:00", 0, 2000, None),
    ("2026-09-08T11:00:00.000Z", "five_hour", 3.0, "2026-09-08T15:00:00+00:00", 0, 2000, None),
    ("2026-09-08T11:00:00.000Z", "seven_day", 12.0, "2026-09-09T15:00:00+00:00", 0, 2000, None),
]
con.executemany("INSERT INTO api_snapshots VALUES (?,?,?,?,?,?,?)", old)
con.commit()
con.close()

st = store_mod.Store(path)
cols = {r[1] for r in st.conn.execute("PRAGMA table_info(api_snapshots)")}
check("label" in cols, "coluna label criada ao abrir banco antigo")
check(st.conn.execute("SELECT COUNT(*) FROM api_snapshots").fetchone()[0] == 4, "linhas antigas preservadas")

latest = st.latest_state()
check(latest["ts"] == "2026-09-08T11:00:00.000Z", "latest_state pega o ultimo ts")
check(latest["windows"]["five_hour"]["label"] is None, "linha antiga: label None (server usa fallback)")
check(latest["windows"]["five_hour"]["utilization"] == 3.0, "linha antiga: utilization legivel")

# taxa por amostragem: 1 -> 3 em 1h = 2/h
r = st.snapshot_rate("five_hour")
check(r is not None and abs(r - 2.0) < 1e-9, f"snapshot_rate = 2/h ({r})")
series = st.snapshot_series("five_hour")
check(series == [("2026-09-08T10:00:00.000Z", 1.0), ("2026-09-08T11:00:00.000Z", 3.0)],
      "snapshot_series do mais antigo ao mais novo")

# insert com label e com janela sem dado
st.insert_snapshot({"windows": {
    "five_hour": {"utilization": 4.0, "resets_at": "2026-09-08T15:00:00+00:00", "label": "Sessão (5h)"},
    "seven_day_model:fable": {"utilization": 0, "resets_at": "2026-09-09T15:00:00+00:00", "label": "Fable"},
    "seven_day_opus": {"utilization": None, "resets_at": None, "label": "Opus (7d)"},
}, "extra_usage": {"used_credits": 0, "monthly_limit": 2000, "currency": None}})
latest = st.latest_state()
check(latest["windows"]["seven_day_model:fable"]["label"] == "Fable"
      and latest["windows"]["seven_day_model:fable"]["utilization"] == 0,
      "janela por modelo gravada com rotulo e zero")
check("seven_day_opus" in latest["windows"] and latest["windows"]["seven_day_opus"]["utilization"] is None,
      "janela sem dado tambem e' gravada (fica INDETERMINADA, nao some)")
check(latest["windows"]["five_hour"]["label"] == "Sessão (5h)", "label com acento gravado")

# queda = reset: a serie 1,3,4 depois recebe 0.5 -> trecho pos-reset curto -> None
st.insert_snapshot({"windows": {"five_hour": {"utilization": 0.5, "resets_at": None, "label": "x"}},
                    "extra_usage": None})
check(st.snapshot_rate("five_hour") is None, "queda de valor (reset) nao vira taxa negativa")

# span curto: dois snapshots no mesmo minuto -> None
st2 = store_mod.Store(os.path.join(tmp, "b.db"))
st2.insert_snapshot({"windows": {"five_hour": {"utilization": 1, "resets_at": None}}, "extra_usage": None})
st2.insert_snapshot({"windows": {"five_hour": {"utilization": 2, "resets_at": None}}, "extra_usage": None})
check(st2.snapshot_rate("five_hour") is None, "span < 30min -> None (deixa o fallback decidir)")

# monkeypatch do test_rate_limit: normalize antigo sem label/ignored_* nao quebra
st2.insert_snapshot({"windows": {}, "extra_usage": None})
check(True, "insert_snapshot tolera dict sem chaves novas")

print()
print(("%d FALHA(S)" % len(falhas)) if falhas else "TUDO PASSOU")
sys.exit(1 if falhas else 0)
