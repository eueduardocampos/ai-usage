async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return r.json();
}

async function postJSON(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `${url} -> ${r.status}`);
  return data;
}

export const api = {
  state: () => getJSON('/api/state'),
  total: () => getJSON('/api/total'),
  history: () => getJSON('/api/history'),
  usage: (period, scope = 'local') => getJSON(`/api/usage?period=${period}&scope=${scope}`),
  hardware: () => getJSON('/api/hardware'),
  accounts: () => getJSON('/api/accounts'),
  setConfig: (body) => postJSON('/api/config', body),
  refresh: () => fetch('/api/refresh', { method: 'POST' }).then((r) => r.json()),
  authStart: () => fetch('/auth/start'),
  addAccount: (name) => postJSON('/api/accounts', { provider: 'claude', name }),
  updateAccount: (id, changes) => postJSON(`/api/accounts/${id}`, changes),
  login: (id, open) => postJSON(`/api/accounts/${id}/login`, { open }),
  disconnect: (id) => postJSON(`/api/accounts/${id}/disconnect`),
  removeAccount: (id) => postJSON(`/api/accounts/${id}/delete`),
  openApp: (id) => postJSON(`/api/accounts/${id}/open`),
  refreshAccount: (id) => postJSON(`/api/accounts/${id}/refresh`),
  reorder: (ids) => postJSON('/api/accounts/reorder', { ids }),
  onboarded: (done = true) => postJSON('/api/onboarded', { done }),
};
