import { useEffect, useState } from 'react';
import { Chart, registerables } from 'chart.js';
import { Bar, Line } from 'react-chartjs-2';
import { api } from './api';
import { fmtTokens, fmtMoney, fmtInt } from './format';
import HeatmapChart from './components/HeatmapChart';
import './dashboard.css';
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
const REPO = 'https://github.com/eueduardocampos/claude-usage';
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
  return <details className="u-settings"><summary>Assinaturas, conexões e metodologia</summary><form onSubmit={async e => {
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
    }}><div className="u-settings-grid">{[['subscription_brl', 'Claude · mensalidade'], ['chatgpt_subscription_brl', 'ChatGPT · mensalidade'], ['chatgpt_extra_brl', 'ChatGPT · extras pagos neste mês']].map(([key, label]) => <label key={key}>{label}<input name={key} inputMode="decimal" defaultValue={state.config[key] ?? ''} placeholder="R$ · informe o valor" /></label>)}<button>Salvar valores</button></div><p role="status">{message}</p></form><div className="u-links"><a href="https://chatgpt.com/codex/settings/usage" target="_blank" rel="noreferrer">Uso e créditos Codex ↗</a><a href="https://chatgpt.com/" target="_blank" rel="noreferrer">ChatGPT · configurações da assinatura ↗</a><a href="https://platform.openai.com/usage" target="_blank" rel="noreferrer">Custos da API ↗</a><button type="button" onClick={onReconnect} disabled={reconnecting}>{reconnecting ? 'Abrindo o login…' : 'Reconectar Claude'}</button></div><p>Tokens: registros locais, incluindo cache. Raciocínio já integra a saída e não é somado novamente. Histórico importado do Claude permanece atribuído ao Claude.</p><p>Equivalência OpenAI: tarifas padrão de texto em USD, convertidas pelo câmbio exibido. Estima contexto longo por chamada (a regra por sessão do GPT-5.5 pode alterar o valor); exclui ferramentas, escrita de cache não registrada e adicional Fast. Não representa uma fatura nem economia garantida.</p><p>Preços verificados em 05/09/2026: {Object.keys(state.chatgpt?.pricing?.models || {}).map(m => <a key={m} href={`https://developers.openai.com/api/docs/models/${m}`} target="_blank" rel="noreferrer">{m} ↗ </a>)}. Modelos sem correspondência permanecem sem valor estimado.</p><p>Extras são compras informadas manualmente, válidas no mês atual. Saldo de créditos é uma unidade da conta, não reais. Aproveitamento de cota e retorno financeiro são indicadores distintos.</p></details>;
}
export default function Dashboard() {
  const [state, setState] = useState(null),
    [history, setHistory] = useState(null);
  const [now, setNow] = useState(() => Date.now()),
    [error, setError] = useState('');
  const [scope, setScope] = useState('mes'),
    [provider, setProvider] = useState('both'),
    [busy, setBusy] = useState(false),
    [saving, setSaving] = useState(false),
    [reconnecting, setReconnecting] = useState(false),
    // aviso com prazo (motivo do "Atualizar" negado, login aberto...): vive
    // ate `until`, e o polling de 10s NAO o apaga — so' `error` (conexao) e'
    // zerado a cada leitura boa
    [notice, setNotice] = useState(null);
  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const s = await api.state();
        if (alive) {
          setState(s);
          setError('');
        }
      } catch {
        if (alive) setError('Conexão interrompida · exibindo último estado');
      }
    }
    async function hist() {
      try {
        const h = await api.history();
        if (alive) setHistory(h);
      } catch {/* preserve */}
    }
    void load();
    void hist();
    const a = setInterval(load, 10000),
      b = setInterval(hist, 60000),
      c = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      alive = false;
      clearInterval(a);
      clearInterval(b);
      clearInterval(c);
    };
  }, []);
  async function refresh() {
    setBusy(true);
    try {
      const r = await api.refresh();
      setState(await api.state());
      setHistory(await api.history());
      if (r?.ok === false) {
        const why = r.reason === 'rate_limited' ? 'A conta está no limite de consultas da API' : 'Consulta manual muito recente';
        setNotice({ text: why, until: Date.now() + (r.retry_in || 0) * 1000 });
      } else setNotice(null);
    } catch {
      setNotice({ text: 'Falha ao atualizar; último estado preservado.', until: Date.now() + 15000 });
    } finally {
      setBusy(false);
    }
  }
  async function reconnect() {
    setReconnecting(true);
    try {
      await api.authStart();
      setNotice({ text: 'Login aberto no navegador: conclua por lá, o painel atualiza sozinho', until: Date.now() + 90000 });
    } catch {
      setNotice({ text: 'Não foi possível iniciar a reconexão', until: Date.now() + 15000 });
    } finally {
      setReconnecting(false);
    }
  }
  async function save(values) {
    setSaving(true);
    try {
      await api.setConfig(values);
      setState(await api.state());
    } finally {
      setSaving(false);
    }
  }
  const noticeText = notice && notice.until > now
    ? `${notice.text}${notice.until - now > 2000 ? ` · ${Math.ceil((notice.until - now) / 1000)}s` : ''}` : '';
  if (!state) return <main className="u-root"><div className="u-wrap"><h1>AI Usage</h1><p role="status">{error ? 'Sem conexão com o painel local (localhost:8090). Confira se o backend está rodando.' : 'Carregando um retrato completo das duas plataformas…'}</p></div></main>;
  const cg = state.chatgpt || {},
    fx = state.config.usd_brl;
  const ca = state.history?.[scope] || {},
    oa = cg.history?.[scope] || {};
  const allA = state.history?.geral?.total_tokens || 0,
    allB = cg.history?.geral?.total_tokens || 0;
  const burn = [...Object.entries(state.burn_tokph || {}).map(([model, value]) => ({
    model,
    value,
    color: ORANGE,
    provider: 'Claude'
  })), ...Object.entries(cg.burn_by_model || {}).map(([model, value]) => ({
    model,
    value,
    color: GREEN,
    provider: 'ChatGPT'
  }))].sort((a, b) => b.value - a.value);
  const burnA = burn.filter(r => r.provider === 'Claude').reduce((a, r) => a + r.value, 0),
    burnB = cg.burn_tokph || 0;
  const limits = (cg.limits || []).flatMap(l => ['primary', 'secondary'].filter(k => l[k]).map(k => ({
    key: `${l.id}-${k}`,
    label: `${l.name || l.id} · ${l[k].window_minutes === 10080 ? '7 dias' : `${l[k].window_minutes / 60} horas`}`,
    value: l[k].used_percent,
    reset: l[k].resets_at,
    ts: l.snapshot_ts
  })));
  const rows = (cg.limit_history || []).flatMap(s => s.limits.flatMap(l => ['primary', 'secondary'].filter(k => l[k]).map(k => ({
    key: `${l.id}-${k}`,
    ts: s.ts,
    value: l[k].used_percent,
    reset: l[k].resets_at
  }))));
  const weekly = limits.find(l => l.key.startsWith('codex-') && l.label.includes('7 dias'));
  const trend = weekly && quotaTrend(rows, weekly.key, weekly, now);
  const credit = cg.limits?.find(l => l.credits)?.credits;
  const openaiExtra = state.config.chatgpt_extra_brl;
  const modelRows = [...(ca.by_model || []).map(m => ({
    model: m.model,
    tokens: m.tokens,
    color: ORANGE,
    cost: m.cost_brl,
    provider: 'Claude'
  })), ...(oa.by_model || []).map(m => ({
    ...m,
    color: GREEN,
    cost: m.unpriced_tokens || !fx ? null : m.equivalent_usd * fx,
    provider: 'ChatGPT'
  }))].sort((a, b) => b.tokens - a.tokens);
  const daily = history?.daily || [],
    od = cg.daily || [];
  const dates = [...new Set([...daily, ...od].map(d => d.day))].filter(d => new Date(d + 'T12:00:00').getTime() >= now - 30 * 86400000).sort().slice(-30);
  const showA = provider !== 'chatgpt',
    showB = provider !== 'claude';
  const monthA = state.history?.mes?.total_cost_brl,
    monthB = fx && cg.history?.mes ? cg.history.mes.equivalent_usd * fx : null;
  const fixed = state.config.subscription_brl != null && state.config.chatgpt_subscription_brl != null ? state.config.subscription_brl + state.config.chatgpt_subscription_brl : null;
  const extrasA = state.extra_usage?.used_brl; // creditos extras sao em USD; used_brl ja vem convertido
  const paid = fixed != null ? fixed + (extrasA ?? 0) + (openaiExtra ?? 0) : null;
  // quotas[] e' o contrato unico do backend: Claude e Codex na mesma forma,
  // com rotulo, projecao e semaforo prontos (inclui a janela semanal por
  // modelo, ex. "Fable · 7 dias", que nao existe em state.windows).
  const allQuotas = (state.quotas || []).map(q => ({
    key: q.key,
    label: q.provider === 'claude' ? `Claude · ${q.label}` : q.label,
    value: q.utilization, reset: q.resets_at, ts: q.snapshot_ts,
    color: q.provider === 'claude' ? ORANGE : GREEN, status: q.status,
    trend: q.projected != null && q.rate != null && q.snapshot_ts && now - new Date(q.snapshot_ts) < 900000
      ? { projected: q.projected, rate: q.rate } : null,
  }));
  const activeQuotas = allQuotas.filter(l => l.value !== 0 || !l.ts || now - new Date(l.ts) > 900000);
  const idleQuotas = allQuotas.filter(l => l.value === 0 && l.ts && now - new Date(l.ts) <= 900000);
  return <div className="u-root"><main className="u-wrap">
    <header className="u-top"><div><span className="u-eyebrow"><img src="/brand.svg" alt="AI Usage" width="24" height="24" style={{ display: "inline-block", verticalAlign: "middle", marginRight: 8 }} />AI USAGE / VISÃO UNIFICADA</span><h1>Limites e consumo<span>.</span></h1></div><div className="u-actions"><span className="u-tag">{noticeText || error || `Atualizado ${new Date(state.generated_at).toLocaleTimeString('pt-BR')}`}</span><button onClick={refresh} disabled={busy}>{busy ? 'Atualizando…' : '↻ Atualizar'}</button></div></header>
    <StatusBanner state={state} offline={!!error} onReconnect={reconnect} reconnecting={reconnecting} />
    <section className="u-current-windows" aria-label="Consumo atual das janelas">
      <div className="u-heading"><h2>Janelas em uso</h2><small>Claude em laranja · Codex em verde</small></div>
      <div className="u-quota-strip">{activeQuotas.map(l => <div key={l.key} style={{ '--provider': l.color }}><Quota {...l} now={now} /></div>)}</div>
      {!activeQuotas.length && <p>Nenhuma janela com consumo no último snapshot.</p>}
      <div className="u-window-details">
        {idleQuotas.length > 0 && <details><summary>{idleQuotas.length} janelas sem consumo · {idleQuotas.map(l => l.label.replace('GPT-5.3-Codex-', '')).join(' / ')}</summary><div className="u-quota-strip">{idleQuotas.map(l => <div key={l.key} style={{ '--provider': l.color }}><Quota {...l} now={now} /></div>)}</div></details>}
        <details><summary>Detalhes da conta Codex</summary><p>Codex: {cg.limits_error ? 'consulta com falha; exibindo último snapshot' : 'janelas retornadas pela conta'}. Saldo extra: {credit == null ? 'indisponível' : credit.unlimited ? 'ilimitado' : `${credit.balance ?? 'não informado'} créditos`}. Chat comum, imagens e voz não incluídos.</p></details>
      </div>
    </section>
    <Verdict sw={state.switch} intended={state.config.intended_hours} onHours={h => save({ intended_hours: h })} saving={saving} />
    <div className="u-grid u-overview">
      <Panel title="Tokens processados" note="Registros locais · histórico completo" className="u-total"><div className="u-big">{fmtInt(allA + allB)}</div><Split a={allA} b={allB} /><p className="u-hint">Inclui entrada, cache e saída. As plataformas têm períodos de histórico diferentes.</p></Panel>
      <Panel title="Investimento mensal" note="Assinaturas informadas"><div className="u-medium">{money(fixed)}</div><p>Claude {money(state.config.subscription_brl)} + ChatGPT {money(state.config.chatgpt_subscription_brl)}</p><p className="u-hint">Extras são contabilizados separadamente.</p></Panel>
      <Panel title="Valor equivalente neste mês" note="Estimativa de tokens em API"><div className="u-medium">{money(monthA != null && monthB != null ? monthA + monthB : null)}</div><p>{paid && monthB != null ? `${((monthA + monthB) / paid).toFixed(2)}× do custo total informado` : 'Informe os extras para comparar o custo total.'}</p><p className="u-hint">{cg.history?.mes?.unpriced_tokens ? 'Parcial: há modelos sem preço mapeado e/ou extras não informados.' : 'Tarifas padrão; não representa cobrança.'}</p></Panel>
    </div>
    <div className="u-advice"><span>LEITURA DO MOMENTO</span><p>{weekly ? `Codex: ${pct(weekly.value)} da cota semanal. ` : 'Cota Codex indisponível. '}{trend ? trend.projected >= 100 ? 'O ritmo observado pode esgotar a cota antes do reset; distribua o trabalho entre as plataformas.' : `Projeção de ${pct(trend.projected)} no reset; há margem no ritmo observado.` : 'A projeção ficará disponível após pelo menos 30 minutos de snapshots da mesma janela.'}</p></div>
    <div className="u-grid u-two"><Panel title="Ritmo por modelo" note="Tokens/h · média das últimas 2 horas"><div className="u-medium">{fmtTokens(burnA + burnB)}<small> / hora</small></div><Split a={burnA} b={burnB} /><div className="u-models">{burn.map(r => <div key={`${r.provider}-${r.model}`}><div className="u-heading"><span><i style={{
                    color: r.color
                  }}>●</i> {r.model} <small>{r.provider}</small></span><strong>{fmtTokens(r.value)}/h</strong></div><Meter value={r.value / Math.max(1, ...burn.map(b => b.value)) * 100} color={r.color} /></div>)}</div></Panel>
      <Panel title="Retorno das assinaturas" note="Mês atual · equivalência de API"><div className="u-finance-grid"><Finance label="Claude" color={ORANGE} summary={state.history?.mes} fixed={state.config.subscription_brl} extra={extrasA} fx={fx} now={now} /><Finance label="ChatGPT" color={GREEN} summary={cg.history?.mes} fixed={state.config.chatgpt_subscription_brl} extra={openaiExtra} fx={fx} now={now} openai /></div></Panel></div>
    <Panel title="Comparativo de consumo" note="Semana começa na segunda; cotas usam suas próprias janelas"><div className="u-tabs">{scopes.map(([id, label]) => <button key={id} aria-pressed={scope === id} onClick={() => setScope(id)}>{label}</button>)}</div><div className="u-table-wrap"><table><thead><tr><th>Plataforma</th><th>Tokens</th><th>Participação</th><th>Interações</th><th>Equivalente de API</th></tr></thead><tbody>{[['Claude', ca, ORANGE], ['ChatGPT / Codex', oa, GREEN]].map(([label, data, color], i) => <tr key={label}><td style={{
                  color
                }}>● {label}</td><td>{fmtTokens(data.total_tokens)}</td><td>{pct(ca.total_tokens + oa.total_tokens ? data.total_tokens / (ca.total_tokens + oa.total_tokens) * 100 : 0)}</td><td>{fmtInt(data.calls ?? data.total_turns)}</td><td>{money(i ? fx ? data.equivalent_usd * fx : null : data.total_cost_brl)}{i && data.unpriced_tokens > 0 ? ' · parcial' : ''}</td></tr>)}</tbody></table></div><div className="u-models">{modelRows.map(r => <div key={`${r.provider}-${r.model}`}><div className="u-heading"><span style={{
                color: r.color
              }}>● {r.model}</span><small>{fmtTokens(r.tokens)} · {money(r.cost)} {r.cost == null ? 'sem preço mapeado' : 'equivalente'}</small></div><Meter value={r.tokens / Math.max(1, ...modelRows.map(m => m.tokens)) * 100} color={r.color} /></div>)}</div><details><summary>Composição dos tokens do Codex</summary><p>Entrada sem cache: {fmtTokens((oa.input_tokens || 0) - (oa.cached_input_tokens || 0))} · cache lido: {fmtTokens(oa.cached_input_tokens)} · saída: {fmtTokens(oa.output_tokens)} · raciocínio dentro da saída: {fmtTokens(oa.reasoning_output_tokens)}.</p></details></Panel>
    <div className="u-heading u-section-title"><h2>Quando você usa cada plataforma</h2><div className="u-tabs">{[['both', 'Ambas'], ['claude', 'Claude'], ['chatgpt', 'ChatGPT']].map(([id, label]) => <button key={id} aria-pressed={provider === id} onClick={() => setProvider(id)}>{label}</button>)}</div></div>
    <div className="u-grid u-two"><Panel title="Tokens por dia" note="Últimos 30 dias"><div className="u-chart"><Bar options={options} data={{
              labels: dates.map(d => d.slice(5)),
              datasets: [...(showA ? [{
                label: 'Claude',
                data: dates.map(d => daily.find(r => r.day === d)?.tokens || 0),
                backgroundColor: ORANGE,
                borderRadius: 3
              }] : []), ...(showB ? [{
                label: 'ChatGPT',
                data: dates.map(d => od.find(r => r.day === d)?.tokens || 0),
                backgroundColor: GREEN,
                borderRadius: 3
              }] : [])]
            }} /></div></Panel><Panel title="Ritmo por dia e hora" note="Média por hora ativa · histórico local"><HeatmapChart fx={fx} heatmap={showA ? history?.heatmap : []} chatgpt={showB ? cg.heatmap : []} /><p className="u-hint">As duas cores usam a mesma escala de tokens. Passe o mouse para comparar os volumes.</p></Panel></div>
    <Panel title="Evolução das cotas" note="Últimas 48 horas · percentuais oficiais"><div className="u-chart"><Line options={{
            ...options,
            scales: {
              ...options.scales,
              x: {
                ...options.scales.x,
                type: 'linear',
                ticks: {
                  color: '#8592a5',
                  maxTicksLimit: 8,
                  callback: v => new Date(v).toLocaleString('pt-BR', {
                    day: '2-digit',
                    month: '2-digit',
                    hour: '2-digit',
                    minute: '2-digit'
                  })
                }
              },
              y: {
                ...options.scales.y,
                ticks: {
                  color: '#8592a5',
                  callback: v => `${v}%`
                },
                suggestedMin: 0,
                suggestedMax: 100
              }
            }
          }} data={{
            datasets: [...(showA ? (state.quotas || []).filter(q => q.provider === 'claude').map((q, index) => ({
              label: `Claude · ${q.label}`,
              data: (history?.snapshots || []).filter(r => r.window === q.source_key).map(r => ({
                x: new Date(r.ts).getTime(),
                y: r.utilization
              })),
              borderColor: ORANGE,
              borderDash: index ? [5, 4] : [],
              pointRadius: 0
            })) : []), ...(showB ? limits.map((l, index) => ({
              label: l.label,
              data: rows.filter(r => r.key === l.key).map(r => ({
                x: new Date(r.ts).getTime(),
                y: r.value
              })),
              borderColor: GREEN,
              borderDash: index ? [5, 4] : [],
              pointRadius: 2
            })) : [])]
          }} /></div></Panel>
    <SystemLinks update={state.update} />
    <Settings state={state} onSave={save} onReconnect={reconnect} reconnecting={reconnecting} /><footer className="u-footer">Dados locais · {cg.ignored_imported_sessions || 0} sessões importadas separadas · câmbio {money(fx)}/US$ · cotas oficiais, valores de API estimados</footer>
  </main></div>;
}
