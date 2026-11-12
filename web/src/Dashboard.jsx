import { useCallback, useEffect, useState } from 'react';
import { Chart, registerables } from 'chart.js';
import { Bar, Line } from 'react-chartjs-2';
import { api } from './api';
import { fmtTokens, fmtMoney, fmtInt } from './format';
import HeatmapChart from './components/HeatmapChart';
import './dashboard.css';
import './shell.css';
import Hardware, { useHardware } from './Hardware';
import { AccountCard, AccountsManager, Onboarding } from './Accounts';
import { WIDGETS, PRESETS, PERIODS, normalizeLayout } from './layout';
Chart.register(...registerables);
const ORANGE = '#ef985d',
  GREEN = '#45d6aa';
const money = v => v == null ? '—' : fmtMoney(v, 'BRL');
const pct = v => v == null ? '—' : `${v.toFixed(1)}%`;
const scopes = [['dia', 'Hoje'], ['semana', 'Semana'], ['mes', 'Mês'], ['geral', 'Histórico']];
function resetText(value, now) {
  const h = (new Date(value).getTime() - now) / 3600000;
  return !Number.isFinite(h) ? 'sem previsão' : h <= 0 ? 'aguardando reset' : h >= 24 ? `${Math.floor(h / 24)}d ${Math.floor(h % 24)}h` : `${Math.floor(h)}h ${Math.floor(h % 1 * 60)}m`;
}
function since(iso, now) {
  if (!iso) return 'ainda sem dados';
  const min = Math.max(0, Math.round((now - new Date(iso)) / 60000));
  if (min < 2) return 'agora';
  if (min < 60) return `há ${min} min`;
  const h = Math.round(min / 60);
  return h < 48 ? `há ${h} h` : `há ${Math.round(h / 24)} dias`;
}
// Consumo de cada computador da conta + a soma. So' aparece com sync_dir
// configurado (state.machines). As cotas de 5h/semana ja sao da conta inteira.
function Machines({ state, cg, fx, now }) {
  const info = state.machines;
  if (!info) return null;
  const hc = state.history || {},
    ho = cg.history || {};
  const part = (scope, name) => {
    const a = hc[scope]?.by_machine?.[name],
      b = ho[scope]?.by_machine?.[name];
    const cost = a?.cost_brl == null && (b?.equivalent_usd == null || !fx) ? null
      : (a?.cost_brl || 0) + (b?.equivalent_usd && fx ? b.equivalent_usd * fx : 0);
    return { tokens: (a?.tokens || 0) + (b?.tokens || 0), cost };
  };
  const rows = info.machines.map(m => ({ ...m, all: part('geral', m.name), month: part('mes', m.name) }));
  const sum = key => rows.reduce((t, r) => t + (r[key].tokens || 0), 0);
  const sumCost = key => rows.some(r => r[key].cost != null) ? rows.reduce((t, r) => t + (r[key].cost || 0), 0) : null;
  const total = sum('all');
  return <Panel title="Consumo por máquina" note="Mesma conta · cada computador publica o próprio histórico"><div className="u-table-wrap"><table><thead><tr><th>Máquina</th><th>Tokens · histórico</th><th>Participação</th><th>Tokens · mês</th><th>Equivalente no mês</th><th>Dados de</th></tr></thead><tbody>
    {rows.map(r => <tr key={r.name}><td>{r.local ? '● ' : '○ '}{r.name}{r.local ? <small> · esta</small> : null}</td><td>{fmtTokens(r.all.tokens)}</td><td>{pct(total ? r.all.tokens / total * 100 : 0)}</td><td>{fmtTokens(r.month.tokens)}</td><td>{money(r.month.cost)}</td><td>{r.local ? 'agora' : since(r.updated_at, now)}</td></tr>)}
    <tr><td><strong>Total</strong></td><td><strong>{fmtTokens(total)}</strong></td><td>100%</td><td><strong>{fmtTokens(sum('month'))}</strong></td><td><strong>{money(sumCost('month'))}</strong></td><td /></tr>
  </tbody></table></div><p className="u-hint">{rows.length < 2 ? `Nenhuma outra máquina publicou ainda em ${info.dir}. ` : ''}{info.error ? `Falha na pasta compartilhada: ${info.error}. ` : ''}Claude e Codex somados. As janelas de 5 horas e semanal já são da conta inteira; o ritmo por hora é só desta máquina.</p></Panel>;
}
function Panel({
  title,
  note,
  children,
  className = ''
}) {
  return <section className={`u-panel ${className}`}><div className="u-heading"><h2>{title}</h2>{note && <small>{note}</small>}</div>{children}</section>;
}
function Split({
  a = 0,
  b = 0
}) {
  const total = a + b;
  return <><div className="u-split" aria-label={`Claude ${pct(total ? a / total * 100 : 0)}, ChatGPT ${pct(total ? b / total * 100 : 0)}`}><i style={{
        width: `${total ? a / total * 100 : 0}%`,
        background: ORANGE
      }} /><i style={{
        width: `${total ? b / total * 100 : 0}%`,
        background: GREEN
      }} /></div><div className="u-legend"><span style={{
        color: ORANGE
      }}>● Claude {fmtTokens(a)} · {pct(total ? a / total * 100 : 0)}</span><span style={{
        color: GREEN
      }}>● ChatGPT {fmtTokens(b)} · {pct(total ? b / total * 100 : 0)}</span></div></>;
}
function Meter({
  value,
  color
}) {
  return <div className="u-meter"><i style={{
      width: `${Math.min(100, Math.max(0, value || 0))}%`,
      background: color
    }} /></div>;
}
function quotaTrend(rows, key, current, now) {
  if (!current || now - new Date(current.ts).getTime() > 15 * 60000) return null;
  const matching = rows.filter(r => r.key === key && r.reset === current.reset && r.value <= current.value && new Date(r.ts).getTime() <= new Date(current.ts).getTime());
  const first = matching[0];
  const hours = first && (new Date(current.ts) - new Date(first.ts)) / 3600000;
  if (!hours || hours < .5) return null;
  const rate = (current.value - first.value) / hours;
  return {
    rate,
    projected: current.value + rate * Math.max(0, (new Date(current.reset) - now) / 3600000)
  };
}
function Quota({
  label,
  value,
  reset,
  color,
  now,
  ts,
  trend
}) {
  const stale = !ts || now - new Date(ts) > 15 * 60000;
  return <div className="u-quota" data-stale={stale}><div className="u-heading"><strong>{label}</strong><span className="u-tag">{stale ? 'desatualizado' : 'conta · oficial'}</span></div><div className="u-quota-number">{pct(value)}<span className="u-used-label"> usado</span><small>{value == null ? 'indisponível' : `${pct(Math.max(0, 100 - value))} disponível`}</small></div><Meter value={value} color={value >= 90 ? '#ff7171' : color} /><div className="u-heading"><small>Reset em {resetText(reset, now)}</small><small>{ts ? new Date(ts).toLocaleTimeString('pt-BR') : '—'}</small></div><p className="u-hint">{trend ? `${pct(trend.rate)}/h · projeção de ${pct(trend.projected)} no reset` : 'Acumulando histórico para projetar o ritmo.'}</p></div>;
}
function Finance({
  label,
  color,
  summary,
  fixed,
  extra,
  fx,
  openai,
  now
}) {
  // Claude: total_cost e' USD; o backend ja entrega total_cost_brl convertido
  // num ponto so' (sem cotacao vem null, e a UI mostra "—" em vez de somar
  // dolar com real). Codex: equivalent_usd * fx, mesmo cambio do backend.
  const value = openai ? fx && summary ? summary.equivalent_usd * fx : null : summary?.total_cost_brl;
  const partial = openai && summary?.unpriced_tokens > 0;
  const totalPaid = fixed == null ? null : fixed + (extra ?? 0);
  const ratio = totalPaid > 0 && value != null ? value / totalPaid : null;
  const today = new Date(now);
  const daysInMonth = new Date(today.getFullYear(), today.getMonth() + 1, 0).getDate();
  const observed = openai && summary?.first_event ? Math.min(today.getDate(), (now - new Date(summary.first_event)) / 86400000) : today.getDate();
  const remaining = daysInMonth - today.getDate();
  const projected = observed >= 3 && value != null ? value + value / observed * remaining : null;
  return <div className="u-finance" style={{
    '--provider': color
  }}><div className="u-heading"><h3>{label}</h3><span className="u-tag">{partial ? 'estimativa parcial' : 'equivalência estimada'}</span></div><div className="u-money">{money(value)}</div><p>em tokens equivalentes de API neste mês</p><div className="u-finance-facts"><span>Assinatura <b>{money(fixed)}/mês</b></span><span>Extras pagos no mês <b>{money(extra)}</b></span><span>Custo fixo semanal <b>{money(fixed == null ? null : fixed * 12 / 52)}</b></span></div><Meter value={ratio == null ? 0 : ratio * 100} color={color} /><h4>{ratio == null ? 'Informe os custos para comparar' : ratio >= 1 ? `${ratio.toFixed(2)}× do custo pago em valor equivalente` : `${money(Math.max(0, totalPaid - value))} até o equilíbrio com API`}</h4><p>{ratio == null ? 'Informe a mensalidade nas configurações.' : ratio >= 1 ? `Diferença estimada: +${money(value - totalPaid)}${partial ? ' nos modelos com preço conhecido' : ''}.` : 'O retorno financeiro ainda está em acompanhamento.'}</p><p className="u-hint">{extra == null ? 'Comparação somente com a mensalidade; extras ainda não informados. ' : ''}{partial ? `${fmtTokens(summary.unpriced_tokens)} tokens sem preço publicado mapeado; não tratados como gratuitos. ` : ''}{openai ? 'Chat, voz e imagens fora do Codex não estão medidos.' : 'Base: cálculo de custos existente do Claude.'}</p><details><summary>Projeção de fechamento · cenários</summary>{projected == null ? <p>São necessários pelo menos três dias observados para uma projeção.</p> : <p>Conservador {money(value + (projected - value) * .7)} · atual {money(projected)} · intenso {money(value + (projected - value) * 1.3)}. Cenários de ±30% do ritmo médio observado, sem garantia de uso futuro. Volume no ritmo atual: {fmtTokens((summary?.total_tokens || 0) * (1 + remaining / observed))} tokens no mês.</p>}</details></div>;
}
const options = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: {
    legend: {
      labels: {
        color: '#c0c8d5',
        usePointStyle: true
      }
    }
  },
  scales: {
    x: {
      ticks: {
        color: '#8592a5',
        maxTicksLimit: 12
      },
      grid: {
        display: false
      }
    },
    y: {
      ticks: {
        color: '#8592a5',
        callback: fmtTokens
      },
      grid: {
        color: '#ffffff0a'
      }
    }
  }
};
const REPO = 'https://github.com/astronauta-martech/ai-usage';
const STATUS_COLOR = { SEGURO: '#34c759', ATENCAO: '#ff9f0a', RISCO: '#ff453a', INDETERMINADO: '#8e8e93' };
const STATUS_LABEL = { SEGURO: 'Seguro', ATENCAO: 'Atenção', RISCO: 'Risco', INDETERMINADO: 'Sem dados' };

// Estados que antes nao tinham tela nenhuma: conta desconectada, rate limit,
// erro do backend, painel offline, Codex ausente. Um banner so', acima das
// janelas, com a acao certa embutida.
function StatusBanner({ state, offline, onReconnect, reconnecting }) {
  const items = [];
  if (offline) items.push({ tone: 'warn', text: 'Sem conexão com o painel local: os números abaixo são o último estado conhecido e não estão sendo atualizados.' });
  if (state.auth_connected === false) {
    items.push({ tone: 'warn', text: 'Conta Claude desconectada: as janelas de cota não estão sendo lidas.', action: onReconnect, actionLabel: reconnecting ? 'Abrindo o login…' : 'Reconectar Claude' });
  } else if (state.rate_limited) {
    items.push({ tone: 'info', text: `A conta atingiu o limite de consultas da API da Anthropic. Mostrando o último snapshot; nova leitura em ${state.retry_in}s.` });
  } else if (state.last_error) {
    items.push({ tone: 'warn', text: state.last_error, detail: state.last_error_detail });
  }
  const cg = state.chatgpt;
  if (cg && cg.available === false) items.push({ tone: 'info', text: 'Codex não encontrado nesta máquina: a coluna verde fica vazia até haver sessões do Codex.' });
  else if (cg?.limits_error) items.push({ tone: 'info', text: `Codex: ${cg.limits_error}`, detail: cg.limits_error_detail });
  if (state.update?.available) {
    items.push({ tone: 'info', text: `AI Usage ${state.update.latest} disponível (você está na ${state.update.current}).`,
      href: state.update.url, actionLabel: 'Ver a versão nova ↗' });
  }
  if (!items.length) return null;
  return <section className="u-banner" aria-live="polite">{items.map((it, i) => <div key={i} className="u-banner-item" data-tone={it.tone}><span>{it.text}</span>{it.action && <button onClick={it.action} disabled={reconnecting}>{it.actionLabel}</button>}{it.href && <a href={it.href} target="_blank" rel="noreferrer">{it.actionLabel}</a>}{it.detail && <details><summary>detalhe técnico</summary><code>{it.detail}</code></details>}</div>)}</section>;
}

// O veredito ("posso trocar de modelo?") e' a informacao mais acionavel do
// sistema: sobe pro topo, com semaforo e com as horas pretendidas rotuladas.
function Verdict({ sw, intended, onHours, saving }) {
  if (!sw) return null;
  const color = STATUS_COLOR[sw.verdict] || STATUS_COLOR.INDETERMINADO;
  return <section className="u-verdict" style={{ '--status': color }} aria-label="Veredito de troca de modelo">
    <div className="u-heading"><h2>Posso trocar de modelo?</h2><small>{sw.dominant_model ? `Dominante agora: ${sw.dominant_model}` : 'Sem ritmo recente'}{sw.target_label ? ` · alvo: ${sw.target_label}` : ''}</small></div>
    <div className="u-verdict-body"><span className="u-verdict-pill">{STATUS_LABEL[sw.verdict] || sw.verdict}</span><p>{sw.message}</p></div>
    <div className="u-verdict-hours"><small>Pretendo trabalhar mais</small><div className="u-tabs">{[.5, 1, 2, 3, 4].map(h => <button key={h} aria-pressed={intended === h} onClick={() => onHours(h)} disabled={saving}>{h}h</button>)}</div><small>{sw.factor_estimated ? 'intensidade do alvo estimada' : `intensidade ${sw.factor}× do modelo dominante`}</small></div>
  </section>;
}

function SystemLinks({ update }) {
  return <section className="u-panel u-system"><div className="u-heading"><h2>Este sistema</h2><small>{update?.current ? `AI Usage ${update.current} · ` : ''}o painel é uma das superfícies; todas leem o mesmo backend local</small></div>
    {update?.available && <p className="u-update">Versão {update.latest} disponível. <a href={update.url} target="_blank" rel="noreferrer">Ver o que mudou e baixar ↗</a></p>}
    <div className="u-links">
    <a href="/widget" target="_blank" rel="noreferrer">Widget flutuante (janela leve) ↗</a>
    <a href={`${REPO}/releases`} target="_blank" rel="noreferrer">App de mesa · Mac, Windows e Linux ↗</a>
    <a href={`${REPO}/tree/main/native-mac`} target="_blank" rel="noreferrer">App nativo macOS · barra de menu ↗</a>
    <a href={`${REPO}/tree/main/streamdeck`} target="_blank" rel="noreferrer">Stream Deck · teclas e dials ↗</a>
    <a href={REPO} target="_blank" rel="noreferrer">Código e documentação ↗</a>
  </div></section>;
}

function Settings({
  state,
  onSave,
  onReconnect,
  reconnecting
}) {
  const [message, setMessage] = useState('');
  return <div className="u-settings"><form onSubmit={async e => {
      e.preventDefault();
      const data = new FormData(e.currentTarget);
      const values = {};
      for (const key of ['subscription_brl', 'chatgpt_subscription_brl', 'chatgpt_extra_brl']) {
        const text = data.get(key).trim();
        if (!text) continue;
        const value = Number(text.replace(',', '.'));
        if (!Number.isFinite(value) || value < 0) {
          setMessage('Use valores positivos ou zero.');
          return;
        }
        values[key] = value;
      }
      try {
        await onSave(values);
        setMessage('Valores salvos.');
      } catch {
        setMessage('Não foi possível salvar. Tente novamente.');
      }
    }}><div className="u-settings-grid">{[['subscription_brl', 'Claude · mensalidade'], ['chatgpt_subscription_brl', 'ChatGPT · mensalidade'], ['chatgpt_extra_brl', 'ChatGPT · extras pagos neste mês']].map(([key, label]) => <label key={key}>{label}<input name={key} inputMode="decimal" defaultValue={state.config[key] ?? ''} placeholder="R$ · informe o valor" /></label>)}<button>Salvar valores</button></div><p role="status">{message}</p></form><div className="u-links"><a href="https://chatgpt.com/codex/settings/usage" target="_blank" rel="noreferrer">Uso e créditos Codex ↗</a><a href="https://chatgpt.com/" target="_blank" rel="noreferrer">ChatGPT · configurações da assinatura ↗</a><a href="https://platform.openai.com/usage" target="_blank" rel="noreferrer">Custos da API ↗</a></div><p>Tokens: registros locais, incluindo cache. Raciocínio já integra a saída e não é somado novamente. Histórico importado do Claude permanece atribuído ao Claude.</p><p>Equivalência OpenAI: tarifas padrão de texto em USD, convertidas pelo câmbio exibido. Estima contexto longo por chamada (a regra por sessão do GPT-5.5 pode alterar o valor); exclui ferramentas, escrita de cache não registrada e adicional Fast. Não representa uma fatura nem economia garantida.</p><p>Preços verificados em 05/09/2026: {Object.keys(state.chatgpt?.pricing?.models || {}).map(m => <a key={m} href={`https://developers.openai.com/api/docs/models/${m}`} target="_blank" rel="noreferrer">{m} ↗ </a>)}. Modelos sem correspondência permanecem sem valor estimado.</p><p>Extras são compras informadas manualmente, válidas no mês atual. Saldo de créditos é uma unidade da conta, não reais. Aproveitamento de cota e retorno financeiro são indicadores distintos.</p></div>;
}

// --- casca nova: barra fixa, widgets e configuracoes -------------------------

const PERIOD_LABEL = Object.fromEntries(PERIODS);

function UsageWidget({ usage, fx, period, scope, setScope, multiMachine }) {
  if (!usage) return <p className="u-hint">Carregando o consumo…</p>;
  const c = usage.claude || {}, o = usage.codex || {};
  const cBRL = c.total_cost_brl, oBRL = o.equivalent_brl;
  const rows = [...(c.by_model || []).map(m => ({ model: m.model, tokens: m.tokens, cost: m.cost_brl, color: ORANGE, provider: 'Claude' })),
    ...(o.by_model || []).map(m => ({ model: m.model, tokens: m.tokens, cost: m.unpriced_tokens || !fx ? null : m.equivalent_usd * fx, color: GREEN, provider: 'ChatGPT' }))]
    .sort((a, b) => b.tokens - a.tokens);
  const max = Math.max(1, ...rows.map(r => r.tokens));
  return <>
    <div className="w-usage-top">
      <div><small>Tokens · {PERIOD_LABEL[period]}</small><div className="u-big">{fmtTokens(usage.total_tokens)}</div></div>
      <div><small>Se fosse API</small><div className="u-medium">{money(usage.total_brl)}</div></div>
      <div><small>Claude</small><div className="u-medium" style={{ color: ORANGE }}>{fmtTokens(c.total_tokens)}</div><small>{money(cBRL)}</small></div>
      <div><small>ChatGPT / Codex</small><div className="u-medium" style={{ color: GREEN }}>{fmtTokens(o.total_tokens)}</div><small>{money(oBRL)}{o.unpriced_tokens ? ' · parcial' : ''}</small></div>
    </div>
    <Split a={c.total_tokens || 0} b={o.total_tokens || 0} />
    {multiMachine && <div className="u-tabs w-scope">{[['local', 'Só esta máquina'], ['all', 'Todas as máquinas']].map(([id, l]) => <button key={id} aria-pressed={scope === id} onClick={() => setScope(id)}>{l}</button>)}</div>}
    <div className="u-models">{rows.slice(0, 8).map(r => <div key={`${r.provider}-${r.model}`}><div className="u-heading"><span style={{ color: r.color }}>● {r.model}</span><small>{fmtTokens(r.tokens)} · {money(r.cost)}</small></div><Meter value={r.tokens / max * 100} color={r.color} /></div>)}</div>
    {!rows.length && <p className="u-hint">Nenhum uso registrado neste período.</p>}
    <p className="u-hint">{scope === 'all' ? 'Somando todas as máquinas da pasta sincronizada.' : `Só o que rodou neste computador (${usage.machine}).`} Equivalente = o que custaria pagando por token na API, pelas tarifas padrão.</p>
  </>;
}

function CostWidget({ state, cg, fx, now }) {
  const monthA = state.history?.mes?.total_cost_brl,
    monthB = fx && cg.history?.mes ? cg.history.mes.equivalent_usd * fx : null;
  const fixed = state.config.subscription_brl != null && state.config.chatgpt_subscription_brl != null ? state.config.subscription_brl + state.config.chatgpt_subscription_brl : null;
  const extrasA = state.extra_usage?.used_brl;
  const openaiExtra = state.config.chatgpt_extra_brl;
  const paid = fixed != null ? fixed + (extrasA ?? 0) + (openaiExtra ?? 0) : null;
  return <>
    <div className="w-usage-top">
      <div><small>Investimento mensal</small><div className="u-medium">{money(fixed)}</div><small>Claude {money(state.config.subscription_brl)} + ChatGPT {money(state.config.chatgpt_subscription_brl)}</small></div>
      <div><small>Equivalente neste mês</small><div className="u-medium">{money(monthA != null && monthB != null ? monthA + monthB : null)}</div><small>{paid && monthB != null ? `${((monthA + monthB) / paid).toFixed(2)}× do custo informado` : 'Informe os valores nas configurações'}</small></div>
    </div>
    <div className="u-finance-grid"><Finance label="Claude" color={ORANGE} summary={state.history?.mes} fixed={state.config.subscription_brl} extra={extrasA} fx={fx} now={now} /><Finance label="ChatGPT" color={GREEN} summary={cg.history?.mes} fixed={state.config.chatgpt_subscription_brl} extra={openaiExtra} fx={fx} now={now} openai /></div>
  </>;
}

function BurnWidget({ state, cg }) {
  const burn = [...Object.entries(state.burn_tokph || {}).map(([model, value]) => ({ model, value, color: ORANGE, provider: 'Claude' })),
    ...Object.entries(cg.burn_by_model || {}).map(([model, value]) => ({ model, value, color: GREEN, provider: 'ChatGPT' }))].sort((a, b) => b.value - a.value);
  const burnA = burn.filter(r => r.provider === 'Claude').reduce((a, r) => a + r.value, 0), burnB = cg.burn_tokph || 0;
  return <><div className="u-medium">{fmtTokens(burnA + burnB)}<small> / hora</small></div><Split a={burnA} b={burnB} /><div className="u-models">{burn.map(r => <div key={`${r.provider}-${r.model}`}><div className="u-heading"><span><i style={{ color: r.color }}>●</i> {r.model} <small>{r.provider}</small></span><strong>{fmtTokens(r.value)}/h</strong></div><Meter value={r.value / Math.max(1, ...burn.map(b => b.value)) * 100} color={r.color} /></div>)}</div>{!burn.length && <p className="u-hint">Sem uso nas últimas 2 horas.</p>}</>;
}

function ChartsWidget({ history, cg, fx, now }) {
  const [provider, setProvider] = useState('both');
  const daily = history?.daily || [], od = cg.daily || [];
  const dates = [...new Set([...daily, ...od].map(d => d.day))].filter(d => new Date(d + 'T12:00:00').getTime() >= now - 30 * 86400000).sort().slice(-30);
  const showA = provider !== 'chatgpt', showB = provider !== 'claude';
  return <>
    <div className="u-tabs">{[['both', 'Ambas'], ['claude', 'Claude'], ['chatgpt', 'ChatGPT']].map(([id, label]) => <button key={id} aria-pressed={provider === id} onClick={() => setProvider(id)}>{label}</button>)}</div>
    <div className="u-grid u-two"><div><h4>Tokens por dia · 30 dias</h4><div className="u-chart"><Bar options={options} data={{ labels: dates.map(d => d.slice(5)), datasets: [...(showA ? [{ label: 'Claude', data: dates.map(d => daily.find(r => r.day === d)?.tokens || 0), backgroundColor: ORANGE, borderRadius: 3 }] : []), ...(showB ? [{ label: 'ChatGPT', data: dates.map(d => od.find(r => r.day === d)?.tokens || 0), backgroundColor: GREEN, borderRadius: 3 }] : [])] }} /></div></div>
      <div><h4>Ritmo por dia e hora</h4><HeatmapChart fx={fx} heatmap={showA ? history?.heatmap : []} chatgpt={showB ? cg.heatmap : []} /></div></div>
  </>;
}

function QuotaHistoryWidget({ state, history, cg }) {
  const limits = (cg.limits || []).flatMap(l => ['primary', 'secondary'].filter(k => l[k]).map(k => ({ key: `${l.id}-${k}`, label: `${l.name || l.id} · ${l[k].window_minutes === 10080 ? '7 dias' : `${l[k].window_minutes / 60} horas`}` })));
  const rows = (cg.limit_history || []).flatMap(s => s.limits.flatMap(l => ['primary', 'secondary'].filter(k => l[k]).map(k => ({ key: `${l.id}-${k}`, ts: s.ts, value: l[k].used_percent }))));
  return <div className="u-chart"><Line options={{ ...options, scales: { ...options.scales, x: { ...options.scales.x, type: 'linear', ticks: { color: '#8592a5', maxTicksLimit: 8, callback: v => new Date(v).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) } }, y: { ...options.scales.y, ticks: { color: '#8592a5', callback: v => `${v}%` }, suggestedMin: 0, suggestedMax: 100 } } }}
    data={{ datasets: [...(state.quotas || []).filter(q => q.provider === 'claude').map((q, index) => ({ label: `Claude · ${q.label}`, data: (history?.snapshots || []).filter(r => r.window === q.source_key).map(r => ({ x: new Date(r.ts).getTime(), y: r.utilization })), borderColor: ORANGE, borderDash: index ? [5, 4] : [], pointRadius: 0 })),
      ...limits.map((l, index) => ({ label: l.label, data: rows.filter(r => r.key === l.key).map(r => ({ x: new Date(r.ts).getTime(), y: r.value })), borderColor: GREEN, borderDash: index ? [5, 4] : [], pointRadius: 2 }))] }} /></div>;
}

function WidgetFrame({ w, layout, editing, onChange, children, extra }) {
  const collapsed = layout.collapsed.includes(w.id);
  const wide = layout.wide.includes(w.id);
  const toggle = (key, id) => {
    const set = new Set(layout[key]);
    set.has(id) ? set.delete(id) : set.add(id);
    onChange({ ...layout, [key]: [...set], preset: null });
  };
  const move = d => {
    const vis = layout.order.filter(id => !layout.hidden.includes(id));
    const i = vis.indexOf(w.id), j = i + d;
    if (j < 0 || j >= vis.length) return;
    const order = [...layout.order];
    const a = order.indexOf(vis[i]), b = order.indexOf(vis[j]);
    [order[a], order[b]] = [order[b], order[a]];
    onChange({ ...layout, order, preset: null });
  };
  return <section className={`w-frame u-panel${wide ? ' w-wide' : ''}${collapsed ? ' w-collapsed' : ''}`} data-widget={w.id}>
    <div className="w-head">
      <h2>{w.title}</h2>{extra}
      <div className="w-tools">
        {editing && <>
          <button title="Subir" onClick={() => move(-1)}>↑</button>
          <button title="Descer" onClick={() => move(1)}>↓</button>
          <button title={wide ? 'Meia largura' : 'Largura inteira'} onClick={() => toggle('wide', w.id)}>{wide ? '½' : '↔'}</button>
          <button title="Esconder" onClick={() => toggle('hidden', w.id)}>✕</button>
        </>}
        <button title={collapsed ? 'Expandir' : 'Minimizar'} aria-expanded={!collapsed} onClick={() => toggle('collapsed', w.id)}>{collapsed ? '+' : '–'}</button>
      </div>
    </div>
    {!collapsed && <div className="w-body">{children}</div>}
  </section>;
}

function LayoutEditor({ layout, onChange }) {
  return <div className="d-layout">
    <h3>Predefinições</h3>
    <div className="d-presets">{PRESETS.map(p => <button key={p.id} aria-pressed={layout.preset === p.id} onClick={() => onChange({ ...p.layout, preset: p.id })}><strong>{p.name}</strong><small>{p.desc}</small></button>)}</div>
    <h3>Widgets</h3>
    <p className="u-hint">Marque o que aparece. Para mudar a ordem e a largura, use "Editar tela" na barra do topo.</p>
    {layout.order.map(id => { const w = WIDGETS.find(x => x.id === id); return <label key={id} className="d-check"><input type="checkbox" checked={!layout.hidden.includes(id)} onChange={() => { const s = new Set(layout.hidden); s.has(id) ? s.delete(id) : s.add(id); onChange({ ...layout, hidden: [...s], preset: null }); }} /><span><strong>{w.title}</strong><small>{w.hint}</small></span></label>; })}
  </div>;
}

function Drawer({ open, onClose, tab, setTab, accounts, reloadAccounts, layout, setLayout, state, save, update }) {
  if (!open) return null;
  return <div className="d-overlay" onClick={onClose}><aside className="d-drawer" onClick={e => e.stopPropagation()} aria-label="Configurações">
    <header><h2>Configurações</h2><button onClick={onClose} aria-label="Fechar">✕</button></header>
    <div className="u-tabs d-tabs">{[['contas', 'Contas'], ['tela', 'Tela'], ['valores', 'Valores'], ['sobre', 'Sobre']].map(([id, l]) => <button key={id} aria-pressed={tab === id} onClick={() => setTab(id)}>{l}</button>)}</div>
    <div className="d-body">
      {tab === 'contas' && <AccountsManager data={accounts} reload={reloadAccounts} />}
      {tab === 'tela' && <LayoutEditor layout={layout} onChange={setLayout} />}
      {tab === 'valores' && <Settings state={state} onSave={save} />}
      {tab === 'sobre' && <SystemLinks update={update} />}
    </div>
  </aside></div>;
}

export default function Dashboard() {
  const [state, setState] = useState(null), [history, setHistory] = useState(null);
  const [usage, setUsage] = useState(null), [accounts, setAccounts] = useState(null);
  const [now, setNow] = useState(() => Date.now()), [error, setError] = useState('');
  const [period, setPeriod] = useState(() => { try { return localStorage.getItem('aiu.period') || 'dia'; } catch { return 'dia'; } });
  const [scope, setScope] = useState('local');
  const [busy, setBusy] = useState(false), [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState(null);
  const [drawer, setDrawer] = useState(false), [tab, setTab] = useState('contas');
  const [editing, setEditing] = useState(false);
  const [localLayout, setLocalLayout] = useState(null);

  const loadAccounts = useCallback(async () => { try { setAccounts(await api.accounts()); } catch { /* preserva */ } }, []);
  useEffect(() => {
    let alive = true;
    const load = async () => { try { const s = await api.state(); if (alive) { setState(s); setError(''); } } catch { if (alive) setError('Conexão interrompida · exibindo último estado'); } };
    const hist = async () => { try { const h = await api.history(); if (alive) setHistory(h); } catch { /* preserva */ } };
    void load(); void hist(); void loadAccounts();
    const a = setInterval(load, 10000), b = setInterval(hist, 60000), c = setInterval(() => setNow(Date.now()), 1000);
    return () => { alive = false; clearInterval(a); clearInterval(b); clearInterval(c); };
  }, [loadAccounts]);
  // contas: rapido enquanto ha login pendente ou o painel de contas esta aberto
  const waiting = accounts?.accounts?.some(a => a.login?.state === 'waiting');
  const onboarding = accounts && !accounts.onboarded;
  useEffect(() => {
    const t = setInterval(loadAccounts, waiting || drawer || onboarding ? 2000 : 15000);
    return () => clearInterval(t);
  }, [loadAccounts, waiting, drawer, onboarding]);
  useEffect(() => {
    let alive = true;
    const load = () => api.usage(period, scope).then(u => alive && setUsage(u)).catch(() => {});
    load();
    const t = setInterval(load, 30000);
    try { localStorage.setItem('aiu.period', period); } catch { /* sem storage */ }
    return () => { alive = false; clearInterval(t); };
  }, [period, scope]);

  const layout = normalizeLayout(localLayout ?? state?.config?.layout);
  const hwVisible = !layout.hidden.includes('hardware');
  const hw = useHardware(hwVisible && !!state);

  async function setLayout(next) {
    setLocalLayout(next);
    try { await api.setConfig({ layout: next }); setState(await api.state()); setLocalLayout(null); } catch { /* fica local */ }
  }
  async function refresh() {
    setBusy(true);
    try {
      const r = await api.refresh();
      setState(await api.state()); setHistory(await api.history()); void loadAccounts();
      if (r?.ok === false) setNotice({ text: r.reason === 'rate_limited' ? 'A conta está no limite de consultas da API' : 'Consulta manual muito recente', until: Date.now() + (r.retry_in || 0) * 1000 });
      else setNotice(null);
    } catch { setNotice({ text: 'Falha ao atualizar; último estado preservado.', until: Date.now() + 15000 }); } finally { setBusy(false); }
  }
  async function save(values) { setSaving(true); try { await api.setConfig(values); setState(await api.state()); } finally { setSaving(false); } }
  async function openApp(acc) { try { await api.openApp(acc.id); } catch (e) { setNotice({ text: e.message, until: Date.now() + 8000 }); } }

  if (!state) return <main className="u-root"><div className="u-wrap"><h1>AI Usage</h1><p role="status">{error ? 'Sem conexão com o painel local. Confira se o servidor está rodando.' : 'Carregando…'}</p></div></main>;
  if (onboarding) return <div className="u-root"><Onboarding data={accounts} reload={loadAccounts} onDone={async () => { await api.onboarded(true); await loadAccounts(); }} /></div>;

  const cg = state.chatgpt || {}, fx = state.config.usd_brl;
  const noticeText = notice && notice.until > now ? `${notice.text}${notice.until - now > 2000 ? ` · ${Math.ceil((notice.until - now) / 1000)}s` : ''}` : '';
  const accs = accounts?.accounts || state.accounts || [];
  const machineName = usage?.machine;

  const content = {
    hardware: () => <Hardware hw={hw} />,
    accounts: () => <div className="a-grid">{accs.map(a => <AccountCard key={a.id} acc={a} now={now} onOpen={openApp} />)}</div>,
    usage: () => <UsageWidget usage={usage} fx={fx} period={period} scope={scope} setScope={setScope} multiMachine={!!state.machines} />,
    verdict: () => state.switch ? <Verdict sw={state.switch} intended={state.config.intended_hours} onHours={h => save({ intended_hours: h })} saving={saving} /> : <p className="u-hint">Sem leitura da conta principal ainda.</p>,
    burn: () => <BurnWidget state={state} cg={cg} />,
    cost: () => <CostWidget state={state} cg={cg} fx={fx} now={now} />,
    machines: () => state.machines ? <Machines state={state} cg={cg} fx={fx} now={now} /> : <p className="u-hint">Ligue a pasta de sincronização (sync_dir) para somar outras máquinas.</p>,
    charts: () => <ChartsWidget history={history} cg={cg} fx={fx} now={now} />,
    'quota-history': () => <QuotaHistoryWidget state={state} history={history} cg={cg} />,
    system: () => <SystemLinks update={state.update} />,
  };
  const extras = {
    accounts: <button className="w-link" onClick={() => { setTab('contas'); setDrawer(true); }}>Gerenciar contas</button>,
  };
  const visible = layout.order.filter(id => !layout.hidden.includes(id));

  return <div className="u-root">
    <header className="t-bar">
      <div className="t-brand"><img src="/brand.svg" alt="" width="22" height="22" /><strong>AI Usage</strong></div>
      <nav className="t-periods" aria-label="Período">{PERIODS.map(([id, l]) => <button key={id} aria-pressed={period === id} onClick={() => setPeriod(id)}>{l}</button>)}</nav>
      <div className="t-right">
        <span className="t-machine" tabIndex={0}>ⓘ Consumo desta máquina
          <span className="t-tip" role="tooltip">Tokens e custo equivalente são medidos nos registros deste computador{machineName ? ` (${machineName})` : ''}. Os limites de 5 horas, 7 dias e por modelo são da conta e já somam todas as máquinas.</span></span>
        <span className="t-status">{noticeText || error || `Atualizado ${new Date(state.generated_at).toLocaleTimeString('pt-BR')}`}</span>
        <button onClick={() => setEditing(e => !e)} aria-pressed={editing}>{editing ? 'Concluir' : 'Editar tela'}</button>
        <button onClick={refresh} disabled={busy} title="Atualizar agora">{busy ? '…' : '↻'}</button>
        <button className="t-gear" onClick={() => setDrawer(true)} aria-label="Configurações" title="Configurações">⚙</button>
      </div>
    </header>
    <main className="u-wrap t-main">
      <StatusBanner state={state} offline={!!error} onReconnect={() => { setTab('contas'); setDrawer(true); }} reconnecting={false} />
      {editing && <div className="t-editing">Modo de edição: use ↑ ↓ para mover, ↔ para largura e ✕ para esconder. Para voltar algo escondido, abra Configurações › Tela.</div>}
      <div className="w-grid">
        {visible.map(id => { const w = WIDGETS.find(x => x.id === id); return <WidgetFrame key={id} w={w} layout={layout} editing={editing} onChange={setLayout} extra={extras[id]}>{content[id]()}</WidgetFrame>; })}
      </div>
      <footer className="u-footer">Dados locais · câmbio {money(fx)}/US$ · limites oficiais das contas, valores de API estimados</footer>
    </main>
    <Drawer open={drawer} onClose={() => setDrawer(false)} tab={tab} setTab={setTab} accounts={accounts} reloadAccounts={loadAccounts} layout={layout} setLayout={setLayout} state={state} save={save} update={state.update} />
  </div>;
}
