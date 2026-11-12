"use strict";
// v5: varias contas e monitor da maquina. Funcoes puras (estado -> view);
// o plugin.js cuida do protocolo e da coleta.
//
// Tecla de limite: escolhe a conta e a janela no inspector; cada clique troca
// a forma de ver (usado, restante, projecao, reset).
// Dial da maquina: girar troca o componente (GPU, CPU, memoria...), apertar
// troca a forma de ver. A forma de ver tem o mesmo sentido em todos os
// componentes: 1) percentual, 2) quantidade, 3) temperatura.

const COLORS = { claude: '#ef985d', codex: '#45d6aa' };
const num = v => typeof v === 'number' && Number.isFinite(v);

// ------------------------------------------------------------ contas

const WINDOWS = [
  ['five_hour', '5H', 'Sessão 5 horas'],
  ['seven_day', '7D', 'Semana 7 dias'],
  ['model', 'MODELO', 'Por modelo (Fable, Spark...)'],
];
const QUOTA_MODES = ['usado', 'restante', 'projecao', 'reset'];

function accountsOf(state) {
  return (state && Array.isArray(state.accounts)) ? state.accounts : [];
}

function findAccount(state, id) {
  const list = accountsOf(state);
  return list.find(a => a.id === id) || null;
}

function shortName(name, max = 11) {
  const s = String(name || '').trim().toUpperCase();
  return s.length > max ? s.slice(0, max - 1) + '…' : s;
}

function pickQuota(acc, win) {
  const qs = (acc && acc.quotas) || [];
  if (acc && acc.provider === 'codex') {
    if (win === 'five_hour') return qs.find(q => Math.round(q.window_hours) === 5) || null;
    if (win === 'seven_day') return qs.find(q => q.key.startsWith('codex:codex:') && Math.round(q.window_hours) === 168)
      || qs.find(q => Math.round(q.window_hours) === 168) || null;
    return qs.filter(q => !q.key.startsWith('codex:codex:')).sort((a, b) => (b.utilization || 0) - (a.utilization || 0))[0] || null;
  }
  if (win === 'model') {
    return qs.filter(q => q.source_key && q.source_key.startsWith('seven_day_model:'))
      .sort((a, b) => (b.utilization || 0) - (a.utilization || 0))[0]
      || qs.find(q => q.source_key === 'seven_day_opus' || q.source_key === 'seven_day_sonnet') || null;
  }
  return qs.find(q => q.source_key === win) || null;
}

function resetText(iso, now) {
  const h = (Date.parse(iso) - now) / 3600000;
  if (!Number.isFinite(h)) return '--';
  if (h <= 0) return 'agora';
  if (h >= 24) return `${Math.floor(h / 24)}d ${Math.floor(h % 24)}h`;
  return `${Math.floor(h)}h ${String(Math.floor((h % 1) * 60)).padStart(2, '0')}m`;
}

function quotaView(state, settings, now = Date.now()) {
  const off = (label, value, sub) => ({ label, value, sub, level: 'offline' });
  if (!state) return off('PAINEL', 'OFF', 'localhost');
  const acc = findAccount(state, settings.accountId || 'principal');
  const winMeta = WINDOWS.find(w => w[0] === (settings.window || 'five_hour')) || WINDOWS[0];
  if (!acc) return off(winMeta[1], '--', 'escolha a conta');
  const color = COLORS[acc.provider] || COLORS.claude;
  const q = pickQuota(acc, winMeta[0]);
  const name = shortName(acc.name, 9);
  const winShort = winMeta[0] === 'model' && q ? shortName((q.product || q.label || 'MODELO').split(' ')[0], 7) : winMeta[1];
  const label = `${winShort} · ${name}`;
  if (acc.provider === 'claude' && !acc.has_token) return { ...off(label, '--', 'conectar conta'), accent: color };
  if (!q || !num(q.utilization)) return { ...off(label, '--', 'sem dados'), accent: color };
  const used = q.utilization;
  const stale = !q.snapshot_ts || now - Date.parse(q.snapshot_ts) > 15 * 60000;
  const mode = QUOTA_MODES[((settings.mode | 0) % QUOTA_MODES.length + QUOTA_MODES.length) % QUOTA_MODES.length];
  const level = stale ? 'offline' : used >= 90 ? 'danger' : 'safe';
  const base = { label, level, accent: color, pct: used };
  const bang = stale ? '!' : '';
  if (mode === 'restante') return { ...base, value: `${Math.round(Math.max(0, 100 - used))}%${bang}`, sub: 'restante', pct: Math.max(0, 100 - used) };
  if (mode === 'projecao') {
    const p = num(q.projected) ? q.projected : used;
    return { ...base, value: `${Math.round(p)}%${bang}`, sub: 'projeção no reset', pct: Math.min(100, p), level: stale ? 'offline' : p >= 100 ? 'danger' : 'safe' };
  }
  if (mode === 'reset') return { ...base, value: resetText(q.resets_at, now), sub: `reset · ${Math.round(used)}% usado` };
  return { ...base, value: `${Math.round(used)}%${bang}`, sub: stale ? 'dado antigo' : `usado · ${resetText(q.resets_at, now)}` };
}

function appView(state, settings) {
  if (!state) return { label: 'PAINEL', value: 'OFF', sub: 'localhost', level: 'offline' };
  const acc = findAccount(state, settings.accountId || 'principal');
  if (!acc) return { label: 'APP', value: '--', sub: 'escolha a conta', level: 'offline' };
  const color = COLORS[acc.provider] || COLORS.claude;
  const ok = acc.provider === 'codex' ? acc.connected : acc.has_token && acc.connected !== false;
  return {
    label: shortName(acc.name, 12), value: 'ABRIR',
    sub: acc.app_name ? shortName(acc.app_name, 16).toLowerCase() : 'sem app definido',
    level: acc.app ? (ok ? 'safe' : 'warn') : 'offline', accent: color,
  };
}

// ------------------------------------------------------------ maquina

const GB = 1024 ** 3;
const gb = v => (num(v) ? `${(v / GB).toFixed(v >= 100 * GB ? 0 : 1)}G` : '--');
const temp = v => (num(v) ? `${Math.round(v)}°C` : '--');
const p = v => (num(v) ? `${Math.round(v)}%` : '--');
function rate(v) {
  if (!num(v)) return '--';
  const b = v * 8;
  return b >= 1e9 ? `${(b / 1e9).toFixed(1)}G` : b >= 1e6 ? `${(b / 1e6).toFixed(1)}M` : `${Math.round(b / 1e3)}k`;
}
const lvl = v => (!num(v) ? 'offline' : v >= 90 ? 'danger' : v >= 75 ? 'warn' : 'safe');
const HW_COLOR = '#7cc4ff';

const COMPONENTS = ['gpu', 'cpu', 'memory', 'disk', 'net', 'battery', 'temps'];
const COMPONENT_NAME = { gpu: 'GPU', cpu: 'CPU', memory: 'MEMÓRIA', disk: 'DISCO', net: 'REDE', battery: 'BATERIA', temps: 'TEMPERATURA' };

function hwViews(hw, comp) {
  const h = hw || {};
  const c = h.cpu || {}, g = h.gpu || {}, m = h.memory || {}, d = h.disk || {}, n = h.net || {}, b = h.battery, t = h.thermal || {};
  const V = (label, value, sub, pct, level) => ({ label, value, sub, pct, level: level || (num(pct) ? lvl(pct) : 'safe'), accent: HW_COLOR });
  switch (comp) {
    case 'gpu': return [
      V('GPU', p(g.usage), num(g.renderer) ? `render ${p(g.renderer)}` : 'uso', g.usage),
      V('GPU · MEMÓRIA', gb(g.memory_used), 'em uso', num(g.memory_used) && num(m.total) ? g.memory_used / m.total * 100 : undefined),
      V('GPU · TEMP', temp(g.temp), 'chip', num(g.temp) ? g.temp : undefined, num(g.temp) ? lvl(g.temp + 5) : 'offline'),
    ];
    case 'cpu': return [
      V('CPU', p(c.usage), num(c.performance_usage) ? `${(c.performance_name || 'P').slice(0, 5)} ${p(c.performance_usage)} · E ${p(c.efficiency_usage)}` : 'uso', c.usage),
      V('CPU · CARGA', num(c.load && c.load[0]) ? c.load[0].toFixed(1) : '--', `${c.cores || '?'} núcleos`, num(c.load && c.load[0]) && c.cores ? c.load[0] / c.cores * 100 : undefined),
      V('CPU · TEMP', temp(c.temp), 'chip', num(c.temp) ? c.temp : undefined, num(c.temp) ? lvl(c.temp + 5) : 'offline'),
    ];
    case 'memory': return [
      V('MEMÓRIA', p(m.usage), `de ${gb(m.total)}`, m.usage),
      V('MEMÓRIA · USO', gb(m.used), `livre ${gb(m.free)}`, m.usage),
      V('MEMÓRIA · PRESSÃO', ['normal', 'normal', 'atenção', 'alta', 'crítica'][m.pressure_level] || '--', num(m.swap_used) ? `swap ${gb(m.swap_used)}` : '', undefined, m.pressure_level >= 3 ? 'danger' : m.pressure_level === 2 ? 'warn' : 'safe'),
    ];
    case 'disk': return [
      V('DISCO', p(d.usage), `livre ${gb(d.free)}`, d.usage),
      V('DISCO · LIVRE', gb(d.free), num(d.read_bps) ? `L ${rate(d.read_bps / 8)}B · G ${rate(d.write_bps / 8)}B` : `de ${gb(d.total)}`, d.usage),
      V('DISCO · TEMP', temp(d.temp), 'SSD', num(d.temp) ? d.temp : undefined, num(d.temp) ? lvl(d.temp + 15) : 'offline'),
    ];
    case 'net': return [
      V('REDE ↓', rate(n.rx_bps), 'bits/s download'),
      V('REDE ↑', rate(n.tx_bps), 'bits/s upload'),
      V('REDE · TOTAL', gb((n.rx_total || 0) + (n.tx_total || 0)), 'desde a abertura'),
    ];
    case 'battery': return b ? [
      V('BATERIA', p(b.percent), b.charging ? 'carregando' : b.plugged ? 'na tomada' : 'na bateria', b.percent, num(b.percent) && b.percent <= 15 && !b.plugged ? 'danger' : 'safe'),
      V('BATERIA · SAÚDE', p(b.health), num(b.cycles) ? `${b.cycles} ciclos` : 'capacidade', b.health, 'safe'),
      V('BATERIA · TEMP', temp(b.temp), 'célula', num(b.temp) ? b.temp : undefined, num(b.temp) ? lvl(b.temp + 45) : 'offline'),
    ] : [V('BATERIA', '--', 'sem bateria', undefined, 'offline')];
    case 'temps': return [
      V('TEMP · CHIP', temp(t.chip_max), `médio ${temp(t.chip_avg)}`, num(t.chip_max) ? t.chip_max : undefined, num(t.chip_max) ? lvl(t.chip_max + 5) : 'offline'),
      V('TEMP · SSD', temp(d.temp), 'disco', num(d.temp) ? d.temp : undefined, num(d.temp) ? lvl(d.temp + 15) : 'offline'),
      V('TEMP · BATERIA', temp(b && b.temp), t.state ? `térmico ${t.state}` : '', b && num(b.temp) ? b.temp : undefined, b && num(b.temp) ? lvl(b.temp + 45) : 'offline'),
    ];
    default: return [V('MÁQUINA', '--', '', undefined, 'offline')];
  }
}

const wrap = (i, n) => ((i % n) + n) % n;

function hwView(hw, settings) {
  if (!hw) return { label: 'MÁQUINA', value: 'OFF', sub: 'painel local', level: 'offline' };
  const comp = COMPONENTS.includes(settings.component) ? settings.component
    : COMPONENTS[wrap(settings.compIdx | 0, COMPONENTS.length)];
  const views = hwViews(hw, comp);
  const v = views[wrap(settings.viewIdx | 0, views.length)];
  // percentual nao faz sentido em temperatura: a barra vai de 30 a 100 °C
  if (v.label.includes('TEMP') && num(v.pct)) v.pct = Math.max(0, Math.min(100, (v.pct - 30) / 70 * 100));
  return v;
}

module.exports = { COMPONENTS, COMPONENT_NAME, WINDOWS, QUOTA_MODES, quotaView, appView, hwView, hwViews, pickQuota, accountsOf };
