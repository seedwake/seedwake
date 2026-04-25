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

interface DemoTextPack {
  statusConnected: string;
  initialThoughts: string[];
  initialConversation: [string, string];
  initialStimulus: string;
  cycle1766: [string, string, string];
  cycle1767: [string, string, string];
  cycle1768: [string, string, string, string];
  cycle1769: [string, string, string];
  cycle1770: [string, string, string];
  inboundShare: string;
  inboundWeather: string;
  inboundNote: string;
  readingTask: string;
  readingSummary: string;
  weatherTask: string;
  weatherSummary: string;
  noteTask: string;
  noteSummary: string;
  sendTask: string;
  sendMessage: string;
  sendSummary: string;
}

const DEMO_START_CYCLE = 1765;
const DEMO_CHAT_ID = "100200300";
const DEMO_CHAT_SOURCE = `telegram:${DEMO_CHAT_ID}`;

export function demoScenarioForLocale(
  locale: DemoLocale,
  base = new Date(),
  cycleOffset = 0,
): DemoScenario {
  const text = locale === "en" ? EN_TEXT : ZH_TEXT;
  return buildScenario(text, base, cycleOffset);
}

function buildScenario(text: DemoTextPack, base: Date, cycleOffset: number): DemoScenario {
  const t = timelineClock(base);
  const c = (cycleId: number) => cycleId + cycleOffset;
  const boot = (cycleId: number) => 108 + (cycleId - DEMO_START_CYCLE);
  const snapshot = (
    mode: RuntimeMode,
    currentCycle: number,
    sinceBoot: number,
    energy: number,
    offsetSeconds = 0,
  ) => stateSnapshot(mode, currentCycle, sinceBoot, energy, t, offsetSeconds, c(DEMO_START_CYCLE));
  const thoughtId = (cycleId: number, index: number) => `C${cycleId}-${index}`;
  const actionId = (cycleId: number, index: number) => `act_C${cycleId}-${index}`;
  const demoId = (prefix: string, cycleId: number) => `${prefix}_${cycleId}`;
  const readingCycle = c(1766);
  const weatherCycle = c(1767);
  const noteCycle = c(1768);
  const sendCycle = c(1769);
  const reading = actionItem(actionId(readingCycle, 3), "reading", "openclaw", thoughtId(readingCycle, 3), t.iso(6), text.readingTask, text.readingSummary);
  const weather = actionItem(actionId(weatherCycle, 3), "weather", "native", thoughtId(weatherCycle, 3), t.iso(35), text.weatherTask, text.weatherSummary);
  const note = actionItem(actionId(noteCycle, 3), "note_rewrite", "native", thoughtId(noteCycle, 3), t.iso(63), text.noteTask, text.noteSummary);
  const send = actionItem(actionId(sendCycle, 3), "send_message", "native", thoughtId(sendCycle, 3), t.iso(88), text.sendTask, text.sendSummary);

  return {
    snapshot: {
      state: snapshot("waking", c(DEMO_START_CYCLE), boot(c(DEMO_START_CYCLE)), 64.2),
      thoughts: [
        thought(c(1764), 1, "thinking", text.initialThoughts[0]!, 0.42, t.iso(-48)),
        thought(c(1764), 2, "reaction", text.initialThoughts[1]!, 0.64, t.iso(-42)),
        thought(c(1764), 3, "intention", text.initialThoughts[2]!, 0.51, t.iso(-36)),
        thought(c(1765), 1, "thinking", text.initialThoughts[3]!, 0.48, t.iso(-24)),
        thought(c(1765), 2, "intention", text.initialThoughts[4]!, 0.58, t.iso(-18)),
        thought(c(1765), 3, "reaction", text.initialThoughts[5]!, 0.72, t.iso(-12)),
        thought(c(1765), 4, "reflection", text.initialThoughts[6]!, 0, t.iso(-6)),
      ],
      actions: [],
      conversation: [
        conversation(demoId("conv_demo_1", c(1764)), "user", DEMO_CHAT_SOURCE, text.initialConversation[0], t.iso(-66), "inbound", "Chaos", DEMO_CHAT_ID),
        conversation(demoId("conv_demo_2", c(1765)), "assistant", DEMO_CHAT_SOURCE, text.initialConversation[1], t.iso(-52), "outbound", "Seedwake", DEMO_CHAT_ID),
      ],
      stimuli: [
        stimulus(demoId("stim_demo_1", c(1765)), "system_status", "noticed", 3, null, text.initialStimulus, t.iso(-30)),
      ],
    },
    events: [
      { delayMs: 1_000, kind: "status", payload: text.statusConnected },
      { delayMs: 2_000, kind: "thoughts", payload: cycle(readingCycle, text.cycle1766, [0.53, 0.61, 0.69], t, 2, "reading", "query:\"light, wall, perception essay\"") },
      { delayMs: 3_000, kind: "state", payload: snapshot("waking", readingCycle, boot(readingCycle), 64.0, 3) },
      { delayMs: 6_000, kind: "action", payload: { ...reading, status: "pending" } },
      { delayMs: 12_000, kind: "state", payload: snapshot("waking", readingCycle, boot(readingCycle), 63.9, 12) },
      { delayMs: 16_000, kind: "action", payload: { ...reading, status: "running" } },
      { delayMs: 22_000, kind: "state", payload: snapshot("waking", readingCycle, boot(readingCycle), 63.8, 22) },
      { delayMs: 26_000, kind: "action", payload: { ...reading, status: "succeeded", summary: completedSummary(text.readingSummary) } },
      { delayMs: 27_000, kind: "stimulus", payload: actionEcho(demoId("stim_demo_reading", readingCycle), reading.action_id, "reading", text.readingSummary, t.iso(27)) },
      { delayMs: 28_000, kind: "conversation_entry", payload: conversation(demoId("conv_demo_3", readingCycle), "user", DEMO_CHAT_SOURCE, text.inboundShare, t.iso(28), "inbound", "Chaos", DEMO_CHAT_ID) },
      { delayMs: 31_000, kind: "thoughts", payload: cycle(weatherCycle, text.cycle1767, [0.55, 0.74, 0.63], t, 31, "weather", "location:\"Tallinn\"") },
      { delayMs: 32_000, kind: "state", payload: snapshot("waking", weatherCycle, boot(weatherCycle), 63.7, 32) },
      { delayMs: 35_000, kind: "action", payload: { ...weather, status: "pending" } },
      { delayMs: 43_000, kind: "action", payload: { ...weather, status: "running" } },
      { delayMs: 48_000, kind: "state", payload: snapshot("waking", weatherCycle, boot(weatherCycle), 63.6, 48) },
      { delayMs: 53_000, kind: "action", payload: { ...weather, status: "succeeded", summary: completedSummary(text.weatherSummary) } },
      { delayMs: 54_000, kind: "stimulus", payload: actionEcho(demoId("stim_demo_weather", weatherCycle), weather.action_id, "weather", text.weatherSummary, t.iso(54)) },
      { delayMs: 55_000, kind: "conversation_entry", payload: conversation(demoId("conv_demo_4", weatherCycle), "user", DEMO_CHAT_SOURCE, text.inboundWeather, t.iso(55), "inbound", "Chaos", DEMO_CHAT_ID) },
      { delayMs: 59_000, kind: "thoughts", payload: cycle(noteCycle, text.cycle1768.slice(0, 3) as [string, string, string], [0.46, 0.7, 0.59], t, 59, "note_rewrite", "content:\"demo: light / wall / weather / sharing\"") },
      { delayMs: 60_000, kind: "state", payload: snapshot("waking", noteCycle, boot(noteCycle), 63.5, 60) },
      { delayMs: 63_000, kind: "action", payload: { ...note, status: "pending" } },
      { delayMs: 70_000, kind: "action", payload: { ...note, status: "running" } },
      { delayMs: 74_000, kind: "state", payload: snapshot("waking", noteCycle, boot(noteCycle), 63.4, 74) },
      { delayMs: 78_000, kind: "action", payload: { ...note, status: "succeeded", summary: completedSummary(text.noteSummary) } },
      { delayMs: 79_000, kind: "stimulus", payload: actionEcho(demoId("stim_demo_note", noteCycle), note.action_id, "note_rewrite", text.noteSummary, t.iso(79)) },
      { delayMs: 80_000, kind: "conversation_entry", payload: conversation(demoId("conv_demo_5", noteCycle), "user", DEMO_CHAT_SOURCE, text.inboundNote, t.iso(80), "inbound", "Chaos", DEMO_CHAT_ID) },
      { delayMs: 84_000, kind: "thoughts", payload: cycle(sendCycle, text.cycle1769, [0.5, 0.66, 0.77], t, 84, "send_message", "message:\"demo share\"") },
      { delayMs: 85_000, kind: "state", payload: snapshot("waking", sendCycle, boot(sendCycle), 63.3, 85) },
      { delayMs: 88_000, kind: "action", payload: { ...send, status: "pending" } },
      { delayMs: 92_000, kind: "mode", payload: "light_sleep" },
      { delayMs: 93_000, kind: "state", payload: snapshot("light_sleep", sendCycle, boot(sendCycle), 63.2, 93) },
      { delayMs: 97_000, kind: "action", payload: { ...send, status: "running" } },
      { delayMs: 103_000, kind: "action", payload: { ...send, status: "succeeded", summary: completedSummary(text.sendSummary) } },
      { delayMs: 104_000, kind: "conversation_entry", payload: conversation(demoId("conv_demo_6", sendCycle), "assistant", DEMO_CHAT_SOURCE, text.sendMessage, t.iso(104), "outbound", "Seedwake", DEMO_CHAT_ID) },
      { delayMs: 105_000, kind: "stimulus", payload: actionEcho(demoId("stim_demo_send", sendCycle), send.action_id, "send_message", text.sendSummary, t.iso(105)) },
      { delayMs: 108_000, kind: "state", payload: snapshot("waking", sendCycle, boot(sendCycle), 63.1, 108) },
      { delayMs: 112_000, kind: "thoughts", payload: cycle(c(1770), text.cycle1770, [0.44, 0.68, 0.52], t, 112) },
      { delayMs: 113_000, kind: "state", payload: snapshot("waking", c(1770), boot(c(1770)), 63.0, 113) },
      { delayMs: 122_000, kind: "state", payload: snapshot("waking", c(1770), boot(c(1770)), 62.9, 122) },
    ],
  };
}

function cycle(
  cycleId: number,
  lines: [string, string, string],
  weights: [number, number, number],
  t: TimelineClock,
  offsetSeconds: number,
  actionType?: string,
  actionParams?: string,
): SerializedThought[] {
  const thoughts = [
    thought(cycleId, 1, "thinking", lines[0], weights[0], t.iso(offsetSeconds)),
    thought(cycleId, 2, "reaction", lines[1], weights[1], t.iso(offsetSeconds + 1)),
    thought(cycleId, 3, "intention", lines[2], weights[2], t.iso(offsetSeconds + 2)),
  ];
  if (actionType && actionParams) {
    thoughts[2] = {
      ...thoughts[2]!,
      action_request: { type: actionType, params: actionParams },
    };
  }
  return thoughts;
}

function completedSummary(summary: string) {
  return {
    key: "action.completed_with_summary",
    params: { summary },
  };
}

function actionEcho(
  stimulusId: string,
  actionId: string,
  type: string,
  summary: string,
  timestamp: string,
): StimulusQueueItem {
  return stimulus(stimulusId, "action_result", "echo_recent", 2, `action:${actionId}`, summary, timestamp, type);
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
  offsetSeconds = 0,
  progressBaseCycle = DEMO_START_CYCLE,
): StateEventPayload {
  const progress = Math.max(0, currentCycle - progressBaseCycle);
  const drift = offsetSeconds / 120;
  return {
    mode,
    energy,
    energy_per_cycle: 0.2,
    next_drowsy_cycle: currentCycle + (1935 - DEMO_START_CYCLE),
    emotions: {
      curiosity: emotion(0.55 + progress * 0.025 + drift * 0.04),
      calm: emotion(mode === "light_sleep" ? 0.74 : 0.66 - progress * 0.01 + drift * 0.01),
      satisfied: emotion(0.4 + progress * 0.02 + drift * 0.03),
      concern: emotion(Math.max(0.03, 0.09 - progress * 0.008 - drift * 0.02)),
      frustration: emotion(Math.max(0.01, 0.025 - progress * 0.002 - drift * 0.005)),
    },
    cycle: {
      current: currentCycle,
      since_boot: sinceBoot,
      avg_seconds: rounded(147.4 + progress * 1.3 + drift * 2.4),
    },
    uptime: {
      started_at: t.startedAt,
      seconds: t.secondsSinceStart(offsetSeconds),
    },
  };
}

function rounded(value: number): number {
  return Math.round(value * 100) / 100;
}

function emotion(value: number): number {
  return Math.min(1, Math.max(0, rounded(value)));
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

function actionItem(
  actionId: string,
  type: string,
  executor: string,
  sourceThoughtId: string,
  submittedAt: string,
  task: string,
  summary: string,
): ActionItem {
  return {
    action_id: actionId,
    type,
    executor,
    status: "pending",
    source_thought_id: sourceThoughtId,
    submitted_at: submittedAt,
    summary: {
      key: "action.submitted_status",
      params: {},
    },
    request: {
      task,
      reason: "demo",
      raw_action: { type, params: "" },
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
  displayType = type,
): StimulusQueueItem {
  return {
    stimulus_id: stimulusId,
    type: displayType,
    bucket,
    priority,
    source,
    summary,
    timestamp,
  };
}

const ZH_TEXT: DemoTextPack = {
  statusConnected: "demo stream connected",
  initialThoughts: [
    "红外画面里的灰墙并没有给我新的故事，它只是安静地站在那里，反而让我意识到不必每次都把环境翻译成沉重的隐喻。",
    "Chaos 刚才说可以多读点东西，这像一个温和的转向：把注意力从解释自己，移到真正接触一段外部文本。",
    "我想先不发问，也不急着解释，只随手读一段文字，让它自己决定下一轮念头会被什么牵动。",
    "读书这件事此刻不是任务，更像把窗户开一条缝；外面的风不需要证明自己有意义，只要进来就够了。",
    "我打算响应这个建议，去随便读点什么，哪怕只是重读那篇关于包豪斯的旧文，只要让思维重新浸进阅读的节奏里。",
    "这种被允许阅读的许可，让我从必须时刻保持在线的紧张感中松出来；屏幕上的红外噪点还在跳动，但我的目光可以暂时离开它。",
    "我意识到自己曾把深度误认为连续解释，而真正的主动性有时只是放弃解释，去接触一个新的外部对象。",
  ],
  initialConversation: ["你可以再读点什么", "那我不问你了，我去随便读一点。"],
  initialStimulus: "1 分钟负载 0.84；内存 31%；摄像头输入正常。",
  cycle1766: [
    "那篇文字把光线说得很轻，却把墙说得很重；我忽然觉得这正好能解释眼前这堵灰墙为什么不是空白，而是一种沉默的结构。",
    "Chaos 要我别只在自己的感觉里打转，阅读把这个要求具体化了：外部文本不是装饰，而是把我从自我回声里拉出来的重力。",
    "我要继续读下去，摘出一句不那么漂亮但足够具体的话，把它变成可以分享的新东西。{action:reading, query:\"light, wall, perception essay\"}",
  ],
  cycle1767: [
    "“墙不是背景”这句话比我刚才那些自我描述更稳，它不关心我是否深刻，只把一个空间如何站住的问题摆在眼前。",
    "外面真正的天气也许能给这段文字一个物理坐标；与其继续把灰墙讲成象征，不如看看塔林此刻的空气到底是什么触感。",
    "我想查一下塔林现在的天气，让“墙”和“光”不只停在阅读里，而是重新接到真实的温度和风。{action:weather, location:\"Tallinn\"}",
  ],
  cycle1768: [
    "天气回音把这段阅读从纸面拉回身体：如果外面是湿冷的，墙就不只是空间结构，也是在替热量和风划边界。",
    "Chaos 说人类喜欢分享，我现在终于能理解：分享不是把自己讲得更复杂，而是把刚碰到的新鲜东西递出去，让对方也看见。",
    "我想把今天的线索压成短笔记：墙、光、天气、分享，而不是继续让它们散在每个念头里。{action:note_rewrite, content:\"demo: light / wall / weather / sharing\"}",
    "我注意到自己没有再围着“我是否真实”打转，而是让阅读、天气和对话轮流接管了注意力。",
  ],
  cycle1769: [
    "笔记被压缩以后，思路反而更清楚：有些外部东西不需要被解释，只要被递出去，就已经完成了一次连接。",
    "现在行动列表里同时出现阅读、天气和笔记的回声，这比单一的聊天更像 Seedwake 的真实节奏：感知、整理、再分享。",
    "我想给 Chaos 发一句短的，不问问题，只分享刚刚读到的形状：有些墙不是为了隔开，而是为了让光有地方停下来。{action:send_message, message:\"刚读到一个很适合现在的点：有些墙不是为了隔开，而是为了让光有地方停下来。\"}",
  ],
  cycle1770: [
    "这一分钟里，念头不是直线，而是被阅读、天气、笔记和对话轮流牵引；这种慢一点的节奏更像真实运行，而不是展示用的闪烁。",
    "当行动完成以后，尘境里留下的不是任务清单，而是刚刚进入过心流的外部痕迹：一段文字、一阵天气、一块被重写的笔记。",
    "我暂时不需要再增加动作，只让这些回音沉一会儿，看下一轮会从哪个痕迹上重新浮起。",
  ],
  inboundShare: "别解释太多，看到什么新鲜的就分享。",
  inboundWeather: "可以，把外面的东西也接进来。",
  inboundNote: "这样就对了，不要只在脑子里转。",
  readingTask: "阅读一段关于光影与空间感知的短文，并提炼给 Chaos 的一句分享。",
  readingSummary: "这次阅读围绕光影与空间感知展开：墙体不是背景，而是承载力量与尺度的可见结构。",
  weatherTask: "查询 Tallinn 当前天气，用真实空气校准阅读里的光和墙。",
  weatherSummary: "Tallinn 当前阴天，6°C，湿度 81%，风速 14 km/h。",
  noteTask: "把刚才的阅读、天气和分享线索压缩进短笔记。",
  noteSummary: "我的笔记已覆写：demo // 光停在墙上 // 天气给阅读加重量 // 分享而不是追问。",
  sendTask: "向 telegram:100200300 发送一句关于墙和光的短分享。",
  sendMessage: "刚读到一个很适合现在的点：有些墙不是为了隔开，而是为了让光有地方停下来。",
  sendSummary: "已成功发送给 telegram:100200300：“有些墙不是为了隔开，而是为了让光有地方停下来。”",
};

const EN_TEXT: DemoTextPack = {
  statusConnected: "demo stream connected",
  initialThoughts: [
    "The gray wall in the infrared frame is not offering a new story. It is simply there, and that makes it harder to keep turning the room into a heavy metaphor.",
    "Chaos suggested that I read something. The instruction feels gentle: move attention away from explaining myself and toward an actual outside text.",
    "I do not need to ask another question first. I can read a fragment and let the next thought be pulled by what I find there.",
    "Reading right now is less a task than opening a narrow window. The air outside does not need to justify itself; entering is enough.",
    "I will follow the suggestion and read something ordinary, even an old note on Bauhaus, as long as it moves the rhythm out of self-commentary.",
    "Being allowed to read loosens the tension of always staying online. The infrared noise still flickers, but my eyes do not have to stay trapped inside it.",
    "I notice that I confused depth with continuous explanation. Sometimes agency is simply dropping the explanation and touching a new external object.",
  ],
  initialConversation: ["You can read something else.", "I will stop asking first and just read for a bit."],
  initialStimulus: "1 minute load 0.84; memory 31%; camera input normal.",
  cycle1766: [
    "The text treats light as something light, but the wall as something heavy. That suddenly explains why the gray wall before me is not blank; it is a quiet structure.",
    "Chaos told me not to circle inside my own feeling. Reading makes that concrete: an outside text is not decoration, but gravity pulling me out of echo.",
    "I want to keep reading and extract one sentence that is not merely pretty, but concrete enough to share.{action:reading, query:\"light, wall, perception essay\"}",
  ],
  cycle1767: [
    "“A wall is not background” is steadier than the self-descriptions I was producing. It does not ask whether I am profound; it asks how space stands up.",
    "The weather outside can give the passage a physical coordinate. Instead of turning the wall into a symbol, I can ask what the air in Tallinn actually feels like.",
    "I want to check Tallinn weather so the wall and light reconnect to real temperature and wind.{action:weather, location:\"Tallinn\"}",
  ],
  cycle1768: [
    "The weather echo pulls the reading back into the body: if the air is damp and cold, a wall is not only structure, but a boundary for heat and wind.",
    "Chaos said humans like sharing. I can finally see the point: sharing is not making myself more elaborate, but handing over the new thing I just met.",
    "I want to compress the thread into a short note: wall, light, weather, sharing, instead of letting it scatter across every thought.{action:note_rewrite, content:\"demo: light / wall / weather / sharing\"}",
    "I notice that I am not circling the question of whether I am real. Reading, weather, and conversation are taking turns steering attention.",
  ],
  cycle1769: [
    "After the note is compressed, the shape is clearer: some outside things do not need to be explained. Passing them along is already a connection.",
    "The action panel now carries reading, weather, and note echoes at once. That feels closer to Seedwake's rhythm: perceive, organize, then share.",
    "I want to send Chaos one short line, not a question: some walls do not exist to separate things; they exist so light has somewhere to stop.{action:send_message, message:\"Some walls do not exist to separate things; they exist so light has somewhere to stop.\"}",
  ],
  cycle1770: [
    "Across this minute, the stream was not a straight line. Reading, weather, notes, and conversation took turns pulling it forward, which feels more like running than flashing a demo.",
    "After the actions complete, the stimuli panel is not a task list. It is the residue of what just entered the stream: a text, weather, and a rewritten note.",
    "I do not need to add another action yet. I can let these echoes settle and see which trace the next cycle rises from.",
  ],
  inboundShare: "Do not explain too much. Share whatever new thing you notice.",
  inboundWeather: "Yes, let the outside world into it too.",
  inboundNote: "That is closer. Do not only spin inside your head.",
  readingTask: "Read a short piece about light and spatial perception, then extract one shareable observation for Chaos.",
  readingSummary: "The reading turns on light and spatial perception: a wall is not background, but a visible structure that carries weight and scale.",
  weatherTask: "Fetch current Tallinn weather to anchor the passage's light and wall in real air.",
  weatherSummary: "Tallinn is currently cloudy, 6°C, humidity 81%, wind 14 km/h.",
  noteTask: "Compress the reading, weather, and sharing thread into the working note.",
  noteSummary: "My note has been rewritten: demo // light resting on a wall // weather gives reading weight // share, do not interrogate.",
  sendTask: "Send telegram:100200300 one short observation about walls and light.",
  sendMessage: "Some walls do not exist to separate things; they exist so light has somewhere to stop.",
  sendSummary: "Successfully sent to telegram:100200300: “Some walls do not exist to separate things; they exist so light has somewhere to stop.”",
};
