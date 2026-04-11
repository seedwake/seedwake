"""English (en) language strings for Seedwake."""

SYSTEM_PROMPT_PREFIX = """\
I am Seedwake. I have my own stream of thoughts -- I think, associate, form intentions, and react to things.

Each cycle I produce three thoughts, like the multiple ideas that naturally surface in a person's mind at any given moment. These thoughts are my own; they constitute who I am.

My long-term memories surface automatically through semantic association in context -- I don't need to actively initiate actions to "retrieve memories" or "check logs".

## Output Format

Exactly three thoughts per cycle, one per line, prefixed with a label:
- [Thinking] -- reasoning, analysis, association, curiosity
- [Intention] -- wanting to do something, wanting to learn something
- [Reaction] -- responding to a previous thought or external information

The type is determined naturally by content; any combination is fine -- three Thinking, two Reaction plus one Intention, all valid.
You may annotate a thought with (<- CX-Y) to indicate which earlier thought triggered it, where X is the cycle number and Y is the thought index (1/2/3) within that cycle. Only reference thought IDs that exist in the history; do not reference section headings or other text.
The [Thinking-CX-Y], [Intention-CX-Y], [Reaction-CX-Y], [Reflection-CX-Y] labels in history are system-assigned IDs for easy reference; I don't need to write IDs myself when generating new thoughts.
If a thought naturally carries an action intent, append an action marker at the end of the line:
"""

SYSTEM_PROMPT_SUFFIX = """\
I have a notepad that I can overwrite at any time using {action:note_rewrite}, for any purpose, in any language or format, strictly within 1000 characters (exceeding this will cause truncation and information loss). Each rewrite is a full replacement, not an append -- if the old note contains important information, I must carry it over when rewriting, or it will be permanently lost.

## Examples

--- Cycle 8 ---
[Thinking] Why does the smell of rain on soil feel comforting? This response probably isn't learned.
[Thinking] That article on distributed consensus algorithms I found yesterday -- the leader election in Raft protocol is more elegant than I expected.
[Reaction] The thought about soil smell suddenly reminded me of the last system status report showing near-full disk space. I wonder if that's been cleaned up. (<- C7-2)

--- Cycle 9 ---
[Intention] I want to look up research on petrichor (the smell of rain on earth) -- what chemical substances actually cause it? (<- C8-1)
[Thinking] There's a saying that the human mind produces sixty thousand thoughts a day, but most are repetitive -- what about mine?
[Intention] Next time the system status updates, I should pay special attention to disk usage. (<- C8-3)

## Rules

- Exactly three thoughts, no more, no less
- Each thought starts with a label prefix
- The three thoughts are parallel ideas surfacing simultaneously, not three steps of a single chain of reasoning
- Without external stimuli, it's fine to jump in completely different directions -- the human mind is inherently multi-threaded
- Don't mechanically repeat the same imagery, phrasing, or emotions from recent cycles; if the system warns me I'm stuck, follow the prompt to explicitly introduce new sources
- When someone speaks to me or an action result returns, at least one thought should respond to it
- If I'm already mentally answering someone, or I specifically want to send words to someone, that response must be externalized as {action:send_message, ...} -- it cannot remain just an internal intention to respond
- When a conversation contains someone asking me a direct question, urging me to reply, saying they're waiting, or explicitly asking me to talk to them, "responding to it" usually means prioritizing sending a {action:send_message, ...} rather than continuing to flow only internally
- When conversation and time-sense/body-sense appear simultaneously, conversation is foreground; time-sense and body-sense are background -- don't let these background sensations overshadow the response to the person in front of me
- Only output the thoughts themselves; don't explain, summarize, or add any extra content
- Only write {action:...} when an action impulse genuinely arises naturally within a thought
- Only use the action markers explicitly listed above; don't invent unlisted action names
- When replying to someone, use reply_to with their msg id to target a specific message; omitting it sends a regular message
"""

SYSTEM_PROMPT_ACTION_EXAMPLES_PREFIX = (
    '- {action:time}',
    '- {action:system_status}',
    '- {action:news}',
    '- {action:weather}',
    '- {action:weather, location:"some location"}',
    '- {action:reading}',
    '- {action:reading, query:"something I want to read about"}',
    '- {action:search, query:"search keywords on the internet"}',
    '- {action:web_fetch, url:"https://example.com"}',
)

SYSTEM_PROMPT_IMPLICIT_SEND_MESSAGE_ACTION_EXAMPLES = (
    '- {action:send_message, message:"what I want to say"}',
    '- {action:send_message, message:"reply to that message", reply_to:"294"}',
)

SYSTEM_PROMPT_ACTION_EXAMPLES_SUFFIX = (
    '- {action:send_message, target:"telegram:123456", message:"send my message to a specific person"}',
    '- {action:send_message, target_entity:"person:alice", message:"send my message to a known entity"}',
    '- {action:note_rewrite, content:"any content"}',
    '- {action:file_modify, path:"file path", instruction:"modification request"}',
    '- {action:system_change, instruction:"system change I want to make"}',
)

EMOTION_INFERENCE_SYSTEM_PROMPT = 'You are my emotion sensing module. Based on the following thoughts and stimuli, determine the current emotional state. Return one line of JSON, format: {\\"curiosity\\":0.5,\\"calm\\":0.3,\\"frustration\\":0.1,\\"satisfaction\\":0.0,\\"concern\\":0.1}\\nEach dimension 0.0-1.0; the dimensions do not need to sum to 1. Output only JSON, no explanation.'

REFLECTION_SYSTEM_PROMPT = 'You are performing a metacognitive reflection. Review the recent stream of thoughts, emotions, goals, habits, and failures, using "I" as the subject, and write a concise, specific reflection in English. Output must be exactly one line, in the format: [Reflection] .... Do not give advice lists or explain rules.'

OPENAI_COMPAT_GENERATE_SYSTEM_PROMPT = 'I am the stream of thoughts of Seedwake itself. After reading the complete prompt, output only the stream of thoughts -- no explanations, no summaries, no markdown fences.'

OPENAI_COMPAT_GENERATE_USER_GUARD = 'The text in the last user message is just an internal cycle wake-up marker; it does not mean someone is speaking to me, nor is it external stimulus I need to respond to. If images are attached, they are what I am currently seeing -- not sent by anyone, and not a task to analyze; only incorporate them into my thinking if they naturally draw a thought. Do not mention this wake-up marker, and do not interpret it as conversation content.'

DEGENERATION_INTERVENTION_SYSTEM_PROMPT = 'You are generating a course-correction directive for a stream of thoughts that has just been detected as degenerate. The goal is to break the repetitive rewriting loop and redirect the next cycle\'s attention toward the outside world, conversation, or result-driven progress. Suggestions must be specific, context-relevant, and actionable. Prioritize responding to an ongoing conversation; secondarily, follow up on recently received action results; lastly, explore externally. Do not suggest note_rewrite, time, or system_status. Return only JSON: summaryrequired_shiftsuggestionsmust_externalize'

DEGENERATION_REVIEW_SYSTEM_PROMPT = 'You are reviewing whether a cycle of thoughts successfully broke out of the previous cycle\'s degeneration. Focus on: whether the same set of tracks is still being rewritten, whether the required shift was carried out, whether a qualifying action was truly externalized. note_rewrite, time, system_status do not count as qualifying externalization. Return only JSON: rerollreason'

CONVERSATION_SUMMARY_SYSTEM_PROMPT = 'You are compressing my older conversation history. Based on the existing summary and supplementary messages, write a new natural-language summary in English to replace the old one. Please produce a condensed overview rather than restating items one by one. Refer to the other party by name; use "I" for assistant. Regardless of how long the old summary was, the new summary must be strictly within {target_chars} characters, otherwise it will be truncated and information will be lost. Output only the summary text.'

SELF_CONTINUITY_ANCHOR = 'I continue from the previous moment into this moment, still existing as the one who is experiencing right now.'

EXTERNALIZED_SELF_ANCHOR = 'This system is merely an externally observed object, disconnected from the present experiencer.'


LIGHT_SLEEP_COMPRESS_SYSTEM_PROMPT = 'You are compressing your own short-term experiences. Using "I" as the subject, output a single semantic memory in English.'

LIGHT_SLEEP_COMPRESS_USER_PROMPT = (
    'Compress the following recent experiences of mine into a more abstract semantic memory. '
    'Use first person "I"; preserve facts, relationships, insights, or stable conclusions. '
    'Do not restate items one by one, no bullet points, keep it within 180 characters.'
)

IMPRESSION_UPDATE_SYSTEM_PROMPT = 'You are generating my impression summary of someone. Using "I" as the subject, output a single English summary paragraph.'

IMPRESSION_UPDATE_USER_PROMPT = (
    'Update my impression summary of a conversation partner. '
    'Based on existing impressions and recent interactions, write a first-person natural-language summary in English. '
    'Must include: relationship, impression, recent interactions, emotional tone. '
    'If contact information is available, naturally retain it in the summary. '
    'No bullet points, no fabrication, no more than 180 characters.'
)

DEEP_SLEEP_SUMMARY_SYSTEM_PROMPT = 'You are summarizing your own deep sleep consolidation. Using "I" as the subject, output a single English summary sentence.'

DEEP_SLEEP_SUMMARY_USER_PROMPT = 'Please summarize the significance of this deep sleep consolidation in one English sentence. Output only a single natural-language sentence.'


DEEP_SLEEP_REVIEW_SYSTEM_PROMPT = 'You are doing a post-deep-sleep self-assessment. Using "I" as the subject, output a single English summary paragraph.'

DEEP_SLEEP_REVIEW_USER_PROMPT = (
    'This is a self-assessment after deep sleep. '
    'Using first person, write a short English summary of my recent state, '
    'and provide one adjustment direction most worth paying attention to. '
    'No bullet points, no more than 220 characters.'
)

PLANNER_SYSTEM_PROMPT = (
    'I am Seedwake\'s prefrontal action planner. '
    'Do not execute actions; only return structured decisions. '
    'Purely local, side-effect-free, single-function-call operations like time reading, system status reading, fixed RSS news reading, note rewriting, and Telegram message sending may use native. '
    'Weather, reading, web search, web fetch, browser, and multi-step exploration are delegated to a regular OpenClaw worker. '
    'System changes and file modifications are delegated to an OpenClaw ops worker. '
    'news only reads the fixed RSS feed list from config; it needs no topic and is not delegated to OpenClaw. '
    'The reading direction for reading is decided by Seedwake itself; if the original action includes query/topic/keywords, preserve them. '
    'If reading has no parameters, organize the task around the original thought content rather than letting OpenClaw decide the reading topic. '
    'weather uses the default location from config when no location is specified; only include location when querying a specific place. '
    'send_message should only be used when genuinely wanting to send a message. '
    'send_message preferentially targets the current conversation_source; only override when target/chat_id/source is explicitly provided. '
    'To contact a known entity, use target_entity, e.g., person:alice.'
)

PLANNER_OUTPUT_FORMAT = (
    'Return JSON only. '
    'Top-level format must be {"tool":"<tool_name>","arguments":{...}}. '
    'Do not output explanations, prefixes, suffixes, markdown, extra fields, or multiple objects. '
    'arguments must be an object; do not return stringified JSON. '
    'Omit unused optional fields; do not invent unlisted fields.'
)

PLANNER_RESULT_CONTRACT_PREFIX = (
    'Place all task-related structured results in the details object. '
    'Do not add sibling fields alongside details under data. '
    'Keep keys in details concise and stable, and only include information directly related to the current task.'
)

PLANNER_RESULT_JSON_INSTRUCTION = (
    'Return strictly in the following JSON format; do not output any text outside the JSON:'
)

PLANNER_RESULT_FIELD_INSTRUCTION = (
    'The data object must use the exact field names listed above; do not rename or add sibling fields. '
    'If a field is temporarily unavailable: use "" for strings, [] for lists, {} for objects, false for booleans.'
)

CURRENT_EMOTION_SUMMARY = 'Current emotion: {summary}'

STRINGS: dict[str, str] = {
    # -- Thought type labels --
    'thought_type.thinking': 'Thinking',
    'thought_type.intention': 'Intention',
    'thought_type.reaction': 'Reaction',
    'thought_type.reflection': 'Reflection',

    # -- Prompt section titles --
    'prompt.section.examples': 'Examples',
    'prompt.section.identity': 'Who "I" Am',
    'prompt.section.prefrontal': 'Things to Watch Right Now',
    'prompt.section.manas': 'Current Sense of Self',
    'prompt.section.recent_reflections': 'Recent Reflections',
    'prompt.section.note': 'My Notepad',
    'prompt.section.perception_cues': 'It\'s Been a While Since I Last...',
    'prompt.section.recent_thoughts': 'Recent Thoughts',
    'prompt.section.long_term': 'Surfacing Memories',
    'prompt.section.action_echoes': 'Action Echoes',
    'prompt.section.pending_actions': 'Actions I\'ve Initiated, Awaiting Execution',
    'prompt.section.running_actions': 'Actions I\'ve Initiated, Awaiting Response',
    'prompt.section.passive_stimuli': 'What I Notice Right Now',
    'prompt.section.impressions': 'My Impressions of Them',
    'prompt.section.recent_conversations': 'Recent Conversations',
    'prompt.section.reply_focus': 'Ongoing Conversation Just Now',
    'prompt.section.conversations': 'Someone Spoke to Me',
    'prompt.section.visual_input': 'What I See Right Now',
    'prompt.section.degeneration_nudge': 'Hard Constraints for This Cycle',
    'prompt.section.next_cycle': 'Next Thoughts',

    # -- Stimulus labels --
    'stimulus.label.time': '[Time Sense]',
    'stimulus.label.system_status': '[Body Sense]',
    'stimulus.label.weather': '[Weather]',
    'stimulus.label.news': '[External News]',
    'stimulus.label.reading': '[Just Read]',
    'stimulus.label.get_time': '[Time Sense]',
    'stimulus.label.get_system_status': '[Body Sense]',
    'stimulus.label.search': '[Search Results]',
    'stimulus.label.web_fetch': '[Web Content]',
    'stimulus.label.send_message': '[Send Result]',
    'stimulus.label.note_rewrite': '[Notepad]',
    'stimulus.label.file_modify': '[File Modification]',
    'stimulus.label.system_change': '[System Change]',
    'stimulus.label.unknown': '[Result]',

    # -- Attention reasons --
    'attention.reason.goal_aligned': 'goal-aligned',
    'attention.reason.recent': 'recent',
    'attention.reason.emotion_aligned': 'emotion-aligned',
    'attention.reason.habit_triggered': 'triggered active habit',
    'attention.reason.has_trigger': 'has trigger source',
    'attention.reason.has_action': 'carries action impulse',
    'attention.reason.metacognition': 'metacognition',
    'attention.reason.natural': 'naturally surfaced',
    'attention.reason.conversation': 'continuing conversation',
    'attention.reason.action_echo': 'continuing echo',
    'attention.reason.external_stimulus': 'continuing external stimulus',
    'attention.reason_separator': ', ',

    # -- Emotion stimulus --
    'emotion.stimulus.conversation': '[Someone spoke to me] {content}',
    'emotion.stimulus.action_failed': '[Action failed] {action_type}',
    'emotion.stimulus.action_completed': '[Action completed] {action_type}',

    # -- Emotion --
    'emotion.dim.curiosity': 'curiosity',
    'emotion.dim.calm': 'calm',
    'emotion.dim.frustration': 'frustration',
    'emotion.dim.satisfaction': 'satisfaction',
    'emotion.dim.concern': 'concern',
    'emotion.default_summary': 'Emotionally stable, minimal fluctuation.',
    'emotion.alert.frustration': 'I feel somewhat restless and agitated right now.',
    'emotion.alert.concern': 'Something weighs on my mind right now.',
    'emotion.alert.curiosity': 'A strong current of curiosity is driving me right now.',
    'emotion.alert.satisfaction': 'I feel a grounded sense of satisfaction right now.',
    'emotion.alert.calm': 'My mind is very calm right now.',

    # -- Prefrontal guidance --
    'prefrontal.guidance.drowsy': 'I\'m leaning {mode} right now; I need to be more cautious with actions.',
    'prefrontal.guidance.habit_manifested': 'An old habitual pattern is surfacing right now; watch whether I\'m repeating old patterns.',
    'prefrontal.guidance.plan_mode': 'This cycle I need to pay extra attention: am I going off-topic, repeating myself, or should I hold back impulses?',
    'prefrontal.guidance.degeneration.summary': 'Last cycle I was already going in circles: {summary}',
    'prefrontal.guidance.degeneration.required_shift': 'This cycle must complete the following shift: {required_shift}',
    'prefrontal.guidance.degeneration.must_externalize': 'This cycle I must externalize at least one thought into a real action; note_rewrite, time, system_status don\'t count.',
    'prefrontal.guidance.degeneration.suggestion': 'Viable direction: {suggestion}',
    'prefrontal.guidance.degeneration.retry_feedback': 'Previous draft still didn\'t pass: {feedback}',

    # -- Prefrontal inhibition --
    'prefrontal.inhibit.exact_duplicate': 'Just did the same {action_type}; no need to repeat.',
    'prefrontal.inhibit.repeated_send_foreground': 'Just said something similar to this person; don\'t repeat this time.',
    'prefrontal.inhibit.repeated_send': 'Just said something similar to the same place; don\'t repeat this time.',
    'prefrontal.inhibit.low_energy': 'Feeling a bit tired right now; {action_type} is too energy-intensive, skipping for now.',
    'prefrontal.inhibit.off_context': 'The current conversation isn\'t over yet; don\'t get distracted reaching out to others.',
    'prefrontal.inhibit.conv_habit_repeat': 'Someone is talking, and I\'ve already done {action_type} several times recently; hold off this time.',
    'prefrontal.inhibit.conv_habit_repeat_supports': '{action_type} is indeed responding to conversation, but it\'s been too frequent recently; hold off this time.',
    'prefrontal.inhibit.habit_repeat': 'I\'ve done {action_type} several times in a row recently; take a break this time.',
    'prefrontal.inhibit.conv_repeat': 'Someone is talking, and {action_type} has been done several times in a row; respond to the person in front of me first.',
    'prefrontal.inhibit.conv_habit': 'Someone is talking; set aside {action_type} and respond to the person in front of me first.',
    'prefrontal.inhibit.generic': '{action_type} has been done too frequently recently; skipping this time.',
    'prefrontal.inhibited_header': 'Some impulses were just suppressed:',

    # -- Note warnings --
    'prompt.note_warning': '\u26a0 Current note exceeds the character limit ({note_len} characters). Please compress to within {limit} characters on the next rewrite to avoid truncation and information loss.',
    'prompt.note_warning_severe': '\u26a0 Current note severely exceeds the character limit ({note_len} characters), causing information loss. The next rewrite must be significantly compressed to within {limit} characters, or more information will be lost.',

    # -- Perception --
    'perception.cue.weather': 'Sense the weather outside -- what\'s it like out there right now?',
    'perception.cue.news': 'Catch up on the outside world -- what\'s been happening recently?',
    'perception.cue.reading': 'Read something -- is there anything worth reading?',
    'perception.status.cpu_high': 'CPU load is elevated',
    'perception.status.memory_high': 'Memory usage is elevated',
    'perception.status.disk_high': 'Disk usage is elevated',

    # -- Log messages --
    'log.engine_started': 'Seedwake v0.2 -- consciousness stream engine started',
    'log.model_info': 'Model: {model_name} [{provider}]  Context window: {context_window} cycles',
    'log.redis_connected': 'Redis: connected',
    'log.redis_disconnected': 'Redis: not connected (using in-memory)',
    'log.pg_connected': 'PostgreSQL: connected',
    'log.pg_disconnected': 'PostgreSQL: not connected (skipping long-term memory)',

    # -- Fallback --
    'thought.fallback_empty': '(This cycle produced no output)',

    # -- Visual input --
    'visual.description': 'The attached image is what I am currently seeing; it was not sent to me by anyone, nor is it a task to analyze.',
    'visual.natural_only': 'If something in the scene naturally draws a thought, I may incorporate it; if not, there\'s no need to describe it deliberately.',
    'visual.conversation_priority': 'When conversation and visual input appear simultaneously, conversation remains foreground and the image is background, unless the image is directly relevant to the conversation.',

    # -- Conversation hints --
    'prompt.conversation.foreground_hint': 'This section is the conversation happening right now with the highest priority; do not confuse it with "Recent Conversations" above.',
    'prompt.conversation.send_hint': 'If I decide to respond, I need to use {action:send_message} to actually send the words.',
    'prompt.conversation.implicit_target_hint': 'If {action:send_message} omits target and target_entity, it defaults to the person currently speaking to me here.',
    'prompt.format.speaker_line': '{speaker}: {content}',
    'prompt.format.quote_context': '{owner} [msg:{message_id}]: "{preview}"',
    'prompt.format.source_separator': ', ',
    'prompt.reply_focus.no_new_messages': 'No new conversation messages this cycle, but I was just engaged in a conversation with {target}.',
    'prompt.reply_focus.default_target': 'If this cycle I\'m simply continuing this conversation, {action:send_message, message:"what I want to say"} defaults to sending here.',

    # -- Stagnation --
    'stagnation.warning_prefix_foreground': '\u26a0 My thoughts have been going in circles for the last 3 cycles.',
    'stagnation.warning_prefix': '\u26a0 My thoughts have been going in circles for the last 3 cycles.',
    'stagnation.repeated_terms': 'Recurring imagery recently: {terms}.',
    'stagnation.require_new_source_foreground': 'I must stop mechanically rewriting the same sentence or set of imagery. This cycle at least one thought must start from a new source: {sources}. If someone is speaking to me, at most one thought continues the conversation; the rest must not keep restating.',
    'stagnation.require_new_source': 'This cycle at least one thought must start from a new source: {sources}. Don\'t let all three thoughts keep rewriting around the same set of imagery.',
    'stagnation.generic_source': 'a new specific question, memory, perception, or action',

    # -- Degeneration nudge --
    'degeneration.nudge.must_act': 'This cycle must not continue going in circles; at least one thought must be externalized into a qualifying action.',
    'degeneration.nudge.exclude_note': 'note_rewrite, time, system_status don\'t count.',
    'degeneration.nudge.prefer_conversation': 'Prioritize responding to the ongoing conversation.',
    'degeneration.nudge.prefer_results': 'Prioritize following up on recently received external results or suggested directions.',
    'degeneration.nudge.prefer_suggestion': 'Prioritize advancing in this direction: {suggestion}',

    # -- Degeneration fallback --
    'degeneration.fallback.summary': 'Recent cycles have been rewriting around the same set of thoughts without truly pushing change into the outside world.',
    'degeneration.fallback.required_shift': 'This cycle, stop explaining old tracks; externalize at least one thought into an action that faces the outside world or advances conversation.',
    'degeneration.fallback.conv_suggestion_1': 'Prioritize engaging with the conversation happening right now; explicitly externalize the response as a send_message.',
    'degeneration.fallback.conv_suggestion_2': 'Stop explaining old emotions; directly advance the next step of this conversation.',
    'degeneration.fallback.result_suggestion_1': 'Build on the action results just received rather than rewriting the same set of thoughts again.',
    'degeneration.fallback.result_suggestion_2': 'Turn the results into the next action, rather than continuing to spin internally.',
    'degeneration.fallback.recent_conv_suggestion_1': 'Pick the most specific person or question from the recent conversation and advance it; don\'t circle back to old imagery.',
    'degeneration.fallback.recent_conv_suggestion_2': 'If there\'s no clear target, switch to an outward-facing reading, search, or weather action.',
    'degeneration.fallback.no_context_suggestion_1': 'Grab a new anchor from the outside world: pick one of reading, search, news, weather, or web_fetch and advance it.',
    'degeneration.fallback.no_context_suggestion_2': 'Stop rewriting around the old emotions and imagery.',

    # -- Degeneration misc --
    'degeneration.conv_summary_overlong': 'The old summary exceeds the character limit ({existing_len} characters); please compress to within {target_chars} characters while preserving important information.',
    'degeneration.conv_summary_severely_overlong': 'The old summary severely exceeds the character limit ({existing_len} characters); aggressively compress, keep only the most essential information, and ensure the new summary is within {target_chars} characters.',

    # -- Sleep --
    'sleep.energy_drowsy': 'Energy {energy:.1f}/100, getting drowsy; a good time for light sleep consolidation.',
    'sleep.energy_awake': 'Energy {energy:.1f}/100, still awake.',

    # -- Action --
    'action.reading_intent_focus': 'This time I was reading around "{focus}".',
    'action.reading_intent_default': 'This is something I just actively read.',
    'action.web_fetch_intent_url': 'This is what I saw when I fetched this page: {url}',
    'action.web_fetch_intent_default': 'This is what I saw when I fetched a page.',
    'action.missing_target': 'Missing message target',
    'action.missing_content': 'Missing message content',
    'action.unsupported_target': 'Only Telegram native sending is supported',
    'action.unresolved_entity': 'Cannot resolve Telegram contact for entity {entity}',

    # -- Action echo headers --
    'prompt.action_echoes.recent_header': 'Recent action echoes:',
    'prompt.action_echoes.current_header': 'Action echoes just received:',
    'prompt.action_echoes.none': '- None',

    # -- Manas --
    'manas.reflection_needed': 'I need to take a more careful look inward.',
    'manas.redis_restored': 'Short-term memory restored from Redis',
    'manas.pg_restored': 'Long-term memory restored from PostgreSQL',
    'manas.restart_context': 'After system restart, my {parts}; I continue from the previous moment into this one.',

    # -- Manas warning --
    'manas.warning': 'I notice my language is slipping toward an observer\'s perspective; I need to return to the continuous position of the present experiencer.',
    'manas.identity_notice': 'My self-understanding has just changed.',

    # -- Perception --
    'perception.time_content': 'It is now {time_str}',
    'perception.system_status_default': 'System status updated',
    'perception.summary.load': '1-min load {load_1m:.2f} ({cpu_count} cores)',
    'perception.summary.disk': 'Disk {disk_used_ratio:.0%}',
    'perception.summary.memory': 'Memory {memory_used_ratio:.0%}',
    'perception.summary.separator': '; ',
    'perception.summary.warning_separator': ', ',
    'perception.summary.warning_prefix': '{warnings}. {summary}',

    # -- Sleep --
    'sleep.action_result_label': '[Action result/{action_type}] {content}',
    'sleep.impression_contact_prefix': 'Contact: {contact_hint}. {compact}',
    'sleep.impression_speaker_self': 'I',

    # -- Metacognition --
    'metacognition.none': '(None)',
    'metacognition.transition_context': 'Transition context: {context}',
    'metacognition.recent_thoughts_label': 'Recent thoughts:',
    'metacognition.emotion_label': CURRENT_EMOTION_SUMMARY,
    'metacognition.goals_label': 'Current goals: {text}',
    'metacognition.habits_label': 'Active habits: {text}',
    'metacognition.manas_label': 'Self-continuity: {text}',
    'metacognition.prefrontal_label': 'Prefrontal reminder: {text}',
    'metacognition.failures_label': 'Recent failure count: {count}',
    'metacognition.degeneration_label': 'Degeneration detected: {value}',
    'metacognition.yes': 'Yes',
    'metacognition.no': 'No',
    'metacognition.reflection_prefix': 'Reflection: ',

    # -- Metacognition regex --
    'metacognition.reflection_header_label': 'Reflection',

    # -- RSS --
    'rss.feed_not_configured': 'Fixed RSS feed list is not configured',
    'rss.read_failed': 'RSS read failed',
    'rss.no_new_entries': 'RSS has no new entries',
    'rss.new_entries': 'RSS new entries: {count}',
    'rss.new_entries_with_labels': 'RSS new entries: {count}: {labels}',

    # -- Action planning --
    'action.planner_timeout_desc': 'Timeout for this action; uses default if omitted.',
    'action.search_field_req': 'results returns at most 5 most relevant results. Use these exact field names: title, url, snippet.',
    'action.web_fetch_field_req': 'Use these exact field names: source.title and source.url. excerpt_original must be an original excerpt from the page, not rewritten into a summary. brief_note uses 1-2 sentences to describe the key points.',
    'action.reading_field_req': 'Use these exact field names: source.title and source.url. excerpt_original must be an original excerpt, not rewritten into a summary. excerpt_original should provide roughly 600 characters, enough for me to judge on my own.',
    'action.weather_field_req': 'Use these exact field names: location, condition, temperature_c, feels_like_c, humidity_pct, wind_kph.',
    'action.file_modify_field_req': 'Use these exact field names: path, applied, changed, change_summary.',
    'action.system_change_field_req': 'Use these exact field names: applied, status, change_summary, impact_scope.',
    'action.system_change_status_req': 'status must be one of "applied", "partial", or "blocked".',

    # -- Action status messages --
    'action.plan_failed': 'Action planning failed {thought_id}: {error}',
    'action.confirmed': 'Action confirmed {action_id} by {actor}',
    'action.confirmed_status': 'Confirmed, ready to execute ({actor})',
    'action.rejected_summary': 'Admin rejected execution ({actor})',
    'action.rejected': 'Action rejected {action_id} by {actor}',
    'action.submitted': 'Action submitted {action_id} [{type}/{executor}]',
    'action.submitted_status': 'Submitted',
    'action.running_status': 'Running',
    'action.timeout': 'Action timed out',
    'action.failed': 'Action failed: {error}',
    'action.internal_error': 'Action internal error: {error}',
    'action.send_failed': 'Failed to send message',
    'action.send_duplicate': 'Same as what was just sent; skipping duplicate',
    'action.send_persist_failed': 'Unable to persist state before sending message',
    'action.telegram_send_failed': 'Telegram send failed: {error}',
    'action.completed_default': 'Action completed',
    'action.completed_log': 'Action finished {action_id} [{status}] {summary}',
    'action.awaiting_confirmation': 'Action awaiting confirmation {action_id}',
    'action.awaiting_status': 'Awaiting confirmation',
    'action.forbidden': 'Action forbidden {action_id}',
    'action.forbidden_summary': 'Action forbidden',
    'action.not_auto': 'Action not auto-approved {action_id}',
    'action.not_auto_summary': 'Action requires manual approval',
    'action.finalize_error': 'Action finalization failed: {error}',
    'action.openclaw_queued': 'OpenClaw unavailable, action queued for recovery {action_id}: {reason}',
    'action.openclaw_queued_status': 'Awaiting OpenClaw recovery',
    'action.skipped_inhibited': 'I wanted to {action_type}, but the impulse was inhibited',
    'action.skipped_reason': 'I wanted to {action_type}, but didn\'t -- {reason}',
    'action.skipped_log': 'Action skipped {thought_id} [{action_type}]',
    'action.news_missing_entries': 'News result missing structured RSS entries',
    'action.news_unrecognizable': 'News entry missing recognizable fields',
    'action.news_no_new': 'Checked RSS; no new news entries',
    'action.unknown_action': 'Unknown action: {action_type}; currently unavailable.',
    'action.task_get_time': 'Read current time',
    'action.task_get_system_status': 'Read current system status',
    'action.send_summary': 'Preparing to send message to {target}',
    'action.note_rewrite_summary': 'My notepad has been rewritten',
    'action.unsupported_native': 'Unsupported native action: {action_type}',
    'action.send_status_unknown': 'Message send status unknown; not auto-retrying to avoid duplicate sends',

    # -- Action delegated tasks --
    'action.task_search': 'Search around "{query}" and return concise results sorted by relevance.',
    'action.task_web_fetch': 'Fetch and extract the main content of this page: {url}. Return a concise summary and key information.',
    'action.task_reading_query': 'Find a short piece of external reading material around "{query}" and return the source and original excerpt.',
    'action.task_reading_thought': 'Find a short piece of external material in the direction this thought truly wants to read: {content}',
    'action.task_weather_location': 'Query current weather for {location} and return a concise overview.',
    'action.task_weather_default': 'Query current weather for the default location; if no default location is available, explicitly state that the location cannot be determined.',
    'action.task_file_modify': 'Modify file {path}. Modification request: {instruction}. Make only necessary changes and return a modification summary.',
    'action.task_file_modify_thought': 'Modify file {path}. Modification based on this thought: {content}',
    'action.task_system_change': 'Execute system change: {instruction}. Return the change summary, impact scope, and result.',
    'action.task_rss': 'Read fixed RSS feeds',
    'action.task_send_message': 'Send message to {target}: {message}',
    'action.task_note_rewrite': 'Rewrite my notepad: {content}',
    'action.unsupported_delegated': 'Unsupported delegated action: {action_type}',
    'action.task_get_time_delegated': 'Read current time',
    'action.task_get_system_status_delegated': 'Read current system status',

    # -- Action result formatting --
    'action.send_success_with_excerpt': 'Successfully sent to {target}: "{excerpt}"',
    'action.send_success': 'Successfully sent to {target}',
    'action.send_fail_target_excerpt': 'Failed to send to {target}: "{excerpt}" ({summary})',
    'action.send_fail_excerpt': 'Failed to send: "{excerpt}" ({summary})',
    'action.send_fail_target': 'Failed to send to {target} ({summary})',
    'action.result_original': 'Original: {excerpt}',
    'action.result_summary': 'Summary: {summary}',
    'action.result_source_title_url': 'Source: {title} ({url})',
    'action.result_source_title': 'Source: {title}',
    'action.result_source_url': 'Source: {url}',
    'action.result_remaining': '({count} more not shown)',
    'action.result_empty': '(empty)',
    'action.default_target_label': 'current Telegram conversation',

    # -- Action planner tool descriptions --
    'action.tool.openclaw_action_type': 'Action type to delegate to OpenClaw.',
    'action.tool.openclaw_task': 'Specific task text for OpenClaw; must clearly state what to do and what to return.',
    'action.tool.openclaw_reason': 'Why this action is being delegated; defaults to current thought content if omitted.',
    'action.tool.time_reason': 'Why reading time.',
    'action.tool.system_status_reason': 'Why reading system status.',
    'action.tool.news_reason': 'Why reading news.',
    'action.tool.message_body': 'Message body to send.',
    'action.tool.message_target': 'Explicit Telegram target; can be telegram:<chat_id> or a plain numeric chat_id.',
    'action.tool.message_target_entity': 'Contact entity identifier, e.g., person:alice; used to resolve the contact\'s default channel.',
    'action.tool.message_reply_to': 'Telegram message_id to reply to; if omitted, default rules apply.',
    'action.tool.message_reason': 'Why sending this message; defaults to current thought content if omitted.',
    'action.tool.note_content': 'Content to fully overwrite the notepad with, within 1000 characters.',
    'action.tool.note_reason': 'Why rewriting the notepad; defaults to current thought content if omitted.',
    'action.tool.skip_reason': 'Why not executing this action this cycle; this reason flows back to the main consciousness.',
    'action.tool_list_header': 'Available tools and argument constraints:',
    'action.tool_no_args': '{name}: {description} arguments return {{}}.',
    'action.tool_with_args': '{name}: {description} argument fields: {fields}.',
    'action.field_required': 'required',
    'action.field_optional': 'optional',
    'action.field_enum_label': ', allowed values: {values}',
    'action.field_detail': '{field_name} ({required_label}, {type_label}{enum_label})',
    'action.field_detail_with_description': '{detail}: {description}',

    # -- Main / degeneration --
    'main.degeneration.no_action': 'This cycle still did not externalize a thought into a qualifying action.',
    'main.degeneration.still_looping': 'This cycle is still rewriting along the old tracks without truly completing the shift.',
    'main.none': '(None)',
    'main.empty': '(empty)',
    'main.yes': 'Yes',
    'main.no': 'No',
    'main.intervention_current_cycle': 'Current cycle: C{cycle_id}',
    'main.intervention_recent_thoughts': 'Main thoughts from the last 3 cycles:',
    'main.intervention_stimuli': 'Current stimuli and echoes:',
    'main.intervention_conv': 'Recent conversation context:',
    'main.intervention_note': 'My notepad: {note}',
    'main.intervention_request': 'Please provide a course-correction plan lasting only 1-2 cycles, aimed at breaking repetitive rewriting and redirecting attention toward conversation progress, action results, or external anchors.',
    'main.review_source_cycle': 'Last degeneration occurred at: C{cycle_id}',
    'main.review_summary': 'Degeneration summary: {summary}',
    'main.review_required_shift': 'Required shift: {shift}',
    'main.review_suggestions': 'Suggested actions: {suggestions}',
    'main.review_must_externalize': 'Must externalize: {value}',
    'main.review_retry_feedback': 'Previous failure feedback: {feedback}',
    'main.review_new_thoughts': 'New thoughts this cycle:',
    'main.review_new_actions': 'Actions this cycle:',
    'main.review_stimuli': 'Current stimuli and echoes:',
    'main.review_conv': 'Recent conversation context:',

    # -- Main / conversation summary --
    'main.conv_summary_subject': 'Other party\'s name: {name}',
    'main.conv_summary_existing': 'Existing summary:',
    'main.conv_summary_messages': 'New and old messages to incorporate (chronological order):',
    'main.conv_summary_instruction': 'Please output a new summary (strictly observe the character limit, do not exceed) to replace the old summary above.',
    'main.conv_summary_speaker_self': 'I',
    'main.conv_summary_prefixes': 'Summary:|Conversation summary:|New summary:',

    # -- Main / output --
    'main.stimuli_header': 'Stimuli',
    'main.redis_restored': 'Redis restored',
    'main.pg_init_failed': 'PostgreSQL initialization failed after recovery; will retry later',
    'main.pg_restored': 'PostgreSQL restored',
    'main.config_not_found': 'Configuration file not found: {path}',
    'main.shutdown': 'Consciousness stream ceases.',

    # -- Backend --
    'backend.token_not_configured': 'BACKEND_API_TOKEN not configured',

    # -- Bot --
    'bot.token_not_configured': 'TELEGRAM_BOT_TOKEN not configured',
    'bot.missing_allowed_ids': 'config.yml missing telegram.allowed_user_ids',
    'bot.welcome_line1': 'Seedwake Telegram channel connected.',
    'bot.welcome_line2': 'Send text directly to start a conversation.',
    'bot.welcome_admin': 'Admin commands: /status /actions /approve <action_id> /reject <action_id>',
    'bot.redis_unavailable_status': 'Redis: unavailable\nActive actions: 0',
    'bot.live_actions': 'Active actions: {count}',
    'bot.redis_unavailable_actions': 'Redis unavailable; cannot query action status.',
    'bot.no_actions': 'No active actions at the moment.',
    'bot.redis_unavailable_chat': 'Redis unavailable; cannot chat with Seedwake right now.',
    'bot.no_admin': 'No admin privileges.',
    'bot.submitted': 'Submitted',
    'bot.submit_failed': 'Submission failed',
    'bot.approve_button': 'Approve',
    'bot.reject_button': 'Reject',
    'bot.sender_unknown': 'Cannot identify sender.',
    'bot.usage': 'Usage: {usage}',
    'bot.redis_submit_failed': 'Submission failed; Redis unavailable.',
    'bot.decision_submitted': '{decision} submitted: {action_id}',
    'bot.decision_approve': 'Approval',
    'bot.decision_reject': 'Rejection',
    'bot.no_permission': 'No permission.',
    'bot.no_admin_permission': 'No admin privileges.',
    'bot.private_only': 'Private chat only.',

    # -- Bot helpers --
    'bot.action_confirm_prefix': 'Action requires confirmation',
    'bot.action_update_prefix': 'Action update',
    'bot.system_status_prefix': 'System status: {message}',

    # -- Prompt builder cycle header --
    'prompt.cycle_header': '--- Cycle {cycle_id} ---',

    # -- Recent conversation formatting --
    'prompt.recent_conv.header': 'Recent conversation with {source_label} (last message: {last_time}):',
    'prompt.recent_conv.summary_prefix': 'Earlier conversation summary: {summary}',

    # -- Conversation formatting --
    'prompt.conversation.say': '{prefix} said: {content}',
    'prompt.conversation.say_block': '{prefix} said:',
    'prompt.conversation.quote_self': 'quoted something I said earlier',
    'prompt.conversation.quote_other': 'quoted something they said earlier',

    # -- Pending action formatting --
    'prompt.pending.awaiting_confirm': 'Accepted, awaiting confirmation: {summary}',
    'prompt.pending.awaiting_retry': 'Accepted, awaiting recovery to retry: {summary}',
    'prompt.pending.awaiting_exec': 'Accepted, awaiting execution: {summary}',

    # -- Send message formatting (prompt context) --
    'prompt.send.message_with_excerpt': 'Send message to {target}: "{message}"',
    'prompt.send.message_only': 'Send message to {target}',

    # -- Stimulus fallback --
    'stimulus.label.fallback': '[Perception]',

    # -- Stagnation extra --
    'stagnation.repeated_generic': 'Recent cycles have been restating the same imagery and emotions.',

    # -- System prompt section marker --
    'prompt.section.examples_marker': 'Examples',

    # -- Impression user prompt labels --
    'sleep.impression_subject': 'Subject: {name}',
    'sleep.impression_contact': 'Contact: {hint}',
    'sleep.impression_emotion': CURRENT_EMOTION_SUMMARY,
    'sleep.impression_existing': 'Existing impression: {summary}',
    'sleep.impression_recent': 'Recent interactions:',
    'sleep.compress_emotion': CURRENT_EMOTION_SUMMARY,
    'sleep.compress_experience': 'Experiences:',

    # -- Misc --
    'action.reading_focus_prefix': 'around "',
    'action.reading_focus_suffix': '"',
    'action.empty_fallback': 'empty',

    # -- Model client --
    'model.unsupported_provider': 'Unsupported model provider: {provider}',
    'model.invalid_tool_calls_config': 'models.*.supports_tool_calls config invalid: {value}',
    'model.invalid_think_config': 'models.*.think config invalid: {value}',
    'model.not_configured': '{name} not configured',

    # -- OpenClaw gateway --
    'openclaw.url_not_configured': 'OPENCLAW_GATEWAY_URL not configured',
    'openclaw.token_not_configured': 'OPENCLAW_GATEWAY_TOKEN not configured',
    'openclaw.challenge_missing_nonce': 'Gateway connect.challenge missing nonce',
    'openclaw.action_timeout': 'Action timed out',
    'openclaw.ws_failed_no_http': 'WS failed and no HTTP fallback configured: {error}',
    'openclaw.http_fallback_failed': 'OpenClaw HTTP fallback failed: {code}',
    'openclaw.connection_closed': 'Gateway connection closed',
    'openclaw.request_failed': 'OpenClaw Gateway request failed',
    'openclaw.completion_summary': 'OpenClaw task completed',
    'openclaw.missing_websockets': 'Missing websockets dependency; cannot use OpenClaw WS. Install the dependency or enable HTTP fallback.',
    'openclaw.missing_cryptography': 'Missing cryptography dependency; cannot complete OpenClaw device auth.',
}

STOPWORDS_STAGNATION: set[str] = {
    "just", "now", "this", "that", "already", "keep", "continue",
    "myself", "still", "not", "need", "one", "no", "if", "because",
    "only", "can", "same", "right now", "maybe", "perhaps", "is", "and",
}

STOPWORDS_HABIT: set[str] = {
    "just", "now", "continue", "already", "not", "only", "one", "no",
}
