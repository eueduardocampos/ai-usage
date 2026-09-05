# AI Usage no Stream Deck

Plugin oficial do painel para Elgato Stream Deck (Windows e macOS). Cada dado é
uma **ação dedicada** — arraste a tecla e ela já funciona, sem configurar nada,
desde que o painel (`python main.py`) esteja rodando em `localhost:8090`.

O plugin é vitrine, não coletor: ele lê `GET /api/state` e `/api/total` do
painel local. Não fala com a API da Anthropic e não guarda chave nenhuma.
Node puro, zero dependências.

## Perfil recomendado: Consumo de IA

![Oito teclas e quatro dials com o novo visual](../docs/streamdeck.png)

Prévia com dados demonstrativos. A versão 4.1 usa título curto, número grande,
reset separado e barra apenas nas cotas. Claude em laranja; Codex em verde.

| Linha | Tecla 1 | Tecla 2 | Tecla 3 | Tecla 4 |
| --- | --- | --- | --- | --- |
| Superior | Claude 5h | Claude 7d | Codex | Claude licença/extras |
| Inferior | Claude tokens/h | Codex tokens/h | Claude retorno | Codex retorno |

Os quatro dials mostram cotas, ritmo, equivalência e totais. Gire para alternar
as métricas; pressione para abrir o painel. O toque atualiza a leitura local.

## Ações clássicas

### Teclas

| Ação | Mostra |
| --- | --- |
| **Janela crítica** | a janela de uso com menos folga (5h ou 7d), anel colorido pela projeção |
| **Sessão 5h + Semana 7d** | as duas janelas empilhadas, com barra e tempo pro reset |
| **Fonte dos tokens** | `LICENCA` (verde) ou `EXTRAS` (vermelho, com R$/h) — se o uso atual está sendo cobrado à parte |
| **Estouro** | `OK`, ou a hora em que a janela estoura (só quando cai antes do reset) |
| **Trocar de modelo?** | veredito: dá pra usar Opus agora? |
| **Custo hoje** | custo estimado do dia em R$ |
| **Balanço do mês** | alavancagem da licença (consumo equivalente ÷ desembolso) |
| **Queima agora** | tokens/h do modelo dominante — vira `R$/h` vermelho ao queimar extras |

Pressionar qualquer tecla abre o painel no navegador.

## Dials (Stream Deck +)

| Dial | Girar faz |
| --- | --- |
| **Horizonte** | ajusta o "pretendo trabalhar mais" (30m–4h) — grava no painel e o veredito recalcula na hora |
| **Janelas** | alterna 5h / 7d (valor = usado, barra = projetado) |
| **Custos** | alterna hoje / semana / mês / extras |
| **Vida toda** | alterna tokens totais / custo total / interações |

Pressionar abre o painel; tocar no strip força uma atualização.

## Instalar

1. Baixe o `digital.astronauta.claudeusage.streamDeckPlugin` na
   [página de releases](https://github.com/eueduardocampos/claude-usage/releases)
   e dê **dois cliques** — o app do Stream Deck instala sozinho.
2. (Stream Deck +) importe [Consumo de IA.streamDeckProfile](Consumo%20de%20IA.streamDeckProfile): as oito teclas e os quatro dials aparecem montados de uma vez.

Para obter a versão 4.1 diretamente deste repositório, use a instalação manual
abaixo. Releases anteriores podem conter o visual clássico.

Instalação manual (dev): copie a pasta `digital.astronauta.claudeusage.sdPlugin`
para `%APPDATA%\Elgato\StreamDeck\Plugins\` (Windows) ou
`~/Library/Application Support/com.elgato.StreamDeck/Plugins/` (macOS) e
reinicie o app do Stream Deck.

## Configuração

Tudo global (vale para todas as teclas), em qualquer inspector: URL do painel
(default `http://127.0.0.1:8090`), intervalo de atualização (default 10s,
mínimo 2 — a leitura é local, sem custo), polaridade usado/restante e
limiares de cor. A ação **Consumo (configurável)** é a genérica da v2, mantida
por compatibilidade com perfis antigos.

## A tecla nunca fica em branco

| Na tela | Significa | O que fazer |
| --- | --- | --- |
| `OFF` | o painel não está rodando | `python main.py` |
| `--` + "reconectar" | painel de pé, conta sem token válido | `python auth.py login` |
| `12%!` | dado antigo, de antes da conta desconectar | reconectar a conta |

## Testar sem o hardware

`test/fake_sd.js` sobe um Stream Deck falso (protocolo real) + um painel falso,
spawna o plugin de verdade, gira os dials e confere cada `setImage`/`setFeedback`:

```bash
node streamdeck/test/fake_sd.js
```

## AI Usage 4 · Claude + Codex

O perfil `AI Usage (SD+).streamDeckProfile` usa as 8 teclas e os 4 encoders:

| Linha | Tecla 1 | Tecla 2 | Tecla 3 | Tecla 4 |
| --- | --- | --- | --- | --- |
| Superior | Claude 5h | Claude 7d | Codex principal | Spark |
| Inferior | Claude tokens/h | Codex tokens/h | Claude retorno | Codex retorno |

| Dial | Rotação |
| --- | --- |
| Cotas | Todas as janelas reais das contas, com consumo primeiro |
| Ritmo | Totais por plataforma e cada modelo nas últimas 2 horas |
| Equivalência | Claude e Codex em hoje, semana e mês |
| Totais | Tokens combinados, separados e retorno das assinaturas |

Laranja identifica Claude; verde identifica Codex. Cotas >=90% ficam vermelhas;
`!` e cinza indicam snapshot antigo ou erro de consulta. Um problema no login
Claude não desconecta a exibição do Codex. O plugin não cria uma janela Codex de
5h quando a conta só fornece 7d. Spark sem dados mostra `--`.

Retorno = equivalência de API dividida por assinatura + extras informados. Usa
mensalidade e câmbio configurados por você no painel.
Modelos sem preço são identificados como parcial; saldo de créditos não é reais.
O botão e a pressão no dial abrem o painel; toque atualiza a leitura local.

As ações v3 e seus UUIDs continuam disponíveis. Gere o perfil com
`python3 streamdeck/build_profile.py`. Testes: `node streamdeck/test/unified.js`
e `node streamdeck/test/fake_sd.js` (inclui ações novas e legadas).

### Perfil diário: Consumo de IA

Importe `Consumo de IA.streamDeckProfile` para um perfil independente, preservando
os anteriores. A linha superior mostra Claude 5h, Claude 7d, Codex e fonte de
cobrança do Claude (licença/extras). A inferior mostra ritmo Claude/Codex e retorno
mensal Claude/Codex. Spark fica no dial de cotas, evitando ocupar uma tecla com
uso normalmente zerado. Os quatro dials mantêm cotas, ritmo, equivalência e totais.

Prioridade diária: cota restante e cobrança extra. Ritmo ajuda a entender a carga;
retorno mensal é uma referência de assinatura, não um incentivo a gastar tokens.
Gere novamente com `python3 streamdeck/build_profile.py --daily`.
