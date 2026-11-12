"""Build on each target OS/architecture. Requires PyInstaller in the build env."""
from pathlib import Path
import subprocess
import sys
root = Path(__file__).resolve().parents[2]
# leitor de hardware do macOS, compilado antes pelo workflow (swiftc)
hw = root / 'hwmon' / 'bin' / 'ai-usage-hwmon'
extra = ['--add-binary', str(hw) + ':hwmon/bin'] if hw.exists() else []
subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
    '--collect-data', 'certifi', '--onedir', '--name', 'ai-usage-engine', '--paths', str(root),
    '--add-data', str(root / 'web' / 'dist') + ':web/dist', *extra,
    '--distpath', str(root / 'desktop' / 'src-tauri' / 'engine'),
    '--workpath', str(root / 'dist-release' / 'engine-work'),
    '--specpath', str(root / 'dist-release'),
    str(root / 'desktop' / 'packaging' / 'engine.py')], check=True)
