"use strict";
// Pure presentation adapter. Only consumes the dashboard's existing snapshots.
const COLORS = { claude: '#ef985d', codex: '#45d6aa' };
const num = v => typeof v === 'number' && Number.isFinite(v);
const tokens = v => !num(v) ? '--' : v >= 1e9 ? `${(v/1e9).toFixed(1)}B` : v >= 1e6 ? `${(v/1e6).toFixed(1)}M` : v >= 1e3 ? `${(v/1e3).toFixed(0)}k` : String(Math.round(v));
const money = v => !num(v) ? '--' : v >= 1000 ? `R$${(v/1000).toFixed(1)}k` : `R$${Math.round(v)}`;
const missing = label => ({kind:'plain',label,value:'--',sub:'sem dados',level:'offline'});
function windows(state, now=Date.now()) {
  if (!state) return [];
  const list = Object.entries(state.windows || {}).map(([key,w]) => ({
    key:`claude-${key}`,provider:'claude',label:`CLAUDE ${key === 'five_hour' ? '5H' : key === 'seven_day' ? '7D' : 'SONNET'}`,
    used:w.utilization,reset:w.resets_at,ts:state.snapshot_ts,failed:state.auth_connected === false,
  }));
  for (const l of state.chatgpt?.limits || []) for (const k of ['primary','secondary']) {
    const w=l[k];if (!w) continue;
    list.push({key:`${l.id}-${k}`,provider:'codex',label:`${l.id==='codex'?'CODEX':'SPARK'} ${w.window_minutes===10080?'7D':`${w.window_minutes/60}H`}`,used:w.used_percent,reset:w.resets_at,ts:l.snapshot_ts,failed:!!state.chatgpt.limits_error});
  }
  return list.map(w=>({...w,stale:w.failed || !w.ts || !Number.isFinite(Date.parse(w.ts)) || now-Date.parse(w.ts)>900000}));
}
function quota(w,remaining=false,now=Date.now()) {
  if(!w || !num(w.used)) return missing(w?.label || 'JANELA');
  const shown=remaining?Math.max(0,100-w.used):w.used;
  const hours=(Date.parse(w.reset)-now)/3600000;
  const reset=!Number.isFinite(hours)?'--':hours<=0?'pendente':hours>=24?`${Math.floor(hours/24)}d ${Math.floor(hours%24)}h`:`${Math.floor(hours)}h ${Math.floor(hours%1*60)}m`;
  return {kind:'pct',label:w.label,value:`${Math.round(shown)}%${w.stale?'!':''}`,sub:w.stale?'dado antigo':`reset ${reset}`,pct:shown,barPct:shown,level:w.stale?'offline':w.used>=90?'danger':'safe',accent:COLORS[w.provider]};
}
function burnRows(s) {
  if(!s)return [];
  return ['claude','codex'].flatMap(provider=>Object.entries(provider==='claude'?s.burn_tokph || {}:s.chatgpt?.burn_by_model || {}).map(([model,value])=>({provider,model,value}))).sort((a,b)=>b.value-a.value);
}
function burn(s,provider) {
  if(!s || (provider==='codex'&&!s.chatgpt))return missing('RITMO');
  const rows=burnRows(s).filter(r=>r.provider===provider);
  return {kind:'plain',label:`${provider.toUpperCase()} /H`,value:tokens(rows.reduce((n,r)=>n+r.value,0)),sub:'tokens/h · 2h',level:'safe',accent:COLORS[provider]};
}
function cost(s,provider,scope='mes',balance=false) {
  const h=provider==='claude'?s?.history?.[scope]:s?.chatgpt?.history?.[scope];
  if(!h)return missing(provider.toUpperCase());
  const fx=s.config?.usd_brl;
  const value=provider==='claude'?h.total_cost:num(fx)&&num(h.equivalent_usd)?h.equivalent_usd*fx:null;
  if(!num(value))return missing(provider.toUpperCase());
  const partial=provider==='codex'&&h.unpriced_tokens>0;
  let text=money(value),sub=`API ${scope}${partial?' parcial':''}`;
  if(balance){
    const fixed=s.config?.[provider==='claude'?'subscription_brl':'chatgpt_subscription_brl'];
    const extra=provider==='claude'?s.extra_usage?.used:s.config?.chatgpt_extra_brl;
    if(!num(fixed)||fixed<=0)return {...missing('RETORNO'),sub:'defina assinatura'};
    text=`${(value/(fixed+(num(extra)?extra:0))).toFixed(2)}x`;
    sub=partial?'API parcial':!num(extra)?'sem extras inform.':'API / gasto';
  }
  return {kind:'plain',label:`${provider.toUpperCase()} ${balance?'ROI':scope.toUpperCase()}`,value:text,sub,level:'safe',accent:COLORS[provider]};
}
function view(s,metric,remaining=false) {
  if(!s)return {kind:'plain',label:'PAINEL',value:'OFF',sub:'localhost',level:'offline'};
  const ws=windows(s);
  if(metric==='claude5')return quota(ws.find(w=>w.key==='claude-five_hour'),remaining);
  if(metric==='claude7')return quota(ws.find(w=>w.key==='claude-seven_day'),remaining);
  if(metric==='codexquota')return quota(ws.filter(w=>w.key.startsWith('codex-')).sort((a,b)=>(b.used??-1)-(a.used??-1))[0],remaining);
  if(metric==='sparkquota')return quota(ws.filter(w=>w.label.startsWith('SPARK')).sort((a,b)=>(b.used??-1)-(a.used??-1))[0],remaining);
  if(metric==='claudeburn'||metric==='codexburn')return burn(s,metric.startsWith('claude')?'claude':'codex');
  if(metric==='clauderoi'||metric==='codexroi')return cost(s,metric.startsWith('claude')?'claude':'codex','mes',true);
  return missing('IA');
}
function dialItems(s,kind,remaining=false) {
  if(!s)return [view(null)];
  if(kind==='aiwindows')return windows(s).sort((a,b)=>Number(b.used>0)-Number(a.used>0)).map(w=>quota(w,remaining));
  if(kind==='aiburn')return [burn(s,'claude'),burn(s,'codex'),...burnRows(s).map(r=>({kind:'plain',label:`${r.provider==='claude'?'CL':'CX'} ${r.model.replace(/^claude-|^gpt-/,'')}`,value:`${tokens(r.value)}/h`,sub:'media 2h',level:'safe',accent:COLORS[r.provider]}))];
  if(kind==='aicosts')return ['dia','semana','mes'].flatMap(scope=>['claude','codex'].map(p=>cost(s,p,scope)));
  if(kind==='aitotals'){
    const a=s.history?.geral?.total_tokens,b=s.chatgpt?.history?.geral?.total_tokens;
    return [{label:'IA · TOKENS',value:num(a)&&num(b)?tokens(a+b):'--',sub:'historico',level:'safe'},...['claude','codex'].map(p=>({label:`${p.toUpperCase()} TOTAL`,value:tokens(p==='claude'?a:b),sub:'tokens locais',level:'safe',accent:COLORS[p]})),cost(s,'claude','mes',true),cost(s,'codex','mes',true)];
  }
  return [];
}
module.exports={view,dialItems,windows,quota,cost,burnRows};
