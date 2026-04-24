/* ═════ Seedwake · main app + stream push controller ═════ */
const { useState: useStateM, useEffect: useEffectM, useRef: useRefM, useCallback, useMemo: useMemoM } = React;

const MAX_VISIBLE = 7;     // how many items to keep mounted in the stream at once
const PUSH_INTERVAL = 2500; // ms between thoughts
const CYCLE_PAUSE   = 500;  // extra pause at cycle boundary

function useStream() {
  // items: array of { kind:"thought"|"sep", ... stamp }
  const [items, setItems] = useStateM([]);
  const [currentCycle, setCurrentCycle] = useStateM(1640);
  const [attendedIdx, setAttendedIdx] = useStateM(null);
  const feedIdx = useRefM(0);
  const stampRef = useRefM(0);
  const timerRef = useRefM(null);

  const pushNext = useCallback(() => {
    const t = window.SW_THOUGHTS[feedIdx.current % window.SW_THOUGHTS.length];
    feedIdx.current += 1;
    stampRef.current += 1;

    setItems(prev => {
      let next = prev.slice();

      // If this thought starts a new cycle, push a cycle separator first
      const lastReal = [...next].reverse().find(it => it.kind === "thought");
      if (!lastReal || lastReal.cycle !== t.cycle) {
        const time = new Intl.DateTimeFormat("en-GB", {
          hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false
        }).format(new Date());
        next.push({
          kind: "sep",
          cycle: t.cycle,
          time,
          stamp: stampRef.current,
        });
        stampRef.current += 1;
        setCurrentCycle(t.cycle);
      }

      next.push({
        kind: "thought",
        ...t,
        stamp: stampRef.current,
      });

      // Mark old items as exiting, then drop them after anim
      if (next.length > MAX_VISIBLE) {
        const overflow = next.length - MAX_VISIBLE;
        next = next.map((it, i) => i < overflow ? { ...it, exiting: true } : it);
        // schedule actual removal
        setTimeout(() => {
          setItems(curr => curr.filter(it => !it.exiting));
        }, 900);
      }

      return next;
    });

    setAttendedIdx(t.attended ? `C${t.cycle}-${t.idx}` : null);
  }, []);

  const scheduleNext = useCallback((delayOverride) => {
    const t = window.SW_THOUGHTS[feedIdx.current % window.SW_THOUGHTS.length];
    // If we're about to push idx=1 of a new cycle, add cycle pause
    const needsCyclePause = t && t.idx === 1 && feedIdx.current > 0;
    const delay = delayOverride != null ? delayOverride
                : needsCyclePause ? PUSH_INTERVAL + CYCLE_PAUSE
                : PUSH_INTERVAL;
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      pushNext();
      scheduleNext();
    }, delay);
  }, [pushNext]);

  const start = useCallback(() => {
    // Seed the stream immediately with the first thought so viewport isn't empty
    pushNext();
    scheduleNext();
  }, [pushNext, scheduleNext]);

  const pause = useCallback(() => {
    clearTimeout(timerRef.current);
    timerRef.current = null;
  }, []);

  const resume = useCallback(() => {
    if (!timerRef.current) scheduleNext();
  }, [scheduleNext]);

  return { items, currentCycle, attendedIdx, start, pause, resume };
}

function App() {
  const [mode, setMode] = useStateM("waking");
  const { items, currentCycle, attendedIdx, start, pause, resume } = useStream();
  const startedRef = useRefM(false);

  // Boot
  useEffectM(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    start();
  }, [start]);

  // Apply mode class on body + pause/resume stream
  useEffectM(() => {
    document.body.classList.remove("waking", "drowsy", "deep");
    document.body.classList.add(mode);
    if (mode === "waking") resume();
    else pause();
  }, [mode, pause, resume]);

  // Energy decay for realism
  const [energy, setEnergy] = useStateM(68);
  useEffectM(() => {
    const id = setInterval(() => {
      setEnergy(e => {
        if (mode === "waking") return Math.max(15, e - 0.05);
        if (mode === "drowsy") return Math.min(70, e + 0.4);
        if (mode === "deep")   return Math.min(100, e + 0.6);
        return e;
      });
    }, 1000);
    return () => clearInterval(id);
  }, [mode]);

  // Gentle emotion drift
  const [emotions, setEmotions] = useStateM(() => EMOTIONS.map(e => ({ ...e, val: e.baseVal })));
  useEffectM(() => {
    const id = setInterval(() => {
      setEmotions(prev => prev.map(e => {
        const drift = (Math.random() - 0.5) * 0.04;
        const target = e.baseVal + drift;
        const val = Math.max(0, Math.min(1, target));
        return { ...e, val };
      }));
    }, 3000);
    return () => clearInterval(id);
  }, []);

  const counter = mode === "drowsy"
    ? "paused at C1832 · integrating"
    : mode === "deep"
    ? "deep sleep"
    : attendedIdx
      ? `${attendedIdx} · 注意 · ▲ scroll to recall`
      : `C${currentCycle} · streaming · ▲ scroll to recall`;

  return (
    <React.Fragment>
      <div className="app">
        <LeftPanel
          mode={mode}
          cycle={currentCycle}
          energy={energy}
          emotions={emotions}
        />
        <StreamColumn items={items} mode={mode} counter={counter} />
        <RightPanel />
      </div>

      <div className="deep-veil">
        <div className="deep-center">
          <div className="seal"><span className="glyph">藏</span></div>
          <div className="deep-label">
            深 睡
            <small>deep sleep · the scroll rolled</small>
          </div>
        </div>
      </div>

      <DevBar mode={mode} setMode={setMode} />
    </React.Fragment>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
