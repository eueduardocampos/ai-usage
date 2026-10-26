# -*- coding: utf-8 -*-
"""scripts/juntar-historico.py: traz os turnos de um banco antigo sem duplicar
o que o destino ja tem, e rodar duas vezes nao muda nada."""
import os
import sqlite3
import subprocess
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
import store as store_mod

falhas = []
def check(cond, label):
    print(("  PASS  " if cond else "  FALHA ") + label)
    if not cond: falhas.append(label)

tmp = tempfile.mkdtemp()
origem, destino = os.path.join(tmp, "velho.db"), os.path.join(tmp, "novo.db")
# origem no formato antigo (sem source/cost), com 3 turnos e 1 snapshot
c = sqlite3.connect(origem)
c.executescript("""CREATE TABLE turns (message_id TEXT PRIMARY KEY, timestamp TEXT, model TEXT,
  input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0, cache_read INTEGER DEFAULT 0, cache_creation INTEGER DEFAULT 0);
CREATE TABLE api_snapshots (ts TEXT, window TEXT, utilization REAL, resets_at TEXT, used_credits REAL, monthly_limit REAL, currency TEXT);
INSERT INTO turns VALUES ('a','2026-05-01T10:00:00.000Z','claude-opus-4-7',1,2,3,4);
INSERT INTO turns VALUES ('b','2026-06-01T10:00:00.000Z','claude-opus-4-7',10,20,30,40);
INSERT INTO turns VALUES ('c','2026-10-10T10:00:00.000Z','claude-opus-5-5',100,200,300,400);
INSERT INTO api_snapshots VALUES ('2026-06-01T10:00:00.000Z','five_hour',10,NULL,NULL,NULL,NULL);""")
c.commit(); c.close()
# destino atual ja tem o turno 'c'
st = store_mod.Store(destino)
st.conn.execute("INSERT INTO turns (message_id, timestamp, model, input_tokens, output_tokens, cache_read, cache_creation) VALUES ('c','2026-10-10T10:00:00.000Z','claude-opus-5-5',100,200,300,400)")
st.conn.commit(); st.conn.close()

script = os.path.join(RAIZ, "scripts", "juntar-historico.py")
def roda(*extra):
    return subprocess.run([sys.executable, script, origem, "--destino", destino, *extra], capture_output=True, text=True, cwd=RAIZ)

r = roda()
check(r.returncode == 0 and "a juntar: 2 turnos" in r.stdout and "nada gravado" in r.stdout, "sem --executar so' mostra: 2 turnos a juntar")
check(sqlite3.connect(destino).execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1, "destino intacto sem --executar")
r = roda("--executar")
db = sqlite3.connect(destino)
check(r.returncode == 0 and db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 3, "com --executar: 3 turnos, 'c' nao duplicou")
check(db.execute("SELECT COALESCE(source,'code') FROM turns WHERE message_id='a'").fetchone()[0] == 'code', "origem antiga sem coluna source vira 'code'")
check(db.execute("SELECT COUNT(*) FROM api_snapshots").fetchone()[0] == 1, "snapshot antigo entra")
r = roda("--executar")
check(db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 3 and db.execute("SELECT COUNT(*) FROM api_snapshots").fetchone()[0] == 1, "rodar de novo nao muda nada")
r = roda("--destino", origem)
check(r.returncode != 0, "origem igual ao destino e' recusado")

print("\nTUDO PASSOU" if not falhas else f"\n{len(falhas)} falha(s)")
sys.exit(1 if falhas else 0)
