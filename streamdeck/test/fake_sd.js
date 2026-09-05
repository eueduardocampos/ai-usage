"use strict";
/**
 * fake_sd.js — Stream Deck falso para validar o plugin sem hardware.
 *
 * Sobe (1) um painel falso com /api/state, /api/total e /api/config e (2) um
 * servidor WebSocket que fala o protocolo real do Stream Deck. Spawna o
 * plugin.js de verdade, manda willAppear para TODAS as acoes do manifest,
 * gira os dials e confere o que o plugin devolve (setImage/setFeedback e o
 * POST do dial de horizonte).
 *
 * Uso: node streamdeck/test/fake_sd.js   (exit 0 = tudo passou)
 */

const http = require("http");
const net = require("net");
const crypto = require("crypto");
const path = require("path");
const { spawn } = require("child_process");

const PLUGIN = path.join(__dirname, "..", "digital.astronauta.claudeusage.sdPlugin", "bin", "plugin.js");
const GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";
const NS = "digital.astronauta.claudeusage";

// ---------------------------------------------------------------- painel falso

const FAKE_STATE = {
  generated_at: new Date().toISOString(),
  snapshot_ts: new Date().toISOString(),
  auth_connected: true,
  last_error: null,
  windows: {
    five_hour: { utilization: 42, projected: 55, status: "SEGURO", resets_at: new Date(Date.now() + 2 * 3600e3).toISOString(), hours_to_reset: 2, rate: 6.5, eta_100: null },
    seven_day: { utilization: 61, projected: 88, status: "ATENCAO", resets_at: new Date(Date.now() + 30 * 3600e3).toISOString(), hours_to_reset: 30, rate: 0.9, eta_100: new Date(Date.now() + 20 * 3600e3).toISOString() },
  },
  switch: { verdict: "ATENCAO", message: "Da, mas com cuidado.", target: "opus", windows: { five_hour: { projected: 58, status: "SEGURO" }, seven_day: { projected: 90, status: "ATENCAO" } } },
  burn_tokph: { "claude-opus-5": 22000000 },
  dominant_model: "claude-opus-5",
  extra_usage: { used: 618.44, limit: null, currency: "BRL", rate_per_hour: 98.2, spent_24h: 281.4, burning: true },
  history: {
    dia: { total_cost: 92.14, total_turns: 955, total_tokens: 156e6 },
    semana: { total_cost: 838.51, total_turns: 6094, total_tokens: 2.12e9 },
    mes: { total_cost: 1797.81, total_turns: 12960, total_tokens: 3.62e9 },
    geral: { total_cost: 8392.75, total_turns: 58908, total_tokens: 15.52e9 },
  },
  config: { refresh_seconds: 120, intended_hours: 2, currency: "BRL", subscription_brl: 550 },
};

FAKE_STATE.config.chatgpt_subscription_brl = 555;
FAKE_STATE.config.usd_brl = 5;
FAKE_STATE.chatgpt = {
 limits: [{id:'codex',snapshot_ts:new Date().toISOString(),primary:{used_percent:7,window_minutes:10080,resets_at:new Date(Date.now()+86400e3).toISOString()}}],
 history: Object.fromEntries(['dia','semana','mes','geral'].map(k=>[k,{total_tokens:1e6,equivalent_usd:20,unpriced_tokens:10}])),
 burn_by_model:{'gpt-6-astra':1e6},
};

const FAKE_TOTAL = { total_tokens: 15.52e9, total_cost: 8392.75, total_turns: 58908 };

let configPosts = [];

const panel = http.createServer((req, res) => {
  if (req.method === "POST" && req.url === "/api/config") {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      try {
        configPosts.push(JSON.parse(body));
      } catch {}
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end('{"ok": true}');
    });
    return;
  }
  const data = req.url.startsWith("/api/total") ? FAKE_TOTAL : FAKE_STATE;
  res.writeHead(200, { "Content-Type": "application/json" });
  res.end(JSON.stringify(data));
});

// ---------------------------------------------------------------- ws server minimo

function wsAccept(key) {
  return crypto.createHash("sha1").update(key + GUID).digest("base64");
}

function writeFrame(socket, text) {
  const payload = Buffer.from(text, "utf8");
  const len = payload.length;
  let header;
  if (len < 126) {
    header = Buffer.alloc(2);
    header[1] = len;
  } else if (len < 65536) {
    header = Buffer.alloc(4);
    header[1] = 126;
    header.writeUInt16BE(len, 2);
  } else {
    header = Buffer.alloc(10);
    header[1] = 127;
    header.writeBigUInt64BE(BigInt(len), 2);
  }
  header[0] = 0x81; // FIN + text (servidor nao mascara)
  socket.write(Buffer.concat([header, payload]));
}

function makeFrameReader(onMessage) {
  let buffer = Buffer.alloc(0);
  return (chunk) => {
    buffer = Buffer.concat([buffer, chunk]);
    for (;;) {
      if (buffer.length < 2) return;
      const opcode = buffer[0] & 0x0f;
      const masked = (buffer[1] & 0x80) !== 0;
      let len = buffer[1] & 0x7f;
      let off = 2;
      if (len === 126) {
        if (buffer.length < off + 2) return;
        len = buffer.readUInt16BE(off);
        off += 2;
      } else if (len === 127) {
        if (buffer.length < off + 8) return;
        len = Number(buffer.readBigUInt64BE(off));
        off += 8;
      }
      let mask = null;
      if (masked) {
        if (buffer.length < off + 4) return;
        mask = buffer.subarray(off, off + 4);
        off += 4;
      }
      if (buffer.length < off + len) return;
      let payload = Buffer.from(buffer.subarray(off, off + len));
      if (mask) for (let i = 0; i < payload.length; i++) payload[i] ^= mask[i % 4];
      buffer = buffer.subarray(off + len);
      if (opcode === 0x1) onMessage(payload.toString("utf8"));
      if (opcode === 0x8) return; // close
    }
  };
}

// ---------------------------------------------------------------- teste

const KEY_ACTIONS = ["claude5","claude7","codexquota","sparkquota","claudeburn","codexburn","clauderoi","codexroi","auto", "dualwin", "source", "eta", "switch", "costday", "balance", "burn", "window"];
const DIAL_ACTIONS = ["aiwindows","aiburn","aicosts","aitotals","dialhorizon", "dialwindows", "dialcosts", "diallife"];

const received = { setImage: new Map(), setFeedback: new Map(), showOk: 0 };
let pluginSocket = null;
let child = null;
let failures = [];

function assert(cond, label) {
  if (cond) console.log(`  PASS  ${label}`);
  else {
    console.log(`  FAIL  ${label}`);
    failures.push(label);
  }
}

function sendToPlugin(obj) {
  writeFrame(pluginSocket, JSON.stringify(obj));
}

function finish() {
  console.log("\n== resultado ==");
  KEY_ACTIONS.forEach((a) =>
    assert(received.setImage.has(`ctx-${a}`), `tecla .${a} desenhou (setImage)`)
  );
  DIAL_ACTIONS.forEach((a) =>
    assert(received.setFeedback.has(`ctx-${a}`), `dial .${a} desenhou (setFeedback)`)
  );

  const fbH = received.setFeedback.get("ctx-dialhorizon") || {};
  assert(String(fbH.title || "").startsWith("HORIZONTE"), `dial horizonte com titulo (${fbH.title})`);
  const fbC = received.setFeedback.get("ctx-dialcosts") || {};
  assert(/SEMANA|MES|EXTRA/.test(fbC.title || ""), `dial custos girou de escopo (${fbC.title})`);
  const fbL = received.setFeedback.get("ctx-diallife") || {};
  assert(/VIDA/.test(fbL.title || ""), `dial vida toda com dado (${fbL.title}: ${fbL.value})`);
  assert(configPosts.some((p) => typeof p.intended_hours === "number"), `dial horizonte gravou intended_hours no painel (${JSON.stringify(configPosts)})`);
  assert(received.showOk > 0, "touchTap respondeu com showOk");

  // a tecla source com burning=true precisa dizer EXTRAS
  const img = received.setImage.get("ctx-source") || "";
  assert(decodeURIComponent(img).includes("EXTRAS"), "tecla fonte mostra EXTRAS quando burning");
  const imgBurn = decodeURIComponent(received.setImage.get("ctx-burn") || "");
  assert(imgBurn.includes("/h") && imgBurn.includes("R$"), "tecla queima virou R$/h no modo extras");
  const imgBal = decodeURIComponent(received.setImage.get("ctx-balance") || "");
  assert(imgBal.includes("1,5x") || imgBal.includes("x"), "tecla balanco calculou alavancagem");

  assert(decodeURIComponent(received.setImage.get('ctx-codexquota') || '').includes('7%'), 'Codex key uses own quota');
  assert(decodeURIComponent(received.setImage.get('ctx-codexroi') || '').includes('0.18x'), 'Codex ROI uses R$555 and exchange rate');
  assert(decodeURIComponent((received.setFeedback.get('ctx-aiburn') || {}).canvas || '').includes('CODEX'), 'new burn dial rotated to Codex');
  child.kill();
  panel.close();
  wss.close();
  console.log(failures.length ? `\n${failures.length} FALHA(S)` : "\nTUDO PASSOU");
  process.exit(failures.length ? 1 : 0);
}

const wss = net.createServer((socket) => {
  pluginSocket = socket;
  let upgraded = false;
  let head = Buffer.alloc(0);
  const reader = makeFrameReader((text) => {
    let msg;
    try {
      msg = JSON.parse(text);
    } catch {
      return;
    }
    if (msg.event === "registerPlugin") {
      // handshake do plugin ok -> manda global settings apontando pro painel falso
      sendToPlugin({ event: "didReceiveGlobalSettings", payload: { settings: { panelUrl: panelUrl, refreshSeconds: 2 } } });
      // teclas
      for (const a of KEY_ACTIONS) {
        sendToPlugin({ event: "willAppear", action: `${NS}.${a}`, context: `ctx-${a}`, payload: { controller: "Keypad", settings: {} } });
      }
      for (const a of DIAL_ACTIONS) {
        sendToPlugin({ event: "willAppear", action: `${NS}.${a}`, context: `ctx-${a}`, payload: { controller: "Encoder", settings: {} } });
      }
      // depois que o primeiro fetch acontecer, interage
      setTimeout(() => {
        sendToPlugin({ event: "dialRotate", action: `${NS}.dialhorizon`, context: "ctx-dialhorizon", payload: { ticks: 1 } });
        sendToPlugin({ event: "dialRotate", action: `${NS}.dialcosts`, context: "ctx-dialcosts", payload: { ticks: 1 } });
        sendToPlugin({ event: "touchTap", action: `${NS}.dialwindows`, context: "ctx-dialwindows", payload: {} });
        sendToPlugin({event:'dialRotate',action:`${NS}.aiburn`,context:'ctx-aiburn',payload:{ticks:1}});
      }, 1500);
      setTimeout(finish, 3500);
    }
    if (msg.event === "setImage") received.setImage.set(msg.context, msg.payload?.image || "");
    if (msg.event === "setFeedback") received.setFeedback.set(msg.context, msg.payload || {});
    if (msg.event === "showOk") received.showOk++;
    if (msg.event === "setSettings") {
      // devolve como o app real faria
      sendToPlugin({ event: "didReceiveSettings", context: msg.context, payload: { settings: msg.payload } });
    }
    if (msg.event === "getGlobalSettings") {
      sendToPlugin({ event: "didReceiveGlobalSettings", payload: { settings: { panelUrl: panelUrl, refreshSeconds: 2 } } });
    }
  });

  socket.on("data", (chunk) => {
    if (upgraded) return reader(chunk);
    head = Buffer.concat([head, chunk]);
    const end = head.indexOf("\r\n\r\n");
    if (end === -1) return;
    const header = head.subarray(0, end).toString("latin1");
    const rest = head.subarray(end + 4);
    const key = /sec-websocket-key:\s*(\S+)/i.exec(header);
    socket.write(
      [
        "HTTP/1.1 101 Switching Protocols",
        "Upgrade: websocket",
        "Connection: Upgrade",
        `Sec-WebSocket-Accept: ${wsAccept(key[1])}`,
        "",
        "",
      ].join("\r\n")
    );
    upgraded = true;
    if (rest.length) reader(rest);
  });
});

let panelUrl = null;

panel.listen(0, "127.0.0.1", () => {
  panelUrl = `http://127.0.0.1:${panel.address().port}`;
  wss.listen(0, "127.0.0.1", () => {
    const wsPort = wss.address().port;
    console.log(`painel falso: ${panelUrl} · ws falso: ${wsPort}\n== eventos ==`);
    child = spawn(process.execPath, [PLUGIN, "-port", String(wsPort), "-pluginUUID", "test-uuid", "-registerEvent", "registerPlugin", "-info", "{}"], { stdio: ["ignore", "inherit", "inherit"] });
    child.on("exit", (code) => {
      if (failures === null) return;
    });
  });
});

setTimeout(() => {
  console.log("TIMEOUT do teste");
  process.exit(1);
}, 15000);
