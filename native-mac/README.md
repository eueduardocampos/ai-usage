# AI Usage — widget nativo (Swift)

App de menu bar em SwiftUI, réplica do widget flutuante do Tauri
(`../desktop/`), mas **sem gerenciar o motor Python**: só consome a API já
rodando em `http://localhost:8090` (nesta máquina, mantida de pé pelo
LaunchAgent `digital.astronauta.claude-usage.plist`).

## Rodar

1. Confirme que o backend está de pé: `curl http://localhost:8090/api/health`
   deve responder `{"app":"ai-usage","protocol":1}`. Se não estiver, suba com
   `python3 ../main.py` ou `launchctl start digital.astronauta.claude-usage`.
2. Abra `Package.swift` no Xcode (ou `swift run` no terminal, dentro desta
   pasta).
3. Rode (Cmd+R). Não aparece ícone no Dock — o app vive só na barra de menu
   (ícone de medidor). Clique nele pra ver o menu: Mostrar/Ocultar, Abrir
   painel completo, Sempre no topo, Sair.

## Escopo (v1)

Só o widget flutuante (cartões Claude + Codex, quotas, ritmo de queima) e o
item de menu bar. O painel completo (gráficos, histórico, heatmap) continua
sendo só a versão web, aberta no navegador pelo botão "Painel completo".

Login/reconexão de conta também não é feito aqui — se `auth_connected` vier
`false`, o footer avisa e o botão abre o painel web, que já sabe reconectar.

