"""Build the 8-key / 4-dial AI Usage profile, retaining legacy profile artifact."""
from pathlib import Path
import copy,json,uuid,zipfile,sys
ROOT=Path(__file__).resolve().parent
NS='digital.astronauta.claudeusage'
KEYS=['claude5','claude7','codexquota','sparkquota','claudeburn','codexburn','clauderoi','codexroi']
DIALS=['aiwindows','aiburn','aicosts','aitotals']

def page(keys=None):
    manifest=json.loads((ROOT/f'{NS}.sdPlugin/manifest.json').read_text())
    actions={a['UUID']:a for a in manifest['Actions']}
    controllers=[]
    for kind,ids in [('Keypad',keys or KEYS),('Encoder',DIALS)]:
        entries={}
        for i,id in enumerate(ids):
            action=actions[f'{NS}.{id}']
            entries[f'{i%4},{i//4}']={'ActionID':str(uuid.uuid4()),'LinkedTitle':True,
                'Name':action['Name'],'Plugin':{'Name':manifest['Name'],'UUID':NS,'Version':manifest['Version']},
                'Resources':None,'Settings':{},'State':0,'States':[{'FTitle':'','Title':''}],'UUID':action['UUID']}
        controllers.append({'Type':kind,'Actions':entries})
    return {'Controllers':controllers}

if __name__=='__main__':
    daily = '--daily' in sys.argv
    profile_id='47830620-EE43-4A66-8EED-02F1E9C37B40'
    page_id='D3A7B85B-E4F8-468A-B0B7-F7BDAD216919'
    with zipfile.ZipFile(ROOT/'Claude Usage (SD+).streamDeckProfile') as old:
        manifests=[n for n in old.namelist() if n.endswith('manifest.json')]
        oldpage=json.loads(old.read(next(n for n in manifests if '/Profiles/' in n)))
        root=json.loads(old.read(next(n for n in manifests if '/Profiles/' not in n)))
    if daily:
        profile_id='A4B5AC5D-F7A6-4C6F-AD84-93E10A3D57E5'
        page_id='5B0E95B4-27A1-4450-A834-A138D73B3F01'
    root['Name']='Consumo de IA' if daily else 'AI Usage · Claude + Codex (SD+)'
    root['Pages']={'Current':page_id.lower(),'Default':page_id.lower(),'Pages':[page_id.lower()]}
    newpage=copy.deepcopy(oldpage);newpage['Controllers']=page(['claude5','claude7','codexquota','source','claudeburn','codexburn','clauderoi','codexroi'] if daily else None)['Controllers']
    with zipfile.ZipFile(ROOT/('Consumo de IA.streamDeckProfile' if daily else 'AI Usage (SD+).streamDeckProfile'),'w',zipfile.ZIP_DEFLATED) as out:
        out.writestr(f'{profile_id}.sdProfile/manifest.json',json.dumps(root,ensure_ascii=False,indent=2))
        out.writestr(f'{profile_id}.sdProfile/Profiles/{page_id}/manifest.json',json.dumps(newpage,ensure_ascii=False,indent=2))
    print('AI Usage profile built: 8 keys, 4 encoders')
