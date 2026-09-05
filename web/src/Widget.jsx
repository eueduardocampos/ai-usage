import { useEffect, useState } from 'react';
import { getCurrentWindow } from '@tauri-apps/api/window';
import { api } from './api';
import { fmtTokens } from './format';
import './widget.css';

const COLORS = { claude: '#ef985d', codex: '#45d6aa' };
function resetAt(ts, now) {
  const minutes = Math.ceil((new Date(ts) - now) / 60000);
  if (!Number.isFinite(minutes)) return 'Reset indisponível';
  if (minutes <= 0) return 'Aguardando reset';
  return minutes >= 1440 ? `Reset em ${Math.floor(minutes / 1440)}d ${Math.floor(minutes % 1440 / 60)}h` : `Reset em ${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}
async function openDashboard() {
  if (window.__TAURI__?.core?.invoke) {
    await window.__TAURI__.core.invoke('open_dashboard');
  } else window.open(window.location.origin + '/', '_blank', 'noopener');
}
function Account({ name, provider, windows, burn, now, error, onOpen }) {
  return <button className="aw-account" style={{ '--accent': COLORS[provider] }} onClick={onOpen} title={`Abrir ${name} no painel completo`}>
    <span className="aw-account-name"><span>● {name}</span><span className="aw-account-arrow" aria-hidden="true">↗</span></span>
    <span className="aw-quotas">{windows.length ? windows.map(w => {
      const stale = !w.ts || now - new Date(w.ts) > 900000 || error;
      return <span className="aw-quota" key={w.label} data-stale={!!stale}>
        <span className="aw-period">{w.label}</span>
        <span className="aw-value">{w.value == null ? '—' : `${Math.round(w.value)}%`}<small> usado</small></span>
        <span className="aw-track"><i style={{ width: `${Math.min(100, Math.max(0, w.value || 0))}%`, background: stale ? '#788493' : w.value >= 90 ? '#ff7171' : COLORS[provider] }} /></span>
        <span className="aw-reset">{stale ? 'Dado desatualizado' : resetAt(w.reset, now)}</span>
      </span>;
    }) : <span className="aw-empty"><strong>Sem cota disponível</strong><span>Conecte a conta no painel ↗</span></span>}</span>
    <span className="aw-burn"><span>Ritmo agora</span><strong>{fmtTokens(burn)}<small> tokens/h</small></strong></span>
  </button>;
}
export default function Widget() {
  const [state, setState] = useState(null), [error, setError] = useState(''), [now, setNow] = useState(Date.now());
  const native = !!window.__TAURI__?.core;
  useEffect(() => {
    let alive = true;
    async function load() { try { const s = await api.state(); if (alive) { setState(s); setError(''); } } catch { if (alive) setError('Painel desconectado · tentando novamente'); } }
    void load(); const poll = setInterval(load, 10000), clock = setInterval(() => setNow(Date.now()), 1000);
    return () => { alive = false; clearInterval(poll); clearInterval(clock); };
  }, []);
  async function open() { try { await openDashboard(); } catch { setError('Não foi possível abrir o navegador.'); } }
  const claude = Object.entries(state?.windows || {}).filter(([k]) => ['five_hour', 'seven_day'].includes(k)).map(([k,w]) => ({ label: k === 'five_hour' ? '5 horas' : '7 dias', value: w.utilization, reset: w.resets_at, ts: state.snapshot_ts }));
  const codex = state?.chatgpt?.limits?.find(l => l.id === 'codex');
  const cw = ['primary', 'secondary'].filter(k => codex?.[k]).map(k => ({ label: codex[k].window_minutes === 10080 ? '7 dias' : `${codex[k].window_minutes / 60} horas`, value: codex[k].used_percent, reset: codex[k].resets_at, ts: codex.snapshot_ts }));
  const burnA = Object.values(state?.burn_tokph || {}).reduce((a,b) => a+b,0), burnB = state?.chatgpt?.burn_tokph || 0;
  const extras = state?.extra_usage?.burning;
  const fresh = state && now - new Date(state.generated_at) < 30000 && !error;
  return <main className="aw-shell"><section className="aw-widget">
    <header className="aw-header" onMouseDown={e => { if (native && e.button === 0 && !e.target.closest('button')) getCurrentWindow().startDragging().catch(() => {}); }}>
      <button className="aw-brand" onClick={open} title="Abrir painel completo"><img src="/brand.svg" alt="" /><span><strong>AI Usage</strong><small>Seu consumo, agora</small></span></button>
      <div className="aw-controls"><span className="aw-live" data-fresh={!!fresh}>{fresh ? 'Ao vivo' : 'Conectando'}</span><button onClick={open} title="Abrir painel completo" aria-label="Abrir painel completo">↗</button>{native && <><button title="Minimizar" aria-label="Minimizar" onClick={() => getCurrentWindow().minimize()}>−</button><button title="Ocultar widget" aria-label="Ocultar widget" onClick={() => getCurrentWindow().hide()}>×</button></>}</div>
    </header>
    <div className="aw-accounts"><Account name="Claude" provider="claude" windows={claude} burn={burnA} now={now} error={state?.auth_connected === false} onOpen={open} /><Account name="Codex" provider="codex" windows={cw} burn={burnB} now={now} error={state?.chatgpt?.limits_error} onOpen={open} /></div>
    <footer className="aw-footer"><span className={extras ? 'aw-alert' : ''}>{error || (extras ? 'Claude usando créditos extras' : 'Claude + Codex · assinaturas separadas')}</span><button onClick={open}>Painel completo ↗</button></footer>
  </section></main>;
}
