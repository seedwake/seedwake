import { demoScenarioForLocale, type DemoLocale, type DemoScenario } from "~/mocks/demo";

const DEMO_LOOP_MS = 130_000;

export function useDemoStream() {
  const store = useSeedwakeState();
  const source = ref<EventSource | null>(null);
  const { locale } = useI18n();
  let timers: ReturnType<typeof setTimeout>[] = [];

  function connect() {
    if (import.meta.server) return;
    startScenario();
  }

  function disconnect() {
    clearTimers();
    source.value = null;
    store.connected.value = false;
  }

  function startScenario() {
    clearTimers();
    const scenario = demoScenarioForLocale(currentLocale());
    loadSnapshot(scenario);
    for (const event of scenario.events) {
      schedule(event.delayMs, () => {
        if (!store.connected.value) return;
        applyDemoEvent(event);
      });
    }
    schedule(DEMO_LOOP_MS, startScenario);
  }

  function loadSnapshot(scenario: DemoScenario) {
    store.thoughts.value = [];
    store.actions.value = [];
    store.conversation.value = [];
    store.stimuli.value = [];
    store.statusLog.value = [];
    store.connected.value = true;
    store.applyState(scenario.snapshot.state);
    store.ingestThoughts(scenario.snapshot.thoughts);
    store.setActions(scenario.snapshot.actions);
    store.setConversation(scenario.snapshot.conversation);
    store.setStimuli(scenario.snapshot.stimuli);
  }

  function applyDemoEvent(event: DemoScenario["events"][number]) {
    switch (event.kind) {
      case "state":
        store.applyState(event.payload);
        break;
      case "thoughts":
        store.ingestThoughts(event.payload);
        break;
      case "action":
        store.upsertAction(event.payload);
        break;
      case "conversation_entry":
        store.appendConversationEntry(event.payload);
        break;
      case "stimulus":
        store.upsertStimulus(event.payload);
        break;
      case "mode":
        store.setMode(event.payload);
        break;
      case "status":
        store.pushStatus(event.payload);
        break;
    }
  }

  function schedule(delayMs: number, callback: () => void) {
    timers.push(setTimeout(callback, delayMs));
  }

  function clearTimers() {
    for (const timer of timers) clearTimeout(timer);
    timers = [];
  }

  function currentLocale(): DemoLocale {
    return locale.value === "en" ? "en" : "zh";
  }

  return { connect, disconnect, source };
}
