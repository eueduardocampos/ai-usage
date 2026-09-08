#!/usr/bin/env python3
"""
bump-version.py — propaga a versao do arquivo VERSION para todos os manifests.

Uso:
    python3 scripts/bump-version.py            # aplica o que esta em VERSION
    python3 scripts/bump-version.py 4.3.0      # grava em VERSION e propaga

Antes disso o mesmo release saia com quatro numeros diferentes (Tauri 3.0.0,
Stream Deck 4.1.0.0, app nativo 1.0.0, web 0.0.0) e o README citava dois deles
na mesma pagina.
"""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def path(*p):
    return os.path.join(ROOT, *p)


def sub_file(rel, pattern, repl, label):
    p = path(rel)
    if not os.path.exists(p):
        print(f"  - {label}: arquivo ausente ({rel}), pulando")
        return
    with open(p, encoding="utf-8") as f:
        src = f.read()
    new, n = re.subn(pattern, repl, src, count=1)
    if n and new != src:
        with open(p, "w", encoding="utf-8") as f:
            f.write(new)
        print(f"  - {label}: atualizado")
    else:
        print(f"  - {label}: ja estava certo")


def main():
    if len(sys.argv) > 1:
        version = sys.argv[1].lstrip("v")
        if not re.fullmatch(r"\d+\.\d+\.\d+", version):
            sys.exit("versao deve ser X.Y.Z")
        with open(path("VERSION"), "w", encoding="utf-8") as f:
            f.write(version + "\n")
    with open(path("VERSION"), encoding="utf-8") as f:
        version = f.read().strip()

    print(f"AI Usage {version}")
    sub_file("desktop/src-tauri/tauri.conf.json", r'"version":\s*"[^"]+"',
             f'"version": "{version}"', "Tauri (tauri.conf.json)")
    sub_file("desktop/src-tauri/Cargo.toml", r'(?m)^version\s*=\s*"[^"]+"',
             f'version = "{version}"', "Tauri (Cargo.toml)")
    # Stream Deck exige quatro componentes
    sub_file("streamdeck/digital.astronauta.claudeusage.sdPlugin/manifest.json",
             r'"Version":\s*"[^"]+"', f'"Version": "{version}.0"', "Stream Deck (manifest.json)")
    sub_file("web/package.json", r'"version":\s*"[^"]+"',
             f'"version": "{version}"', "Painel web (package.json)")
    sub_file("native-mac/Info.plist",
             r'(<key>CFBundleShortVersionString</key>\s*<string>)[^<]+(</string>)',
             rf'\g<1>{version}\g<2>', "App nativo (CFBundleShortVersionString)")
    sub_file("native-mac/Info.plist",
             r'(<key>CFBundleVersion</key>\s*<string>)[^<]+(</string>)',
             rf'\g<1>{version}\g<2>', "App nativo (CFBundleVersion)")
    print("Backend le VERSION direto (updates.current_version); nada a propagar.")


if __name__ == "__main__":
    main()
