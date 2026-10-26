# -*- coding: utf-8 -*-
"""Scan incremental: so' o que foi anexado e' lido, linha pela metade espera o
proximo scan, arquivo que encolheu e' relido do zero sem duplicar, e um banco
antigo (processed_files sem offset) migra ao abrir."""
import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import store as store_mod

falhas = []
def check(cond, label):
    print(("  PASS  " if cond else "  FALHA ") + label)
    if not cond: falhas.append(label)

def turno(mid, tokens=10):
    return json.dumps({"type": "assistant", "timestamp": "2026-10-20T12:00:00.000Z",
                       "message": {"id": mid, "model": "claude-opus-5-5",
                                   "usage": {"input_tokens": tokens, "output_tokens": 1}}})

tmp = tempfile.mkdtemp()
proj = os.path.join(tmp, "projects", "p1"); os.makedirs(proj)
store_mod.PROJECTS_DIR = os.path.join(tmp, "projects")
store_mod.SCIENCE_DB_GLOB = os.path.join(tmp, "nada", "*.db")
fp = os.path.join(proj, "s.jsonl")

with open(fp, "w", encoding="utf-8") as f:
    f.write(turno("a") + "\n" + turno("b") + "\n")
st = store_mod.Store(os.path.join(tmp, "painel.db"))
r = st.scan()
check(r["new_turns"] == 2 and r["new_files"] == 1, "primeiro scan le as 2 linhas")
tam1 = os.path.getsize(fp)
off = st.conn.execute("SELECT offset, size FROM processed_files WHERE path=?", (fp,)).fetchone()
check(off[0] == tam1 and off[1] == tam1, "offset e tamanho gravados no fim do arquivo")

check(st.scan()["new_turns"] == 0, "sem mudanca: nada relido")

# anexa uma linha inteira e meia linha (escrita em andamento)
with open(fp, "a", encoding="utf-8") as f:
    f.write(turno("c") + "\n" + turno("d")[:20])
os.utime(fp, None)
r = st.scan()
check(r["new_turns"] == 1, "anexo: le so' a linha nova inteira")
esperado = os.path.getsize(fp) - len(turno("d")[:20].encode("utf-8"))  # tudo menos a meia linha
check(st.conn.execute("SELECT offset FROM processed_files WHERE path=?", (fp,)).fetchone()[0] == esperado,
      "offset para antes da linha pela metade")
with open(fp, "a", encoding="utf-8") as f:
    f.write(turno("d")[20:] + "\n")
r = st.scan()
check(r["new_turns"] == 1 and st.conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 4, "linha completada entra no scan seguinte")

# arquivo reescrito menor: relido do zero, sem duplicar
with open(fp, "w", encoding="utf-8") as f:
    f.write(turno("a") + "\n" + turno("e") + "\n")
r = st.scan()
check(r["new_turns"] == 1 and st.conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 5, "arquivo encolheu: relido do zero, so' 'e' e' nova")

# _parse_file nao depende do store
rows, n, offset = store_mod.Store._parse_file(fp, 0)
check(len(rows) == 2 and offset == os.path.getsize(fp), "_parse_file devolve linhas e offset")

# banco antigo sem as colunas novas migra
velho = os.path.join(tmp, "velho.db")
c = sqlite3.connect(velho)
c.executescript("""CREATE TABLE turns (message_id TEXT PRIMARY KEY, timestamp TEXT, model TEXT,
  input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0, cache_read INTEGER DEFAULT 0, cache_creation INTEGER DEFAULT 0);
CREATE TABLE processed_files (path TEXT PRIMARY KEY, mtime REAL, lines INTEGER);
INSERT INTO processed_files VALUES ('x', 1.0, 3);""")
c.commit(); c.close()
st2 = store_mod.Store(velho)
cols = {r[1] for r in st2.conn.execute("PRAGMA table_info(processed_files)")}
check({"size", "offset"} <= cols, "banco antigo ganha size e offset")
check(st2.scan()["new_turns"] == 2, "banco antigo le os arquivos do zero (hoje o arquivo tem 'a' e 'e')")

print("\nTUDO PASSOU" if not falhas else f"\n{len(falhas)} falha(s)")
sys.exit(1 if falhas else 0)
