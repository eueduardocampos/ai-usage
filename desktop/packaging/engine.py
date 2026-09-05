"""Standalone backend entry point bundled by PyInstaller."""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
parser = argparse.ArgumentParser()
parser.add_argument('--data-dir')
parser.add_argument('--port', type=int, default=8090)
parser.add_argument('--no-collect', action='store_true', help='Offline packaging smoke test')
args = parser.parse_args()
if args.data_dir:
    data = Path(args.data_dir)
elif sys.platform == 'win32':
    data = Path(os.environ['LOCALAPPDATA']) / 'claude-usage'
elif sys.platform == 'darwin':
    data = Path.home() / 'Library' / 'Application Support' / 'claude-usage'
else:
    data = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share')) / 'claude-usage'
data.mkdir(parents=True, exist_ok=True)
os.environ['AI_USAGE_DATA_DIR'] = str(data)
import certifi
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
import server
server.run(desktop=True, port=args.port, collect=not args.no_collect)
