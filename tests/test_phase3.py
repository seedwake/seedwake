import unittest
from unittest.mock import MagicMock

from core.main import _publish_reply_event
from core.action import ActionManager, ActionPlan, pop_action_controls, push_action_control
from core.prompt_builder import build_prompt
from core.stimulus import CONVERSATION_HISTORY_KEY, StimulusQueue
from core.thought_parser import Thought


def _make_thought(
    cycle_id: int = 1,
    index: int = 1,
    thought_type: str = "意图",
    content: str = "我想查一下时间",
    action_request: dict | None = None,
) -> Thought:
    return Thought(
        thought_id=f"C{cycle_id}-{index}",
        cycle_id=cycle_id,
        index=index,
        type=thought_type,
        content=content,
        action_request=action_request,
    )


class _Planner:
    def __init__(self, plan: ActionPlan | None):
        self._plan = plan

    def plan(self, thought: Thought) -> ActionPlan | None:
        return self._plan


class _OpenClawExecutor:
    def __init__(self, result: dict[str, object] | None = None):
        self.calls = []
        self._result = result or {
            "ok": True,
            "summary": "搜索完成",
            "data": {"items": 1},
            "run_id": "run_1",
            "session_key": "seedwake:action:act_C1-1",
        }

    def execute(self, action):
        self.calls.append(action.action_id)
        return self._result


class StimulusQueueTests(unittest.TestCase):
    def test_conversation_push_is_recorded_in_history(self) -> None:
        class RedisStub:
            def __init__(self):
                self.lists = {}

            def rpush(self, key, payload):
                self.lists.setdefault(key, []).append(payload)

            def ltrim(self, key, start, end):
                items = self.lists.get(key, [])
                if start < 0:
                    start = max(len(items) + start, 0)
                if end < 0:
                    end = len(items) + end
                self.lists[key] = items[start:end + 1]

        redis_stub = RedisStub()
        queue = StimulusQueue(redis_client=redis_stub)

        queue.push("conversation", 1, "telegram:1", "你好")

        self.assertIn(CONVERSATION_HISTORY_KEY, redis_stub.lists)
        self.assertIn('"role": "user"', redis_stub.lists[CONVERSATION_HISTORY_KEY][0])

    def test_pop_many_prefers_higher_priority(self) -> None:
        queue = StimulusQueue(redis_client=None)
        queue.push("time", 4, "system:clock", "现在是早上")
        queue.push("conversation", 1, "user:alice", "你好")
        queue.push("action_result", 2, "action:1", "完成")

        popped = queue.pop_many(limit=2)

        self.assertEqual([stimulus.type for stimulus in popped], ["conversation", "action_result"])

    def test_requeue_front_restores_order(self) -> None:
        queue = StimulusQueue(redis_client=None)
        first = queue.push("conversation", 1, "user:alice", "hi")
        second = queue.push("time", 4, "system:clock", "tick")
        popped = queue.pop_many(limit=2)
        queue.requeue_front(popped)

        restored = queue.pop_many(limit=2)
        self.assertEqual([stimulus.stimulus_id for stimulus in restored], [first.stimulus_id, second.stimulus_id])

    def test_reply_event_is_recorded_in_history(self) -> None:
        class RedisStub:
            def __init__(self):
                self.lists = {}

            def rpush(self, key, payload):
                self.lists.setdefault(key, []).append(payload)

            def ltrim(self, key, start, end):
                items = self.lists.get(key, [])
                if start < 0:
                    start = max(len(items) + start, 0)
                if end < 0:
                    end = len(items) + end
                self.lists[key] = items[start:end + 1]

            def publish(self, channel, payload):
                return None

        redis_stub = RedisStub()
        queue = StimulusQueue(redis_client=None)
        stimulus = queue.push("conversation", 1, "telegram:1", "你好")

        _publish_reply_event(redis_stub, [stimulus], [_make_thought(thought_type="反应", content="你好，我在。")])

        self.assertIn(CONVERSATION_HISTORY_KEY, redis_stub.lists)
        self.assertIn('"role": "assistant"', redis_stub.lists[CONVERSATION_HISTORY_KEY][0])


class PromptBuilderPhase3Tests(unittest.TestCase):
    def test_prompt_includes_stimuli_and_running_actions(self) -> None:
        queue = StimulusQueue(redis_client=None)
        stimulus = queue.push("conversation", 1, "user:alice", "你好")
        action = MagicMock()
        action.action_id = "act_1"
        action.type = "search"
        action.executor = "openclaw"
        action.status = "running"
        action.request = {"task": "搜索最近的反馈"}
        action.source_content = "我想搜一下最近的反馈"

        prompt = build_prompt(
            3,
            {"self_description": "我是 Seedwake"},
            [],
            30,
            stimuli=[stimulus],
            running_actions=[action],
        )

        self.assertIn("## 当前外部刺激", prompt)
        self.assertIn("你好", prompt)
        self.assertIn("## 正在进行的行动", prompt)
        self.assertIn("act_1", prompt)


class ActionManagerTests(unittest.TestCase):
    def test_openclaw_action_generates_action_result_stimulus(self) -> None:
        queue = StimulusQueue(redis_client=None)
        planner = _Planner(ActionPlan("search", "openclaw", "搜索最近的反馈", 30, "测试"))
        executor = _OpenClawExecutor()
        manager = ActionManager(
            redis_client=None,
            stimulus_queue=queue,
            planner=planner,
            openclaw_executor=executor,
            auto_execute=["search"],
            require_confirmation=[],
            forbidden=[],
        )

        try:
            manager.submit_from_thoughts([
                _make_thought(action_request={"type": "search", "params": 'query:"反馈"'})
            ])
            manager.shutdown()
        finally:
            manager.shutdown()

        stimuli = queue.pop_many(limit=1)
        self.assertEqual(len(stimuli), 1)
        self.assertEqual(stimuli[0].type, "action_result")
        self.assertIn("搜索完成", stimuli[0].content)
        self.assertEqual(executor.calls, ["act_C1-1"])

    def test_confirmation_required_action_fails_immediately(self) -> None:
        queue = StimulusQueue(redis_client=None)
        planner = _Planner(ActionPlan("system_change", "openclaw", "修改系统配置", 30, "测试"))
        manager = ActionManager(
            redis_client=None,
            stimulus_queue=queue,
            planner=planner,
            openclaw_executor=_OpenClawExecutor(),
            auto_execute=["search"],
            require_confirmation=["system_change"],
            forbidden=[],
        )

        try:
            created = manager.submit_from_thoughts([
                _make_thought(action_request={"type": "system_change", "params": 'target:"sys"'})
            ])
            manager.shutdown()
        finally:
            manager.shutdown()

        self.assertEqual(created[0].status, "pending")
        self.assertTrue(created[0].awaiting_confirmation)
        self.assertEqual(queue.pop_many(limit=1), [])

    def test_native_action_is_allowed_without_auto_execute_list(self) -> None:
        queue = StimulusQueue(redis_client=None)
        planner = _Planner(ActionPlan("get_time", "native", "读取当前时间", 30, "测试"))
        manager = ActionManager(
            redis_client=None,
            stimulus_queue=queue,
            planner=planner,
            openclaw_executor=_OpenClawExecutor(),
            auto_execute=[],
            require_confirmation=[],
            forbidden=[],
        )

        try:
            created = manager.submit_from_thoughts([
                _make_thought(action_request={"type": "time", "params": ""})
            ])
            manager.shutdown()
        finally:
            manager.shutdown()

        self.assertEqual(created[0].status, "succeeded")
        stimulus = queue.pop_many(limit=1)[0]
        self.assertIn("get_time", stimulus.content)
        self.assertIn("run_id", created[0].result)
        self.assertIn("session_key", created[0].result)

    def test_confirmation_control_starts_pending_action(self) -> None:
        queue = StimulusQueue(redis_client=None)
        planner = _Planner(ActionPlan("system_change", "openclaw", "修改系统配置", 30, "测试"))
        executor = _OpenClawExecutor()
        manager = ActionManager(
            redis_client=None,
            stimulus_queue=queue,
            planner=planner,
            openclaw_executor=executor,
            auto_execute=["search"],
            require_confirmation=["system_change"],
            forbidden=[],
        )

        try:
            created = manager.submit_from_thoughts([
                _make_thought(action_request={"type": "system_change", "params": 'target:"sys"'})
            ])
            self.assertTrue(created[0].awaiting_confirmation)
            manager.apply_controls([{
                "action_id": created[0].action_id,
                "approved": True,
                "actor": "alice",
                "note": "",
            }])
            manager.shutdown()
        finally:
            manager.shutdown()

        self.assertEqual(executor.calls, [created[0].action_id])

    def test_confirmation_rejection_generates_action_result(self) -> None:
        queue = StimulusQueue(redis_client=None)
        planner = _Planner(ActionPlan("system_change", "openclaw", "修改系统配置", 30, "测试"))
        manager = ActionManager(
            redis_client=None,
            stimulus_queue=queue,
            planner=planner,
            openclaw_executor=_OpenClawExecutor(),
            auto_execute=[],
            require_confirmation=["system_change"],
            forbidden=[],
        )

        try:
            created = manager.submit_from_thoughts([
                _make_thought(action_request={"type": "system_change", "params": 'target:"sys"'})
            ])
            manager.apply_controls([{
                "action_id": created[0].action_id,
                "approved": False,
                "actor": "alice",
                "note": "不允许",
            }])
            manager.shutdown()
        finally:
            manager.shutdown()

        stimulus = queue.pop_many(limit=1)[0]
        self.assertEqual(stimulus.type, "action_result")
        self.assertIn("管理员拒绝执行", stimulus.content)


class ActionControlQueueTests(unittest.TestCase):
    def test_push_and_pop_action_controls(self) -> None:
        class RedisStub:
            def __init__(self):
                self.items = []

            def rpush(self, key, payload):
                self.items.append(payload)

            def lpop(self, key):
                if not self.items:
                    return None
                return self.items.pop(0)

        redis_stub = RedisStub()
        pushed = push_action_control(
            redis_stub,
            "act_1",
            approved=True,
            actor="alice",
            note="ok",
        )
        controls = pop_action_controls(redis_stub)

        self.assertTrue(pushed)
        self.assertEqual(len(controls), 1)
        self.assertEqual(controls[0]["action_id"], "act_1")


if __name__ == "__main__":
    unittest.main()
