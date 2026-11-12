"""Perfil v5 do Stream Deck+ (8 teclas, 4 dials): uma linha por conta.

Cada linha tem 4 teclas: abrir o app da conta, janela de 5 horas, janela de
7 dias e janela por modelo. Duas linhas por pagina; as contas do Claude vem
primeiro, depois as do ChatGPT, e cada provedor comeca numa pagina nova.
Os 4 dials de todas as paginas mostram a maquina (GPU, CPU, memoria, disco).

Uso:
  python build_profile_v5.py                         # Claude 1 + Claude 2, ChatGPT
  python build_profile_v5.py --claude principal,c-ab12cd --codex codex
  python build_profile_v5.py --out "Meu perfil.streamDeckProfile"
"""
from pathlib import Path
import argparse, copy, json, uuid, zipfile

ROOT = Path(__file__).resolve().parent
NS = 'digital.astronauta.claudeusage'
PROFILE_ID = '6E2B3C10-5A0F-4B8E-9C61-1D6F0A7E5B21'  # fixo: reimportar substitui
DIAL_COMPONENTS = ['gpu', 'cpu', 'memory', 'disk']


def page_id(n):
    return str(uuid.uuid5(uuid.UUID(PROFILE_ID), f'pagina-{n}')).upper()


def entry(actions, manifest, action_id, settings):
    a = actions[f'{NS}.{action_id}']
    return {'ActionID': str(uuid.uuid4()), 'LinkedTitle': True, 'Name': a['Name'],
            'Plugin': {'Name': manifest['Name'], 'UUID': NS, 'Version': manifest['Version']},
            'Resources': None, 'Settings': settings, 'State': 0,
            'States': [{'FTitle': '', 'Title': ''}], 'UUID': a['UUID']}


def row(account):
    return [('acctapp', {'accountId': account}),
            ('acctquota', {'accountId': account, 'window': 'five_hour', 'mode': 0}),
            ('acctquota', {'accountId': account, 'window': 'seven_day', 'mode': 0}),
            ('acctquota', {'accountId': account, 'window': 'model', 'mode': 0})]


def build(claude, codex, out):
    manifest = json.loads((ROOT / f'{NS}.sdPlugin/manifest.json').read_text())
    actions = {a['UUID']: a for a in manifest['Actions']}
    with zipfile.ZipFile(ROOT / 'Claude Usage (SD+).streamDeckProfile') as old:
        names = [n for n in old.namelist() if n.endswith('manifest.json')]
        template = json.loads(old.read(next(n for n in names if '/Profiles/' in n)))
        root = json.loads(old.read(next(n for n in names if '/Profiles/' not in n)))

    # linhas agrupadas em paginas de 2, um provedor por pagina
    pages_rows = []
    for group in (claude, codex):
        for i in range(0, len(group), 2):
            pages_rows.append(group[i:i + 2])
    if not pages_rows:
        pages_rows = [[]]

    pages = []
    for n, rows in enumerate(pages_rows):
        keys = {}
        for r, acc in enumerate(rows):
            for c, (aid, st) in enumerate(row(acc)):
                keys[f'{c},{r}'] = entry(actions, manifest, aid, st)
        dials = {f'{i},0': entry(actions, manifest, 'machine', {'component': comp, 'viewIdx': 0})
                 for i, comp in enumerate(DIAL_COMPONENTS)}
        p = copy.deepcopy(template)
        p['Controllers'] = [{'Type': 'Keypad', 'Actions': keys}, {'Type': 'Encoder', 'Actions': dials}]
        p['Name'] = 'Claude' if rows and rows[0] in claude else 'ChatGPT'
        pages.append((page_id(n), p))

    root['Name'] = 'Consumo de IA · Contas'
    ids = [pid.lower() for pid, _ in pages]
    root['Pages'] = {'Current': ids[0], 'Default': ids[0], 'Pages': ids}
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(f'{PROFILE_ID}.sdProfile/manifest.json', json.dumps(root, ensure_ascii=False, indent=2))
        for pid, p in pages:
            z.writestr(f'{PROFILE_ID}.sdProfile/Profiles/{pid}/manifest.json', json.dumps(p, ensure_ascii=False, indent=2))
    print(f'perfil gerado: {out} ({len(pages)} paginas)')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--claude', default='principal,claude-2')
    ap.add_argument('--codex', default='codex')
    ap.add_argument('--out', default=str(ROOT / 'Consumo de IA · Contas.streamDeckProfile'))
    a = ap.parse_args()
    split = lambda s: [x for x in (s or '').split(',') if x]
    build(split(a.claude), split(a.codex), a.out)
