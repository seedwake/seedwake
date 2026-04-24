// Seedwake English UI + backend message mirrors

export default {
  brand: {
    name: "Seedwake",
    zh_name: "心相续",
  },

  section: {
    present: "The Present",
    present_en: "the present",
    stream: "Cittasantāna",
    others: "Others",
    others_en: "others",
  },

  mode: {
    waking: "Waking",
    light_sleep: "Light Sleep",
    deep_sleep: "Deep Sleep",
    waking_en: "WAKING",
    light_sleep_en: "LIGHT SLEEP · INTEGRATING",
    deep_sleep_en: "DEEP SLEEP",
  },

  thought_type: {
    thinking: "Thinking",
    intention: "Intention",
    reaction: "Reaction",
    reflection: "Reflection",
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
    curiosity: "Curiosity",
    calm: "Calm",
    satisfied: "Satisfied",
    concern: "Concern",
    frustration: "Frustration",
  },
  emotion_en: {
    curiosity: "CURIOSITY",
    calm: "CALM",
    satisfied: "SATISFIED",
    concern: "CONCERN",
    frustration: "FRUSTRATION",
  },

  meter: {
    energy: "Energy",
    energy_en: "ENERGY",
    cycles: "Cycles",
    cycles_en: "CYCLES",
    uptime: "Uptime",
    uptime_en: "UPTIME",
    since_boot: "since boot",
    avg_seconds: "avg {seconds} s · 3 thoughts / cycle",
    next_drowsy: "↘ {per} / cycle · next drowsy ≈ C{cycle}",
    drowsy_integrating: "drowsy threshold crossed · integrating",
    since_awakening: "since {timestamp} awakening",
  },

  right: {
    conversation_label: "Conversation",
    conversation_label_en: "TELEGRAM",
    in_flight_label: "In flight",
    in_flight_label_en: "IN FLIGHT",
    stimulus_label: "Stimuli",
    stimulus_label_en: "STIMULUS QUEUE",
    priority: "P{n}",
    empty_conversation: "No recent conversation",
    empty_actions: "No actions in flight",
    empty_stimuli: "Queue is quiet",
    self_name: "SEEDWAKE",
  },

  stream_foot: {
    streaming: "stream · streaming from /api/stream",
    demo_streaming: "stream · static demo timeline",
    paused: "stream · paused · heartbeat still there",
    sse_types: "SSE · thought / conversation / action / status",
    demo_types: "DEMO · mock thought / action / conversation / stimulus",
    counter_streaming: "C{cycle} · streaming · ▲ scroll to recall",
    counter_attended: "{thought_id} · attended · ▲ scroll to recall",
    counter_paused: "paused at C{cycle} · integrating",
    counter_deep: "deep sleep",
    drowsy_banner: "integrating · light sleep · C{cycle} → memory.archive → habit.distill",
    resume_hint: "resume ≈ {eta}",
  },

  deep: {
    title: "Deep Sleep",
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

  // Mirror of backend i18n keys in I18nTextPayload envelopes.
  // Keep in sync with core/i18n/en.py.
  status: {
    redis_unavailable: "Redis unavailable",
    stream_error: "Event stream error",
    stream_connected: "Event stream connected: {username}",
    core_started: "Core started",
    deep_sleep: "Entering deep sleep",
    light_sleep: "Entering light sleep",
    redis_recovered: "Redis recovered",
    postgres_recovered: "PostgreSQL recovered",
  },

  action: {
    submitted_status: "Submitted",
    running_status: "Running",
    awaiting_status: "Awaiting confirmation",
    completed_default: "Action completed",
    completed_with_summary: "{summary}",
    confirmed_status: "Confirmed, ready to execute ({actor})",
    openclaw_queued_status: "Awaiting OpenClaw recovery",
    result_system_status: "{summary}",
    send_status_unknown: "Message send status unknown; not auto-retrying to avoid duplicate sends",
  },
};
