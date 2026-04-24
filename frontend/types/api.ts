// Seedwake backend contract — mirrors core/common_types.py TypedDicts.
// Keep in sync when backend payload shapes change.

export type ThoughtType = "thinking" | "intention" | "reaction" | "reflection";
export type RuntimeMode = "waking" | "light_sleep" | "deep_sleep";

export interface I18nTextPayload {
  key: string;
  params: Record<string, unknown>;
}

export interface RawActionRequest {
  type: string;
  params: string;
}

export interface SerializedThought {
  thought_id: string;
  cycle_id: number;
  index: number;
  type: ThoughtType | string;
  content: string;
  trigger_ref?: string | null;
  action_request?: RawActionRequest | null;
  additional_action_requests?: RawActionRequest[];
  attention_weight: number;
  timestamp: string;
}

// SSE thought events publish a list (one full cycle)
export type ThoughtEventPayload = SerializedThought[];

export interface ActionEventPayload {
  action_id: string;
  type: string;
  executor: string;
  status: string;
  source_thought_id?: string;
  summary: I18nTextPayload;
  run_id: string | null;
  session_key: string | null;
  awaiting_confirmation: boolean;
}

export interface ReplyEventPayload {
  source: string;
  message: string;
  stimulus_id: string | null;
  target_name?: string;
  target_source?: string;
}

export interface StatusEventPayload {
  message: I18nTextPayload;
  username?: string;
  mode?: RuntimeMode;
}

export interface StateEmotionsPayload {
  curiosity: number;
  calm: number;
  satisfied: number;
  concern: number;
  frustration: number;
}

export interface StateCyclePayload {
  current: number;
  since_boot: number;
  avg_seconds: number;
}

export interface StateUptimePayload {
  started_at: string;
  seconds: number;
}

export interface StateEventPayload {
  mode: RuntimeMode;
  energy: number;
  energy_per_cycle: number;
  next_drowsy_cycle: number;
  emotions: StateEmotionsPayload;
  cycle: StateCyclePayload;
  uptime: StateUptimePayload;
}

export type StimulusBucket = "noticed" | "echo_current" | "echo_recent";

export interface StimulusQueueItem {
  stimulus_id: string;
  type: string;
  bucket: StimulusBucket;
  priority: number;
  source: string | null;
  summary: string;
  timestamp: string;
}

export interface ConversationEntry {
  entry_id: string;
  role: string;
  source: string;
  content: string;
  timestamp: string;
  stimulus_id: string | null;
  metadata: Record<string, unknown>;
  direction?: string;
  speaker_name?: string;
  chat_id?: string;
  username?: string;
  full_name?: string;
  message_id?: string;
}

export interface ConversationHistoryResponse {
  ok: boolean;
  items: ConversationEntry[];
  count: number;
  requested_by: string;
}

export interface ThoughtsResponse {
  ok: boolean;
  items: SerializedThought[];
  count: number;
  requested_by: string;
}

// Backend-side ActionItem — a superset of ActionEventPayload.
// Fields we rely on for rendering come from backend/routes/query._action_response_item.
export interface ActionItem {
  action_id: string;
  type: string;
  executor?: string;
  status: string;
  source_thought_id?: string;
  summary: I18nTextPayload;
  run_id?: string | null;
  session_key?: string | null;
  awaiting_confirmation?: boolean;
  submitted_at?: string;
  request?: Record<string, unknown>;
  result?: Record<string, unknown> | null;
  timeout_seconds?: number;
}

export interface ActionsResponse {
  ok: boolean;
  items: ActionItem[];
  count: number;
  requested_by: string;
}

export interface StimuliResponse {
  ok: boolean;
  items: StimulusQueueItem[];
  count: number;
  requested_by: string;
}

export type SseEventName = "thought" | "state" | "action" | "reply" | "status";

export type SseEvent =
  | { type: "thought"; payload: ThoughtEventPayload }
  | { type: "state"; payload: StateEventPayload }
  | { type: "action"; payload: ActionEventPayload }
  | { type: "reply"; payload: ReplyEventPayload }
  | { type: "status"; payload: StatusEventPayload }
  // Initial snapshot events — reuse the response shapes.
  | { type: "actions"; payload: ActionsResponse }
  | { type: "conversation"; payload: ConversationHistoryResponse }
  | { type: "stimuli"; payload: StimuliResponse };

export const EMOTION_DIMENSIONS: (keyof StateEmotionsPayload)[] = [
  "curiosity",
  "calm",
  "satisfied",
  "concern",
  "frustration",
];
