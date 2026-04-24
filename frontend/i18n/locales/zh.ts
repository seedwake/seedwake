// Seedwake 中文 UI + 部分 backend 消息镜像

export default {
  brand: {
    name: "Seedwake",
    zh_name: "心相续",
  },

  section: {
    present: "此刻",
    present_en: "the present",
    stream: "心相续",
    others: "他者",
    others_en: "others",
  },

  mode: {
    waking: "清醒",
    light_sleep: "浅睡",
    deep_sleep: "深睡",
    waking_en: "WAKING",
    light_sleep_en: "LIGHT SLEEP · INTEGRATING",
    deep_sleep_en: "DEEP SLEEP",
  },

  thought_type: {
    thinking: "思考",
    intention: "意图",
    reaction: "反应",
    reflection: "反思",
  },
  thought_type_en: {
    thinking: "THINKING",
    intention: "INTENT",
    reaction: "REACTION",
    reflection: "REFLECTION",
  },

  attention: {
    attended: "ATTENDED",
  },

  emotion: {
    curiosity: "好奇",
    calm: "平静",
    satisfied: "满足",
    concern: "关切",
    frustration: "沮丧",
  },
  emotion_en: {
    curiosity: "CURIOSITY",
    calm: "CALM",
    satisfied: "SATISFIED",
    concern: "CONCERN",
    frustration: "FRUSTRATION",
  },

  meter: {
    energy: "精力",
    energy_en: "ENERGY",
    cycles: "循环",
    cycles_en: "CYCLES",
    uptime: "时长",
    uptime_en: "UPTIME",
    since_boot: "since boot",
    avg_seconds: "avg {seconds} s · 3 thoughts / cycle",
    next_drowsy: "↘ {per} / cycle · next drowsy ≈ C{cycle}",
    drowsy_integrating: "drowsy threshold crossed · integrating",
    since_awakening: "since {timestamp} awakening",
  },

  right: {
    conversation_label: "对话",
    conversation_label_en: "TELEGRAM",
    in_flight_label: "行动",
    in_flight_label_en: "IN FLIGHT",
    stimulus_label: "尘境",
    stimulus_label_en: "STIMULUS QUEUE",
    priority: "P{n}",
    empty_conversation: "暂无对话",
    empty_actions: "暂无进行中的行动",
    empty_stimuli: "尘境已静",
    self_name: "SEEDWAKE",
  },

  stream_foot: {
    streaming: "流 · streaming from /api/stream",
    paused: "流 · paused · 心跳仍在",
    sse_types: "SSE · thought / reply / action / status",
    counter_streaming: "C{cycle} · streaming · ▲ scroll to recall",
    counter_attended: "{thought_id} · 注意 · ▲ scroll to recall",
    counter_paused: "paused at C{cycle} · integrating",
    counter_deep: "deep sleep",
    drowsy_banner: "整理中 · light sleep · C{cycle} → memory.archive → habit.distill",
    resume_hint: "resume ≈ {eta}",
  },

  deep: {
    title: "深睡",
    subtitle: "deep sleep · the scroll rolled",
    seal_glyph: "藏",
  },

  footmark: "SANTĀNA · CYCLE STREAM V1.0",

  action_state: {
    pending: "PENDING",
    running: "RUNNING",
    succeeded: "COMPLETED",
    failed: "FAILED",
    awaiting: "AWAITING",
  },

  stimulus_type: {
    conversation: "conversation",
    action_result: "action result",
    time: "time",
    system_status: "system status",
    news: "news",
    weather: "weather",
    reading: "reading",
    custom: "custom",
  },

  dev: {
    label: "DEV · MODE",
  },

  // Mirror of backend i18n keys that appear in I18nTextPayload envelopes.
  // Keep in sync with core/i18n/zh.py.
  status: {
    redis_unavailable: "Redis 不可用",
    stream_error: "事件流错误",
    stream_connected: "事件流已连接：{username}",
    core_started: "核心已启动",
    deep_sleep: "进入深睡",
    light_sleep: "进入浅睡",
    redis_recovered: "Redis 已恢复",
    postgres_recovered: "PostgreSQL 已恢复",
  },

  action: {
    submitted_status: "已提交",
    running_status: "执行中",
    awaiting_status: "等待确认",
    completed_default: "行动完成",
    completed_with_summary: "{summary}",
    confirmed_status: "已确认，准备执行（{actor}）",
    openclaw_queued_status: "等待 OpenClaw 恢复",
    result_system_status: "{summary}",
    send_status_unknown: "消息发送状态未知，为避免重复发送，未自动重试",
  },
};
