import { useRef, useState } from 'react';

const DAYS = ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb'];
const HOURS = [...Array(24).keys()];
const DOW_ORDER = [1, 2, 3, 4, 5, 6, 0]; // Seg → Dom

function fmtTokens(v) {
  if (!v) return '—';
  if (v >= 1e9) return (v / 1e9).toFixed(2) + ' B';
  if (v >= 1e6) return (v / 1e6).toFixed(1) + ' M';
  if (v >= 1e3) return (v / 1e3).toFixed(0) + ' k';
  return String(Math.round(v));
}

export default function HeatmapChart({ heatmap = [], chatgpt = [] }) {
  const containerRef = useRef(null);
  const [tooltip, setTooltip] = useState(null);

  if (!heatmap.length && !chatgpt.length) return null;

  const matrix = { claude: {}, chatgpt: {} };
  const dayTotals = { claude: {}, chatgpt: {} };
  const peaks = { claude: 0, chatgpt: 0 };

  const ingest = (rows, provider) => rows.forEach(({ dow, hour, avg_tokens }) => {
    if (!matrix[provider][dow]) matrix[provider][dow] = {};
    matrix[provider][dow][hour] = avg_tokens;
    dayTotals[provider][dow] = (dayTotals[provider][dow] || 0) + avg_tokens;
    if (avg_tokens > peaks[provider]) peaks[provider] = avg_tokens;
  });
  ingest(heatmap, 'claude');
  ingest(chatgpt, 'chatgpt');

  const maxDayTotal = Math.max(...Object.values(dayTotals.claude), ...Object.values(dayTotals.chatgpt), 1);

  const handleEnter = (e, dow, hour, claude, openai) => {
    if (!containerRef.current) return;
    const cRect = containerRef.current.getBoundingClientRect();
    const tRect = e.currentTarget.getBoundingClientRect();
    setTooltip({
      dow, hour, claude, openai,
      x: tRect.left - cRect.left + tRect.width / 2,
      y: tRect.top - cRect.top,
    });
  };

  return (
    <div
      ref={containerRef}
      className="relative select-none"
      onMouseLeave={() => setTooltip(null)}
    >
      {/* Tooltip */}
      {tooltip && (
        <div
          className="absolute z-20 pointer-events-none"
          style={{
            left: tooltip.x,
            top: tooltip.y > 80 ? tooltip.y - 68 : tooltip.y + 26,
            transform: 'translateX(-50%)',
          }}
        >
          <div className="bg-gray-950 dark:bg-white text-white dark:text-gray-950 text-xs rounded-xl px-3 py-2 shadow-2xl whitespace-nowrap">
            <div className="font-semibold tracking-tight">
              {DAYS[tooltip.dow]}, {tooltip.hour}h–{tooltip.hour + 1}h
            </div>
            <div className="mt-0.5 opacity-60 font-mono tabular-nums">
              <span style={{ color: '#4285ee' }}>Claude {fmtTokens(tooltip.claude)}</span>
              {' · '}
              <span style={{ color: '#10a37f' }}>ChatGPT {fmtTokens(tooltip.openai)}</span>
            </div>
          </div>
        </div>
      )}

      {/* Hour header */}
      <div className="flex mb-1.5">
        <div className="w-10 shrink-0" />
        <div className="flex flex-1 gap-[2px]">
          {HOURS.map(h => (
            <div key={h} className="flex-1 text-center text-[9px] text-black/20 dark:text-white/20 leading-none font-mono">
              {h % 6 === 0 ? `${h}h` : ''}
            </div>
          ))}
        </div>
        <div className="w-14 shrink-0" />
      </div>

      {/* Rows */}
      {DOW_ORDER.map(dow => (
        <div key={dow} className="flex items-center gap-1 mb-[3px]">
          <div className="w-10 shrink-0 text-xs font-medium text-black/40 dark:text-white/40 text-right pr-2 tracking-tight">
            {DAYS[dow]}
          </div>
          <div className="flex flex-1 gap-[2px]">
            {HOURS.map(h => {
              const claude = matrix.claude[dow]?.[h] || 0;
              const openai = matrix.chatgpt[dow]?.[h] || 0;
              const cn = peaks.claude > 0 ? claude / peaks.claude : 0;
              const on = peaks.chatgpt > 0 ? openai / peaks.chatgpt : 0;
              const ca = claude ? 0.14 + cn * 0.86 : 0.04;
              const oa = openai ? 0.14 + on * 0.86 : 0.04;
              return (
                <div
                  key={h}
                  className="flex-1 rounded-[3px] cursor-default transition-transform duration-75 hover:scale-125 hover:z-10 relative"
                  style={{
                    aspectRatio: '1',
                    background: `linear-gradient(135deg, rgba(66,133,238,${ca}) 0 50%, rgba(16,163,127,${oa}) 50% 100%)`,
                  }}
                  onMouseEnter={(e) => handleEnter(e, dow, h, claude, openai)}
                />
              );
            })}
          </div>
          {/* Day total */}
          <div className="w-14 shrink-0 flex items-center pl-2">
            <div className="w-full h-1.5 rounded-full bg-black/5 dark:bg-white/8 overflow-hidden flex">
              <div
                className="h-full bg-[#4285ee]"
                style={{ width: `${((dayTotals.claude[dow] || 0) / maxDayTotal) * 50}%` }}
              />
              <div
                className="h-full bg-[#10a37f]"
                style={{ width: `${((dayTotals.chatgpt[dow] || 0) / maxDayTotal) * 50}%` }}
              />
            </div>
          </div>
        </div>
      ))}

      <div className="flex items-center justify-end gap-4 mt-3 text-[10px] tracking-tight">
        <span style={{ color: '#4285ee' }}>● Claude</span>
        <span style={{ color: '#10a37f' }}>● ChatGPT</span>
        <span className="text-black/25 dark:text-white/25">cada plataforma normalizada pelo próprio pico</span>
      </div>
    </div>
  );
}
