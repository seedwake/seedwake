# i18n Migration — Remaining Work

## What's done (Step 1-3)
- `core/i18n/__init__.py` — init(), t(), thought_types(), localized_thought_type(), stopwords(), validate_against()
- `core/i18n/zh.py` — thought type labels, section titles, stimulus labels, emotion labels, prefrontal guidance/inhibition, perception cues, log messages, visual input text, note warnings, fallback thought, stopwords
- `core/thought_parser.py` — lazy-built regex from i18n labels, canonical keys (thinking/intention/reaction/reflection)
- All files: thought.type comparisons changed from Chinese to canonical keys
- All tests: thought type references updated + i18n init added
- 334 tests passing

## What remains (Step 4-10)

### Priority 1: prompt_builder.py (172 lines)
Large blocks to extract:
- SYSTEM_PROMPT_PREFIX — entire multi-line block → `"prompt.system_prefix"`
- SYSTEM_PROMPT_ACTION_EXAMPLES_PREFIX/SUFFIX — action example lines
- SYSTEM_PROMPT_SUFFIX — note description + examples + rules → `"prompt.system_suffix"`
- PASSIVE_STIMULUS_LABELS / ACTION_ECHO_LABELS dicts — already partially in zh.py, need to wire up
- All _render_section() calls — already have section title keys in zh.py, need to replace hardcoded strings
- _format_conversations() / _format_reply_focus() — instruction text
- Stagnation warning text
- Degeneration nudge text
- _emotion_alert() — alert texts already in zh.py
- _format_next_cycle() — "--- 第 {cycle_id} 轮 ---"

### Priority 2: action.py (135 lines)
- Planner system prompt (lines 1460-1482) — entire block
- Tool descriptions (lines 1700-1770) — JSON field descriptions
- Action status messages — "行动提交", "行动结束", etc.
- Error messages — "缺少消息目标", etc.
- Reading/news stimulus content formatting

### Priority 3: main.py (75 lines)
- Degeneration intervention/review system prompts
- Conversation summary system prompt
- Startup log messages (already in zh.py, need to wire up)
- Console output formatting

### Priority 4: Other core files (80 lines total)
- sleep.py (28) — LLM prompts for semantic/impression/deep sleep
- metacognition.py (21) — reflection prompt, prompt building
- prefrontal.py (19) — already in zh.py, need to wire up
- emotion.py (13) — inference prompt, dimension names
- perception.py (11) — cues, status formatting
- model_client.py (12) — system instructions
- manas.py (7) — anchor texts

### Priority 5: bot/ (27 lines)
- bot/main.py, bot/helpers.py — Telegram formatting

### Priority 6: en.py + config split
- Create core/i18n/en.py with all English translations
- Split config.example.yml into zh and en versions

## Recommended approach for Codex
Process one file at a time:
1. Read the file, identify all Chinese strings
2. Add new keys to zh.py
3. Replace hardcoded strings with t() calls
4. Run tests to verify
5. Move to next file
