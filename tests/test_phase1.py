import unittest
from unittest.mock import MagicMock, patch

from core.cycle import run_cycle
from core.thought_parser import fallback_thought, parse_thoughts


class ThoughtParserTests(unittest.TestCase):
    def test_parse_three_standard_thoughts(self) -> None:
        raw_output = (
            "[思考] 第一个念头\n"
            "[意图] 第二个念头 (← C1-1)\n"
            "[反应] 第三个念头\n"
        )
        thoughts = parse_thoughts(raw_output, 2)

        self.assertEqual(len(thoughts), 3)
        self.assertEqual([t.type for t in thoughts], ["思考", "意图", "反应"])
        self.assertEqual(thoughts[1].trigger_ref, "C1-1")

    def test_parse_multiline_thought(self) -> None:
        raw_output = (
            "[思考] 第一行\n"
            "第二行\n"
            "[意图] 下一轮我要更聚焦。 (← C1-1)\n"
            "[反应] 第三个念头\n"
        )
        thoughts = parse_thoughts(raw_output, 2)

        self.assertEqual(len(thoughts), 3)
        self.assertEqual(thoughts[0].content, "第一行\n第二行")
        self.assertEqual(thoughts[1].trigger_ref, "C1-1")

    def test_parse_discards_reflection_type(self) -> None:
        raw_output = "[思考] a\n[反思] b\n[意图] c\n"
        thoughts = parse_thoughts(raw_output, 1)

        self.assertEqual(len(thoughts), 2)
        self.assertEqual([t.type for t in thoughts], ["思考", "意图"])
        self.assertEqual(thoughts[0].content, "a")
        self.assertNotIn("反思", thoughts[0].content)

    def test_parse_discards_multiline_reflection(self) -> None:
        raw_output = "[思考] a\n[反思] b\n续行\n[意图] c\n"
        thoughts = parse_thoughts(raw_output, 1)

        self.assertEqual(len(thoughts), 2)
        self.assertEqual(thoughts[0].content, "a")
        self.assertEqual(thoughts[1].content, "c")

    def test_fallback_thought_wraps_raw_output(self) -> None:
        t = fallback_thought("模型输出了奇怪的东西", 5)

        self.assertEqual(t.type, "思考")
        self.assertEqual(t.content, "模型输出了奇怪的东西")
        self.assertEqual(t.thought_id, "C5-1")


class CycleTests(unittest.TestCase):
    @patch("core.cycle._call_ollama", return_value="[思考] a\n[意图] b\n[反应] c\n")
    def test_run_cycle_returns_parsed_thoughts(self, mock_call_ollama) -> None:
        mock_client = MagicMock()
        thoughts = run_cycle(
            mock_client,
            cycle_id=4,
            identity={"self_description": "我", "core_goals": "学", "self_understanding": "知"},
            recent_thoughts=[],
            context_window=30,
            model_config={"name": "test-model"},
        )

        self.assertEqual(len(thoughts), 3)
        self.assertEqual([t.thought_id for t in thoughts], ["C4-1", "C4-2", "C4-3"])

    @patch("core.cycle._call_ollama", return_value="无法解析的输出")
    def test_run_cycle_fallback_on_unparseable(self, mock_call_ollama) -> None:
        mock_client = MagicMock()
        thoughts = run_cycle(
            mock_client,
            cycle_id=7,
            identity={"self_description": "我"},
            recent_thoughts=[],
            context_window=30,
            model_config={"name": "test-model"},
        )

        self.assertEqual(len(thoughts), 1)
        self.assertEqual(thoughts[0].type, "思考")
        self.assertIn("无法解析的输出", thoughts[0].content)


if __name__ == "__main__":
    unittest.main()
