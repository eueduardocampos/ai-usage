"""Smoke-test the engine from the assembled app/deb, not only the build folder."""
from pathlib import Path
import subprocess
import sys
import tempfile
root=Path(__file__).resolve().parents[2]
bundle=root/'desktop/src-tauri/target'/sys.argv[1]/'release/bundle'
if sys.platform=='darwin':
    with tempfile.TemporaryDirectory() as temp:
        mount=Path(temp)/'mount'
        mount.mkdir()
        subprocess.run(['hdiutil','attach','-readonly','-nobrowse','-mountpoint',str(mount),str(next(bundle.glob('dmg/*.dmg')))],check=True)
        try:
            engine=next(mount.glob('*.app/Contents/Resources/engine/ai-usage-engine'))
            subprocess.run([sys.executable,str(root/'desktop/packaging/smoke.py'),str(engine)],check=True)
        finally:
            subprocess.run(['hdiutil','detach',str(mount)],check=True)
elif sys.platform.startswith('linux'):
    with tempfile.TemporaryDirectory() as temp:
        subprocess.run(['dpkg-deb','-x',str(next(bundle.glob('deb/*.deb'))),temp],check=True)
        engine=next(Path(temp).rglob('engine/ai-usage-engine'))
        subprocess.run([sys.executable,str(root/'desktop/packaging/smoke.py'),str(engine)],check=True)
elif sys.platform=='win32':
    with tempfile.TemporaryDirectory() as temp:
        setup=next(bundle.glob('nsis/*.exe'))
        install=Path(temp)/'app'
        subprocess.run([str(setup),'/S',f'/D={install}'],check=True,timeout=120)
        engine=install/'engine/ai-usage-engine.exe'
        try:
            subprocess.run([sys.executable,str(root/'desktop/packaging/smoke.py'),str(engine)],check=True)
        finally:
            uninstall=install/'uninstall.exe'
            if uninstall.exists():subprocess.run([str(uninstall),'/S'],timeout=120)
print('INSTALLER CONTENTS PASSED')
