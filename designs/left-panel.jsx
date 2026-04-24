/* ═════ Seedwake · left panel (此刻) ═════ */
const { useState, useEffect, useRef, useMemo } = React;

const EMOTIONS = [
  { key:"curiosity",    zh:"好 奇", en:"CURIOSITY",   pigment:"赭黄", oklch:"0.55 0.12 72",  hue:72,  baseVal:0.72 },
  { key:"calm",         zh:"平 静", en:"CALM",        pigment:"青黛", oklch:"0.42 0.08 245", hue:245, baseVal:0.58 },
  { key:"satisfaction", zh:"满 足", en:"SATISFIED",   pigment:"石绿", oklch:"0.55 0.10 160", hue:160, baseVal:0.46 },
  { key:"concern",      zh:"关 切", en:"CONCERN",     pigment:"紫檀", oklch:"0.40 0.07 330", hue:330, baseVal:0.28 },
  { key:"frustration",  zh:"沮 丧", en:"FRUSTRATION", pigment:"胭脂", oklch:"0.45 0.14 18",  hue:18,  baseVal:0.11 },
];

// Emotion ring SVG. Each ring breathes at a period and amplitude derived from value.
// Higher value → shorter period, larger amplitude, warmer hue bias.
// value < 0.2 → nearly static.
function EmotionRings({ emotions }) {
  const [t, setT] = useState(0);
  useEffect(() => {
    let raf; const start = performance.now();
    const loop = (now) => {
      setT((now - start) / 1000);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);

  // Sort by value so larger halos are drawn behind smaller ones
  const ordered = [...emotions].sort((a, b) => b.val - a.val);

  return (
    <svg viewBox="0 0 260 260" aria-hidden="true">
      <defs>
        {ordered.map(e => {
          // Hue shift: curiosity high → warmer (+10° toward orange/red); low → neutral.
          // Frustration high → cooler (−6°). This is a subtle sway.
          const biasWarm = (e.key === "curiosity" || e.key === "satisfaction") ? e.val * 8 : 0;
          const biasCool = (e.key === "frustration" || e.key === "concern") ? e.val * 6 : 0;
          const period = 3 + (1 - e.val) * 3; // 3s (high) → 6s (low)
          const phase = Math.sin((t / period) * Math.PI * 2);
          // Hue sway ±2 over cycle, plus the emotion's own value bias
          const h = e.hue + biasWarm - biasCool + phase * 2;
          const [l, c] = e.oklch.split(" ");
          const color = `oklch(${l} ${c} ${h})`;
          return (
            <radialGradient key={e.key} id={`hal-${e.key}`} cx="50%" cy="50%" r="50%">
              <stop offset="0%"  stopColor={color} stopOpacity={0.22 + e.val * 0.38} />
              <stop offset="60%" stopColor={color} stopOpacity={0.05 + e.val * 0.08} />
              <stop offset="100%" stopColor={color} stopOpacity="0" />
            </radialGradient>
          );
        })}
      </defs>

      {/* ryoan-ji rake rings */}
      <g opacity="0.36" stroke="oklch(0.55 0.015 60)" fill="none" strokeWidth="0.4">
        <circle cx="130" cy="130" r="122" />
        <circle cx="130" cy="130" r="104" />
        <circle cx="130" cy="130" r="84" />
        <circle cx="130" cy="130" r="62" />
        <circle cx="130" cy="130" r="38" />
      </g>

      {ordered.map(e => {
        const r0 = 20 + e.val * 100;
        // Amplitude: high value → 8px sway, low value → <1px sway
        const amp = e.val < 0.2 ? 0.5 : 2 + e.val * 6;
        const period = 3 + (1 - e.val) * 3;
        const r = r0 + Math.sin((t / period) * Math.PI * 2) * amp;
        return (
          <circle
            key={e.key}
            cx="130" cy="130" r={r}
            fill={`url(#hal-${e.key})`}
          />
        );
      })}

      {/* core */}
      <circle cx="130" cy="130" r="3" fill="oklch(0.3 0.01 60)" opacity="0.85" />
      <circle cx="130" cy="130" r="9" fill="none" stroke="oklch(0.3 0.01 60 / 0.25)" strokeWidth="0.6" />
    </svg>
  );
}

function EmotionLegend({ emotions }) {
  return (
    <div className="emotion-legend">
      {emotions.map(e => (
        <div className="legend-row" key={e.key} style={{ "--swatch": `oklch(${e.oklch})` }}>
          <span className="legend-name">
            {e.zh}
            <span className="tiny">{e.pigment}</span>
          </span>
          <span className="legend-val">{e.val.toFixed(2)}</span>
        </div>
      ))}
    </div>
  );
}

function LeftPanel({ mode, cycle, energy, emotions }) {
  const modeLabel = mode === "waking"
    ? { zh: "清 醒", en: `Waking · cycle ${cycle}` }
    : mode === "drowsy"
    ? { zh: "浅 睡", en: "Light sleep · integrating" }
    : { zh: "深 睡", en: "Deep sleep" };

  const nextDrowsy = Math.max(cycle + Math.floor((energy - 30) / 0.2), cycle + 1);
  const energyPct = Math.round(energy) + "%";

  return (
    <aside className="col left">
      <header className="masthead">
        <span className="mark">Seed<i>wake</i></span>
        <span className="zh">心 相 续</span>
      </header>

      <div className="section-title">
        <span className="zh-big">此 刻</span>
        <span className="en">the present</span>
      </div>

      <div className="mode">
        <span className="dot" />
        <span className="label">
          {modeLabel.zh}
          <small>{modeLabel.en}</small>
        </span>
      </div>

      <div className="emotion"><EmotionRings emotions={emotions} /></div>
      <EmotionLegend emotions={emotions} />

      <div className="rule-h" />

      <div className="meters">
        <div className="meter">
          <div className="head"><span className="zh">精 力</span><span>Energy</span></div>
          <div className="value">{Math.round(energy)}<small>/ 100</small></div>
          <div className="bar" style={{ "--fill": energyPct }} />
          <div className="sub">
            {mode === "drowsy" ? "drowsy threshold crossed · integrating"
             : mode === "deep" ? "deep sleep · recovering"
             : `↘ 0.2 / cycle · next drowsy ≈ C${nextDrowsy}`}
          </div>
        </div>
        <div className="meter">
          <div className="head"><span className="zh">循 环</span><span>Cycles</span></div>
          <div className="value">{cycle.toLocaleString()}<small>since boot</small></div>
          <div className="bar" style={{ "--fill": "34%" }} />
          <div className="sub">avg 11.4 s · 3 thoughts / cycle</div>
        </div>
        <div className="meter">
          <div className="head"><span className="zh">时 长</span><span>Uptime</span></div>
          <div className="value">05:12<small>h · m</small></div>
          <div className="bar" style={{ "--fill": "22%" }} />
          <div className="sub">since 2026-04-24 · 04:48 awakening</div>
        </div>
      </div>

      <span className="footmark">santāna · cycle stream v1.0</span>
    </aside>
  );
}

Object.assign(window, { EMOTIONS, EmotionRings, EmotionLegend, LeftPanel });
