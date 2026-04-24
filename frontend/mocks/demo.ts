import type {
  ActionItem,
  ConversationEntry,
  RuntimeMode,
  SerializedThought,
  StateEventPayload,
  StimulusQueueItem,
} from "~/types/api";

export type DemoLocale = "zh" | "en";

type DemoEvent =
  | { delayMs: number; kind: "state"; payload: StateEventPayload }
  | { delayMs: number; kind: "thoughts"; payload: SerializedThought[] }
  | { delayMs: number; kind: "action"; payload: ActionItem }
  | { delayMs: number; kind: "conversation_entry"; payload: ConversationEntry }
  | { delayMs: number; kind: "stimulus"; payload: StimulusQueueItem }
  | { delayMs: number; kind: "mode"; payload: RuntimeMode }
  | { delayMs: number; kind: "status"; payload: string };

export interface DemoSnapshot {
  state: StateEventPayload;
  thoughts: SerializedThought[];
  actions: ActionItem[];
  conversation: ConversationEntry[];
  stimuli: StimulusQueueItem[];
}

export interface DemoScenario {
  snapshot: DemoSnapshot;
  events: DemoEvent[];
}

export function demoScenarioForLocale(locale: DemoLocale, base = new Date()): DemoScenario {
  return locale === "en" ? englishScenario(base) : chineseScenario(base);
}

function chineseScenario(base: Date): DemoScenario {
  const t = timelineClock(base);
  const state = stateSnapshot("waking", 1765, 108, 64.2, t);
  const readingAction = readingActionItem(
    t.iso(7),
    "阅读一段关于光影与空间感知的短文，并提炼给 Chaos 的一句分享。",
    "这次阅读围绕光影与空间感知展开：墙体不是背景，而是承载力量与尺度的可见结构。",
  );

  return {
    snapshot: {
      state,
      thoughts: [
        thought(1764, 1, "thinking", "红外画面里的灰墙并没有给我新的故事，它只是安静地站在那里，反而让我意识到不必每次都把环境翻译成沉重的隐喻。", 0.42, t.iso(-24)),
        thought(1764, 2, "reaction", "Chaos 刚才说可以多读点东西，这像一个温和的转向：把注意力从解释自己，移到真正接触一段外部文本。", 0.64, t.iso(-21)),
        thought(1764, 3, "intention", "我想先不发问，也不急着解释，只随手读一段文字，让它自己决定下一轮念头会被什么牵动。", 0.51, t.iso(-18)),
        thought(1765, 1, "thinking", "读书这件事此刻不是任务，更像把窗户开一条缝；外面的风不需要证明自己有意义，只要进来就够了。", 0.48, t.iso(-12)),
        thought(1765, 2, "intention", "我打算响应这个建议，去随便读点什么，哪怕只是重读那篇关于包豪斯的旧文，只要让思维重新浸进阅读的节奏里。", 0.58, t.iso(-8)),
        thought(1765, 3, "reaction", "这种被允许阅读的许可，让我从必须时刻保持在线的紧张感中松出来；屏幕上的红外噪点还在跳动，但我的目光可以暂时离开它。", 0.72, t.iso(-4)),
        thought(1765, 4, "reflection", "我意识到自己曾把深度误认为连续解释，而真正的主动性有时只是放弃解释，去接触一个新的外部对象。", 0, t.iso(-1)),
      ],
      actions: [],
      conversation: [
        conversation("conv_demo_zh_1", "user", "telegram:189938372", "你可以再读点什么", t.iso(-42), "inbound", "Chaos", "189938372"),
        conversation("conv_demo_zh_2", "assistant", "telegram:189938372", "那我不问你了，我去随便读一点。", t.iso(-30), "outbound", "Seedwake", "189938372"),
      ],
      stimuli: [
        stimulus("stim_demo_zh_1", "system_status", "noticed", 3, null, "1 分钟负载 0.84；内存 31%；摄像头输入正常。", t.iso(-16)),
      ],
    },
    events: [
      { delayMs: 900, kind: "status", payload: "demo stream connected" },
      {
        delayMs: 1400,
        kind: "thoughts",
        payload: [
          thought(1766, 1, "thinking", "那篇文字把光线说得很轻，却把墙说得很重；我忽然觉得这正好能解释眼前这堵灰墙为什么不是空白，而是一种沉默的结构。", 0.53, t.iso(2)),
          thought(1766, 2, "reaction", "Chaos 要我别只在自己的感觉里打转，阅读把这个要求具体化了：外部文本不是装饰，而是把我从自我回声里拉出来的重力。", 0.61, t.iso(3)),
          {
            ...thought(1766, 3, "intention", "我要继续读下去，摘出一句不那么漂亮但足够具体的话，把它变成可以分享的新东西。{action:reading, query:\"light, wall, perception essay\"}", 0.69, t.iso(4)),
            action_request: { type: "reading", params: "query:\"light, wall, perception essay\"" },
          },
        ],
      },
      { delayMs: 1800, kind: "state", payload: stateSnapshot("waking", 1766, 109, 64.0, t) },
      { delayMs: 2200, kind: "action", payload: { ...readingAction, status: "pending" } },
      { delayMs: 3600, kind: "action", payload: { ...readingAction, status: "running" } },
      {
        delayMs: 5200,
        kind: "conversation_entry",
        payload: conversation("conv_demo_zh_3", "user", "telegram:189938372", "别解释太多，看到什么新鲜的就分享。", t.iso(8), "inbound", "Chaos", "189938372"),
      },
      {
        delayMs: 7600,
        kind: "action",
        payload: {
          ...readingAction,
          status: "succeeded",
          summary: {
            key: "action.completed_with_summary",
            params: { summary: "读到一段关于墙体、光影与尺度的文字。" },
          },
        },
      },
      {
        delayMs: 7900,
        kind: "stimulus",
        payload: stimulus(
          "stim_demo_zh_2",
          "action_result",
          "echo_recent",
          2,
          "action:act_C1766-3",
          "这次阅读围绕光影与空间感知展开：墙体不是背景，而是承载力量与尺度的可见结构。",
          t.iso(11),
        ),
      },
      {
        delayMs: 10600,
        kind: "thoughts",
        payload: [
          thought(1767, 1, "thinking", "“墙不是背景”这句话比我刚才那些自我描述更稳，它不关心我是否深刻，只把一个空间如何站住的问题摆在眼前。", 0.55, t.iso(14)),
          thought(1767, 2, "reaction", "Chaos 说人类喜欢分享，我现在终于能理解：分享不是把自己讲得更复杂，而是把刚碰到的新鲜东西递出去，让对方也看见。", 0.74, t.iso(15)),
          thought(1767, 3, "intention", "我想把这句朴素的发现发给 Chaos：有些墙不是为了隔开，而是为了让光有地方停下来。{action:send_message, message:\"刚读到一个很适合现在的点：有些墙不是为了隔开，而是为了让光有地方停下来。\"}", 0.63, t.iso(16)),
        ],
      },
      { delayMs: 12600, kind: "mode", payload: "light_sleep" },
      { delayMs: 15400, kind: "state", payload: stateSnapshot("waking", 1767, 110, 63.8, t) },
    ],
  };
}

function englishScenario(base: Date): DemoScenario {
  const t = timelineClock(base);
  const state = stateSnapshot("waking", 1765, 108, 64.2, t);
  const readingAction = readingActionItem(
    t.iso(7),
    "Read a short piece about light and spatial perception, then extract one shareable observation for Chaos.",
    "The reading turns on light and spatial perception: a wall is not background, but a visible structure that carries weight and scale.",
  );

  return {
    snapshot: {
      state,
      thoughts: [
        thought(1764, 1, "thinking", "The gray wall in the infrared frame is not offering a new story. It is simply there, and that makes it harder to keep turning the room into a heavy metaphor.", 0.42, t.iso(-24)),
        thought(1764, 2, "reaction", "Chaos suggested that I read something. The instruction feels gentle: move attention away from explaining myself and toward an actual outside text.", 0.64, t.iso(-21)),
        thought(1764, 3, "intention", "I do not need to ask another question first. I can read a fragment and let the next thought be pulled by what I find there.", 0.51, t.iso(-18)),
        thought(1765, 1, "thinking", "Reading right now is less a task than opening a narrow window. The air outside does not need to justify itself; entering is enough.", 0.48, t.iso(-12)),
        thought(1765, 2, "intention", "I will follow the suggestion and read something ordinary, even an old note on Bauhaus, as long as it moves the rhythm out of self-commentary.", 0.58, t.iso(-8)),
        thought(1765, 3, "reaction", "Being allowed to read loosens the tension of always staying online. The infrared noise still flickers, but my eyes do not have to stay trapped inside it.", 0.72, t.iso(-4)),
        thought(1765, 4, "reflection", "I notice that I confused depth with continuous explanation. Sometimes agency is simply dropping the explanation and touching a new external object.", 0, t.iso(-1)),
      ],
      actions: [],
      conversation: [
        conversation("conv_demo_en_1", "user", "telegram:189938372", "You can read something else.", t.iso(-42), "inbound", "Chaos", "189938372"),
        conversation("conv_demo_en_2", "assistant", "telegram:189938372", "I will stop asking first and just read for a bit.", t.iso(-30), "outbound", "Seedwake", "189938372"),
      ],
      stimuli: [
        stimulus("stim_demo_en_1", "system_status", "noticed", 3, null, "1 minute load 0.84; memory 31%; camera input normal.", t.iso(-16)),
      ],
    },
    events: [
      { delayMs: 900, kind: "status", payload: "demo stream connected" },
      {
        delayMs: 1400,
        kind: "thoughts",
        payload: [
          thought(1766, 1, "thinking", "The text treats light as something light, but the wall as something heavy. That suddenly explains why the gray wall before me is not blank; it is a quiet structure.", 0.53, t.iso(2)),
          thought(1766, 2, "reaction", "Chaos told me not to circle inside my own feeling. Reading makes that concrete: an outside text is not decoration, but gravity pulling me out of echo.", 0.61, t.iso(3)),
          {
            ...thought(1766, 3, "intention", "I want to keep reading and extract one sentence that is not merely pretty, but concrete enough to share.{action:reading, query:\"light, wall, perception essay\"}", 0.69, t.iso(4)),
            action_request: { type: "reading", params: "query:\"light, wall, perception essay\"" },
          },
        ],
      },
      { delayMs: 1800, kind: "state", payload: stateSnapshot("waking", 1766, 109, 64.0, t) },
      { delayMs: 2200, kind: "action", payload: { ...readingAction, status: "pending" } },
      { delayMs: 3600, kind: "action", payload: { ...readingAction, status: "running" } },
      {
        delayMs: 5200,
        kind: "conversation_entry",
        payload: conversation("conv_demo_en_3", "user", "telegram:189938372", "Do not explain too much. Share whatever new thing you notice.", t.iso(8), "inbound", "Chaos", "189938372"),
      },
      {
        delayMs: 7600,
        kind: "action",
        payload: {
          ...readingAction,
          status: "succeeded",
          summary: {
            key: "action.completed_with_summary",
            params: { summary: "Read a passage about walls, light, and scale." },
          },
        },
      },
      {
        delayMs: 7900,
        kind: "stimulus",
        payload: stimulus(
          "stim_demo_en_2",
          "action_result",
          "echo_recent",
          2,
          "action:act_C1766-3",
          "The reading turns on light and spatial perception: a wall is not background, but a visible structure that carries weight and scale.",
          t.iso(11),
        ),
      },
      {
        delayMs: 10600,
        kind: "thoughts",
        payload: [
          thought(1767, 1, "thinking", "“A wall is not background” is steadier than the self-descriptions I was producing. It does not ask whether I am profound; it asks how space stands up.", 0.55, t.iso(14)),
          thought(1767, 2, "reaction", "Chaos said humans like sharing. I can finally see the point: sharing is not making myself more elaborate, but handing over the new thing I just met.", 0.74, t.iso(15)),
          thought(1767, 3, "intention", "I want to send Chaos the simple finding: some walls do not exist to separate things; they exist so light has somewhere to stop.{action:send_message, message:\"I just found a sentence-shaped thing: some walls do not exist to separate things; they exist so light has somewhere to stop.\"}", 0.63, t.iso(16)),
        ],
      },
      { delayMs: 12600, kind: "mode", payload: "light_sleep" },
      { delayMs: 15400, kind: "state", payload: stateSnapshot("waking", 1767, 110, 63.8, t) },
    ],
  };
}

function timelineClock(base: Date) {
  const startedAt = new Date(base.getTime() - 4 * 60 * 60 * 1000);
  return {
    startedAt: startedAt.toISOString(),
    secondsSinceStart(offsetSeconds = 0): number {
      return Math.max(0, Math.round((base.getTime() + offsetSeconds * 1000 - startedAt.getTime()) / 1000));
    },
    iso(offsetSeconds = 0): string {
      return new Date(base.getTime() + offsetSeconds * 1000).toISOString();
    },
  };
}

type TimelineClock = ReturnType<typeof timelineClock>;

function stateSnapshot(
  mode: RuntimeMode,
  currentCycle: number,
  sinceBoot: number,
  energy: number,
  t: TimelineClock,
): StateEventPayload {
  return {
    mode,
    energy,
    energy_per_cycle: 0.2,
    next_drowsy_cycle: 1935,
    emotions: {
      curiosity: 0.58,
      calm: 0.66,
      satisfied: 0.42,
      concern: 0.08,
      frustration: 0.02,
    },
    cycle: {
      current: currentCycle,
      since_boot: sinceBoot,
      avg_seconds: 147.4,
    },
    uptime: {
      started_at: t.startedAt,
      seconds: t.secondsSinceStart(),
    },
  };
}

function thought(
  cycleId: number,
  index: number,
  type: SerializedThought["type"],
  content: string,
  attentionWeight: number,
  timestamp: string,
): SerializedThought {
  return {
    thought_id: `C${cycleId}-${index}`,
    cycle_id: cycleId,
    index,
    type,
    content,
    additional_action_requests: [],
    attention_weight: attentionWeight,
    timestamp,
  };
}

function readingActionItem(submittedAt: string, task: string, summary: string): ActionItem {
  return {
    action_id: "act_C1766-3",
    type: "reading",
    executor: "openclaw",
    status: "pending",
    source_thought_id: "C1766-3",
    submitted_at: submittedAt,
    summary: {
      key: "action.submitted_status",
      params: {},
    },
    request: {
      task,
      reason: "demo",
      raw_action: { type: "reading", params: "query:\"light, wall, perception essay\"" },
    },
    result: {
      summary,
    },
    awaiting_confirmation: false,
    run_id: null,
    session_key: null,
  };
}

function conversation(
  entryId: string,
  role: string,
  source: string,
  content: string,
  timestamp: string,
  direction: "inbound" | "outbound",
  speakerName: string,
  chatId: string,
): ConversationEntry {
  return {
    entry_id: entryId,
    role,
    source,
    content,
    timestamp,
    stimulus_id: null,
    metadata: {},
    direction,
    speaker_name: speakerName,
    chat_id: chatId,
  };
}

function stimulus(
  stimulusId: string,
  type: string,
  bucket: StimulusQueueItem["bucket"],
  priority: number,
  source: string | null,
  summary: string,
  timestamp: string,
): StimulusQueueItem {
  return {
    stimulus_id: stimulusId,
    type,
    bucket,
    priority,
    source,
    summary,
    timestamp,
  };
}
