"""
juntar-historico.py — junta os turnos de um painel.db antigo no painel.db em uso.

  python scripts/juntar-historico.py ORIGEM.db [--destino DESTINO.db] [--executar]

Sem --executar so' mostra o que faria. O destino padrao e' o banco que o painel
usa nesta maquina (mesma regra do server.py). A chave de cada turno e' o id da
mensagem, entao juntar duas vezes, ou juntar um periodo que o destino ja tem,
nao duplica nada (INSERT OR IGNORE). Os snapshots das janelas (api_snapshots)
tambem entram, sem repetir (ts, janela).

Quando usar: o historico ficou dividido em dois bancos (por exemplo, um painel
iniciado de dentro de um app em conteiner escreveu numa copia virtualizada da
pasta local). Pare o painel antes, ou aceite que ele pode segurar o banco por
alguns segundos durante a copia.
"""

import argparse
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


def destino_padrao():
    import server  # noqa: E402  (usa a mesma regra de caminho do painel)
    return server._db_path()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("origem")
    ap.add_argument("--destino", default=None)
    ap.add_argument("--executar", action="store_true", help="grava; sem isso so' mostra")
    a = ap.parse_args()
    destino = a.destino or destino_padrao()
    if not os.path.isfile(a.origem):
        sys.exit(f"origem nao encontrada: {a.origem}")
    if os.path.normcase(os.path.abspath(a.origem)) == os.path.normcase(os.path.abspath(destino)):
        sys.exit("origem e destino sao o mesmo arquivo")

    db = sqlite3.connect(destino, timeout=30)
    db.execute("ATTACH DATABASE ? AS velho", (a.origem,))
    antes = db.execute("SELECT COUNT(*), COALESCE(SUM(input_tokens+output_tokens+cache_read+cache_creation),0) FROM turns").fetchone()
    novos = db.execute("""SELECT COUNT(*), COALESCE(SUM(input_tokens+output_tokens+cache_read+cache_creation),0),
                                 MIN(timestamp), MAX(timestamp)
                          FROM velho.turns v WHERE NOT EXISTS
                          (SELECT 1 FROM main.turns m WHERE m.message_id = v.message_id)""").fetchone()
    cols_velho = {r[1] for r in db.execute("PRAGMA velho.table_info(turns)")}
    snaps = db.execute("""SELECT COUNT(*) FROM velho.api_snapshots v WHERE NOT EXISTS
                          (SELECT 1 FROM main.api_snapshots m WHERE m.ts = v.ts AND m.window = v.window)""").fetchone()[0]
    print(f"destino: {destino}\n  hoje: {antes[0]} turnos, {antes[1]:,} tokens")
    print(f"origem: {a.origem}\n  a juntar: {novos[0]} turnos, {novos[1]:,} tokens, de {novos[2]} a {novos[3]}; {snaps} snapshots")
    if not a.executar:
        print("\nnada gravado (use --executar)")
        return
    src = "COALESCE(source,'code')" if "source" in cols_velho else "'code'"
    cost = "cost" if "cost" in cols_velho else "NULL"
    with db:
        db.execute(f"""INSERT OR IGNORE INTO main.turns
            (message_id, timestamp, model, input_tokens, output_tokens, cache_read, cache_creation, source, cost)
            SELECT message_id, timestamp, model, input_tokens, output_tokens, cache_read, cache_creation, {src}, {cost}
            FROM velho.turns""")
        db.execute("""INSERT INTO main.api_snapshots (ts, window, utilization, resets_at, used_credits, monthly_limit, currency, label)
            SELECT v.ts, v.window, v.utilization, v.resets_at, v.used_credits, v.monthly_limit, v.currency,
                   CASE WHEN EXISTS (SELECT 1 FROM pragma_table_info('api_snapshots') WHERE name='label') THEN NULL ELSE NULL END
            FROM velho.api_snapshots v WHERE NOT EXISTS
            (SELECT 1 FROM main.api_snapshots m WHERE m.ts = v.ts AND m.window = v.window)""")
    depois = db.execute("SELECT COUNT(*), COALESCE(SUM(input_tokens+output_tokens+cache_read+cache_creation),0) FROM turns").fetchone()
    print(f"\nfeito: {depois[0]} turnos, {depois[1]:,} tokens (+{depois[0]-antes[0]} turnos)")


if __name__ == "__main__":
    main()
