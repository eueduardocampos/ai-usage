"use strict";
/**
 * digital.astronauta.claudeusage — vitrine do painel claude-usage no Stream Deck.
 *
 * Le o estado do painel local (http://127.0.0.1:8090/api/state + /api/total) e
 * mostra a metrica de cada tecla. Nao fala com a API da Anthropic: a coleta e do
 * painel, o plugin so exibe.
 *
 * v3: uma ACAO POR METRICA. Cada tecla/dial da lista do Stream Deck ja vem com o
 * dado fixo — arrastou, funcionou. A acao generica antiga (".window") continua
 * existindo por compatibilidade com perfis antigos, com metric/mode via settings.
 *
 * Config global (todas as acoes): panelUrl, refreshSeconds, polaridade e limiares.
 * Roda igual no macOS e no Windows: Node puro, zero dependencias.
 */

const http = require("http");
const { spawn } = require("child_process");
const { MiniWebSocket } = require("./ws");
const R = require("./render");
const U = require("./unified");

// ---------------------------------------------------------------- argumentos

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith("-")) out[a.replace(/^-+/, "")] = argv[++i];
  }
  return out;
}

const args = parseArgs(process.argv.slice(2));
const PORT = Number(args.port);
const PLUGIN_UUID = args.pluginUUID;
const REGISTER_EVENT = args.registerEvent;

function log(...parts) {
  console.log(`[claude-usage] ${parts.join(" ")}`);
}

// ---------------------------------------------------------------- acoes

const NS = "digital.astronauta.claudeusage";

/**
 * Preset por acao. `metric`/`mode` fixos para teclas; `dial` para encoders.
 * A acao legada `.window` fica sem preset e continua lendo settings.
 */
const ACTION_PRESETS = {
  [`${NS}.auto`]: { metric: "auto", mode: "ring" },
  [`${NS}.dualwin`]: { metric: "both", mode: "dual" },
  [`${NS}.source`]: { metric: "source", mode: "plain" },
  [`${NS}.eta`]: { metric: "eta_100", mode: "plain" },
  [`${NS}.switch`]: { metric: "switch", mode: "plain" },
  [`${NS}.costday`]: { metric: "cost_day", mode: "plain" },
  [`${NS}.balance`]: { metric: "balance", mode: "plain" },
  [`${NS}.burn`]: { metric: "burn", mode: "plain" },
  [`${NS}.dialhorizon`]: { dial: "horizon" },
  [`${NS}.dialwindows`]: { dial: "windows" },
  [`${NS}.dialcosts`]: { dial: "costs" },
  [`${NS}.diallife`]: { dial: "life" },
};

const AI_KEYS = ['claude5','claude7','codexquota','sparkquota','claudeburn','codexburn','clauderoi','codexroi'];
const AI_DIALS = ['aiwindows','aiburn','aicosts','aitotals'];
for (const metric of AI_KEYS) ACTION_PRESETS[`${NS}.${metric}`] = {metric,mode:'auto'};
for (const dial of AI_DIALS) ACTION_PRESETS[`${NS}.${dial}`] = {dial};

// ---------------------------------------------------------------- config

const WINDOW_META = {
  five_hour: { short: "5H", label: "Sessao 5h" },
  seven_day: { short: "WK", label: "Semana 7d" },
  seven_day_opus: { short: "OP", label: "Opus 7d" },
  seven_day_sonnet: { short: "SO", label: "Sonnet 7d" },
};

const HORIZONS = [
  [0.5, "30m"], [1, "1h"], [2, "2h"], [3, "3h"], [4, "4h"],
];

const DEFAULT_GLOBAL = {
  panelUrl: "http://127.0.0.1:8090",
  refreshSeconds: 10,
  percent: "used", // "used" | "remaining"
  yellow: 80,
  red: 100,
  critical: 110,
  flicker: false,
  showResetTime: true,
};

/** Ao trocar a polaridade, os limiares padrao trocam junto. */
const DEFAULT_THRESHOLDS = {
  used: { yellow: 80, red: 100, critical: 110 },
  remaining: { yellow: 50, red: 20, critical: 10 },
};

let global = { ...DEFAULT_GLOBAL };

function cfg() {
  return global;
}

// ---------------------------------------------------------------- estado

let sd = null;
const contexts = new Map(); // context -> { action, controller, settings }
let latest = null; // /api/state
let latestTotal = null; // /api/total
let lastError = null;
let pollTimer = null;
let flickerTimer = null;
let flickerOn = true;

function settingsFor(context) {
  const entry = contexts.get(context) || {};
  const s = entry.settings || {};
  const preset = ACTION_PRESETS[entry.action];
  if (preset && preset.metric) {
    return { metric: preset.metric, mode: preset.mode, histStyle: s.histStyle || "off" };
  }
  return {
    metric: s.metric || "auto",
    mode: s.mode || "auto",
    histStyle: s.histStyle || "off",
  };
}

function dialKind(context) {
  const entry = contexts.get(context) || {};
  const preset = ACTION_PRESETS[entry.action];
  return preset && preset.dial ? preset.dial : null;
}

function pollInterval() {
  return Math.max(2, Number(cfg().refreshSeconds) || 10) * 1000;
}

// ---------------------------------------------------------------- protocolo

function send(payload) {
  if (sd) sd.send(JSON.stringify(payload));
}

function setImage(context, svgString) {
  send({ event: "setImage", context, payload: { image: R.toDataUri(svgString), target: 0 } });
}

function setTitle(context, title) {
  send({ event: "setTitle", context, payload: { title, target: 0 } });
}

function setFeedback(context, payload) {
  send({ event: "setFeedback", context, payload });
}

function showAlert(context) {
  send({ event: "showAlert", context });
}

function showOk(context) {
  send({ event: "showOk", context });
}

// ---------------------------------------------------------------- nivel e limiares

/**
 * Converte um percentual USADO no nivel de alerta, respeitando a polaridade.
 * Unico lugar do plugin que olha limiar (com "remaining" a comparacao inverte).
 */
function levelForUsed(usedPct) {
  if (typeof usedPct !== "number" || !isFinite(usedPct)) return "offline";
  const c = cfg();
  if (c.percent === "remaining") {
    const rem = 100 - usedPct;
    if (rem <= c.critical) return "critical";
    if (rem <= c.red) return "danger";
    if (rem <= c.yellow) return "warn";
    return "safe";
  }
  if (usedPct >= c.critical) return "critical";
  if (usedPct >= c.red) return "danger";
  if (usedPct >= c.yellow) return "warn";
  return "safe";
}

/** O numero que vai na tela, ja na polaridade escolhida. */
function displayPct(usedPct) {
  return cfg().percent === "remaining" ? 100 - usedPct : usedPct;
}

// ---------------------------------------------------------------- formatacao

function fmtReset(hours) {
  if (typeof hours !== "number" || !isFinite(hours)) return "";
  if (hours >= 24) return `${Math.round(hours / 24)}d`;
  if (hours >= 1) return `${Math.floor(hours)}h`;
  return `${Math.max(1, Math.round(hours * 60))}m`;
}

function fmtBRL(v) {
  if (typeof v !== "number" || !isFinite(v)) return "--";
  if (v >= 1000) return `R$ ${(v / 1000).toFixed(1)}k`;
  return `R$ ${Math.round(v)}`;
}

function fmtTokens(v) {
  if (typeof v !== "number" || !isFinite(v)) return "--";
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `${Math.round(v / 1e3)}k`;
  return String(Math.round(v));
}

function fmtClock(iso) {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "--";
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

// ---------------------------------------------------------------- views

/**
 * Estado "sem dado", com o motivo. Nunca deixa a tecla em branco.
 */
function offlineView(label) {
  const why = !latest ? "painel" : latest.auth_connected === false ? "reconectar" : "sem dado";
  const value = !latest ? "OFF" : "--";
  return { kind: "plain", label, value, sub: why, level: "offline", pct: null };
}

function windowsAvailable() {
  if (!latest || !latest.windows) return [];
  return Object.keys(latest.windows).filter((k) => typeof latest.windows[k].utilization === "number");
}

/** Janela com menos folga (maior projecao de uso). */
function worstWindowKey() {
  const keys = windowsAvailable();
  if (!keys.length) return "five_hour";
  return keys.reduce((worst, k) => {
    const a = latest.windows[k];
    const b = latest.windows[worst];
    const av = typeof a.projected === "number" ? a.projected : a.utilization;
    const bv = typeof b.projected === "number" ? b.projected : b.utilization;
    return av > bv ? k : worst;
  }, keys[0]);
}

/** View de uma janela. `stale` marca dado velho com "!" em vez de mentir. */
function windowView(key) {
  const meta = WINDOW_META[key] || { short: "??", label: key };
  const win = latest && latest.windows ? latest.windows[key] : null;
  if (!win || typeof win.utilization !== "number") return offlineView(meta.short);

  const used = win.utilization;
  const proj = typeof win.projected === "number" ? win.projected : null;
  const stale = latest.auth_connected === false;
  const shown = Math.round(displayPct(used));

  // Cor sempre pelo projetado — e o que muda decisao, nao o valor atual.
  const level = stale ? "offline" : levelForUsed(proj === null ? used : proj);

  const bits = [];
  if (proj !== null) bits.push(`proj ${Math.round(displayPct(proj))}%`);
  if (cfg().showResetTime && win.hours_to_reset != null) bits.push(fmtReset(win.hours_to_reset));

  return {
    kind: "pct",
    label: meta.short,
    value: `${shown}%${stale ? "!" : ""}`,
    sub: stale ? "desconectado" : bits.join("  "),
    level,
    pct: shown,
    barPct: proj === null ? shown : Math.round(displayPct(proj)),
  };
}

/** Quando estoura a janela — so faz sentido se o estouro cai ANTES do reset. */
function etaView() {
  if (!latest) return offlineView("ESTOURO");
  const keys = windowsAvailable();
  if (!keys.length) return offlineView("ESTOURO");

  let soonest = null;
  for (const k of keys) {
    const w = latest.windows[k];
    if (!w.eta_100 || !w.resets_at) continue;
    const eta = new Date(w.eta_100).getTime();
    const reset = new Date(w.resets_at).getTime();
    if (!isFinite(eta) || !isFinite(reset)) continue;
    if (eta >= reset) continue; // nao estoura esta janela
    if (!soonest || eta < soonest.eta) soonest = { eta, key: k };
  }

  if (!soonest) {
    return { kind: "plain", label: "ESTOURO", value: "OK", sub: "nao estoura", level: "safe" };
  }

  const horas = (soonest.eta - Date.now()) / 3600000;
  const level = horas < 1 ? "critical" : horas < 3 ? "danger" : "warn";
  return {
    kind: "plain",
    label: "ESTOURO",
    value: fmtClock(new Date(soonest.eta).toISOString()),
    sub: `${WINDOW_META[soonest.key]?.short || soonest.key} em ${fmtReset(horas)}`,
    level,
  };
}

function switchView() {
  if (!latest || !latest.switch) return offlineView("MODELO");
  const s = latest.switch;
  const v = String(s.verdict || "").toUpperCase();
  const level = v.includes("PERIGO") || v.includes("RISCO") ? "danger" : v.includes("ATEN") ? "warn" : "safe";
  const alvo = String(s.target || latest.dominant_model || "?").replace(/^claude-/, "").split("-")[0];
  return {
    kind: "plain",
    label: "MODELO",
    value: alvo.toUpperCase(),
    sub: v || "",
    level,
  };
}

function costView(scope) {
  if (!latest) return offlineView("CUSTO");

  if (scope === "extra") {
    const e = latest.extra_usage || {};
    if (typeof e.used !== "number") return offlineView("EXTRA");
    return { kind: "plain", label: "EXTRA", value: fmtBRL(e.used), sub: e.currency || "", level: "safe" };
  }

  const h = (latest.history || {})[scope];
  if (!h || typeof h.total_cost !== "number") return offlineView("CUSTO");

  const rotulo = { dia: "HOJE", semana: "SEMANA", mes: "MES" }[scope] || scope.toUpperCase();
  const bits = [];
  if (scope === "mes") {
    const extra = (latest.extra_usage || {}).used;
    if (typeof extra === "number" && extra > 0) bits.push(`+${fmtBRL(extra)} extra`);
  }
  if (!bits.length && typeof h.total_turns === "number") bits.push(`${h.total_turns} turnos`);

  return { kind: "plain", label: rotulo, value: fmtBRL(h.total_cost), sub: bits.join(" "), level: "safe" };
}

/** Fonte dos tokens: dentro da licenca ou queimando creditos extras. */
function sourceView() {
  if (!latest) return offlineView("FONTE");
  const e = latest.extra_usage;
  if (!e) return offlineView("FONTE");
  if (e.burning) {
    const rate = typeof e.rate_per_hour === "number" && e.rate_per_hour > 0
      ? `${fmtBRL(e.rate_per_hour)}/h` : null;
    return {
      kind: "plain",
      label: "FONTE",
      value: "EXTRAS",
      sub: rate || (typeof e.used === "number" ? `${fmtBRL(e.used)} no mes` : ""),
      level: "critical",
    };
  }
  return {
    kind: "plain",
    label: "FONTE",
    value: "LICENCA",
    sub: typeof e.used === "number" && e.used > 0 ? `${fmtBRL(e.used)} extras/mes` : "sem extras",
    level: "safe",
  };
}

/** Balanco do mes: alavancagem consumo equivalente / desembolso. */
function balanceView() {
  if (!latest) return offlineView("BALANCO");
  const eq = latest.history && latest.history.mes ? latest.history.mes.total_cost : null;
  const extras = latest.extra_usage && typeof latest.extra_usage.used === "number"
    ? latest.extra_usage.used : 0;
  const sub = latest.config && typeof latest.config.subscription_brl === "number"
    ? latest.config.subscription_brl : null;
  if (typeof eq !== "number") return offlineView("BALANCO");
  const desembolso = (sub || 0) + extras;
  if (desembolso <= 0) {
    return { kind: "plain", label: "BALANCO", value: fmtBRL(eq), sub: "defina a licenca no painel", level: "offline" };
  }
  const lev = eq / desembolso;
  return {
    kind: "plain",
    label: "BALANCO",
    value: `${lev.toFixed(1).replace(".", ",")}x`,
    sub: `${fmtBRL(eq)} por ${fmtBRL(desembolso)}`,
    level: lev >= 1 ? "safe" : "warn",
  };
}

function burnView() {
  if (!latest) return offlineView("QUEIMA");
  // Queimando extras: a tecla vira dinheiro — o tok/h nao importa nessa hora.
  const e = latest.extra_usage;
  if (e && e.burning && typeof e.rate_per_hour === "number" && e.rate_per_hour > 0) {
    return {
      kind: "plain",
      label: "QUEIMA",
      value: `${fmtBRL(e.rate_per_hour)}/h`,
      sub: "tokens extras",
      level: "critical",
    };
  }
  if (!latest.burn_tokph) return offlineView("QUEIMA");
  const modelo = latest.dominant_model;
  const v = latest.burn_tokph[modelo] ?? Object.values(latest.burn_tokph)[0];
  if (typeof v !== "number") return offlineView("QUEIMA");
  return {
    kind: "plain",
    label: "QUEIMA",
    value: `${fmtTokens(v)}/h`,
    sub: String(modelo || "").replace(/^claude-/, ""),
    level: "safe",
  };
}

function dualView() {
  const keys = windowsAvailable();
  if (!keys.length) return offlineView("5H+WK");
  const rows = keys.slice(0, 2).map((k) => {
    const v = windowView(k);
    return { label: v.label, value: v.value, sub: v.sub.split("  ").pop() || "", level: v.level, pct: v.barPct ?? v.pct };
  });
  return { kind: "dual", rows };
}

/** Traduz `metric` na view. */
function buildView(metric) {
  if (AI_KEYS.includes(metric)) return U.view(latest, metric, cfg().percent === "remaining");
  switch (metric) {
    case "auto":
      return windowView(worstWindowKey());
    case "five_hour":
    case "seven_day":
    case "seven_day_opus":
    case "seven_day_sonnet":
      return windowView(metric);
    case "both":
      return dualView();
    case "eta_100":
      return etaView();
    case "switch":
      return switchView();
    case "source":
      return sourceView();
    case "balance":
      return balanceView();
    case "cost_day":
      return costView("dia");
    case "cost_week":
      return costView("semana");
    case "cost_month":
      return costView("mes");
    case "cost_extra":
      return costView("extra");
    case "burn":
      return burnView();
    default:
      return windowView(worstWindowKey());
  }
}

/** O desenho e resolvido pelo `kind` da view, nao pela metrica pedida. */
function resolveMode(viewKind, wanted) {
  if (viewKind === "dual") return "dual";
  if (viewKind === "plain") return "plain";
  if (wanted === "bar" || wanted === "ring") return wanted;
  return "ring";
}

// ---------------------------------------------------------------- dials

const COST_SCOPES = [
  ["dia", "HOJE"], ["semana", "SEMANA"], ["mes", "MES"], ["extra", "EXTRA"],
];
const LIFE_FIELDS = [
  ["tokens", "TOKENS"], ["custo", "CUSTO"], ["turnos", "TURNOS"],
];

function wrapIdx(i, len) {
  return ((i % len) + len) % len;
}

function dialFeedback(context) {
  const kind = dialKind(context);
  const entry = contexts.get(context) || {};
  const s = entry.settings || {};

  if (AI_DIALS.includes(kind)) {
    const items = U.dialItems(latest, kind, cfg().percent === "remaining");
    const v = items[wrapIdx(s.aiIndex || 0, items.length || 1)] || {label:'IA',value:'--',level:'offline'};
    return {canvas: R.toDataUri(R.clean(v, true))};
  }

  if (kind === "windows") {
    const key = s.windowKey && windowsAvailable().includes(s.windowKey)
      ? s.windowKey : worstWindowKey();
    const v = windowView(key);
    return {
      title: `${WINDOW_META[key]?.label || key}`,
      value: String(v.value ?? "--"),
      indicator: {
        value: typeof v.barPct === "number" ? v.barPct : 0,
        bar_fill_c: R.colorForLevel(v.level),
      },
    };
  }

  if (kind === "costs") {
    const [scope, rotulo] = COST_SCOPES[wrapIdx(Number.isInteger(s.scopeIdx) ? s.scopeIdx : 0, COST_SCOPES.length)];
    const v = costView(scope);
    return {
      title: rotulo,
      value: String(v.value ?? "--"),
      indicator: { value: 0, bar_fill_c: R.colorForLevel(v.level) },
    };
  }

  if (kind === "life") {
    const [field, rotulo] = LIFE_FIELDS[wrapIdx(Number.isInteger(s.fieldIdx) ? s.fieldIdx : 0, LIFE_FIELDS.length)];
    if (!latestTotal) return { title: "VIDA TODA", value: latest ? "--" : "OFF", indicator: { value: 0 } };
    const value = field === "tokens" ? fmtTokens(latestTotal.total_tokens)
      : field === "custo" ? fmtBRL(latestTotal.total_cost)
      : fmtTokens(latestTotal.total_turns);
    return { title: `VIDA · ${rotulo}`, value, indicator: { value: 0, bar_fill_c: R.colorForLevel("safe") } };
  }

  if (kind === "horizon") {
    const h = latest && latest.config ? latest.config.intended_hours : null;
    const meta = HORIZONS.find(([v]) => v === h);
    const label = meta ? meta[1] : h != null ? `${h}h` : "--";
    const sw = latest && latest.switch ? latest.switch : null;
    const verdict = sw ? String(sw.verdict || "").toUpperCase() : "";
    // pior projecao entre as janelas do veredito, para a barra
    let worst = 0;
    if (sw && sw.windows) {
      for (const w of Object.values(sw.windows)) {
        if (typeof w.projected === "number" && w.projected > worst) worst = w.projected;
      }
    }
    return {
      title: `HORIZONTE ${label}`,
      value: verdict || (latest ? "--" : "OFF"),
      indicator: {
        value: Math.round(Math.min(100, worst)),
        bar_fill_c: R.colorForLevel(levelForUsed(worst)),
      },
    };
  }

  // legado: acao .window colocada num encoder
  const v = buildView(settingsFor(context).metric);
  const linha = v.kind === "dual" ? v.rows[0] : v;
  return {
    title: linha.label || "",
    value: String(linha.value ?? "--"),
    indicator: {
      value: typeof linha.pct === "number" ? Math.round(linha.pct) : 0,
      bar_fill_c: R.colorForLevel(linha.level),
    },
  };
}

// ---------------------------------------------------------------- render

function render(context) {
  const entry = contexts.get(context);
  if (!entry) return;

  if (entry.controller === "Encoder") {
    setFeedback(context, dialFeedback(context));
    return;
  }

  const { metric, mode, histStyle } = settingsFor(context);
  const view = buildView(metric);
  if (AI_KEYS.includes(metric) || metric === 'source') {
    setImage(context, R.clean(view));
    setTitle(context, '');
    return;
  }
  const resolved = resolveMode(view.kind, mode);

  // Flicker apaga a tecla em ciclos alternados, so no estado critico.
  if (cfg().flicker && view.level === "critical" && !flickerOn) {
    setImage(context, R.blank("critical"));
    setTitle(context, "");
    return;
  }

  setImage(context, R.draw(resolved, { ...view, pct: view.barPct ?? view.pct, hist: null, histStyle }));
  setTitle(context, "");
}

function renderAll() {
  for (const context of contexts.keys()) render(context);
}

/** So pisca se existir alguma tecla critica; senao o timer nem roda. */
function updateFlicker() {
  const precisa =
    cfg().flicker &&
    [...contexts.entries()].some(([c, entry]) => {
      if (entry.controller === "Encoder") return false;
      return buildView(settingsFor(c).metric).level === "critical";
    });

  if (precisa && !flickerTimer) {
    flickerTimer = setInterval(() => {
      flickerOn = !flickerOn;
      renderAll();
    }, 700);
  } else if (!precisa && flickerTimer) {
    clearInterval(flickerTimer);
    flickerTimer = null;
    flickerOn = true;
  }
}

// ---------------------------------------------------------------- coleta

function baseUrl() {
  return String(cfg().panelUrl).replace(/\/+$/, "");
}

function getJSON(path, cb) {
  const req = http.get(`${baseUrl()}${path}`, { timeout: 4000 }, (res) => {
    if (res.statusCode !== 200) {
      res.resume();
      cb(new Error(`HTTP ${res.statusCode}`));
      return;
    }
    let body = "";
    res.setEncoding("utf8");
    res.on("data", (c) => (body += c));
    res.on("end", () => {
      try {
        cb(null, JSON.parse(body));
      } catch (err) {
        cb(new Error(`JSON invalido: ${err.message}`));
      }
    });
  });
  req.on("timeout", () => req.destroy(new Error("timeout")));
  req.on("error", (err) => cb(err));
}

function fetchState() {
  getJSON("/api/state", (err, data) => {
    if (err) {
      latest = null;
      lastError = err.message;
    } else {
      latest = data;
      lastError = null;
    }
    renderAll();
    updateFlicker();
  });
  getJSON("/api/total", (err, data) => {
    latestTotal = err ? null : data;
    for (const c of contexts.keys()) {
      if (dialKind(c) === "life") render(c);
    }
  });
}

/** POST /api/config — usado pelo dial de horizonte. */
function postConfig(body, cb) {
  const data = JSON.stringify(body || {});
  let u;
  try {
    u = new URL(`${baseUrl()}/api/config`);
  } catch {
    if (cb) cb(new Error("panelUrl invalida"));
    return;
  }
  const req = http.request(
    {
      hostname: u.hostname,
      port: u.port,
      path: u.pathname,
      method: "POST",
      headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(data) },
      timeout: 4000,
    },
    (res) => {
      res.resume();
      res.on("end", () => cb && cb(null));
    }
  );
  req.on("timeout", () => req.destroy(new Error("timeout")));
  req.on("error", (err) => cb && cb(err));
  req.write(data);
  req.end();
}

function restartPolling() {
  if (pollTimer) clearInterval(pollTimer);
  if (contexts.size === 0) {
    pollTimer = null;
    return;
  }
  fetchState();
  pollTimer = setInterval(fetchState, pollInterval());
}

// ---------------------------------------------------------------- acoes

function openPanel(context) {
  const url = cfg().panelUrl;
  const cmd = process.platform === "darwin" ? "open" : process.platform === "win32" ? "cmd" : "xdg-open";
  const cmdArgs = process.platform === "win32" ? ["/c", "start", "", url] : [url];
  try {
    spawn(cmd, cmdArgs, { detached: true, stdio: "ignore" }).unref();
  } catch (err) {
    log("falha ao abrir o painel:", err.message);
    showAlert(context);
  }
}

/** Dial de janelas: gira entre as janelas que a conta realmente tem. */
function cycleWindow(context, ticks) {
  const entry = contexts.get(context);
  if (!entry) return;
  const disponiveis = windowsAvailable();
  if (disponiveis.length < 2) return;

  const atual = (entry.settings || {}).windowKey || worstWindowKey();
  const idx = Math.max(0, disponiveis.indexOf(atual));
  const proxima = disponiveis[wrapIdx(idx + ticks, disponiveis.length)];

  entry.settings = { ...(entry.settings || {}), windowKey: proxima };
  send({ event: "setSettings", context, payload: entry.settings });
  render(context);
}

/** Dial de horizonte: gira o "pretendo trabalhar mais" e grava no painel. */
function cycleHorizon(context, ticks) {
  const atual = latest && latest.config ? latest.config.intended_hours : 2;
  let idx = HORIZONS.findIndex(([v]) => v === atual);
  if (idx < 0) idx = 2;
  const next = HORIZONS[wrapIdx(idx + ticks, HORIZONS.length)];
  // otimista: atualiza local para o strip responder na hora
  if (latest && latest.config) latest.config.intended_hours = next[0];
  render(context);
  postConfig({ intended_hours: next[0] }, (err) => {
    if (err) {
      showAlert(context);
      return;
    }
    fetchState(); // o veredito recalcula no painel
  });
}

/** Dials de indice simples (custos, vida toda). */
function cycleIndex(context, ticks, key, len) {
  const entry = contexts.get(context);
  if (!entry) return;
  const s = entry.settings || {};
  const atual = Number.isInteger(s[key]) ? s[key] : 0;
  entry.settings = { ...s, [key]: wrapIdx(atual + ticks, len) };
  send({ event: "setSettings", context, payload: entry.settings });
  render(context);
}

function onRotate(context, ticks) {
  const kind = dialKind(context);
  if (AI_DIALS.includes(kind)) return cycleIndex(context, ticks, "aiIndex", Math.max(1,U.dialItems(latest,kind).length));
  if (kind === "horizon") return cycleHorizon(context, ticks);
  if (kind === "costs") return cycleIndex(context, ticks, "scopeIdx", COST_SCOPES.length);
  if (kind === "life") return cycleIndex(context, ticks, "fieldIdx", LIFE_FIELDS.length);
  // "windows" e o encoder legado giram janela
  return cycleWindow(context, ticks);
}

// ---------------------------------------------------------------- global settings

function applyGlobal(raw) {
  const s = raw || {};
  const polaridade = s.percent === "remaining" ? "remaining" : "used";
  const padrao = DEFAULT_THRESHOLDS[polaridade];

  global = {
    panelUrl: (s.panelUrl || DEFAULT_GLOBAL.panelUrl).replace(/\/+$/, ""),
    refreshSeconds: Math.max(2, Number(s.refreshSeconds) || DEFAULT_GLOBAL.refreshSeconds),
    percent: polaridade,
    yellow: Number.isFinite(Number(s.yellow)) ? Number(s.yellow) : padrao.yellow,
    red: Number.isFinite(Number(s.red)) ? Number(s.red) : padrao.red,
    critical: Number.isFinite(Number(s.critical)) ? Number(s.critical) : padrao.critical,
    flicker: !!s.flicker,
    showResetTime: s.showResetTime !== false,
  };
}

// ---------------------------------------------------------------- conexao

function connect() {
  sd = new MiniWebSocket(PORT);

  sd.on("open", () => {
    send({ event: REGISTER_EVENT, uuid: PLUGIN_UUID });
    send({ event: "getGlobalSettings", context: PLUGIN_UUID });
    log("registrado");
  });

  sd.on("message", (raw) => {
    let msg;
    try {
      msg = JSON.parse(raw);
    } catch {
      return;
    }
    const { event, action, context, payload } = msg;

    switch (event) {
      case "didReceiveGlobalSettings":
        applyGlobal(payload?.settings);
        renderAll();
        restartPolling();
        updateFlicker();
        break;

      case "willAppear":
        contexts.set(context, {
          action: action || `${NS}.window`,
          controller: payload?.controller || "Keypad",
          settings: payload?.settings || {},
        });
        render(context);
        restartPolling();
        break;

      case "willDisappear":
        contexts.delete(context);
        restartPolling();
        updateFlicker();
        break;

      case "didReceiveSettings": {
        const entry = contexts.get(context);
        if (entry) {
          entry.settings = payload?.settings || {};
          render(context);
          updateFlicker();
        }
        break;
      }

      case "dialRotate":
        onRotate(context, payload?.ticks > 0 ? 1 : -1);
        break;

      case "touchTap":
        fetchState();
        showOk(context);
        break;

      case "keyDown":
      case "dialDown":
        openPanel(context);
        break;

      default:
        break;
    }
  });

  sd.on("error", (err) => log("erro de socket:", err.message));
  sd.on("close", () => {
    log("socket fechado, encerrando");
    process.exit(0);
  });
}

if (!PORT || !PLUGIN_UUID || !REGISTER_EVENT) {
  console.error("[claude-usage] argumentos de registro ausentes; o app do Stream Deck os passa.");
  process.exit(1);
}

connect();

// Exportado so para o harness de teste (streamdeck/test/fake_sd.js).
module.exports = {
  buildView,
  levelForUsed,
  resolveMode,
  applyGlobal,
  ACTION_PRESETS,
  __setLatest: (v) => (latest = v),
  __setTotal: (v) => (latestTotal = v),
};
