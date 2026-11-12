// Widgets do painel: ordem, quem aparece, quem esta minimizado e quem ocupa
// a largura inteira. O layout fica no servidor (config.layout), entao vale
// para qualquer navegador ou perfil do Chrome que abrir o localhost.

export const WIDGETS = [
  { id: 'hardware', title: 'Esta máquina', hint: 'CPU, GPU, memória, disco, rede e bateria' },
  { id: 'accounts', title: 'Contas e limites', hint: 'Janelas de 5 horas, 7 dias e por modelo de cada conta' },
  { id: 'usage', title: 'Consumo no período', hint: 'Tokens e equivalente de API do período escolhido no topo' },
  { id: 'verdict', title: 'Posso trocar de modelo?', hint: 'Veredito pela janela mais apertada' },
  { id: 'burn', title: 'Ritmo por modelo', hint: 'Tokens por hora nas últimas 2 horas' },
  { id: 'cost', title: 'Retorno por conta', hint: 'Mensalidade de cada conta contra o equivalente de API no mês' },
  { id: 'machines', title: 'Consumo por máquina', hint: 'Só aparece com a pasta de sincronização ligada' },
  { id: 'charts', title: 'Quando você usa', hint: 'Tokens por dia e ritmo por dia e hora' },
  { id: 'quota-history', title: 'Evolução das cotas', hint: 'Últimas 48 horas' },
  { id: 'system', title: 'Este sistema', hint: 'Versão, apps e Stream Deck' },
];

const ALL = WIDGETS.map(w => w.id);

export const PRESETS = [
  { id: 'completo', name: 'Completo', desc: 'Tudo aberto', layout: { order: ALL, hidden: [], collapsed: [], wide: ['hardware', 'accounts', 'usage', 'charts', 'quota-history'] } },
  { id: 'limites', name: 'Limites', desc: 'Máquina e contas, sem custo', layout: { order: ['hardware', 'accounts', 'verdict', 'burn', 'usage'], hidden: ['cost', 'machines', 'charts', 'quota-history', 'system'], collapsed: [], wide: ['hardware', 'accounts'] } },
  { id: 'custos', name: 'Custos', desc: 'Consumo, retorno e gráficos', layout: { order: ['usage', 'cost', 'burn', 'charts', 'accounts', 'machines', 'hardware'], hidden: ['verdict', 'quota-history', 'system'], collapsed: ['hardware'], wide: ['usage', 'cost', 'charts'] } },
  { id: 'minimo', name: 'Mínimo', desc: 'Só o essencial, cabe numa tela', layout: { order: ['hardware', 'accounts'], hidden: ALL.filter(i => !['hardware', 'accounts'].includes(i)), collapsed: [], wide: ['hardware', 'accounts'] } },
];

export function normalizeLayout(raw) {
  const base = PRESETS[0].layout;
  const l = raw && typeof raw === 'object' ? raw : base;
  const order = (l.order || []).filter(id => ALL.includes(id));
  for (const id of ALL) if (!order.includes(id)) order.push(id);
  const pick = k => (Array.isArray(l[k]) ? l[k] : base[k]).filter(id => ALL.includes(id));
  return { order, hidden: pick('hidden'), collapsed: pick('collapsed'), wide: pick('wide'), preset: l.preset ?? (raw ? null : 'completo') };
}

export const PERIODS = [
  ['dia', 'Hoje'],
  ['24h', '24 horas'],
  ['7d', '7 dias'],
  ['semana', 'Esta semana'],
  ['mes', 'Este mês'],
  ['geral', 'Total'],
];
