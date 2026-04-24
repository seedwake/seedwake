// SSE stream connector. Subscribes to /api/seed/stream and dispatches events
// into the shared Seedwake state.

import type {
  ActionEventPayload,
  ActionsResponse,
  ConversationHistoryResponse,
  ReplyEventPayload,
  StateEventPayload,
  StatusEventPayload,
  StimuliResponse,
  ThoughtEventPayload,
} from "~/types/api";

function parseJson<T>(data: string, kind: string): T | null {
  try {
    return JSON.parse(data) as T;
  } catch (err) {
    console.error(`[seedwake] SSE ${kind} JSON parse failed:`, err);
    return null;
  }
}

export function useStream() {
  const api = useApi();
  const store = useSeedwakeState();
  const source = ref<EventSource | null>(null);
  const retryMs = ref(2_000);

  function connect() {
    if (import.meta.server) return;
    if (source.value) source.value.close();

    const es = new EventSource(api.streamUrl("/stream"));
    source.value = es;

    es.onopen = () => {
      store.connected.value = true;
      retryMs.value = 2_000;
    };

    es.onerror = () => {
      store.connected.value = false;
      es.close();
      source.value = null;
      // simple expo backoff up to 30s
      const delay = Math.min(retryMs.value, 30_000);
      retryMs.value = Math.min(retryMs.value * 2, 30_000);
      setTimeout(connect, delay);
    };

    es.addEventListener("thought", (e: MessageEvent<string>) => {
      const payload = parseJson<ThoughtEventPayload>(e.data, "thought");
      if (payload && Array.isArray(payload)) {
        store.ingestThoughts(payload);
      }
    });

    es.addEventListener("state", (e: MessageEvent<string>) => {
      const payload = parseJson<StateEventPayload>(e.data, "state");
      if (payload) store.applyState(payload);
    });

    es.addEventListener("action", (e: MessageEvent<string>) => {
      const payload = parseJson<ActionEventPayload>(e.data, "action");
      if (payload) {
        store.upsertAction({
          action_id: payload.action_id,
          type: payload.type,
          executor: payload.executor,
          status: payload.status,
          source_thought_id: payload.source_thought_id,
          summary: payload.summary,
          run_id: payload.run_id,
          session_key: payload.session_key,
          awaiting_confirmation: payload.awaiting_confirmation,
        });
      }
    });

    es.addEventListener("reply", (e: MessageEvent<string>) => {
      const payload = parseJson<ReplyEventPayload>(e.data, "reply");
      if (payload) {
        store.appendConversationReply(
          payload.target_name || "",
          payload.message,
          new Date().toISOString(),
        );
      }
    });

    es.addEventListener("status", (e: MessageEvent<string>) => {
      const payload = parseJson<StatusEventPayload>(e.data, "status");
      if (!payload) return;
      if (payload.mode) store.setMode(payload.mode);
      if (payload.message?.key) {
        store.pushStatus(`${payload.message.key} ${JSON.stringify(payload.message.params || {})}`);
      }
    });

    // Initial snapshot events (emitted by backend on SSE connect)
    es.addEventListener("actions", (e: MessageEvent<string>) => {
      const payload = parseJson<ActionsResponse>(e.data, "actions");
      if (payload?.items) store.setActions(payload.items);
    });

    es.addEventListener("conversation", (e: MessageEvent<string>) => {
      const payload = parseJson<ConversationHistoryResponse>(e.data, "conversation");
      if (payload?.items) store.setConversation(payload.items);
    });

    es.addEventListener("stimuli", (e: MessageEvent<string>) => {
      const payload = parseJson<StimuliResponse>(e.data, "stimuli");
      if (payload?.items) store.setStimuli(payload.items);
    });
  }

  function disconnect() {
    if (source.value) {
      source.value.close();
      source.value = null;
      store.connected.value = false;
    }
  }

  return { connect, disconnect, source };
}
