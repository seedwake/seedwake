# Prompt 观察问题记录

## 1. search action_result 只显示"成功"摘要，不展示实际结果

`## 此刻我注意到` 里 search 的行动回音只有一句话：

> （行动回音） search succeeded: 已按相关性整理出 5 条与"有氧锻炼、心肺功能、训练建议"最相关的科普结果，优先选择医学健康与官方体育类来源。

模型看不到搜索到的文章标题、链接或摘要，无法基于结果做下一步动作（选文章、分享给用户、发起 web_fetch）。search 结果的具体内容在 action_result stimulus 生成时被丢掉了。

## 2. "我正在等待的事" 标题误导 + agent 提示词泄漏

两个问题叠加：

**a) 标题语义误导，导致模型重复发起相同 action。** "我正在等待的事"容易被模型理解为"我还没做的事"，于是模型再次发起同样的 action（如 C263-2 和 C264-2 发了相同的 search）。应改为明确表达"已发起、正在等结果"的语义，如"我已经发起、正在等回音的事"。

**b) agent 指令原文泄漏到意识流 prompt。** `_format_running_actions` 使用 `action.request.get("task")` 拿到的是发给 OpenClaw worker 的完整指令，包括 JSON 契约、字段名约束、返回格式要求等内部实现细节。这些不应该出现在意识流 prompt 里。应只展示模型自己能理解的摘要（如 source_content 或一句话描述）。

## 3. "我"是谁与念头历史之间缺少分节标题

`## "我"是谁` 的 identity 内容结束后，直接接 `--- 第 235 轮 ---` 的念头历史，中间没有任何段落标题。模型看到的是 identity 文字突然变成了念头列表，缺少结构分隔。应在念头历史前加一个 `## 最近的念头` 之类的标题，与其他 `##` 段落保持一致。

## 4. 感知标签用全角括号不美观

`## 此刻我注意到` 里的感知类型标签用的是 `（时间感）`、`（身体感觉）`，全角括号夹在列表项中间视觉上不够干净。改用 `时间感：` 或 `[时间感]` 等形式更清晰。

## 5. "规则"与"我是谁"之间多了一个空行

SYSTEM_PROMPT 末尾 `## 规则` 的最后一条规则之后，到 `_build_system` 拼接的 `## "我"是谁` 之间出现了两个空行（即三个 `\n`），其他 `##` 段之间都只有一个空行。原因是 SYSTEM_PROMPT 末尾带了一个多余的 `\n`，和 `"\n".join` 叠加后多出了一行。

## 6. "此刻我注意到" 混杂了被动感知和行动回音，应拆成两个段落

当前 time、system_status、weather、news、reading 的被动注入和 search/reading/weather 等 action 返回的结果全部混在 `## 此刻我注意到` 里。应拆分为：

- **## 此刻我注意到** — 被动感知（时间感、身体感觉、天气、新闻等系统主动注入的）
- **## 行动有了回音** — 模型自己发起 action 后返回的结果（search、reading、weather 等）

## 7. "浮上来的记忆" 从未出现过

prompt.txt 全文没有一次 `## 浮上来的记忆`。追踪代码路径：`_execute_cycle` → `_retrieve_associations` → `ltm.search(vec)` → 返回空 → `long_term_context=None` → `build_prompt` 里 `if long_term_context:` 为假 → 段落永远不渲染。

最可能的原因：LTM 数据库里还没有存入过 embedding（`_store_to_ltm` 可能静默失败），或 embedding 模型维度与 schema 不匹配（schema.sql 期望 4096 维），导致 search 永远返回空列表。需要排查 `_store_to_ltm` 是否真正写入成功以及 embedding 维度是否对齐。

## 8. 段落顺序调整方案

对话离生成点最近以确保模型优先回应对方。完整顺序：

```
SYSTEM_PROMPT 开头段落
## 输出格式
## 示例
## 规则
## "我"是谁
## 最近的念头
## 浮上来的记忆
## 好像有一阵子没有……
## 行动有了回音
## 我已经发起、正在等回音的事
## 此刻我注意到
## 最近的对话
## 有人对我说话了
## 接下来的念头
--- 第 N 轮 ---
```

## 9. action 结果的 stimulus type 不一致，拆段落时需统一归类

模型发起 `{action:search}` 返回的 stimulus 是 `type="action_result"`，但 `{action:reading}` 返回的是 `type="reading"`，news/weather 可能也有类似问题。拆成"此刻我注意到"和"行动有了回音"两个段落后，区分依据应该是"这个 stimulus 是否由模型发起的 action 触发"，而不是 stimulus type 字符串。

## 10. 标签格式与行动回音段落内标签冗余

a) 所有感知/回音标签统一用半角方括号 `[时间感]`、`[刚读到的]`，不用全角括号 `（时间感）`。

b) "行动有了回音"既然已经是独立段落，每条回音不需要再标 `[行动回音]`，改为标注具体类型：`[搜索结果]`、`[刚读到的]`、`[天气]` 等。

## 11. 回音内容和对话消息中的换行应压成单行

当前 action 结果的 content 和 conversation 的多条消息合并（`_merge_conversation_stimuli` 用 `\n` 拼接）可能导致段落内出现意外换行，破坏列表结构。应在渲染时把内容中的换行替换为空格，保持每条回音/每条消息各占一行。conversation 合并时也不应用 `\n` 拼接，改用空格或其他分隔。

## 12. 前向 trigger 引用未校验

C236-3 引用了 C237-1，C250-3 引用了 C251-1——都是引用尚未产生的念头。这些错误引用被原样存入 STM，后续轮次模型在 prompt 里反复看到，等于在教它"前向引用是可以的"。应在存入 STM 前校验 trigger_ref 是否指向已存在的 thought_id，无效的去掉。

## 13. 示例格式与念头历史格式不一致

SYSTEM_PROMPT 示例中写的是 `[思考]`、`[意图]`、`[反应]`（无 ID 后缀），但 `_format_thought_history` 渲染的历史是 `[思考-C217-1]`（带 `-CX-Y` 后缀）。模型同时看到两种格式，不确定自己该输出哪种。应统一：要么示例也带 ID，要么历史也不带。

## 14. "我"是谁内部三段 identity 缺少分隔

`_build_system` 里 `self_description`、`core_goals`、`self_understanding` 三段只用 `\n` 拼接，渲染出来连成一片。"探索和学习，理解自身的运作方式。"看起来像 `self_description` 的延续而不是独立的 `core_goals`。应在各段之间加空行或小标题。

## 15. 对话消息缺少 msg id，且没有携带引用上下文

`## 有人对我说话了` 里的消息没有带 telegram_message_id：

> telegram:8469901143 说：谢谢你

模型看不到 msg id，自然不会在回复时使用 `reply_to`。此外还有三个子问题：

**a) 引用上下文缺失。** 如果用户的消息 reply_to 了 Seedwake 之前发出的某条消息，这个引用上下文没有传入 prompt，模型不知道对方"谢谢你"是在回应自己之前说的哪句话。应把被引用消息的摘要一并传入，并区分引用的是用户自己的消息还是 Seedwake 发出的消息。Seedwake 的 bot user id 可以从 bot token 中推断出来（token 格式为 `{bot_id}:{secret}`）。

**b) 引用格式设计。** 需要一种清晰的格式让模型看出引用关系，例如：
```
telegram:8469901143 (Alice) [msg:305] 引用了我之前说的 [msg:298]："好，我自己找一篇关于有氧锻炼的文章"
说：谢谢你
```

**c) 用户昵称缺失。** 当前只展示 `telegram:8469901143`，模型不知道这个人叫什么。应把 Telegram 的 first_name/username 传入，展示为类似 `telegram:8469901143 (Alice)` 的格式，让模型在回应时能用对方的名字。

## 16. "我已经发起、正在等回音的事" 的类型/状态标签应放在前面

当前格式：

> \- 我想顺着这股"听身体说话"的安静…… [news/running]

类型和状态放在行末，模型可能不会注意到。应调整为放在行首，如 `[news/running] 我想顺着……`，让模型第一眼就看到这是什么类型的 action、什么状态。

## 17. action 完成但无新内容时，应在行动回音里给出明确反馈

例如 `act_C271-3 [succeeded]` RSS 没有新条目，但模型没有收到这个反馈。如果模型看不到 news action 已经完成且结果为空，可能会继续重复发起 `{action:news}`。应在"行动有了回音"里显示类似 `[外界消息] 已查看 RSS，没有新的新闻条目` 的反馈。

## 18. send_message 成功后应在行动回音里确认

send_message 在执行期间出现在"我已经发起、正在等回音的事"里，但成功后没有在"行动有了回音"里出现确认。模型不知道消息是否成功送达。应在回音里显示类似 `[发信结果] 已成功发送给 telegram:xxx："消息内容摘要"` 的反馈。

## 19. prompt.txt 日志的分隔符 emoji 改为开始/结束标记

当前 `_write_prompt_log` 用 🔥 做分隔。改为开始用 🟢，结束用 🔴，方便在日志里区分一段 prompt 的起止。

## 20. "浮上来的记忆" 有重复条目且带 action 标记

LTM 检索到的记忆存在两个问题：

**a) 重复条目。** 同一条记忆在 `## 浮上来的记忆` 里出现多次（观察到最多 3 次）。原因是 `_store_to_ltm` 没有去重，同样内容的念头被多次写入向量数据库。检索时应对结果去重（按内容文本），或存入时做近似重复检测。

**b) 记忆里带 `{action:...}` 标记。** 念头被原样存入 LTM，包括其中的 action 标记。当这些记忆浮现到 prompt 里时，模型可能误把 `{action:news}` 当成执行指令。应在存入 LTM 前或渲染到 prompt 时去掉 action 标记。

## 21. 已存入 STM 的旧历史中的无效 trigger_ref 不会被修正

`_sanitize_cycle_trigger_refs` 只在新念头产出后校验，但之前已经存入 STM 的旧念头（如 C236-3 的 `← C237-1` 前向引用）不会被追溯清理。这些错误引用会一直留在 prompt 的念头历史里，直到被滑窗淘汰。

## 22. "我已经发起、正在等回音的事" 显示已完成的 action 且内容是念头而非 action 摘要

C278 prompt 中出现：

> \- [send_message/succeeded] 那句"你怎么不说话"里的等待感一下子把我往前轻轻推了一步，我不想再让在场卡在心里不落地。

两个问题：

**a) 状态应为 running 而非 succeeded。** 该 action 在构建 prompt 时结果尚未返回，应显示 `[send_message/running]`。当前显示 `succeeded` 是状态获取时机有误——不应在渲染时预判结果，而应反映 prompt 构建那一刻的实际状态。

**b) 内容是念头原文而非 action 摘要。** 展示的是触发 action 的念头内容，而不是"正在尝试给 telegram:xxx 发送消息：'我在，刚刚在想事情……'"这样的 action 描述。`_running_action_summary` 用的 `source_content` 是念头原文，应该改为从 action 的实际 request（target、message）提取可读摘要。

## 23. "行动有了回音" 应排在 "我已经发起、正在等回音的事" 之前

当前顺序：等回音 → 行动回音。但逻辑上应该先展示"这些 action 已经有结果了"（行动回音），再展示"这些 action 还在进行中"（等回音），因为：
- 回音是新信息，模型需要先消化才能判断是否要基于结果做下一步
- 等回音的事模型已经知道了（是自己发起的），优先级更低
- 让"还在跑的"紧邻生成点反而可能让模型焦虑地重复发起

调整后顺序：

```
## 好像有一阵子没有……
## 行动有了回音            ← 已完成的结果先展示
## 我已经发起、正在等回音的事  ← 还在跑的排后面
## 此刻我注意到
## 有人对我说话了
## 接下来的念头
```

## 24. bot/main.py 中 `_build_conversation_metadata` 的 `message` 参数缺少类型标注

`def _build_conversation_metadata(message, user: AuthorizedTelegramUser)` 的 `message` 参数没有类型标注。应标注为 `telegram.Message`（或对应的 python-telegram-bot 类型）。同文件中 `_telegram_message_preview(message, limit: int = 200)` 也缺少 `message` 的类型标注。

## 25. 全面排查缺失类型标注的函数参数

CLAUDE.md 要求"参数、函数使用类型标注"。需要全面排查所有模块中缺失类型标注的函数参数和返回值，包括但不限于：
- `bot/main.py` 中多处 `message` 参数
- `core/cycle.py` 中 `prompt_log_file` 参数
- `core/main.py` 中 `log_file`、`prompt_log_file` 等文件句柄参数
- `core/prompt_builder.py` 中新增的各函数

逐个文件检查，补全遗漏的类型标注。

## 26. bot/main.py 中滥用 getattr 访问已知类型的属性

`_build_conversation_metadata` 和 `_telegram_message_preview` 中大量使用 `getattr(message, "reply_to_message", None)`、`getattr(reply, "message_id", None)` 等写法。但如果参数标注了正确的类型（如 `telegram.Message`），这些属性在类型上是确定存在的，不存在时值为 `None`，直接用 `message.reply_to_message` 即可。`getattr` 应留给真正不确定类型的场景，已知类型的属性直接点访问更清晰。需要排查所有模块中是否还有类似的 getattr 滥用。

## 27+28. 对话历史感知与未回复提醒——统一通过 `## 最近的对话` 解决

当前两个问题可以合并解决：
- 未回复的 conversation stimulus 被消费后丢失，模型不会再想起有人联系过
- 模型没有对话历史，不知道之前聊了什么，容易重复说话或回复缺乏上下文

**方案：在 prompt 中增加 `## 最近的对话` 段落**，不需要重新入队机制，对话历史本身就是提醒。

格式设计：

```
## 最近的对话

与 [Bob](telegram:999) 的近期对话：

- 对话历史摘要：Bob 前天问了我天气，我查了告诉他塔林 4°C。
- Bob：谢谢
- 我：不客气。

与 [Jam](telegram:8469901143) 的近期对话：

- 对话历史摘要：Jam 打了个招呼，我回应了。之后他问我能不能找一篇有氧锻炼的文章，我答应了并搜到一篇国家体育总局的科普发给了他，他表示感谢。
- Jam：你怎么不说话
- 我：我在，刚刚在想事情。你在呢，我现在跟你说话。
- Jam：我在观看你的思考
```

**人名格式**：统一使用 `[显示名](telegram:ID)` 格式，名字在前、ID 在后。所有涉及人名的地方都用这个格式——"有人对我说话了"、"最近的对话"、"行动有了回音"的发送目标。

**排序**：多人对话按最后一条消息的时间排序，最近活跃的排最后，离生成点最近。

**时效**：最后一条消息超过 24 小时的对话不展示。如果有人当前轮发了新消息（出现在"有人对我说话了"里），其对话历史一定展示。

**摘要机制**：
- 每人维护一份滚动摘要，存 Redis
- 保留最近 10 条原文，更早的消息滚入摘要
- 生成新摘要时，旧摘要 + 即将被滚出的消息作为输入
- 当原文条数超过阈值时触发压缩
- 摘要作为 `- 对话历史摘要：...` 展示在原文之前

**位置**：放在"有人对我说话了"之前，让模型先看到历史上下文，再看到当前新消息。

**最后消息时间**：每段对话附一行 `- 最后一条消息时间：2026-03-30 01:43`，用机器本地时间，不需要额外计算。这行不纳入摘要文本，渲染时从消息 timestamp 实时取。

## 29. "浮上来的记忆" 与最近念头高度重叠，LTM 检索应排除 STM 窗口内的念头

当前 LTM 存储了所有念头，而 LTM 检索用最近一条念头的 embedding 做向量搜索，自然会匹配到最近几轮的念头本身（因为语义最接近）。这导致 `## 浮上来的记忆` 的内容和 `## 最近的念头` 高度重叠，浪费了 prompt 空间，也没有提供任何新信息。

应在检索时排除 STM 窗口内的念头。方法：把当前 STM 中所有念头的 `source_cycle_id` 传给 `ltm.search`，在 SQL 查询中加 `WHERE source_cycle_id NOT IN (...)` 条件，确保浮上来的记忆来自更久远的过去。

## 30. 时间感不需要同时展示本地时间和 UTC

当前 `[时间感]` 展示为 `现在是 2026-03-30 01:56:20 EEST，UTC 时间 2026-03-29 22:56:20 UTC`。对意识流来说只需要当地时间，UTC 是多余信息。直接用机器 localtime，展示为 `现在是 2026-03-30 01:56 EEST` 即可。

## 31. 身体感觉的系统状态格式有歧义

当前展示为 `1 分钟负载 0.86 / CPU 32；磁盘 20%；内存 19%`。其中 `CPU 32` 是指 32 个核心，但格式上容易被误读为 CPU 使用率 32%。应改为更明确的表述，如 `1 分钟负载 0.86（32 核）；磁盘 20%；内存 19%`。

## 32. 完整的分层耗时日志覆盖

每个 cycle 从 while 循环开始到结束的全部环节都需要耗时统计日志，由粗到细：

**最粗层（main.py while 循环）**：
- 整个 cycle 从开始到下一个 cycle 开始的总耗时（包含 sleep 等待时间和实际执行时间）

**中间层（_execute_cycle 内部各阶段）**：
- STM get_context
- LTM 检索（_retrieve_associations）
- 对话历史加载（_load_recent_conversations）
- 念头生成（run_cycle，包含 prompt 构建 + LLM 调用 + 解析）
- trigger_ref 校验
- STM 写入
- LTM 存储
- action 提交
- 已有部分覆盖，需检查是否有遗漏

**细粒度层（各模块内部）**：
- prompt_builder.build_prompt 各段落拼接
- model_client 每次 generate/chat/embed 调用（已有）
- action 的 planner 决策耗时
- _send_telegram_message 耗时（已有）
- OpenClaw gateway WS/HTTP 调用耗时（已有）
- Redis 操作耗时（hset/hgetall/eval 等，如果单次超过阈值则记录）
- PostgreSQL 查询耗时（LTM search/store/mark_accessed）
- 对话摘要 LLM 调用耗时（已有）

**原则**：
- 使用 `core.types.elapsed_ms` 统一计时
- 粗层和细层都要有，粗层日志包含 status 和关键计数
- 不遗漏任何外部调用（网络 IO、数据库、LLM）
- 程序内部计算如果可能超过 10ms 也要记录（如 prompt 构建、JSON 序列化）
- 格式统一：`logger.info("描述 finished in %.1f ms (key=value, ...)", elapsed_ms(started_at), ...)`

## 33. 被选中但未被模型回应的信息类刺激会永久丢失

news、reading、search 等信息类 action_result 被选入本轮 prompt 后即被消费。如果模型的三个念头都没有对这条信息做出反应（比如同时有对话进来，模型优先回应了对话），这条信息就永久丢失了，下一轮不会再出现。

这意味着模型发起的 search/reading/news 获取到了结果，结果也送到了 prompt 里，但模型因为注意力被其他刺激拉走而没有消化它，之后再也看不到这条信息。

## 34. 添加 `## 我的笔记` 段落和 `note_rewrite` action

给模型一块持久的草稿纸，不受 STM 滑窗淘汰、不受 LTM 语义检索的随机性，完全由模型自己决定写什么、什么时候改写。

**存储**：Redis 单 key `seedwake:note`，一个字符串，覆写即可。

**action**：`{action:note_rewrite, content:"笔记内容"}`，每次完全覆写（不是追加），native 执行。

**限制**：800 字。在 action 执行时截断，不在 prompt 渲染时截断。

**prompt 位置**：`## 我的笔记` 放在 `## 浮上来的记忆` 后面、`## 好像有一阵子没有……` 前面。如果 note 为空（还没写过），不展示这个段落。

**SYSTEM_PROMPT**：在 action 列表里加 `{action:note_rewrite, content:"任意内容"}`，并加一条简短说明，如："我有一块笔记，可以用 {action:note_rewrite} 随时覆写，内容不限语言和形式，800 字以内。"不过度引导用途。

**内容自由度**：不限语言，不限格式，可以是中文、英文、emoji、符号、甚至模型自创的压缩表示。

段落顺序更新：

```
## 最近的念头
## 浮上来的记忆
## 我的笔记              ← 新增
## 好像有一阵子没有……
## 行动有了回音
## 我已经发起、正在等回音的事
## 此刻我注意到
## 最近的对话
## 有人对我说话了
## 接下来的念头
--- 第 N 轮 ---
```

## 35. reading action_result 的展示格式需要调整

当前 reading 结果展示格式有三个问题：

**a) 包含"笔记"部分。** `brief_note` 字段是外部 OpenClaw worker 的观点输出（"这段值得读，因为……"），不应该出现在意识流 prompt 里。外部程序只提供信息，不输出观点。模型应该自己判断值不值得读。

**b) 原文片段太短。** 当前实际返回的摘要往往很短。应确保 excerpt 在 600 字左右，给模型足够的信息量来形成自己的判断。

**c) 格式应简化为来源 + 原文要点。** 当前格式把 summary、source.title、source.url、excerpt_original、brief_note 全部拼在一行里，杂乱。应简化为：

```
来源：标题 (URL)
原文：摘要或原文片段（约 600 字）
```

不需要"笔记"行。外部程序只提供信息，不输出观点。

## 36. 一个念头里的多个 action 只有第一个被执行，后续的被丢弃

`_parse_action` 用 `ACTION_PATTERN.search()` 只匹配第一个 `{action:...}`。当模型在一个念头里写了多个 action（如先 `{action:note_rewrite, ...}` 再 `{action:send_message, ...}`），只有第一个被解析到 `Thought.action_request`，后续的被完全忽略。

实际案例（C843）：模型在一个意图念头里同时写了 note_rewrite 和 send_message，只有 note_rewrite 被提交，send_message 丢失。

**方案**：在 `Thought` dataclass 新增 `additional_action_requests: list[RawActionRequest]` 字段。`_parse_action` 用 `findall` 提取所有匹配，第一个放 `action_request`，其余放 `additional_action_requests`。`submit_from_thoughts` 对每个额外 action 也调用 planner 提交。下游的 planner/executor 逻辑不需要改——它们处理的仍然是单个 action。
