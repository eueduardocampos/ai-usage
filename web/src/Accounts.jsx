import { useState } from 'react';
import { api } from './api';

export const PROVIDER_COLOR = { claude: '#ef985d', codex: '#45d6aa' };
const PLAN = {
  pro: 'Pro', max_5x: 'Max 5x', max_20x: 'Max 20x', team_standard: 'Team', team_premium: 'Team Premium',
  enterprise: 'Enterprise', plus: 'Plus', prolite: 'Pro Lite',
};
const pct = v => (v == null ? '—' : `${Math.round(v)}%`);

export function resetText(value, now) {
  const h = (new Date(value).getTime() - now) / 3600000;
  if (!Number.isFinite(h)) return 'sem previsão';
  if (h <= 0) return 'aguardando reset';
  if (h >= 24) return `${Math.floor(h / 24)}d ${Math.floor(h % 24)}h`;
  return `${Math.floor(h)}h ${String(Math.floor((h % 1) * 60)).padStart(2, '0')}m`;
}

export function statusOf(acc) {
  if (acc.provider === 'claude') {
    if (acc.login?.state === 'waiting') return ['wait', 'Aguardando login'];
    if (!acc.has_token) return ['off', 'Não conectada'];
    if (acc.connected === false) return ['off', 'Desconectada'];
    if (acc.rate_limited) return ['wait', 'Limite de consultas'];
    return ['on', 'Conectada'];
  }
  return acc.connected ? ['on', 'Login do Codex'] : ['off', 'Codex não encontrado'];
}

function QuotaRow({ q, now }) {
  const v = q.utilization;
  const proj = q.projected;
  const color = v >= 90 ? '#ff6b6b' : v >= 70 ? '#ffb454' : 'var(--acc)';
  return <div className="a-quota">
    <div className="a-quota-head"><span>{q.label}</span><strong>{pct(v)}</strong></div>
    <div className="a-meter"><i style={{ width: `${Math.min(100, v || 0)}%`, background: color }} />
      {proj != null && proj > (v || 0) && <b style={{ left: `${Math.min(100, proj)}%` }} title={`Projeção no reset: ${pct(proj)}`} />}</div>
    <div className="a-quota-foot"><small>Reset em {resetText(q.resets_at, now)}</small><small>{proj != null ? `projeção ${pct(proj)}` : ''}</small></div>
  </div>;
}

export function AccountCard({ acc, now, onOpen }) {
  const [st, label] = statusOf(acc);
  const quotas = acc.quotas || [];
  return <article className="a-card" style={{ '--acc': PROVIDER_COLOR[acc.provider] }}>
    <header>
      <div><h3>{acc.name}</h3><small>{[acc.provider === 'claude' ? 'Claude' : 'ChatGPT', PLAN[acc.plan] || acc.plan, acc.email].filter(Boolean).join(' · ')}</small></div>
      <span className="a-status" data-st={st}>{label}</span>
    </header>
    {acc.duplicate_of && <p className="a-warn">Mesmo e-mail de outra conta. O navegador provavelmente já estava logado: reconecte usando "Copiar link" no perfil certo.</p>}
    {quotas.length ? quotas.map(q => <QuotaRow key={q.key} q={q} now={now} />)
      : <p className="u-hint">{st === 'on' ? 'Esperando a primeira leitura da conta.' : 'Conecte a conta nas configurações para ver os limites.'}</p>}
    {acc.app && <button className="a-open" onClick={() => onOpen(acc)}>Abrir {acc.app_name}</button>}
  </article>;
}

function MoneyField({ label, value, onSave }) {
  const [text, setText] = useState(value == null ? '' : String(value).replace('.', ','));
  const commit = () => {
    const t = text.trim();
    const v = t === '' ? null : Number(t.replace(/\./g, '').replace(',', '.'));
    if (v !== null && (!Number.isFinite(v) || v < 0)) return;
    if (v !== value) onSave(v);
  };
  return <label className="a-field">{label}<span><i>R$</i><input inputMode="decimal" value={text} placeholder="0,00"
    onChange={e => setText(e.target.value)} onBlur={commit} onKeyDown={e => e.key === 'Enter' && e.currentTarget.blur()} /></span></label>;
}

async function copy(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch { return false; }
}

// Uma conta dentro do painel de configurações: nome, app, conectar.
export function AccountEditor({ acc, apps, onChange, compact }) {
  const [name, setName] = useState(acc.name);
  const [link, setLink] = useState(null);
  const [msg, setMsg] = useState('');
  const [st, label] = statusOf(acc);
  const run = async (fn, ok) => {
    try { await fn(); if (ok) setMsg(ok); onChange(); } catch (e) { setMsg(e.message || 'Não deu certo'); }
  };
  const login = async open => {
    try {
      const r = await api.login(acc.id, open);
      setLink(r.url);
      if (!open) setMsg((await copy(r.url)) ? 'Link copiado. Cole no navegador ou perfil do Chrome logado nesta conta.' : 'Copie o link abaixo e abra no navegador certo.');
      else setMsg('Login aberto no navegador padrão. Se abriu na conta errada, use "Copiar link".');
      onChange();
    } catch (e) { setMsg(e.message); }
  };
  return <div className="a-editor" style={{ '--acc': PROVIDER_COLOR[acc.provider] }}>
    <div className="a-editor-row">
      <input value={name} maxLength={40} aria-label="Nome da conta" onChange={e => setName(e.target.value)}
        onBlur={() => name !== acc.name && run(() => api.updateAccount(acc.id, { name }), 'Nome salvo.')} />
      <span className="a-status" data-st={st}>{label}</span>
    </div>
    <small className="u-hint">{[acc.provider === 'claude' ? 'Claude' : 'ChatGPT / Codex', acc.email, acc.org_name].filter(Boolean).join(' · ')}</small>
    {acc.duplicate_of && <p className="a-warn">Esta conta entrou com o mesmo e-mail de outra. Desconecte e use "Copiar link" no perfil do navegador logado na conta certa.</p>}
    <div className="a-money">
      <MoneyField label="Mensalidade" value={acc.price_brl} onSave={v => run(() => api.updateAccount(acc.id, { price_brl: v }), 'Mensalidade salva.')} />
      <MoneyField label="Extras pagos neste mês" value={acc.extra_brl} onSave={v => run(() => api.updateAccount(acc.id, { extra_brl: v }), 'Extras salvos.')} />
    </div>
    {!compact && <label className="a-app">Abre o app
      <select value={acc.app || ''} onChange={e => run(() => api.updateAccount(acc.id, { app: e.target.value || null }), 'App salvo.')}>
        <option value="">Nenhum</option>
        {apps.map(a => <option key={a.path} value={a.path}>{a.name}</option>)}
      </select>
    </label>}
    {acc.provider === 'claude' ? <div className="a-actions">
      <button onClick={() => login(true)}>{acc.has_token ? 'Reconectar' : 'Conectar'} no navegador</button>
      <button onClick={() => login(false)}>Copiar link de login</button>
      {acc.has_token && <button onClick={() => run(() => api.disconnect(acc.id), 'Desconectada.')}>Desconectar</button>}
      {acc.id !== 'principal' && <button className="danger" onClick={() => confirm(`Remover a conta "${acc.name}" do painel?`) && run(() => api.removeAccount(acc.id))}>Remover</button>}
    </div> : <p className="u-hint">O ChatGPT usa o login do Codex deste computador. Para trocar de conta, entre no Codex com a outra conta.</p>}
    {link && <div className="a-link"><code>{link}</code><button onClick={async () => setMsg((await copy(link)) ? 'Link copiado.' : 'Selecione e copie manualmente.')}>Copiar</button></div>}
    {msg && <p role="status" className="a-msg">{msg}</p>}
  </div>;
}

export function AccountsManager({ data, reload, compact }) {
  const [busy, setBusy] = useState(false);
  if (!data) return <p className="u-hint">Carregando contas…</p>;
  const add = async () => {
    setBusy(true);
    try { await api.addAccount(); await reload(); } finally { setBusy(false); }
  };
  return <div className="a-manager">
    {data.accounts.map(a => <AccountEditor key={a.id} acc={a} apps={data.apps} onChange={reload} compact={compact} />)}
    <button className="a-add" onClick={add} disabled={busy}>+ Adicionar conta do Claude</button>
    <p className="u-hint">Cada conta abre o próprio login. Se você usa um perfil do Chrome por conta, use "Copiar link" e cole no perfil logado naquela conta. O link volta para este computador, então funciona em qualquer navegador daqui.</p>
  </div>;
}

export function Onboarding({ data, reload, onDone }) {
  return <div className="o-root"><div className="o-box">
    <span className="u-eyebrow">AI USAGE · PRIMEIRO ACESSO</span>
    <h1>Conecte suas contas<span>.</span></h1>
    <p>Conecte quantas contas precisar e dê um nome para cada uma. Os limites de 5 horas e de 7 dias vêm da conta. O consumo de tokens é medido neste computador.</p>
    <AccountsManager data={data} reload={reload} />
    <div className="o-foot"><button className="primary" onClick={onDone}>Concluir e abrir o painel</button></div>
  </div></div>;
}
