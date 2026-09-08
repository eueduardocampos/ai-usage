#!/bin/bash
# Monta AIUsageWidget.app a partir do build release do Swift Package.
# Sem assinatura de Developer ID (ad-hoc) -- na primeira abertura o macOS
# pede "Abrir mesmo assim" em Privacidade e Segurança, igual aos
# instaladores unsigned do Tauri (ver ../desktop/RELEASE.md).
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="AIUsageWidget"
BUILD_DIR=".build/release"
APP_DIR=".build/${APP_NAME}.app"
ICON_SRC="../desktop/src-tauri/icons/icon.icns"

echo "==> Compilando release..."
swift build -c release

echo "==> Montando ${APP_DIR}..."
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"
cp "$BUILD_DIR/$APP_NAME" "$APP_DIR/Contents/MacOS/$APP_NAME"
cp Info.plist "$APP_DIR/Contents/Info.plist"
cp "$ICON_SRC" "$APP_DIR/Contents/Resources/AppIcon.icns"

echo "==> Assinando (ad-hoc)..."
codesign --force --deep --sign - "$APP_DIR"

echo "==> Pronto: $APP_DIR"
