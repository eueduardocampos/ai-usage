Painel **Claude + Codex** com motor integrado: não é necessário instalar Python, Node ou iniciar um servidor manualmente.

| Sistema | Baixe |
| --- | --- |
| Mac com Apple Silicon (M1 ou posterior) | `.dmg` com `aarch64` no nome |
| Mac Intel | `.dmg` com `x64` no nome |
| Windows 10/11 64 bits | `setup.exe` ou `.msi` |
| Linux 64 bits | `.AppImage`, `.deb` ou `.rpm` |

Abra o aplicativo **AI Usage**. Ele inicia o painel local automaticamente. As contas e os registros de uso precisam existir nessa máquina: conecte Claude nas configurações do painel; para Codex, entre na sua assinatura ChatGPT pelo Codex e utilize-o normalmente.

O aplicativo fica na bandeja/barra de menus. Use **Sair** para encerrar também o motor iniciado por ele. Se já houver uma instância compatível do painel rodando, ela será reutilizada e não será encerrada pelo aplicativo.

**Aplicativos sem assinatura de distribuição:** macOS pode exigir liberação em Privacidade e Segurança; Windows pode exibir SmartScreen. No Linux, AppImage pode exigir permissão de execução e FUSE; os pacotes `.deb`/`.rpm` são alternativas. Esses instaladores ainda não oferecem a experiência de confiança de um aplicativo assinado/notarizado.

O plugin Stream Deck 4.1 e os perfis estão disponíveis nos anexos. O Stream Deck é opcional e seu plugin é instalado separadamente no aplicativo da Elgato (macOS/Windows).

Os testes automatizados verificam o motor empacotado e os builds nas quatro plataformas. Isso não substitui testes de instalação e uso em cada computador.
