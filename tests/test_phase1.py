import unittest
from unittest.mock import MagicMock, patch

from core.cycle import run_cycle
from core.model_client import create_model_client
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

    def test_parse_action_without_params(self) -> None:
        thoughts = parse_thoughts("[意图] 看看新闻 {action:news}\n", 1)

        self.assertEqual(thoughts[0].action_request, {"type": "news", "params": ""})

    def test_parse_action_without_params_allows_trailing_whitespace(self) -> None:
        thoughts = parse_thoughts("[意图] 看看新闻 {action:news }\n", 1)

        self.assertEqual(thoughts[0].action_request, {"type": "news", "params": ""})

    def test_parse_action_without_params_allows_newline_before_closing_brace(self) -> None:
        thoughts = parse_thoughts("[意图] 看看新闻 {action:news\n}\n", 1)

        self.assertEqual(thoughts[0].action_request, {"type": "news", "params": ""})

    def test_parse_action_ignores_action_marker_inside_simple_code_span(self) -> None:
        thoughts = parse_thoughts("[意图] 我在想 `{action:news}` 是什么语法\n", 1)

        self.assertIsNone(thoughts[0].action_request)

    def test_parse_action_ignores_action_marker_inside_code_span_with_prefix_text(self) -> None:
        thoughts = parse_thoughts("[意图] 我在想 `示例 {action:news}` 是什么语法\n", 1)

        self.assertIsNone(thoughts[0].action_request)

    def test_parse_action_prefers_real_marker_outside_code_span(self) -> None:
        thoughts = parse_thoughts("[意图] 我知道 `示例 {action:news}` 这个语法，但我现在真想看看新闻 {action:news}\n", 1)

        self.assertEqual(thoughts[0].action_request, {"type": "news", "params": ""})


class CycleTests(unittest.TestCase):
    @patch("core.cycle._call_ollama", return_value="[思考] a\n[意图] b\n[反应] c\n")
    def test_run_cycle_returns_parsed_thoughts(self, _) -> None:
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
    def test_run_cycle_fallback_on_unparseable(self, _) -> None:
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


class ModelClientTests(unittest.TestCase):
    def test_create_model_client_defaults_to_ollama(self) -> None:
        client = create_model_client({"name": "test-model"})

        self.assertEqual(client.provider, "ollama")
        self.assertTrue(client.supports_tool_calls)

    def test_openclaw_disables_tool_calls_by_default(self) -> None:
        with patch.dict("os.environ", {
            "OPENCLAW_HTTP_BASE_URL": "http://127.0.0.1:18789",
            "OPENCLAW_GATEWAY_TOKEN": "gateway-token",
        }, clear=False):
            client = create_model_client({
                "provider": "openclaw",
                "name": "openclaw/main",
            })

        self.assertFalse(client.supports_tool_calls)

    def test_openai_compatible_enables_tool_calls_by_default(self) -> None:
        with patch.dict("os.environ", {
            "OPENAI_COMPAT_BASE_URL": "https://api.example.com",
            "OPENAI_COMPAT_API_KEY": "secret",
        }, clear=False):
            client = create_model_client({
                "provider": "openai_compatible",
                "name": "gpt-compat",
            })

        self.assertTrue(client.supports_tool_calls)

    def test_supports_tool_calls_can_override_provider_default(self) -> None:
        with patch.dict("os.environ", {
            "OPENCLAW_HTTP_BASE_URL": "http://127.0.0.1:18789",
            "OPENCLAW_GATEWAY_TOKEN": "gateway-token",
        }, clear=False):
            client = create_model_client({
                "provider": "openclaw",
                "name": "openclaw/main",
                "supports_tool_calls": True,
            })

        self.assertTrue(client.supports_tool_calls)

    def test_openai_compatible_generate_uses_chat_completions(self) -> None:
        requests = []

        def fake_urlopen(req, timeout):
            _ = timeout
            requests.append(req)
            response = MagicMock()
            response.read.return_value = (
                '{"choices":[{"message":{"content":"[思考] a\\n[意图] b\\n[反应] c"}}]}'.encode("utf-8")
            )
            cm = MagicMock()
            cm.__enter__.return_value = response
            return cm

        with patch.dict("os.environ", {
            "OPENAI_COMPAT_BASE_URL": "https://api.example.com",
            "OPENAI_COMPAT_API_KEY": "secret",
        }, clear=False):
            with patch("core.model_client.request.urlopen", side_effect=fake_urlopen):
                client = create_model_client({
                    "provider": "openai_compatible",
                    "name": "gpt-compat",
                    "timeout": 5,
                })
                text = client.generate_text("prompt-body", {
                    "name": "gpt-compat",
                    "num_predict": 128,
                    "temperature": 0.7,
                })

        self.assertEqual(text, "[思考] a\n[意图] b\n[反应] c")
        self.assertEqual(requests[0].full_url, "https://api.example.com/v1/chat/completions")
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer secret")

    def test_openclaw_provider_adds_scopes_header(self) -> None:
        requests = []

        def fake_urlopen(req, timeout):
            _ = timeout
            requests.append(req)
            response = MagicMock()
            response.read.return_value = b'{"choices":[{"message":{"content":"ok"}}]}'
            cm = MagicMock()
            cm.__enter__.return_value = response
            return cm

        with patch.dict("os.environ", {
            "OPENCLAW_HTTP_BASE_URL": "http://127.0.0.1:18789",
            "OPENCLAW_GATEWAY_TOKEN": "gateway-token",
        }, clear=False):
            with patch("core.model_client.request.urlopen", side_effect=fake_urlopen):
                client = create_model_client({
                    "provider": "openclaw",
                    "name": "openclaw/main",
                    "timeout": 5,
                })
                client.chat(
                    model="openclaw/main",
                    messages=[{"role": "user", "content": "hello"}],
                )

        self.assertEqual(requests[0].get_header("Authorization"), "Bearer gateway-token")
        self.assertIn(
            ("X-openclaw-scopes", "operator.read, operator.write"),
            requests[0].header_items(),
        )


if __name__ == "__main__":
    unittest.main()
