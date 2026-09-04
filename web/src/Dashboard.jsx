/**
 * Dashboard.jsx — cockpit "mission control" do consumo do Claude.
 *
 * Dark-first, denso e em tempo quase real:
 *   /api/total   a cada 3s  (contador vivo)
 *   /api/state   a cada 10s (janelas, veredito, custo)
 *   /api/history a cada 60s (graficos)
 *   tick local de 1s para countdowns de reset.
 *
 * Responsivo por grid fluido (auto-fit) — de Full HD a 4K ultrawide o layout
 * ganha colunas em vez de esticar uma coluna central.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Chart, registerables } from 'chart.js';
import { Bar, Line } from 'react-chartjs-2';
import { api } from './api';
import { fmtTokens, fmtMoneyUSD, fmtMoney, fmtInt } from './format';
import HeatmapChart from './components/HeatmapChart';

Chart.register(...registerables);

// design system Astronauta sobre navy #050A30 — serie validada pelo
// validate_palette da skill dataviz (#4285ee / #d96d0d / #cc0099, modo dark)
const C = {
  page: '#050a30', // navy da marca (CTAFinal / ProposalHero)
  card: '#0a1140',
  border: 'rgba(255,255,255,0.10)',
  ink: '#ffffff',
  ink2: '#c9d2f2',
  muted: '#8590c2',
  grid: '#1b2452',
  s1: '#4285ee', // azul secundario da marca (simbolo)
  s2: '#d96d0d', // brand-orange, um passo mais escuro p/ o navy
  s3: '#cc0099', // brand-magenta
  brand: '#004fff', // Azul Astronauta — acoes e destaque
  good: '#21c45d', // --success
  warn: '#facc14', // --warning
  crit: '#f2404c', // --destructive
};
const WIN_COLORS = { five_hour: C.s1, seven_day: C.s2, seven_day_sonnet: C.s3 };
const WIN_SHORT = { five_hour: 'SESSÃO 5H', seven_day: 'SEMANA 7D', seven_day_sonnet: 'SONNET 7D' };
const STATUS_C = { SEGURO: C.good, ATENCAO: C.warn, RISCO: C.crit };
const STATUS_LABEL = { SEGURO: 'seguro', ATENCAO: 'atenção', RISCO: 'risco' };

// ---------------------------------------------------------------- helpers

function statusColor(s) {
  return STATUS_C[(s || '').toUpperCase()] || C.muted;
}

/** Os valores de custo ja SAO em reais (o "$" da fonte e erro de rotulo,
 *  nao dolar de verdade) — entao so trocamos o simbolo, sem converter. */
function costBRL(v) {
  if (v == null) return '—';
  return fmtMoney(v, 'BRL');
}

function modelShort(m) {
  return String(m || '')
    .replace(/^claude-/, '')
    .replace(/-\d{8}$/, '');
}

function fmtCountdown(iso, now) {
  if (!iso) return '—';
  const ms = new Date(iso).getTime() - now;
  if (!isFinite(ms)) return '—';
  if (ms <= 0) return 'resetando…';
  const s = Math.floor(ms / 1000);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`;
  return `${m}m ${String(ss).padStart(2, '0')}s`;
}

function ago(iso, now) {
  if (!iso) return '—';
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

function useNow(ms = 1000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now;
}

/** interpola o numero exibido ate o alvo (contador "vivo") */
function useAnimated(target, dur = 800) {
  const [val, setVal] = useState(target ?? 0);
  const fromRef = useRef(target ?? 0);
  useEffect(() => {
    if (target == null) return;
    const from = fromRef.current;
    const to = target;
    if (from === to) return;
    const t0 = performance.now();
    let raf;
    const step = (t) => {
      const k = Math.min(1, (t - t0) / dur);
      const eased = 1 - Math.pow(1 - k, 3);
      const v = from + (to - from) * eased;
      setVal(v);
      fromRef.current = v;
      if (k < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target, dur]);
  return val;
}

// ---------------------------------------------------------------- atoms

function Card({ children, className = '', style }) {
  return (
    <div className={`mc-card p-4 ${className}`} style={style}>
      {children}
    </div>
  );
}

function Chip({ color, children }) {
  return (
    <span
      className="mc-num inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider"
      style={{ color, background: `${color}1f`, border: `1px solid ${color}55` }}
    >
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: color }} />
      {children}
    </span>
  );
}

/** barra de janela: preenchimento = uso atual, marcador = projecao no reset */
function GaugeBar({ pct, projected, color }) {
  const cur = Math.min(100, Math.max(0, pct || 0));
  const proj = projected == null ? null : Math.min(100, Math.max(0, projected));
  return (
    <div className="relative h-2.5 w-full overflow-hidden rounded-full" style={{ background: 'rgba(255,255,255,0.08)' }}>
      <div
        className="h-full rounded-full transition-[width] duration-700"
        style={{ width: `${cur}%`, background: color }}
      />
      {proj != null && proj > cur && (
        <div
          className="absolute inset-y-0 rounded-full opacity-35 transition-[width,left] duration-700"
          style={{ left: `${cur}%`, width: `${proj - cur}%`, background: color }}
        />
      )}
      {proj != null && (
        <div
          className="absolute top-[-2px] bottom-[-2px] w-[2px] transition-[left] duration-700"
          style={{ left: `calc(${proj}% - 1px)`, background: C.ink2 }}
          title={`projeção no reset: ${Math.round(proj)}%`}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------- seções

function StatusBar({ state, latency, now, onRefresh, onReconnect, aviso }) {
  const connected = state?.auth_connected;
  // 429 e limite de requisicoes da conta, nao token invalido: mostra espera,
  // nunca oferece "reconectar" (reconectar gera mais requisicoes e piora).
  const limitado = !!state?.rate_limited;
  const err = state?.last_error;
  const situacao = !state
    ? 'conectando…'
    : limitado
      ? `limite da conta · nova tentativa em ${state.retry_in}s`
      : connected
        ? 'conta conectada'
        : 'desconectado';
  return (
    <header
      className="sticky top-0 z-10 border-b backdrop-blur"
      style={{ borderColor: C.border, background: 'rgba(5,10,48,0.88)' }}
    >
      <div className="mx-auto flex w-full max-w-[2400px] flex-wrap items-center gap-x-5 gap-y-1 px-4 py-2.5 sm:px-6 xl:px-8">
        <div className="flex items-center gap-2.5">
          <span className={`mc-live ${limitado ? 'warn' : connected ? '' : 'off'}`} />
          <div className="leading-tight">
            <div className="text-[13px] font-bold tracking-wide">CLAUDE USAGE · OPS</div>
            <div className="mc-label">{situacao}</div>
          </div>
        </div>

        <div className="mc-num hidden items-center gap-4 text-[12px] sm:flex" style={{ color: C.muted }}>
          <span>
            snapshot{' '}
            <span
              style={{
                color:
                  state?.snapshot_ts && now - new Date(state.snapshot_ts).getTime() > 150000
                    ? C.warn
                    : C.ink2,
              }}
            >
              {ago(state?.snapshot_ts, now)}
            </span>
          </span>
          <span>
            api <span style={{ color: C.ink2 }}>{latency == null ? '—' : `${Math.round(latency)}ms`}</span>
          </span>
          {err && !limitado && (
            <span className="max-w-[40ch] truncate" style={{ color: C.warn }} title={err}>
              ⚠ {err}
            </span>
          )}
          {aviso && (
            <span style={{ color: C.warn }} title={aviso}>
              {aviso}
            </span>
          )}
        </div>

        <div className="ml-auto flex items-center gap-3">
          <span className="mc-num text-[13px]" style={{ color: C.ink2 }}>
            {new Date(now).toLocaleTimeString('pt-BR')}
          </span>
          {!connected && !limitado && state && (
            <button className="mc-btn" style={{ color: C.warn, borderColor: `${C.warn}66` }} onClick={onReconnect}>
              reconectar
            </button>
          )}
          <button className="mc-btn" onClick={onRefresh}>
            atualizar
          </button>
        </div>
      </div>
    </header>
  );
}

function VerdictPanel({ sw, intendedHours, dominant, onChangeHours }) {
  const color = statusColor(sw?.verdict);
  const hours = [
    [0.5, '30m'], [1, '1h'], [2, '2h'], [3, '3h'], [4, '4h'],
  ];
  return (
    <Card className="flex h-full flex-col justify-between gap-4">
      <div>
        <div className="mb-2 flex items-center justify-between gap-3">
          <span className="mc-label">é seguro trocar de modelo?</span>
          {sw?.verdict && <Chip color={color}>{sw.verdict}</Chip>}
        </div>
        <div className="mc-display text-xl font-bold leading-snug xl:text-2xl" style={{ color: sw ? C.ink : C.muted }}>
          {sw?.message || 'coletando dados…'}
        </div>
        <div className="mc-num mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12px]" style={{ color: C.muted }}>
          {sw?.windows &&
            Object.entries(sw.windows).map(([k, w]) => (
              <span key={k}>
                {WIN_SHORT[k] || k} → <span style={{ color: statusColor(w.status) }}>{Math.round(w.projected)}%</span>
              </span>
            ))}
          {dominant && <span>modelo atual: {modelShort(dominant)}</span>}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <span className="mc-label mr-1">pretendo trabalhar mais</span>
        {hours.map(([v, l]) => (
          <button
            key={v}
            className="mc-pill"
            data-active={intendedHours === v}
            onClick={() => onChangeHours(v)}
          >
            {l}
          </button>
        ))}
      </div>
    </Card>
  );
}

function LiveTotal({ total, today, burn, burnBySource, burnModels, dominant, fx }) {
  // legenda da queima: origem quando ha Science ativo; senao o modelo dominante,
  // com +N quando mais de um modelo entra na soma
  const burnParts = [];
  if (burnBySource?.code > 0) burnParts.push(`code ${fmtTokens(burnBySource.code)}/h`);
  if (burnBySource?.science > 0) burnParts.push(`science ${fmtTokens(burnBySource.science)}/h`);
  const lead = modelShort(dominant);
  const byModel = lead && burnModels > 1 ? `${lead} +${burnModels - 1}` : lead;
  const burnLegend = burnParts.length > 1 ? burnParts.join(' · ') : byModel;
  const tokens = useAnimated(total?.total_tokens, 900);
  return (
    <Card className="flex h-full flex-col justify-between gap-4">
      <div>
        <div className="mb-1 flex items-center justify-between">
          <span className="mc-label">total de tokens · vida toda</span>
          <span className="mc-label" style={{ color: C.good }}>
            live
          </span>
        </div>
        <div className="mc-num text-4xl font-black tracking-tight xl:text-5xl" style={{ color: C.ink }}>
          {fmtInt(Math.round(tokens))}
        </div>
        <div className="mc-num mt-1 text-[13px]" style={{ color: C.muted }}>
          {costBRL(total?.total_cost)} estimado
          {fx && total?.total_cost != null ? ` (≈ ${fmtMoneyUSD(total.total_cost / fx)})` : ''} ·{' '}
          {fmtInt(total?.total_turns)} interações
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <div className="mc-label">hoje</div>
          <div className="mc-num text-lg font-semibold">{fmtTokens(today?.total_tokens)}</div>
          <div className="mc-num text-[12px]" style={{ color: C.muted }}>
            {costBRL(today?.total_cost, fx)}
          </div>
        </div>
        <div>
          <div className="mc-label">queima agora</div>
          <div className="mc-num text-lg font-semibold">
            {burn ? `${fmtTokens(burn)}/h` : '—'}
          </div>
          <div className="mc-num text-[12px]" style={{ color: C.muted }}>
            {burnLegend || '—'}
          </div>
        </div>
      </div>
    </Card>
  );
}

function WindowCard({ k, w, now, snapshotTs }) {
  const color = WIN_COLORS[k] || C.s1;
  const st = statusColor(w.status);
  // O numero vem do ultimo snapshot, nao de agora. Em uso pesado ele anda
  // ~1 ponto/min, entao esconder a idade faria a tecla mentir por omissao.
  const idade = snapshotTs ? Math.round((now - new Date(snapshotTs).getTime()) / 1000) : 0;
  const velho = idade > 150;
  return (
    <Card>
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="mc-label" style={{ color: C.ink2 }}>
          <span className="mr-1.5 inline-block h-2 w-2 rounded-sm align-middle" style={{ background: color }} />
          {WIN_SHORT[k] || w.label}
        </span>
        <Chip color={st}>{STATUS_LABEL[(w.status || '').toUpperCase()] || w.status || '—'}</Chip>
      </div>
      <div className="flex items-baseline gap-2">
        <span className="mc-num text-3xl font-bold">{Math.round(w.utilization)}%</span>
        {velho && (
          <span className="mc-num text-[12px]" style={{ color: C.warn }} title="idade do último snapshot da API">
            há {idade < 120 ? `${idade}s` : `${Math.round(idade / 60)}min`}
          </span>
        )}
        {w.projected != null && (
          <span className="mc-num text-[13px]" style={{ color: C.muted }}>
            → {Math.round(w.projected)}% no reset
          </span>
        )}
      </div>
      <div className="mt-3">
        <GaugeBar pct={w.utilization} projected={w.projected} color={color} />
      </div>
      <div className="mc-num mt-3 flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-[12px]" style={{ color: C.muted }}>
        <span>
          reset em{' '}
          <span className="font-semibold" style={{ color: C.ink2 }}>
            {fmtCountdown(w.resets_at, now)}
          </span>
        </span>
        {w.rate != null && <span>{w.rate.toFixed(1)} %/h</span>}
        {w.eta_100 && (
          <span style={{ color: C.crit }}>
            estoura {new Date(w.eta_100).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}
          </span>
        )}
      </div>
    </Card>
  );
}

/** licenca x tokens extras (excedente): de onde estao saindo os tokens agora */
function SourcePanel({ extra, cur }) {
  const burning = !!extra?.burning;
  const color = burning ? C.crit : C.good;
  return (
    <Card>
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="mc-label" style={{ color: C.ink2 }}>
          fonte dos tokens
        </span>
        <Chip color={color}>{burning ? 'tokens extras' : 'licença'}</Chip>
      </div>
      <div className="mc-display text-lg font-bold leading-snug">
        {burning
          ? 'Queimando tokens extras agora — isso é cobrado à parte.'
          : 'Dentro da licença — nada extra sendo cobrado.'}
      </div>
      <div className="mc-num mt-3 grid grid-cols-3 gap-3 text-[12px]" style={{ color: C.muted }}>
        <div>
          <div className="mc-label">extras no mês</div>
          <div className="text-base font-semibold" style={{ color: burning ? C.crit : C.ink2 }}>
            {fmtMoney(extra?.used, cur)}
          </div>
        </div>
        <div>
          <div className="mc-label">últimas 24h</div>
          <div className="text-base font-semibold" style={{ color: C.ink2 }}>
            {extra?.spent_24h != null ? fmtMoney(extra.spent_24h, cur) : '—'}
          </div>
        </div>
        <div>
          <div className="mc-label">média / hora</div>
          <div className="text-base font-semibold" style={{ color: C.ink2 }}>
            {extra?.rate_per_hour != null ? `${fmtMoney(extra.rate_per_hour, cur)}/h` : '—'}
          </div>
        </div>
      </div>
    </Card>
  );
}

function BurnPanel({ burn }) {
  const rows = Object.entries(burn || {})
    .filter(([, v]) => typeof v === 'number' && v > 0)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5);
  const max = rows.length ? rows[0][1] : 1;
  return (
    <Card>
      <div className="mc-label mb-3" style={{ color: C.ink2 }}>
        queima por modelo · tok/h (2h)
      </div>
      {rows.length === 0 && (
        <div className="mc-num text-[13px]" style={{ color: C.muted }}>
          sem atividade recente
        </div>
      )}
      <div className="space-y-2.5">
        {rows.map(([m, v]) => (
          <div key={m} className="flex items-center gap-3">
            <span className="mc-num w-24 shrink-0 truncate text-[12px]" style={{ color: C.ink2 }} title={m}>
              {modelShort(m)}
            </span>
            <div className="h-2 flex-1 overflow-hidden rounded-full" style={{ background: 'rgba(255,255,255,0.08)' }}>
              <div
                className="h-full rounded-full transition-[width] duration-700"
                style={{ width: `${Math.max(2, (v / max) * 100)}%`, background: C.s2 }}
              />
            </div>
            <span className="mc-num w-16 shrink-0 text-right text-[12px]" style={{ color: C.muted }}>
              {fmtTokens(v)}/h
            </span>
          </div>
        ))}
      </div>
    </Card>
  );
}

// licencas conhecidas: tabela BRASIL de claude.com/pricing (ago/2026).
// `brl` = cobranca mensal; `brlAnnual` = por mes no plano anual, quando existe.
// Max 20x nao e exibido no site ("a partir de R$ 550"); R$ 1.100 segue a
// proporcao 2x da tabela americana. Conferir quando a Anthropic reajustar.
const PLANS = {
  pro: { label: 'Pro', brl: 110, brlAnnual: 92 },
  max_5x: { label: 'Max 5×', brl: 550 },
  max_20x: { label: 'Max 20×', brl: 1100 },
  team_standard: { label: 'Time · padrão', brl: 138, brlAnnual: 110 },
  team_premium: { label: 'Time · premium', brl: 688, brlAnnual: 550 },
  enterprise: { label: 'Enterprise', brl: null },
};

function planPriceHint(p) {
  if (!p?.brl) return 'R$ 110/assento + uso cobrado a preço de API';
  const anual = p.brlAnnual ? ` · R$ ${p.brlAnnual}/mês no anual` : '';
  return `tabela Brasil: R$ ${p.brl}/mês${anual}`;
}

/**
 * Balanço licença vs consumo — os dados para a conta "está valendo a pena?".
 * Consumo equivalente = o que o mês custaria em API (subsidiado pela licença).
 * Desembolso real = licença mensal + créditos extras pagos à parte.
 */
function BalancePanel({ mes, geral, extra, sub, plan, fx, onSaveSub, onSavePlan }) {
  const [subInput, setSubInput] = useState('');
  const effectivePlan = plan?.selected || plan?.detected || null;
  const detectedInfo = plan?.detected ? PLANS[plan.detected] : null;
  const planInfo = effectivePlan ? PLANS[effectivePlan] : null;
  // sugestao: preco anual quando existe (o mais comum em contas Time), senao mensal
  const suggested = planInfo?.brlAnnual ?? planInfo?.brl ?? null;
  const equivalente = mes?.total_cost ?? null;
  const extras = extra?.used ?? 0;
  const coberto = equivalente != null ? Math.max(0, equivalente - extras) : null;
  const desembolso = (sub || 0) + extras;
  const leverage = equivalente != null && desembolso > 0 ? equivalente / desembolso : null;
  const saldo = equivalente != null ? equivalente - desembolso : null;
  const pctLic = equivalente > 0 ? (coberto / equivalente) * 100 : 0;

  return (
    <Card>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <span className="mc-label" style={{ color: C.ink2 }}>
          licença vs consumo · este mês
        </span>
        {leverage != null && (
          <Chip color={leverage >= 1 ? C.good : C.warn}>
            {leverage >= 1 ? 'compensando' : 'abaixo do pago'}
          </Chip>
        )}
      </div>

      <div className="grid gap-4 lg:grid-cols-[auto_1fr] lg:items-center">
        <div>
          <div className="mc-num text-4xl font-bold tracking-tight">
            {leverage != null ? `${leverage.toFixed(1).replace('.', ',')}×` : '—'}
          </div>
          <div className="mc-num mt-1 max-w-[36ch] text-[12px]" style={{ color: C.muted }}>
            {equivalente != null && desembolso > 0
              ? `consumo equivalente de ${costBRL(equivalente)} pagando ${costBRL(desembolso)}`
              : 'defina o valor da licença para calcular'}
          </div>
        </div>

        <div className="min-w-0">
          {equivalente != null && (
            <>
              <div className="flex h-3 w-full overflow-hidden rounded-full" style={{ background: 'rgba(255,255,255,0.08)' }}>
                <div className="h-full" style={{ width: `${pctLic}%`, background: C.s1 }} />
                <div
                  className="h-full"
                  style={{ width: `${100 - pctLic}%`, background: C.s2, marginLeft: pctLic > 0 && pctLic < 100 ? 2 : 0 }}
                />
              </div>
              <div className="mc-num mt-2 flex flex-wrap gap-x-5 gap-y-1 text-[12px]" style={{ color: C.muted }}>
                <span>
                  <span className="mr-1.5 inline-block h-2 w-2 rounded-sm align-middle" style={{ background: C.s1 }} />
                  coberto pela licença <span style={{ color: C.ink2 }}>{costBRL(coberto)}</span>
                </span>
                <span>
                  <span className="mr-1.5 inline-block h-2 w-2 rounded-sm align-middle" style={{ background: C.s2 }} />
                  créditos extras <span style={{ color: C.ink2 }}>{costBRL(extras)}</span>
                </span>
                {saldo != null && (
                  <span>
                    saldo vs API{' '}
                    <span style={{ color: saldo >= 0 ? C.good : C.crit }}>
                      {saldo >= 0 ? '+' : '−'}{costBRL(Math.abs(saldo))}
                    </span>
                  </span>
                )}
                {geral?.total_cost != null && (
                  <span>
                    vida toda (equivalente) <span style={{ color: C.ink2 }}>{costBRL(geral.total_cost)}</span>
                  </span>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      {/* seletor de licenca */}
      <div className="mt-3 border-t pt-3" style={{ borderColor: C.border }}>
        <div className="flex flex-wrap items-center gap-2">
          <span className="mc-label mr-1">sua licença</span>
          {Object.entries(PLANS).map(([id, p]) => (
            <button
              key={id}
              className="mc-pill"
              data-active={effectivePlan === id}
              title={planPriceHint(p)}
              onClick={() => onSavePlan(id)}
            >
              {p.label}
              {plan?.detected === id ? ' ✓' : ''}
            </button>
          ))}
        </div>
        {detectedInfo && (
          <div className="mc-num mt-1.5 text-[11px]" style={{ color: C.muted }}>
            identificado pela conexão com a sua conta{plan?.org_name ? ` (${plan.org_name})` : ''}:{' '}
            <span style={{ color: C.ink2 }}>{detectedInfo.label}</span>
            {plan?.rate_limit_tier ? ` · limites ${plan.rate_limit_tier.replace(/^default_/, '').replace(/_/g, ' ')}` : ''}
            {' — clique acima para alternar'}
            {plan?.selected && plan.selected !== plan.detected ? ' (você escolheu outra manualmente)' : ''}
          </div>
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-end gap-2 border-t pt-3" style={{ borderColor: C.border }}>
        <div>
          <div className="mc-label">licença mensal (R$)</div>
          <input
            className="mc-input"
            placeholder={sub != null ? String(sub) : 'ex.: 550'}
            value={subInput}
            onChange={(e) => setSubInput(e.target.value)}
          />
        </div>
        <button
          className="mc-btn"
          onClick={() => {
            const v = parseFloat(subInput.replace(',', '.'));
            if (!Number.isNaN(v) && v >= 0) onSaveSub(v);
            setSubInput('');
          }}
        >
          salvar
        </button>
        {suggested != null && (
          <button
            className="mc-btn"
            title={`tabela Brasil (${planInfo?.brlAnnual ? 'plano anual' : 'mensal'}) — mensal: R$ ${planInfo?.brl}`}
            onClick={() => onSaveSub(suggested)}
          >
            usar tabela ({fmtMoney(suggested, 'BRL')})
          </button>
        )}
        <span className="mc-num text-[11px]" style={{ color: C.muted }}>
          equivalente = preço de tabela da API; extras = cobrados à parte no mês
        </span>
      </div>
    </Card>
  );
}

function ScopeTile({ label, h, fx }) {
  return (
    <Card>
      <div className="mc-label mb-1.5">{label}</div>
      <div className="mc-num text-2xl font-bold">{fmtTokens(h?.total_tokens)}</div>
      <div className="mc-num mt-1 space-y-0.5 text-[12px]" style={{ color: C.muted }}>
        <div>{costBRL(h?.total_cost, fx)} · {fmtInt(h?.total_turns)} turnos</div>
        <div>
          {fmtTokens(h?.tokens_per_hour)}/h · {fmtInt(h?.active_hours)}h ativas
        </div>
      </div>
    </Card>
  );
}

function ModelMix({ byModel, fx }) {
  const rows = (byModel || []).slice(0, 6);
  const total = rows.reduce((a, r) => a + r.tokens, 0) || 1;
  return (
    <Card className="h-full">
      <div className="mc-label mb-3" style={{ color: C.ink2 }}>
        mix de modelos · este mês
      </div>
      <div className="space-y-2.5">
        {rows.map((r) => {
          const share = (r.tokens / total) * 100;
          return (
            <div key={r.model} className="flex items-center gap-3">
              <span className="mc-num w-24 shrink-0 truncate text-[12px]" style={{ color: C.ink2 }} title={r.model}>
                {modelShort(r.model)}
              </span>
              <div className="h-2 flex-1 overflow-hidden rounded-full" style={{ background: 'rgba(255,255,255,0.08)' }}>
                <div className="h-full rounded-full" style={{ width: `${Math.max(1.5, share)}%`, background: C.s1 }} />
              </div>
              <span className="mc-num w-24 shrink-0 text-right text-[12px]" style={{ color: C.muted }}>
                {share.toFixed(0)}% · {costBRL(r.cost, fx)}
              </span>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

// opcoes dark dos graficos chart.js
const chartBase = {
  responsive: true,
  maintainAspectRatio: false,
  interaction: { mode: 'index', intersect: false },
  plugins: {
    legend: { labels: { color: C.ink2, boxWidth: 12, boxHeight: 12, font: { size: 11 } } },
    tooltip: { backgroundColor: '#0e1750', titleColor: C.ink, bodyColor: C.ink2, borderColor: C.border, borderWidth: 1 },
  },
  scales: {
    x: { ticks: { color: C.muted, font: { size: 10 } }, grid: { color: 'transparent' }, border: { color: C.grid } },
    y: { ticks: { color: C.muted, font: { size: 10 } }, grid: { color: C.grid }, border: { display: false } },
  },
};

function DailyChart({ daily }) {
  return (
    <div className="h-52 2xl:h-60">
      <Bar
        data={{
          labels: (daily || []).map((d) => d.day.slice(5)),
          datasets: [
            {
              label: 'tokens',
              data: (daily || []).map((d) => d.tokens),
              backgroundColor: C.s1,
              borderRadius: 4,
              maxBarThickness: 22,
            },
          ],
        }}
        options={{
          ...chartBase,
          plugins: { ...chartBase.plugins, legend: { display: false } },
          scales: {
            ...chartBase.scales,
            y: { ...chartBase.scales.y, ticks: { ...chartBase.scales.y.ticks, callback: (v) => fmtTokens(v) } },
          },
        }}
      />
    </div>
  );
}

function SnapChart({ snapshots }) {
  const ts = [...new Set((snapshots || []).map((s) => s.ts))].sort();
  const datasets = Object.keys(WIN_COLORS)
    .map((w) => {
      const mm = {};
      (snapshots || []).filter((s) => s.window === w).forEach((s) => (mm[s.ts] = s.utilization));
      if (!Object.keys(mm).length) return null;
      return {
        label: WIN_SHORT[w],
        data: ts.map((t) => (t in mm ? mm[t] : null)),
        borderColor: WIN_COLORS[w],
        backgroundColor: WIN_COLORS[w],
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 4,
        spanGaps: true,
        tension: 0.3,
      };
    })
    .filter(Boolean);
  return (
    <div className="h-52 2xl:h-60">
      <Line
        data={{
          labels: ts.map((t) => new Date(t).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })),
          datasets,
        }}
        options={{
          ...chartBase,
          scales: {
            ...chartBase.scales,
            y: {
              ...chartBase.scales.y,
              min: 0,
              suggestedMax: 100,
              ticks: { ...chartBase.scales.y.ticks, callback: (v) => `${v}%` },
            },
          },
        }}
      />
    </div>
  );
}

function FooterStrip({ state, generatedAt, now }) {
  const cur = state?.config?.currency || 'BRL';
  const fx = state?.config?.usd_brl || null;
  const extra = state?.extra_usage;
  return (
    <Card className="flex flex-wrap items-center gap-x-8 gap-y-3">
      {extra && (
        <div>
          <div className="mc-label">excedente (créditos)</div>
          <div className="mc-num text-lg font-semibold">{fmtMoney(extra.used, cur)}</div>
        </div>
      )}
      {fx && (
        <div>
          <div className="mc-label">câmbio</div>
          <div className="mc-num text-lg font-semibold">
            R$ {fx.toFixed(2)}
            <span className="text-[12px] font-normal" style={{ color: C.muted }}>
              {' '}/ US$
            </span>
          </div>
        </div>
      )}
      <div>
        <div className="mc-label">limites ao vivo</div>
        <div className="mc-num text-[13px]" style={{ color: C.ink2 }}>
          a cada {state?.config?.refresh_seconds ?? 300}s
          <span className="text-[11px]" style={{ color: C.muted }}> · acelera perto do limite</span>
        </div>
      </div>
      <div className="mc-num ml-auto text-right text-[11px]" style={{ color: C.muted }}>
        estado gerado há {ago(generatedAt, now)} · roda em localhost · uso pessoal
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------- página

export default function Dashboard() {
  const [state, setState] = useState(null);
  const [total, setTotal] = useState(null);
  const [history, setHistory] = useState(null);
  const [latency, setLatency] = useState(null);
  const [aviso, setAviso] = useState(null);
  const now = useNow(1000);

  const loadState = useCallback(async () => {
    try {
      const t0 = performance.now();
      const s = await api.state();
      setLatency(performance.now() - t0);
      setState(s);
    } catch {
      setLatency(null);
    }
  }, []);
  const loadTotal = useCallback(async () => {
    try {
      setTotal(await api.total());
    } catch {
      /* mantém o último */
    }
  }, []);
  const loadHistory = useCallback(async () => {
    try {
      setHistory(await api.history());
    } catch {
      /* mantém o último */
    }
  }, []);

  useEffect(() => {
    loadState();
    loadTotal();
    loadHistory();
    const a = setInterval(loadTotal, 3000);
    const b = setInterval(loadState, 10000);
    const c = setInterval(loadHistory, 60000);
    return () => {
      clearInterval(a);
      clearInterval(b);
      clearInterval(c);
    };
  }, [loadState, loadTotal, loadHistory]);

  const refreshNow = async () => {
    try {
      const r = await api.refresh();
      if (r && r.ok === false) {
        // o backend recusou para nao furar o rate limit da conta
        setAviso(
          r.reason === 'rate_limited'
            ? `limite da conta — nova tentativa em ${r.retry_in}s`
            : `aguarde ${r.retry_in}s para atualizar de novo`,
        );
        setTimeout(() => setAviso(null), 5000);
      }
    } catch {
      /* mantem o ultimo estado */
    }
    loadState();
    loadTotal();
    loadHistory();
  };
  const setHours = async (h) => {
    await api.setConfig({ intended_hours: h });
    loadState();
  };

  const windows = state?.windows || {};
  const fx = state?.config?.usd_brl || null;
  const winKeys = ['five_hour', 'seven_day', 'seven_day_sonnet'].filter((k) => windows[k]);
  const burnMap = state?.burn_tokph || {};
  // queima total (todos os modelos, Claude Code + Science) e quebra por origem
  const totalBurn = Object.values(burnMap).reduce((a, b) => a + b, 0) || null;
  const burnModels = Object.values(burnMap).filter((v) => v > 0).length;
  const burnBySource = { code: 0, science: 0 };
  Object.values(state?.burn_by_source || {}).forEach((d) => {
    Object.entries(d).forEach(([s, v]) => {
      burnBySource[s] = (burnBySource[s] || 0) + v;
    });
  });
  const heatmap = history?.heatmap || [];

  return (
    <div className="mc-root min-h-screen" style={{ background: C.page, color: C.ink }}>
      <StatusBar
        state={state}
        latency={latency}
        now={now}
        aviso={aviso}
        onRefresh={refreshNow}
        onReconnect={() => api.authStart()}
      />

      <main className="mx-auto w-full max-w-[2400px] space-y-4 px-4 pb-12 pt-4 sm:px-6 xl:px-8">
        {/* decisão + contador vivo */}
        <section className="grid gap-4 xl:grid-cols-12">
          <div className="xl:col-span-7 2xl:col-span-8">
            <VerdictPanel
              sw={state?.switch}
              intendedHours={state?.config?.intended_hours}
              dominant={state?.dominant_model}
              onChangeHours={setHours}
            />
          </div>
          <div className="xl:col-span-5 2xl:col-span-4">
            <LiveTotal
              total={total}
              today={state?.history?.dia}
              burn={totalBurn}
              burnBySource={burnBySource}
              burnModels={burnModels}
              dominant={state?.dominant_model}
              fx={fx}
            />
          </div>
        </section>

        {/* janelas ao vivo + queima */}
        <section className="grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(290px,1fr))]">
          {winKeys.map((k) => (
            <WindowCard key={k} k={k} w={windows[k]} now={now} snapshotTs={state?.snapshot_ts} />
          ))}
          <SourcePanel extra={state?.extra_usage} cur={state?.config?.currency || 'BRL'} />
          <BurnPanel burn={burnMap} />
        </section>

        {/* licença vs consumo */}
        <BalancePanel
          mes={state?.history?.mes}
          geral={state?.history?.geral}
          extra={state?.extra_usage}
          sub={state?.config?.subscription_brl}
          plan={state?.plan}
          fx={fx}
          onSaveSub={(v) => api.setConfig({ subscription_brl: v }).then(loadState)}
          onSavePlan={(id) => api.setConfig({ plan: id }).then(loadState)}
        />

        {/* escopos históricos */}
        <section className="grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(220px,1fr))]">
          <ScopeTile label="hoje" h={state?.history?.dia} fx={fx} />
          <ScopeTile label="esta semana" h={state?.history?.semana} fx={fx} />
          <ScopeTile label="este mês" h={state?.history?.mes} fx={fx} />
          <ScopeTile label="vida toda" h={state?.history?.geral} fx={fx} />
        </section>

        {/* gráficos */}
        <section className="grid gap-4 xl:grid-cols-2 2xl:grid-cols-3">
          <Card>
            <div className="mc-label mb-3" style={{ color: C.ink2 }}>
              tokens por dia · 30d
            </div>
            <DailyChart daily={history?.daily} />
          </Card>
          <Card>
            <div className="mc-label mb-3" style={{ color: C.ink2 }}>
              utilização das janelas · 48 snapshots
            </div>
            <SnapChart snapshots={history?.snapshots} />
          </Card>
          <div className="xl:col-span-2 2xl:col-span-1">
            <ModelMix byModel={state?.history?.mes?.by_model} fx={fx} />
          </div>
        </section>

        {/* ritmo de uso */}
        {heatmap.length > 0 && (
          <Card>
            <div className="mc-label mb-3" style={{ color: C.ink2 }}>
              ritmo de uso · média de tokens por hora
            </div>
            <HeatmapChart heatmap={heatmap} />
          </Card>
        )}

        <FooterStrip state={state} generatedAt={state?.generated_at} now={now} />
      </main>
    </div>
  );
}
