// SSE stream connector. Subscribes to /api/seed/stream and dispatches events
// into the shared Seedwake state.

import type {
  ActionEventPayload,
  ActionItem,
  ActionsResponse,
  ConversationEntry,
  ConversationHistoryResponse,
  ReplyEventPayload,
  StateEventPayload,
  StatusEventPayload,
  StimulusEventPayload,
  StimuliResponse,
  ThoughtEventPayload,
  ThoughtsResponse,
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
  let conversationRefreshInFlight: Promise<void> | null = null;
  let lastConversationRefreshAt = 0;

  async function refreshConversationSnapshot(force = false): Promise<void> {
    const now = Date.now();
    if (!force && now - lastConversationRefreshAt < 1_500) return;
    if (conversationRefreshInFlight) return conversationRefreshInFlight;
    lastConversationRefreshAt = now;
    conversationRefreshInFlight = api
      .get<ConversationHistoryResponse>("/conversation", { limit: 100 })
      .then((payload) => {
        if (payload?.items) store.setConversation(payload.items);
      })
      .catch((err) => {
        console.error("[seedwake] conversation snapshot refresh failed:", err);
      })
      .finally(() => {
        conversationRefreshInFlight = null;
      });
    return conversationRefreshInFlight;
  }

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
        void refreshConversationSnapshot();
      }
    });

    es.addEventListener("state", (e: MessageEvent<string>) => {
      const payload = parseJson<StateEventPayload>(e.data, "state");
      if (payload) {
        store.applyState(payload);
        void refreshConversationSnapshot();
      }
    });

    es.addEventListener("action", (e: MessageEvent<string>) => {
      const payload = parseJson<ActionEventPayload>(e.data, "action");
      if (payload) {
        // Only include `request` when the event actually carries one; the field
        // is optional and a stale-but-correct `request` from an earlier upsert
        // must not get wiped by a later event that omits it.
        const update: Partial<ActionItem> & { action_id: string } = {
          action_id: payload.action_id,
          type: payload.type,
          executor: payload.executor,
          status: payload.status,
          source_thought_id: payload.source_thought_id,
          summary: payload.summary,
          run_id: payload.run_id,
          session_key: payload.session_key,
          awaiting_confirmation: payload.awaiting_confirmation,
        };
        if (payload.request !== undefined) update.request = payload.request;
        store.upsertAction(update as ActionItem);
        void refreshConversationSnapshot();
      }
    });

    es.addEventListener("reply", (e: MessageEvent<string>) => {
      const payload = parseJson<ReplyEventPayload>(e.data, "reply");
      if (payload) void refreshConversationSnapshot(true);
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
    es.addEventListener("thoughts", (e: MessageEvent<string>) => {
      const payload = parseJson<ThoughtsResponse>(e.data, "thoughts");
      if (payload?.items) store.ingestThoughts(payload.items);
    });

    es.addEventListener("actions", (e: MessageEvent<string>) => {
      const payload = parseJson<ActionsResponse>(e.data, "actions");
      if (payload?.items) store.setActions(payload.items);
    });

    es.addEventListener("conversation", (e: MessageEvent<string>) => {
      const payload = parseJson<ConversationHistoryResponse>(e.data, "conversation");
      if (payload?.items) store.setConversation(payload.items);
    });

    // Per-entry live append (both inbound Telegram and outbound seedwake sends);
    // emitted from core/stimulus.append_conversation_history.
    es.addEventListener("conversation_entry", (e: MessageEvent<string>) => {
      const payload = parseJson<ConversationEntry>(e.data, "conversation_entry");
      if (payload?.entry_id) store.appendConversationEntry(payload);
    });

    es.addEventListener("stimulus", (e: MessageEvent<string>) => {
      const payload = parseJson<StimulusEventPayload>(e.data, "stimulus");
      if (payload?.stimulus_id) store.upsertStimulus(payload);
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
