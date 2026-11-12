import { useEffect, useState } from 'react';
import { api } from './api';

const GB = 1024 ** 3;
export const fmtGB = v => (v == null ? '—' : `${(v / GB).toFixed(v >= 100 * GB ? 0 : 1)} GB`);
export function fmtRate(v) {
  if (v == null) return '—';
  const bits = v * 8;
  if (bits >= 1e9) return `${(bits / 1e9).toFixed(1)} Gb/s`;
  if (bits >= 1e6) return `${(bits / 1e6).toFixed(1)} Mb/s`;
  if (bits >= 1e3) return `${(bits / 1e3).toFixed(0)} Kb/s`;
  return `${Math.round(bits)} b/s`;
}
const fmtBytesRate = v => (v == null ? '—' : v >= 1e9 ? `${(v / 1e9).toFixed(1)} GB/s` : v >= 1e6 ? `${(v / 1e6).toFixed(1)} MB/s` : `${(v / 1e3).toFixed(0)} KB/s`);
const pct = v => (v == null ? '—' : `${Math.round(v)}%`);
const temp = v => (v == null ? null : `${Math.round(v)} °C`);
const tone = v => (v == null ? 'idle' : v >= 90 ? 'hot' : v >= 70 ? 'warm' : 'ok');

function Tile({ label, value, bar, lines, title }) {
  return <div className="h-tile" data-tone={tone(bar)} title={title}>
    <span className="h-label">{label}</span>
    <strong className="h-value">{value}</strong>
    <div className="h-bar"><i style={{ width: `${Math.min(100, Math.max(0, bar || 0))}%` }} /></div>
    <div className="h-lines">{lines.filter(Boolean).map((l, i) => <small key={i}>{l}</small>)}</div>
  </div>;
}

export function useHardware(active = true, every = 2000) {
  const [hw, setHw] = useState(null);
  useEffect(() => {
    if (!active) return undefined;
    let alive = true;
    const load = () => api.hardware().then(h => alive && setHw(h)).catch(() => {});
    load();
    const t = setInterval(load, every);
    return () => { alive = false; clearInterval(t); };
  }, [active, every]);
  return hw;
}

export default function Hardware({ hw }) {
  if (!hw) return <p className="u-hint">Lendo o hardware…</p>;
  const { cpu = {}, gpu = {}, memory: mem = {}, disk = {}, net = {}, battery: bat, thermal = {} } = hw;
  const chip = hw.machine?.chip;
  return <>
    <div className="h-grid">
      <Tile label="CPU" value={pct(cpu.usage)} bar={cpu.usage}
        title={cpu.per_core ? `Por núcleo: ${cpu.per_core.map(Math.round).join(' · ')}` : undefined}
        lines={[
          cpu.performance_usage != null && `${cpu.performance_name || 'Desempenho'} ${pct(cpu.performance_usage)} · Eficiência ${pct(cpu.efficiency_usage)}`,
          cpu.cores && `${cpu.cores} núcleos${cpu.load ? ` · carga ${cpu.load[0]}` : ''}`,
          temp(cpu.temp) && `Chip ${temp(cpu.temp)}`,
        ]} />
      <Tile label="GPU" value={pct(gpu.usage)} bar={gpu.usage}
        lines={[
          gpu.memory_used != null && `${fmtGB(gpu.memory_used)} em uso`,
          gpu.cores && `${gpu.cores} núcleos${gpu.renderer != null ? ` · render ${pct(gpu.renderer)}` : ''}`,
          temp(gpu.temp) && `Chip ${temp(gpu.temp)}`,
        ]} />
      <Tile label="Memória" value={pct(mem.usage)} bar={mem.usage}
        lines={[
          mem.used != null && `${fmtGB(mem.used)} de ${fmtGB(mem.total)}`,
          mem.compressed != null && `Comprimida ${fmtGB(mem.compressed)}${mem.swap_used ? ` · swap ${fmtGB(mem.swap_used)}` : ''}`,
          mem.pressure_level != null && `Pressão ${['normal', 'normal', 'atenção', 'alta', 'crítica'][mem.pressure_level] || mem.pressure_level}`,
        ]} />
      <Tile label="Disco" value={fmtGB(disk.free)} bar={disk.usage}
        lines={[
          disk.total != null && `livres de ${fmtGB(disk.total)} (${pct(disk.usage)} usado)`,
          disk.read_bps != null && `Lê ${fmtBytesRate(disk.read_bps)} · grava ${fmtBytesRate(disk.write_bps)}`,
          temp(disk.temp) && `SSD ${temp(disk.temp)}`,
        ]} />
      <Tile label="Rede" value={`↓ ${fmtRate(net.rx_bps)}`} bar={null}
        lines={[
          `↑ ${fmtRate(net.tx_bps)}`,
          net.rx_total != null && `Desde a abertura: ↓ ${fmtGB(net.rx_total)} · ↑ ${fmtGB(net.tx_total)}`,
        ]} />
      {bat && <Tile label="Bateria" value={pct(bat.percent)} bar={100 - (bat.percent ?? 100)}
        lines={[
          bat.charging ? 'Carregando' : bat.plugged ? 'Na tomada' : bat.minutes_remaining ? `${Math.floor(bat.minutes_remaining / 60)}h ${bat.minutes_remaining % 60}m restantes` : 'Na bateria',
          temp(bat.temp) && `Temperatura ${temp(bat.temp)}`,
          bat.health != null && `Saúde ${pct(bat.health)}${bat.cycles != null ? ` · ${bat.cycles} ciclos` : ''}`,
        ]} />}
    </div>
    <p className="u-hint">{chip ? `${chip} · ` : ''}{thermal.state ? `Térmico: ${thermal.state}` : ''}{hw.source === 'basic' ? ` · modo básico${hw.note ? ` (${hw.note})` : ''}` : ''}</p>
  </>;
}
