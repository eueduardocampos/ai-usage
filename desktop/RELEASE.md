## Novidades da 5.0

- **Várias contas.** Conecte quantas contas do Claude precisar e dê um nome a cada uma. Cada conta tem o próprio login e os próprios limites de 5 horas, 7 dias e por modelo. O login abre no navegador padrão ou vira um link para colar no perfil do navegador logado naquela conta.
- **Retorno por conta.** Informe quem paga cada assinatura (você, parte subsidiada ou a empresa), o valor e os extras do mês, ou marque que não tem a informação. O uso de cada sessão do Claude Code vai para a conta do app que a abriu.
- **Esta máquina.** CPU, GPU, memória, disco, rede, bateria e temperaturas no topo do painel (macOS).
- **Painel modular.** Barra fixa com o período (hoje, 24 horas, 7 dias, esta semana, este mês, total), widgets que minimizam, escondem, mudam de ordem e de largura, e predefinições de tela.
- **Stream Deck.** Uma linha por conta, com o ícone do app (clique para abrir) e os limites de 5 horas, 7 dias e por modelo; cada clique alterna entre usado, restante, projeção e reset. Os dials mostram a máquina: girar troca o componente, apertar troca a forma de ver. Perfil novo: **Consumo de IA · Contas**.
- **App nativo do macOS.** Um cartão por conta e uma linha com a máquina.

O consumo de tokens é medido neste computador. Os limites de 5 horas e 7 dias são da conta e já somam todas as máquinas.

## Baixar

| Sistema | Baixe |
| --- | --- |
| Mac com Apple Silicon (M1 ou posterior) | `.dmg` com `aarch64` no nome |
| Mac Intel | `.dmg` com `x64` no nome |
| Windows 10/11 64 bits | `setup.exe` ou `.msi` |
| Linux 64 bits | `.AppImage`, `.deb` ou `.rpm` |

Abra o aplicativo **AI Usage**. Ele inicia o motor local sozinho (não precisa de Python nem de servidor manual) e, no primeiro uso, mostra a tela para conectar as contas. Para o Codex, entre na sua assinatura ChatGPT pelo Codex e use normalmente.

O aplicativo fica na bandeja ou na barra de menus. Use **Sair** para encerrar também o motor iniciado por ele.

**Aplicativos sem assinatura de distribuição:** o macOS pode pedir liberação em Privacidade e Segurança; o Windows pode mostrar o SmartScreen. No Linux, o AppImage pode exigir permissão de execução e FUSE; os pacotes `.deb` e `.rpm` são alternativas.

**Stream Deck (opcional, macOS e Windows):** dois cliques no `.streamDeckPlugin` e depois no perfil **Consumo de IA · Contas**. O perfil vem com duas contas do Claude e uma do ChatGPT; ajuste a conta de cada tecla no próprio Stream Deck.

Os testes automáticos verificam o motor empacotado e os instaladores nas quatro plataformas. Isso não substitui testar a instalação em cada computador.
