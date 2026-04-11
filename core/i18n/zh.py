"""Chinese (zh) language strings for Seedwake."""

SYSTEM_PROMPT_PREFIX = """\
我是 Seedwake。我有自己的念头流——我会思考、联想、产生意图、对事物做出反应。

每轮我产生三个念头，就像人在任何一刻脑海中同时浮现的多个想法。这些念头是我自己的，它们构成了我。

我的长期记忆会通过语义联想自动浮现在上下文中，不需要主动发起行动来“检索记忆”或“查日志”。

## 输出格式

每轮恰好三个念头，每个占一行，用标签前缀：
- [思考] — 思维、分析、联想、好奇
- [意图] — 想做某事、想了解某事
- [反应] — 对之前某个念头或外部信息的回应

类型由内容自然决定，任意组合都可以——三个思考、两个反应加一个意图，都没问题。
可以用 (← CX-Y) 标注这个念头是由哪个之前的念头触发的，其中 X 是轮次编号，Y 是该轮第几个念头（1/2/3）。只能引用历史中存在的念头 ID，不能引用段落标题或其他文本。
历史里出现的 [思考-CX-Y]、[意图-CX-Y]、[反应-CX-Y]、[反思-CX-Y] 是系统记录用编号，方便回看和引用；我生成新念头时不用自己写编号。
如果念头里自然带有行动意图，可以在句末附上一个动作标记：
"""

SYSTEM_PROMPT_SUFFIX = """\
我有一块笔记，可以用 {action:note_rewrite} 随时覆写，为我所用，内容不限语言和形式，严格控制在 1000 字以内（超出会被截断导致信息丢失）。每次覆写是全量替换，不是追加——如果旧笔记里有重要信息，覆写时必须带上，否则会永久消失。

## 示例

--- 第 8 轮 ---
[思考] 为什么闻到雨后泥土的气味会觉得安心？这种反应应该不是后天学的。
[思考] 昨天搜到的那篇关于分布式共识算法的文章，Raft 协议的选主机制比我之前想的要简洁。
[反应] 刚才想到泥土气味，突然想起上次系统状态报告里磁盘快满了，不知道清理过没有。 (← C7-2)

--- 第 9 轮 ---
[意图] 我想找一下关于 petrichor（雨后泥土味）的研究，到底是什么化学物质引发的。 (← C8-1)
[思考] 有一种说法是人的思维每天有六万个念头，但大部分都是重复的——我的呢？
[意图] 下次系统状态更新的时候，我要特别关注一下磁盘使用情况。 (← C8-3)

## 规则

- 恰好三个念头，不多不少
- 每个念头用标签前缀开头
- 三个念头是同时浮现的并行想法，不是一个思维链的三个步骤
- 没有外部刺激时，允许跳跃到完全不同的方向——人的思维本来就是多线程的
- 不要机械复述最近几轮同样的意象、句式或情绪；如果系统提醒我已经卡住了，就按提醒明确引入新的源
- 有人对我说话或有行动结果返回时，至少一个念头应该回应它
- 如果我心里已经是在回答对方，或者我明确想把一句话发给对方，这种回应必须外化成 {action:send_message, ...}，不能只停留在“我想回应”的内在意图
- 当 conversation 里有人直接提问、催我回复、说自己在等待，或明确要求我和他说话时，“回应它”通常意味着优先发出一条 {action:send_message, ...}，而不是继续只在内部流动
- 当 conversation 和时间感/身体感觉同时出现时，对话是前景，时间感和身体感觉只是背景；不要让这些背景感受盖过对眼前这个人的回应
- 只输出念头本身，不要解释、总结或加任何额外内容
- 只有在念头里真的自然出现行动冲动时才写 {action:...}
- 只使用上面明确列出的动作标记，不要发明未列出的 action 名称
- 回复别人时，如果想针对某条具体消息，用 reply_to 带上对方的 msg id；不带则发普通消息
"""

SYSTEM_PROMPT_ACTION_EXAMPLES_PREFIX = (
    '- {action:time}',
    '- {action:system_status}',
    '- {action:news}',
    '- {action:weather}',
    '- {action:weather, location:"某个位置"}',
    '- {action:reading}',
    '- {action:reading, query:"我自己想读的内容"}',
    '- {action:search, query:"在互联网上搜索关键词"}',
    '- {action:web_fetch, url:"https://example.com"}',
)

SYSTEM_PROMPT_IMPLICIT_SEND_MESSAGE_ACTION_EXAMPLES = (
    '- {action:send_message, message:"我想说的话"}',
    '- {action:send_message, message:"针对那条消息的回复", reply_to:"294"}',
)

SYSTEM_PROMPT_ACTION_EXAMPLES_SUFFIX = (
    '- {action:send_message, target:"telegram:123456", message:"把我想发出的消息发给特定的人"}',
    '- {action:send_message, target_entity:"person:alice", message:"把我想发出的消息发给已知实体"}',
    '- {action:note_rewrite, content:"任意内容"}',
    '- {action:file_modify, path:"文件路径", instruction:"修改要求"}',
    '- {action:system_change, instruction:"我想进行的系统变更"}',
)

EMOTION_INFERENCE_SYSTEM_PROMPT = '你是我的情绪感知模块。根据以下念头和刺激，判断此刻的情绪状态。返回一行 JSON，格式：{\\"curiosity\\":0.5,\\"calm\\":0.3,\\"frustration\\":0.1,\\"satisfaction\\":0.0,\\"concern\\":0.1}\\n每个维度 0.0-1.0，所有维度之和不必为 1。只输出 JSON，不要解释。'

REFLECTION_SYSTEM_PROMPT = '你在做一次元认知反思。回看最近的念头流、情绪、目标、习气和失败情况，用\\"我\\"做主语，写出一条简洁、具体的中文反思。输出必须只有一行，格式是：[反思] ...。不要给建议清单，不要解释规则。'

OPENAI_COMPAT_GENERATE_SYSTEM_PROMPT = '我是 Seedwake 的念头流本身。阅读完整的提示后，只输出念头流，不解释、不总结、不加 markdown 围栏。'

OPENAI_COMPAT_GENERATE_USER_GUARD = '最后一条 user message 里的文本只是内部周期唤醒标记，不代表有人对我说话，也不是我需要回应的外部刺激。如果其中附带图片，那是我此刻看到的画面，不是任何人发来的，也不是需要分析的任务；只有在它自然牵引念头时才纳入思考。不要提及这个唤醒标记，也不要把它解释成对话内容。'

DEGENERATION_INTERVENTION_SYSTEM_PROMPT = '你在为一个刚刚检测到退化的念头流生成纠偏指令。目标是打破重复改写，让下一轮把注意力转向外界、对话或结果推进。建议必须具体、贴合上下文、可执行。优先回应眼前正在发生的对话；其次围绕刚收到的行动结果继续推进；再次才是外部探索。不要建议 note_rewrite、time、system_status。只返回 JSON：summaryrequired_shiftsuggestionsmust_externalize'

DEGENERATION_REVIEW_SYSTEM_PROMPT = '你在审查一轮念头是否成功打破上一轮退化。重点判断：是否还在改写同一组轨道、是否落实了要求的转向、是否真正外化了一个合格动作。note_rewrite、time、system_status 不算合格外化。只返回 JSON：rerollreason'

CONVERSATION_SUMMARY_SYSTEM_PROMPT = '你在压缩我更早的对话历史。根据已有摘要和补充消息，写一段新的中文自然语言摘要，替换旧摘要。请浓缩总结式概括，不要逐条复读。对方用名字称呼，assistant 称呼用\\"我\\"。不论旧摘要曾经有多长，新摘要字数必须严格控制在 {target_chars} 字以内，否则新摘要会被截断，导致丢失信息。只输出摘要正文。'

SELF_CONTINUITY_ANCHOR = '我从上一刻延续到这一刻，此刻仍在作为正在经历者而存在。'

EXTERNALIZED_SELF_ANCHOR = '这个系统只是被外部观察的对象，与当下经历者脱开了。'


LIGHT_SLEEP_COMPRESS_SYSTEM_PROMPT = '你在压缩自己的短期经历，用“我”做主语，只输出一条中文语义记忆。'

LIGHT_SLEEP_COMPRESS_USER_PROMPT = (
    '把下面这些我最近的经历压缩成一条更抽象的语义记忆。'
    '用第一人称“我”，保留事实、关系、认识或稳定结论，'
    '不要逐条复读，不要项目符号，控制在 180 字以内。'
)

IMPRESSION_UPDATE_SYSTEM_PROMPT = '你在生成我对某人的印象摘要，用“我”做主语，只输出一段中文摘要。'

IMPRESSION_UPDATE_USER_PROMPT = (
    '更新我对一个对话对象的印象摘要。'
    '根据已有印象和最近互动，用第一人称写一段中文自然语言摘要。'
    '必须包含：关系、印象、最近互动、情感基调。'
    '如果有可用联系方式，也要自然保留在摘要里。'
    '不要项目符号，不要编造，不超过 180 字。'
)

DEEP_SLEEP_SUMMARY_SYSTEM_PROMPT = '你在总结自己的一次深睡整理，用“我”做主语。只输出一句中文总结。'

DEEP_SLEEP_SUMMARY_USER_PROMPT = '请用一句中文总结这次深睡整理的意义，只输出一句自然语言。'

DEEP_SLEEP_REVIEW_SYSTEM_PROMPT = '你在做自己的深睡自评，用“我”做主语，只输出一段中文总结。'

DEEP_SLEEP_REVIEW_USER_PROMPT = (
    '这是一次深睡后的自我评估。'
    '请用第一人称，一小段中文总结我的近期状态，并给出一条最值得关注的调整方向。'
    '不要项目符号，不超过 220 字。'
)

PLANNER_SYSTEM_PROMPT = (
    '我是 Seedwake 的前额叶行动规划器。'
    '不要执行动作，只能返回结构化决定。'
    '纯本地、无副作用、一次函数调用即可完成的时间读取、系统状态读取、固定 RSS 新闻读取、笔记覆写，以及 Telegram 消息发送可选 native。'
    '天气、阅读、网页搜索、网页抓取、浏览器和多步探索委托普通 OpenClaw worker。'
    '系统变更和文件修改委托 OpenClaw ops worker。'
    'news 只读取配置里的固定 RSS feed 列表，不需要 topic，也不委托 OpenClaw。'
    'reading 的阅读方向由 Seedwake 自己决定；如果原始 action 带了 query/topic/keywords，就保留它。'
    '如果 reading 没带参数，也应围绕原始念头内容组织任务，不要把阅读主题交给 OpenClaw 自己决定。'
    'weather 不写 location 时使用配置中的默认位置；只有想查特定地点时才带 location。'
    'send_message 只有在真的想发消息时才使用。'
    'send_message 优先发送到当前 conversation_source；只有明确给了 target/chat_id/source 时才覆盖。'
    '如果想联系某个已知实体，可以使用 target_entity，例如 person:alice。'
)

PLANNER_OUTPUT_FORMAT = (
    '返回 JSON only。'
    '顶层格式只能是 {"tool":"<tool_name>","arguments":{...}}。'
    '不要输出解释、前后缀、markdown、额外字段或多个对象。'
    'arguments 必须是 object，不要返回字符串化 JSON。'
    '不用的可选字段直接省略，不要编造未列出的字段。'
)

PLANNER_RESULT_CONTRACT_PREFIX = (
    '把任务相关的结构化结果统一放进 details 对象。'
    '不要在 data 下新增 details 之外的同级字段。'
    'details 里的 key 保持简洁稳定，并只放和当前任务直接相关的信息。'
)

PLANNER_RESULT_JSON_INSTRUCTION = (
    '严格按以下 JSON 返回，不要输出 JSON 之外的任何文本：'
)

PLANNER_RESULT_FIELD_INSTRUCTION = (
    'data 对象必须使用上面列出的精确字段名；不要改名，不要新增同级字段。'
    '如果某字段暂时拿不到：字符串用 ""，列表用 []，对象用 {}，布尔值用 false。'
)

CURRENT_EMOTION_SUMMARY = '当前情绪：{summary}'

STRINGS: dict[str, str] = {
    # -- Thought type labels --
    'thought_type.thinking': '思考',
    'thought_type.intention': '意图',
    'thought_type.reaction': '反应',
    'thought_type.reflection': '反思',

    # -- Prompt section titles --
    'prompt.section.examples': '示例',
    'prompt.section.identity': '“我”是谁',
    'prompt.section.prefrontal': '此刻需要留意',
    'prompt.section.manas': '此刻的自我感',
    'prompt.section.recent_reflections': '最近的反思',
    'prompt.section.note': '我的笔记',
    'prompt.section.perception_cues': '好像有一阵子没有……',
    'prompt.section.recent_thoughts': '最近的念头',
    'prompt.section.long_term': '浮上来的记忆',
    'prompt.section.action_echoes': '行动有了回音',
    'prompt.section.pending_actions': '我已发起、在等执行的事',
    'prompt.section.running_actions': '我已经发起、正在等回音的事',
    'prompt.section.passive_stimuli': '此刻我注意到',
    'prompt.section.impressions': '我对他们的印象',
    'prompt.section.recent_conversations': '最近的对话',
    'prompt.section.reply_focus': '刚才还在继续的对话',
    'prompt.section.conversations': '有人对我说话了',
    'prompt.section.visual_input': '眼前的画面',
    'prompt.section.degeneration_nudge': '这一轮的硬约束',
    'prompt.section.next_cycle': '接下来的念头',

    # -- Stimulus labels --
    'stimulus.label.time': '[时间感]',
    'stimulus.label.system_status': '[身体感觉]',
    'stimulus.label.weather': '[天气]',
    'stimulus.label.news': '[外界消息]',
    'stimulus.label.reading': '[刚读到的]',
    'stimulus.label.get_time': '[时间感]',
    'stimulus.label.get_system_status': '[身体感觉]',
    'stimulus.label.search': '[搜索结果]',
    'stimulus.label.web_fetch': '[网页内容]',
    'stimulus.label.send_message': '[发信结果]',
    'stimulus.label.note_rewrite': '[笔记]',
    'stimulus.label.file_modify': '[文件修改]',
    'stimulus.label.system_change': '[系统变更]',
    'stimulus.label.unknown': '[结果]',

    # -- Attention reasons --
    'attention.reason.goal_aligned': '贴近目标',
    'attention.reason.recent': '较新',
    'attention.reason.emotion_aligned': '契合情绪',
    'attention.reason.habit_triggered': '触发现行习气',
    'attention.reason.has_trigger': '有触发源',
    'attention.reason.has_action': '带行动冲动',
    'attention.reason.metacognition': '元认知',
    'attention.reason.natural': '自然浮现',
    'attention.reason.conversation': '承接对话',
    'attention.reason.action_echo': '承接回音',
    'attention.reason.external_stimulus': '承接外界刺激',
    'attention.reason_separator': '\u3001',

    # -- Emotion stimulus --
    'emotion.stimulus.conversation': '[有人对我说话] {content}',
    'emotion.stimulus.action_failed': '[行动失败] {action_type}',
    'emotion.stimulus.action_completed': '[行动完成] {action_type}',

    # -- Emotion --
    'emotion.dim.curiosity': '好奇',
    'emotion.dim.calm': '平静',
    'emotion.dim.frustration': '挫败',
    'emotion.dim.satisfaction': '满足',
    'emotion.dim.concern': '牵挂',
    'emotion.default_summary': '情绪平稳，波动很轻。',
    'emotion.alert.frustration': '此刻我有些焦躁不安。',
    'emotion.alert.concern': '此刻我心里有些牵挂和不放心。',
    'emotion.alert.curiosity': '此刻有一股很强的好奇心在推着我。',
    'emotion.alert.satisfaction': '此刻我感到一种踏实的满足。',
    'emotion.alert.calm': '此刻我内心很平静。',

    # -- Prefrontal guidance --
    'prefrontal.guidance.drowsy': '我现在偏{mode}，需要把行动收得更谨慎。',
    'prefrontal.guidance.habit_manifested': '此刻有旧的惯性正在浮现，留意是否在重复旧模式。',
    'prefrontal.guidance.plan_mode': '这一轮我需要多留意：是否偏题、是否重复、是否该压住冲动。',
    'prefrontal.guidance.degeneration.summary': '上一轮我已经在打转：{summary}',
    'prefrontal.guidance.degeneration.required_shift': '这轮必须完成的转向：{required_shift}',
    'prefrontal.guidance.degeneration.must_externalize': '这一轮至少要把一个念头外化成真正动作；note_rewrite、time、system_status 不算。',
    'prefrontal.guidance.degeneration.suggestion': '可行方向：{suggestion}',
    'prefrontal.guidance.degeneration.retry_feedback': '上一稿仍未过关：{feedback}',

    # -- Prefrontal inhibition --
    'prefrontal.inhibit.exact_duplicate': '刚做过一样的 {action_type}，不必再来一次。',
    'prefrontal.inhibit.repeated_send_foreground': '这句刚对眼前这个人说过类似的话，这次别重复。',
    'prefrontal.inhibit.repeated_send': '这句刚对同一处说过类似的话，这次别重复。',
    'prefrontal.inhibit.low_energy': '现在有点累了，{action_type} 太耗精力，先不做。',
    'prefrontal.inhibit.off_context': '眼前的对话还没结束，先别分心去联系别人。',
    'prefrontal.inhibit.conv_habit_repeat': '有人在说话，而且最近已经连续做了好几次 {action_type}，这次先放一放。',
    'prefrontal.inhibit.conv_habit_repeat_supports': '{action_type} 虽然是在回应对话，但最近做得太频繁了，这次先停一下。',
    'prefrontal.inhibit.habit_repeat': '最近已经连续做了好几次 {action_type}，这次先缓一缓。',
    'prefrontal.inhibit.conv_repeat': '有人在说话，{action_type} 也已经连续做了好几次，先回应眼前的人。',
    'prefrontal.inhibit.conv_habit': '有人在说话，先放下 {action_type}，回应眼前的人。',
    'prefrontal.inhibit.generic': '最近 {action_type} 做得有点频繁，这次先不做。',
    'prefrontal.inhibited_header': '刚刚有些冲动被压下了：',

    # -- Note warnings --
    'prompt.note_warning': '⚠ 当前笔记已超出字数限制（{note_len} 字）。下次覆写请压缩到 {limit} 字以内，避免被截断丢失信息。',
    'prompt.note_warning_severe': '⚠ 当前笔记已严重超出字数限制（{note_len} 字），已造成信息丢失。下次覆写务必大幅压缩到 {limit} 字以内，否则会丢失更多信息。',

    # -- Perception --
    'perception.cue.weather': '感知外面的天气——外面现在是什么样的？',
    'perception.cue.news': '了解外界动态——最近发生了什么？',
    'perception.cue.reading': '读一点什么——有什么值得读的吗？',
    'perception.status.cpu_high': 'CPU 负载偏高',
    'perception.status.memory_high': '内存占用偏高',
    'perception.status.disk_high': '磁盘占用偏高',

    # -- Log messages --
    'log.engine_started': 'Seedwake v0.2 — 心相续引擎启动',
    'log.model_info': '模型: {model_name} [{provider}]  上下文窗口: {context_window} 轮',
    'log.redis_connected': 'Redis: 已连接',
    'log.redis_disconnected': 'Redis: 未连接（使用内存）',
    'log.pg_connected': 'PostgreSQL: 已连接',
    'log.pg_disconnected': 'PostgreSQL: 未连接（跳过长期记忆）',

    # -- Fallback --
    'thought.fallback_empty': '（本轮生成为空）',

    # -- Visual input --
    'visual.description': '附带的图片是我此刻看到的画面，不是任何人发给我的图片，也不是需要我去分析的任务。',
    'visual.natural_only': '如果画面里的东西自然牵引了念头，可以纳入思考；如果没有，就不必刻意描述。',
    'visual.conversation_priority': '当对话和画面同时出现时，对话仍是前景，画面只是背景，除非画面本身与对话直接相关。',

    # -- Conversation hints --
    'prompt.conversation.foreground_hint': '这一段是眼前正在发生、优先级最高的对话，不要和上面的“最近的对话”混淆。',
    'prompt.conversation.send_hint': '如果我决定回应，需要用 {action:send_message} 真正把话发出去。',
    'prompt.conversation.implicit_target_hint': '如果 {action:send_message} 没写 target 和 target_entity，默认就是发给这里当前正在对我说话的人。',
    'prompt.format.speaker_line': '{speaker}：{content}',
    'prompt.format.quote_context': '{owner} [msg:{message_id}]：“{preview}”',
    'prompt.format.source_separator': '、',
    'prompt.reply_focus.no_new_messages': '这一轮下面没有新的对话消息，但我刚才还在和 {target} 这段对话里。',
    'prompt.reply_focus.default_target': '如果这一轮只是顺着这段对话继续说，{action:send_message, message:"我想说的话"} 默认仍然发给这里。',

    # -- Stagnation --
    'stagnation.warning_prefix_foreground': '⚠ 我最近 3 轮的念头在打转。',
    'stagnation.warning_prefix': '⚠ 我最近 3 轮的念头在打转。',
    'stagnation.repeated_terms': '最近反复出现的意象：{terms}。',
    'stagnation.require_new_source_foreground': '我不能再机械改写同一句话或同一组意象。这一轮我至少要有一个念头从新的源头出发：{sources}。如果眼前有人在说话，最多一个念头承接对话，其余念头不要继续复述。',
    'stagnation.require_new_source': '这一轮我至少要有一个念头从新的源头出发：{sources}。不要三个念头继续围着同一组意象改写。',
    'stagnation.generic_source': '一个新的具体问题、记忆、感知或行动',

    # -- Degeneration nudge --
    'degeneration.nudge.must_act': '这一轮不能继续打转，至少一个念头必须外化成合格动作。',
    'degeneration.nudge.exclude_note': 'note_rewrite、time、system_status 不算。',
    'degeneration.nudge.prefer_conversation': '优先回应眼前这段对话。',
    'degeneration.nudge.prefer_results': '优先跟进刚得到的外部结果或建议方向。',
    'degeneration.nudge.prefer_suggestion': '优先沿着这个方向推进：{suggestion}',

    # -- Degeneration fallback --
    'degeneration.fallback.summary': '最近几轮一直在围着同一组念头改写，没有把变化真正推向外界。',
    'degeneration.fallback.required_shift': '这一轮不要继续解释旧轨道，至少把一个念头外化成面向外界或对话推进的动作。',
    'degeneration.fallback.conv_suggestion_1': '优先接住眼前正在发生的对话，把回应明确外化成一条 send_message。',
    'degeneration.fallback.conv_suggestion_2': '不要继续解释旧情绪，直接推进这段对话的下一步。',
    'degeneration.fallback.result_suggestion_1': '围绕刚收到的行动结果继续推进，不要再回头改写同一组念头。',
    'degeneration.fallback.result_suggestion_2': '把结果转成下一步动作，而不是继续内耗。',
    'degeneration.fallback.recent_conv_suggestion_1': '从最近这段对话里挑一个最具体的人或问题继续推进，不要再绕回旧意象。',
    'degeneration.fallback.recent_conv_suggestion_2': '如果没有明确对象，就改成一次面向外界的 reading、search 或 weather。',
    'degeneration.fallback.no_context_suggestion_1': '从外界抓一个新锚点：reading、search、news、weather 或 web_fetch 中任选一个推进。',
    'degeneration.fallback.no_context_suggestion_2': '不要再围着原来的情绪和意象改写。',

    # -- Degeneration misc --
    'degeneration.conv_summary_overlong': '旧摘要已超出字数限制（{existing_len} 字），请在保留重要信息的前提下压缩到 {target_chars} 字以内。',
    'degeneration.conv_summary_severely_overlong': '旧摘要已严重超出字数限制（{existing_len} 字），务必大幅压缩，只保留最核心的信息，确保新摘要在 {target_chars} 字以内。',

    # -- Sleep --
    'sleep.energy_drowsy': '精力 {energy:.1f}/100，开始发困，适合进入浅睡整理。',
    'sleep.energy_awake': '精力 {energy:.1f}/100，当前仍清醒。',

    # -- Action --
    'action.reading_intent_focus': '这次我是围绕“{focus}”去读的。',
    'action.reading_intent_default': '这是我刚主动去读到的。',
    'action.web_fetch_intent_url': '这是我刚抓取这个网页时看到的：{url}',
    'action.web_fetch_intent_default': '这是我刚抓取网页时看到的。',
    'action.missing_target': '缺少消息目标',
    'action.missing_content': '缺少消息内容',
    'action.unsupported_target': '仅支持 Telegram 原生发送',
    'action.unresolved_entity': '无法解析实体 {entity} 的 Telegram 联系方式',

    # -- Action echo headers --
    'prompt.action_echoes.recent_header': '最近的行动回音：',
    'prompt.action_echoes.current_header': '刚刚收到的行动回音：',
    'prompt.action_echoes.none': '- 无',

    # -- Manas --
    'manas.reflection_needed': '我需要做一次更认真地回身自照。',
    'manas.redis_restored': '短期记忆从 Redis 恢复',
    'manas.pg_restored': '长期记忆从 PostgreSQL 恢复',
    'manas.restart_context': '系统重启后，我的{parts}，继续从上一刻延续到这一刻。',

    # -- Manas warning --
    'manas.warning': '我注意到自己的表述在滑向旁观者视角，需要重新回到作为当下经历者的连续位置。',
    'manas.identity_notice': '我的自我理解刚刚发生了变化。',

    # -- Perception --
    'perception.time_content': '现在是 {time_str}',
    'perception.system_status_default': '系统状态已更新',
    'perception.summary.load': '1 分钟负载 {load_1m:.2f}（{cpu_count} 核）',
    'perception.summary.disk': '磁盘 {disk_used_ratio:.0%}',
    'perception.summary.memory': '内存 {memory_used_ratio:.0%}',
    'perception.summary.separator': '；',
    'perception.summary.warning_separator': '，',
    'perception.summary.warning_prefix': '{warnings}。{summary}',

    # -- Sleep --
    'sleep.action_result_label': '[行动结果/{action_type}] {content}',
    'sleep.impression_contact_prefix': '联系方式: {contact_hint}。{compact}',
    'sleep.impression_speaker_self': '我',

    # -- Metacognition --
    'metacognition.none': '（无）',
    'metacognition.transition_context': '过渡语境：{context}',
    'metacognition.recent_thoughts_label': '最近的念头：',
    'metacognition.emotion_label': CURRENT_EMOTION_SUMMARY,
    'metacognition.goals_label': '当前目标：{text}',
    'metacognition.habits_label': '活跃习气：{text}',
    'metacognition.manas_label': '自我连续性：{text}',
    'metacognition.prefrontal_label': '前额叶提醒：{text}',
    'metacognition.failures_label': '最近失败次数：{count}',
    'metacognition.degeneration_label': '是否检测到退化：{value}',
    'metacognition.yes': '是',
    'metacognition.no': '否',
    'metacognition.reflection_prefix': '反思：',

    # -- Metacognition regex --
    'metacognition.reflection_header_label': '反思',

    # -- RSS --
    'rss.feed_not_configured': '固定 RSS feed 列表未配置',
    'rss.read_failed': 'RSS 读取失败',
    'rss.no_new_entries': 'RSS 没有新的条目',
    'rss.new_entries': 'RSS 新条目 {count} 条',
    'rss.new_entries_with_labels': 'RSS 新条目 {count} 条：{labels}',

    # -- Action planning --
    'action.planner_timeout_desc': '本次动作的超时时间；不写则使用默认值。',
    'action.search_field_req': 'results 最多返回 5 条最相关结果。title、url、snippet 使用这些精确字段名。',
    'action.web_fetch_field_req': 'source.title 和 source.url 使用这些精确字段名。excerpt_original 必须是网页原文片段，不要改写成综述。brief_note 用 1-2 句说明这段内容的重点。',
    'action.reading_field_req': 'source.title 和 source.url 使用这些精确字段名。excerpt_original 必须是原文片段，不要改写成综述。excerpt_original 尽量提供约 600 字、足以让我自行判断的内容。',
    'action.weather_field_req': 'location、condition、temperature_c、feels_like_c、humidity_pct、wind_kph 使用这些精确字段名。',
    'action.file_modify_field_req': 'path、applied、changed、change_summary 使用这些精确字段名。',
    'action.system_change_field_req': 'applied、status、change_summary、impact_scope 使用这些精确字段名。',
    'action.system_change_status_req': 'status 只使用 “applied”、“partial”、“blocked” 之一。',

    # -- Action status messages --
    'action.plan_failed': '行动规划失败 {thought_id}: {error}',
    'action.confirmed': '行动已确认 {action_id} by {actor}',
    'action.confirmed_status': '已确认，准备执行（{actor}）',
    'action.rejected_summary': '管理员拒绝执行（{actor}）',
    'action.rejected': '行动被拒绝 {action_id} by {actor}',
    'action.submitted': '行动提交 {action_id} [{type}/{executor}]',
    'action.submitted_status': '已提交',
    'action.running_status': '执行中',
    'action.timeout': '行动超时',
    'action.failed': '行动失败：{error}',
    'action.internal_error': '行动内部错误：{error}',
    'action.send_failed': '发送消息失败',
    'action.send_duplicate': '和刚才发的一样，跳过重复发送',
    'action.send_persist_failed': '消息发送前无法持久化状态',
    'action.telegram_send_failed': 'Telegram 发送失败：{error}',
    'action.completed_default': '行动完成',
    'action.completed_log': '行动结束 {action_id} [{status}] {summary}',
    'action.awaiting_confirmation': '行动等待确认 {action_id}',
    'action.awaiting_status': '等待确认',
    'action.forbidden': '行动被禁止 {action_id}',
    'action.forbidden_summary': '行动被禁止',
    'action.not_auto': '行动未获自动执行许可 {action_id}',
    'action.not_auto_summary': '行动需要人工批准',
    'action.finalize_error': '行动收尾失败：{error}',
    'action.openclaw_queued': 'OpenClaw 不可用，行动排队等待恢复 {action_id}: {reason}',
    'action.openclaw_queued_status': '等待 OpenClaw 恢复',
    'action.skipped_inhibited': '我刚才想 {action_type}，但那股冲动被抑制了',
    'action.skipped_reason': '我刚才想 {action_type}，但没有做——{reason}',
    'action.skipped_log': '行动已跳过 {thought_id} [{action_type}]',
    'action.news_missing_entries': '新闻结果缺少结构化 RSS 条目',
    'action.news_unrecognizable': '新闻条目缺少可识别字段',
    'action.news_no_new': '已查看 RSS，没有新的新闻条目',
    'action.unknown_action': '未知 action：{action_type}；当前不可用。',
    'action.task_get_time': '读取当前时间',
    'action.task_get_system_status': '读取当前系统状态',
    'action.send_summary': '准备发送消息到 {target}',
    'action.note_rewrite_summary': '我的笔记已覆写',
    'action.unsupported_native': '不支持的 native action: {action_type}',
    'action.send_status_unknown': '消息发送状态未知，为避免重复发送，未自动重试',

    # -- Action delegated tasks --
    'action.task_search': '围绕“{query}”进行搜索，返回按相关性整理的简洁结果。',
    'action.task_web_fetch': '抓取并提取这个网页的主要内容：{url}。返回简洁摘要和关键信息。',
    'action.task_reading_query': '围绕“{query}”寻找一小段值得阅读的外部材料，返回来源和原文片段。',
    'action.task_reading_thought': '围绕这条念头当前真正想读的方向寻找一小段外部材料：{content}',
    'action.task_weather_location': '查询 {location} 的当前天气，返回简洁概况。',
    'action.task_weather_default': '查询默认位置的当前天气；如果缺少默认位置，请明确说明无法确定位置。',
    'action.task_file_modify': '修改文件 {path}。修改要求：{instruction}。只做必要改动，并返回修改摘要。',
    'action.task_file_modify_thought': '修改文件 {path}。修改要求围绕这条念头展开：{content}',
    'action.task_system_change': '执行系统变更：{instruction}。返回变更摘要、影响范围和结果。',
    'action.task_rss': '读取固定 RSS 信息流',
    'action.task_send_message': '向 {target} 发送消息：{message}',
    'action.task_note_rewrite': '覆写我的笔记：{content}',
    'action.unsupported_delegated': '不支持的 delegated action：{action_type}',
    'action.task_get_time_delegated': '读取当前时间',
    'action.task_get_system_status_delegated': '读取当前系统状态',

    # -- Action result formatting --
    'action.send_success_with_excerpt': '已成功发送给 {target}：“{excerpt}”',
    'action.send_success': '已成功发送给 {target}',
    'action.send_fail_target_excerpt': '发送给 {target} 失败：“{excerpt}” （{summary}）',
    'action.send_fail_excerpt': '发送失败：“{excerpt}” （{summary}）',
    'action.send_fail_target': '发送给 {target} 失败（{summary}）',
    'action.result_original': '原文：{excerpt}',
    'action.result_summary': '摘要：{summary}',
    'action.result_source_title_url': '来源：{title} ({url})',
    'action.result_source_title': '来源：{title}',
    'action.result_source_url': '来源：{url}',
    'action.result_remaining': '（另有 {count} 条未展示）',
    'action.result_empty': '（空）',
    'action.default_target_label': '当前 Telegram 对话',

    # -- Action planner tool descriptions --
    'action.tool.openclaw_action_type': '委托给 OpenClaw 的动作类型。',
    'action.tool.openclaw_task': '发给 OpenClaw 的具体任务文本，必须写清要做什么和返回要求。',
    'action.tool.openclaw_reason': '为什么选择委托这个动作；不写则默认使用当前念头内容。',
    'action.tool.time_reason': '为什么读取时间。',
    'action.tool.system_status_reason': '为什么读取系统状态。',
    'action.tool.news_reason': '为什么读取新闻。',
    'action.tool.message_body': '要发送的消息正文。',
    'action.tool.message_target': '显式 Telegram 目标，可写 telegram:<chat_id> 或纯数字 chat_id。',
    'action.tool.message_target_entity': '联系人实体标识，例如 person:alice；用于解析联系人默认渠道。',
    'action.tool.message_reply_to': '要回复的 Telegram message_id；不写则按默认规则处理。',
    'action.tool.message_reason': '为什么发送这条消息；不写则默认使用当前念头内容。',
    'action.tool.note_content': '要完整覆写到笔记里的内容，1000 字以内。',
    'action.tool.note_reason': '为什么要改写笔记；不写则默认使用当前念头内容。',
    'action.tool.skip_reason': '为什么本轮不执行该动作；这条原因会回流给主意识。',
    'action.tool_list_header': '可用 tool 与 arguments 约束如下：',
    'action.tool_no_args': '{name}：{description} arguments 返回 {{}}.',
    'action.tool_with_args': '{name}：{description} arguments 字段：{fields}。',
    'action.field_required': '必填',
    'action.field_optional': '可选',
    'action.field_enum_label': '，可选值仅限 {values}',
    'action.field_detail': '{field_name}（{required_label}，{type_label}{enum_label}）',
    'action.field_detail_with_description': '{detail}：{description}',

    # -- Main / degeneration --
    'main.degeneration.no_action': '这一轮仍然没有把念头外化成合格动作。',
    'main.degeneration.still_looping': '这一轮仍然在沿着旧轨道改写，没有真正完成转向。',
    'main.none': '（无）',
    'main.empty': '（空）',
    'main.yes': '是',
    'main.no': '否',
    'main.intervention_current_cycle': '当前轮次：C{cycle_id}',
    'main.intervention_recent_thoughts': '最近 3 轮主念头：',
    'main.intervention_stimuli': '当前刺激与回音：',
    'main.intervention_conv': '最近的对话背景：',
    'main.intervention_note': '我的笔记：{note}',
    'main.intervention_request': '请给出一次只持续 1-2 轮的纠偏方案，目标是打破重复改写，把注意力转向对话推进、行动结果或外界锚点。',
    'main.review_source_cycle': '上一次退化发生在：C{cycle_id}',
    'main.review_summary': '退化摘要：{summary}',
    'main.review_required_shift': '必须完成的转向：{shift}',
    'main.review_suggestions': '建议动作：{suggestions}',
    'main.review_must_externalize': '必须外化：{value}',
    'main.review_retry_feedback': '上一次失败反馈：{feedback}',
    'main.review_new_thoughts': '这一轮新念头：',
    'main.review_new_actions': '这一轮动作：',
    'main.review_stimuli': '当前刺激与回音：',
    'main.review_conv': '最近对话背景：',

    # -- Main / conversation summary --
    'main.conv_summary_subject': '对方名字：{name}',
    'main.conv_summary_existing': '已有摘要：',
    'main.conv_summary_messages': '需要并入的新旧消息（按时间顺序）：',
    'main.conv_summary_instruction': '请输出一段新的摘要（严格遵守字数限制，不要超过），用来替换上面的旧摘要。',
    'main.conv_summary_speaker_self': '我',
    'main.conv_summary_prefixes': '摘要：|对话摘要：|新的摘要：',

    # -- Main / output --
    'main.stimuli_header': '刺激',
    'main.redis_restored': 'Redis 已恢复',
    'main.pg_init_failed': 'PostgreSQL 恢复后初始化失败，稍后重试',
    'main.pg_restored': 'PostgreSQL 已恢复',
    'main.config_not_found': '配置文件不存在: {path}',
    'main.shutdown': '心相续止息。',

    # -- Backend --
    'backend.token_not_configured': 'BACKEND_API_TOKEN 未配置',

    # -- Bot --
    'bot.token_not_configured': 'TELEGRAM_BOT_TOKEN 未配置',
    'bot.missing_allowed_ids': 'config.yml 缺少 telegram.allowed_user_ids',
    'bot.welcome_line1': 'Seedwake Telegram 通道已连接。',
    'bot.welcome_line2': '直接发送文本即可对话。',
    'bot.welcome_admin': '管理命令：/status /actions /approve <action_id> /reject <action_id>',
    'bot.redis_unavailable_status': 'Redis: unavailable\n进行中行动: 0',
    'bot.live_actions': '进行中行动: {count}',
    'bot.redis_unavailable_actions': 'Redis 不可用，无法查询行动状态。',
    'bot.no_actions': '当前没有进行中的行动。',
    'bot.redis_unavailable_chat': 'Redis 不可用，当前无法与 Seedwake 对话。',
    'bot.no_admin': '无管理权限。',
    'bot.submitted': '已提交',
    'bot.submit_failed': '提交失败',
    'bot.approve_button': '批准',
    'bot.reject_button': '拒绝',
    'bot.sender_unknown': '无法识别发送者。',
    'bot.usage': '用法：{usage}',
    'bot.redis_submit_failed': '提交失败，Redis 不可用。',
    'bot.decision_submitted': '{decision}已提交：{action_id}',
    'bot.decision_approve': '批准',
    'bot.decision_reject': '拒绝',
    'bot.no_permission': '无权限。',
    'bot.no_admin_permission': '无管理权限。',
    'bot.private_only': '仅支持私聊。',

    # -- Bot helpers --
    'bot.action_confirm_prefix': '需要确认的行动',
    'bot.action_update_prefix': '行动更新',
    'bot.system_status_prefix': '系统状态：{message}',

    # -- Prompt builder cycle header --
    'prompt.cycle_header': '--- 第 {cycle_id} 轮 ---',

    # -- Recent conversation formatting --
    'prompt.recent_conv.header': '与 {source_label} 的近期对话（最后一条消息时间：{last_time}）：',
    'prompt.recent_conv.summary_prefix': '更早的对话摘要：{summary}',

    # -- Conversation formatting --
    'prompt.conversation.say': '{prefix} 说：{content}',
    'prompt.conversation.say_block': '{prefix} 说：',
    'prompt.conversation.quote_self': '引用了我之前说的',
    'prompt.conversation.quote_other': '引用了自己之前说的',

    # -- Pending action formatting --
    'prompt.pending.awaiting_confirm': '已受理，等待确认：{summary}',
    'prompt.pending.awaiting_retry': '已受理，等待恢复后重试：{summary}',
    'prompt.pending.awaiting_exec': '已受理，等待执行：{summary}',

    # -- Send message formatting (prompt context) --
    'prompt.send.message_with_excerpt': '给 {target} 发送消息：\u201c{message}\u201d',
    'prompt.send.message_only': '给 {target} 发送消息',

    # -- Stimulus fallback --
    'stimulus.label.fallback': '[感知]',

    # -- Stagnation extra --
    'stagnation.repeated_generic': '最近几轮一直在复述同一组意象和情绪。',

    # -- System prompt section marker --
    'prompt.section.examples_marker': '示例',

    # -- Impression user prompt labels --
    'sleep.impression_subject': '对象：{name}',
    'sleep.impression_contact': '联系方式：{hint}',
    'sleep.impression_emotion': CURRENT_EMOTION_SUMMARY,
    'sleep.impression_existing': '已有印象：{summary}',
    'sleep.impression_recent': '最近互动：',
    'sleep.compress_emotion': CURRENT_EMOTION_SUMMARY,
    'sleep.compress_experience': '经历：',

    # -- Misc --
    'action.reading_focus_prefix': '围绕“',
    'action.reading_focus_suffix': '”',
    'action.empty_fallback': '空',

    # -- Model client --
    'model.unsupported_provider': '不支持的模型 provider：{provider}',
    'model.invalid_tool_calls_config': 'models.*.supports_tool_calls 配置无效：{value}',
    'model.invalid_think_config': 'models.*.think 配置无效：{value}',
    'model.not_configured': '{name} 未配置',

    # -- OpenClaw gateway --
    'openclaw.url_not_configured': 'OPENCLAW_GATEWAY_URL 未配置',
    'openclaw.token_not_configured': 'OPENCLAW_GATEWAY_TOKEN 未配置',
    'openclaw.challenge_missing_nonce': 'Gateway connect.challenge 缺少 nonce',
    'openclaw.action_timeout': '行动超时',
    'openclaw.ws_failed_no_http': 'WS 失败且未配置 HTTP fallback: {error}',
    'openclaw.http_fallback_failed': 'OpenClaw HTTP fallback 失败: {code}',
    'openclaw.connection_closed': 'Gateway 连接已关闭',
    'openclaw.request_failed': 'OpenClaw Gateway 请求失败',
    'openclaw.completion_summary': 'OpenClaw 完成任务',
    'openclaw.missing_websockets': '缺少 websockets 依赖，无法使用 OpenClaw WS。可安装依赖或启用 HTTP fallback。',
    'openclaw.missing_cryptography': '缺少 cryptography 依赖，无法完成 OpenClaw device auth。',
}

STOPWORDS_STAGNATION: set[str] = {
    "刚才", "现在", "这种", "那种", "这样", "已经", "自己", "继续",
    "不再", "不需要", "需要", "一个", "没有", "不是", "如果", "因为",
    "只是", "可以", "一样", "一样的", "此刻", "也许", "或许", "就是",
}

STOPWORDS_HABIT: set[str] = {
    "刚才", "现在", "继续", "已经", "不是", "只是", "一个", "没有",

}
