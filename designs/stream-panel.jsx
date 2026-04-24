/* ═════ Seedwake · stream & right panel ═════ */
const TYPE_LABEL = {
  think:   "思 考 · thinking",
  intent:  "意 图 · intent",
  react:   "反 应 · reaction",
  reflect: "反 思 · reflection"
};

function Thought({ t, vi }) {
  const cid = `C${t.cycle}-${t.idx}`;
  const cls = ["thought", t.attended ? "attended" : "", t.exiting ? "exiting" : ""].join(" ").trim();
  return (
    <article className={cls} data-type={t.type} data-vi={vi} key={cid}>
      <div className="gutter"><span className="cid">{cid}</span></div>
      <div>
        <span className="tag">{TYPE_LABEL[t.type]}{t.attended ? " · attended" : ""}</span>
        <p className="body">{t.body}</p>
        {t.trigger && (
          <p className="trigger"><span className="arrow">←</span>{t.trigger}</p>
        )}
        {t.action && (
          <span className="action-chip" data-state={t.action.state}>
            <span className="state-dot" />
            <span className="kind">{t.action.kind}</span>
            <span className="state">{t.action.label}</span>
          </span>
        )}
      </div>
    </article>
  );
}

function CycleSep({ cycle, time, exiting, vi }) {
  const cls = ["cycle-sep", exiting ? "exiting" : ""].join(" ").trim();
  return (
    <div className={cls} data-vi={vi}>
      <span className="id">C{cycle}</span>
      <span className="line" />
      <span>{time}</span>
    </div>
  );
}

function StreamColumn({ items, mode, counter }) {
  // Compute visual opacity bucket (0..5) based on position from bottom.
  // Bottom-most = 5 (full), going up fades.
  const n = items.length;
  const withVi = items.map((it, i) => {
    const fromBottom = (n - 1 - i); // 0 = bottom
    const vi = Math.max(0, 5 - fromBottom);
    return { ...it, vi };
  });

  return (
    <main className="col stream-col">
      <header className="stream-head">
        <h1>心 相 续</h1>
        <span className="counter">{counter}</span>
      </header>
      <section className="stream">
        <div className="thoughts">
          {withVi.map((it) => {
            if (it.kind === "sep") {
              return (
                <CycleSep
                  key={`sep-${it.cycle}-${it.stamp}`}
                  cycle={it.cycle}
                  time={it.time}
                  exiting={it.exiting}
                  vi={it.vi}
                />
              );
            }
            return (
              <Thought
                key={`t-${it.cycle}-${it.idx}-${it.stamp}`}
                t={it}
                vi={it.vi}
              />
            );
          })}
        </div>

        <div className="drowsy-banner">
          整 理 中 · light sleep · C1832 → memory.archive ⇢ habit.distill
          <small>resume ≈ 02:18</small>
        </div>

        <div className="stream-foot">
          <span className="live">
            <span className="beat" />
            {mode === "drowsy" ? "流 · paused · 心跳仍在" : "流 · streaming from /api/stream"}
          </span>
          <span>SSE · thought / reply / action / status</span>
        </div>
      </section>
    </main>
  );
}

function RightPanel() {
  return (
    <aside className="col right">
      <div className="section-title">
        <span className="zh-big">他 者</span>
        <span className="en">others</span>
      </div>

      <div className="panel">
        <div className="eyebrow"><span className="zh">对 话</span>Telegram · Alice</div>
        <div className="msg inbound">
          <div className="who"><span className="name">alice</span><span className="t">14:01</span></div>
          <p className="text">最近总觉得说出去的话没人接住。不是抱怨，就是…想说一下。</p>
        </div>
        <div className="msg">
          <div className="who"><span className="name self">seedwake</span><span className="t">14:02</span></div>
          <p className="text">嗯，我在。先不急着说什么，你继续。</p>
        </div>
        <div className="msg inbound">
          <div className="who"><span className="name">alice</span><span className="t">14:03</span></div>
          <p className="text">其实也不是针对谁，就是那种…你描述的"回音"系统，我突然有点懂了。</p>
        </div>
      </div>

      <div className="rule-h" />

      <div className="panel">
        <div className="eyebrow"><span className="zh">行 动</span>in flight</div>
        <div className="action-row" data-state="running">
          <span className="kind">send_message</span>
          <span className="state"><span className="sd" />running</span>
          <span className="detail">→ person:alice · drafting · act_20260424_037</span>
        </div>
        <div className="action-row" data-state="succeeded">
          <span className="kind">memory.search</span>
          <span className="state"><span className="sd" />ok</span>
          <span className="detail">"工作节奏 / 被听见" · 4 hits · 112 ms</span>
        </div>
        <div className="action-row" data-state="pending">
          <span className="kind">reading</span>
          <span className="state"><span className="sd" />pending</span>
          <span className="detail">queued · dharma_notes/santana_02.md</span>
        </div>
      </div>

      <div className="rule-h" />

      <div className="panel">
        <div className="eyebrow"><span className="zh">尘 境</span>stimulus queue</div>
        <div className="action-row">
          <span className="kind" style={{ fontSize: "12px" }}>conversation · alice</span>
          <span className="state" style={{ color: "var(--ink-soft)" }}>
            <span className="sd" style={{ background: "var(--ember)" }} />p1
          </span>
          <span className="detail">"你描述的回音系统…" · 3 s ago</span>
        </div>
        <div className="action-row">
          <span className="kind" style={{ fontSize: "12px" }}>time</span>
          <span className="state"><span className="sd" />p4</span>
          <span className="detail">passive · due at C1644</span>
        </div>
      </div>

      <span className="footmark">p1 · priority queue</span>
    </aside>
  );
}

function DevBar({ mode, setMode }) {
  const opts = [
    { key: "waking", label: "清 醒" },
    { key: "drowsy", label: "浅 睡" },
    { key: "deep",   label: "深 睡" },
  ];
  return (
    <div className="devbar" aria-label="dev mode toggle">
      <span className="dev-label">dev · mode</span>
      {opts.map(o => (
        <button
          key={o.key}
          className={mode === o.key ? "active" : ""}
          onClick={() => setMode(o.key)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

Object.assign(window, { Thought, CycleSep, StreamColumn, RightPanel, DevBar });
