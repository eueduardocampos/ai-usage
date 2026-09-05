async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return r.json();
}

export const api = {
  state: () => getJSON('/api/state'),
  total: () => getJSON('/api/total'),
  history: () => getJSON('/api/history'),
  setConfig: (body) =>
    fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    }).then((r) => { if (!r.ok) throw new Error('Falha ao salvar'); return r.json(); }),
  refresh: () => fetch('/api/refresh', { method: 'POST' }).then((r) => r.json()),
  authStart: () => fetch('/auth/start'),
  setSemrush: (balance) =>
    fetch('/api/semrush', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ balance }),
    }).then((r) => r.json()),
};
