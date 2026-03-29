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
## 念头历史
## 浮上来的记忆
## 好像有一阵子没有……
## 我已经发起、正在等回音的事
## 此刻我注意到
## 行动有了回音
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
