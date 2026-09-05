"""Verify a packaged engine without installed Python, accounts or a network API."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
root=Path(__file__).resolve().parents[2]
exe=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else root/'desktop/src-tauri/engine/ai-usage-engine'/('ai-usage-engine.exe' if os.name=='nt' else 'ai-usage-engine')
with socket.socket() as sock:
    sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
with tempfile.TemporaryDirectory() as temp:
    env=dict(os.environ,HOME=temp,USERPROFILE=temp,PATH='')
    with open(Path(temp)/'engine.log','w+') as log:
        process=subprocess.Popen([str(exe),'--data-dir',temp,'--port',str(port),'--no-collect'],env=env,stdout=log,stderr=log,cwd=temp)
        try:
            url=f'http://127.0.0.1:{port}'
            for _ in range(100):
                try:
                    with urllib.request.urlopen(url+'/api/health',timeout=1) as r:
                        assert json.load(r)=={'app':'ai-usage','protocol':1}
                    break
                except OSError:
                    if process.poll() is not None:
                        log.seek(0);raise RuntimeError(log.read())
                    time.sleep(.1)
            else:raise TimeoutError('Engine did not start')
            for path in ['/api/state','/api/total','/api/history']:
                with urllib.request.urlopen(url+path) as r:json.load(r)
            with urllib.request.urlopen(url) as r:
                assert '<html' in r.read().decode().lower()
            payload=json.dumps({'chatgpt_subscription_brl':100}).encode()
            req=urllib.request.Request(url+'/api/config',data=payload,headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(req) as r:assert json.load(r)['ok']
            assert json.loads((Path(temp)/'config.json').read_text())['chatgpt_subscription_brl']==100
            assert (Path(temp)/'painel.db').exists()
            print('PACKAGED ENGINE PASSED: isolated HOME, empty PATH, APIs, bundled UI, writable settings/database')
        finally:
            process.terminate();process.wait(timeout=10)
