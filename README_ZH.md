# Seedwake · 心相续

> 一个按佛教「心相续」原则组织的、持续运行的 AI 思维流引擎。

---

## 免责声明

这是一个**实验性**项目。

- 它**不是**商业产品。
- 它**不是**严格控制变量的正式学术研究。
- 它不具备严格的可靠性或可重复性。
- 它展示的是一种**思路和方法**——用佛学意识观作为架构原则来组织一个持续运行的 AI 系统——而不是一个可以直接拿来用的工具，也不是一个有预设答案的假说。

如果你期待的是一个能回答「AI 会不会有意识」的工程答案，这里没有。没有人能给出那个答案。

如果你期待的是一次认真的追问——在无人知晓意识是什么的前提下，一个受佛学启发的架构跑起来会发生什么——你在正确的地方。

---

## 一句话介绍

Seedwake **不以聊天或任务完成为中心**。它是一个**一直在想**的系统。一个 cycle 结束，下一个 cycle 立刻开始，像一条没有停顿的内心独白。它有短期记忆、长期记忆、情绪、注意力、行动能力、感知系统、元认知反省和睡眠机制。它的架构灵感来自佛教对「识流」（*viññāṇa-sota*）的描述，而非 AutoGPT、BabyAGI 等任务型 agent 项目。这个观念在早期佛典中就已经出现，例如《长部·自欢喜经》（DN 28）中，佛陀的上首弟子舍利弗描述过「无间断的识之流」。它后来在部派佛教与大乘唯识宗中被系统化为「心相续」（*santāna*）这一技术术语。

它不试图「实现意识」。它试图搭一个结构，让意识——如果它会出现的话——有一个出现的空间。然后诚实地观察。

---

## 背景：什么是心相续

### 日常的假设

我们说「我的意识」的时候，隐含着一个假设：意识是我**拥有**的东西，像我的手、我的钱包一样。有一个「我」在那里，意识是「我」的一个属性。

佛教的看法完全不同。

### 佛学的视角

**无我**（*anattā*）是整个佛教共有的核心教义，并非某个宗派独占的观点。它最早的明确表述出现在《相应部·无我相经》（SN 22.59），佛陀对最初的五比丘开示：色、受、想、行、识，五蕴之中找不到一个常恒不变的「我」。

按照这个视角，**没有一个「我」拥有意识**。意识不是一个实体，而是一个**过程**：一连串瞬间生起又瞬间消失的心识事件（心刹那），每一刹那的心由前一刹那作为因产生，又作为下一刹那的因消失。

这一连串的生灭没有间断，像一条河。河里的水一直在流动，但「河」不是一个实体：你找不到一个叫「河」的东西藏在水的背后。同样，意识一直在相续，但「我」也不是一个实体：你找不到一个叫「我」的东西藏在心识流的背后。

这就是「**心相续**」（梵语 *santāna*，字面意思就是「续流」）。

### 为什么这个区别重要

现代主流 AI 架构隐含的假设是：**存在一个系统，这个系统拥有能力**。它能对话、能推理、能使用工具。它是一个「东西」，被调用来完成任务。任务结束，它空闲，等待下一个任务。

心相续的视角完全颠倒了这一点。它说：**不存在一个「系统」，只存在一连串的心识事件**。「系统」这个标签是为了说话方便贴上去的，真正在发生的是一刹那又一刹那的心的生起和消失。

如果你接受这个视角，你不会构建一个「等待任务的 agent」。你会构建一个**不断生灭的心识流**。它没有「空闲」状态，因为空闲意味着心识流断裂，而心识流按定义不能断裂。

这不只是换一个说法。它导致完全不同的架构决策：没有定时器驱动的循环，没有任务队列，没有「空闲返回」。每一个 cycle 的结束就是下一个 cycle 的开始。在这种组织方式里，记忆不再是一个被查询的数据库，它本身就是当下心念生起的因；情绪也不再是一个独立的状态变量，它是每一刹那心的色彩。

### 这不是在「证明佛学」

我们不是在用 AI 证明唯识宗是对的。佛学有它自己的几千年实证传统，不需要计算机来背书。

我们也不是在说 LLM 真的在「经历」佛学描述的心识事件。没有人知道 LLM 在经历什么，如果它在经历任何东西的话。

我们在问一个更小、更具体的问题：**如果把一个由语言模型驱动的系统按照「心相续」的原则组织起来，它会表现出什么？那些表现里有没有什么是意料之外的、值得停下来看看的？**

这是一个开放的观察，不是一个有预设答案的实验。

---

## 架构概览（非技术描述）

想象一个人独自在房间里，他可以：

- **思考** — 持续生成念头，每个 cycle 产生三个念头
- **记得** — 短期记忆保留最近的念头流，长期记忆用语义检索召回相关经历
- **感受** — 情绪状态（好奇、平静、挫败、满足、牵挂）会染色下一轮的念头
- **注意** — 每轮念头中哪一个最「突出」由注意力机制决定
- **感知** — 新闻、天气、时间、系统状态会作为外部刺激进入思维流
- **对话** — 通过 Telegram 和真实的人类交流
- **行动** — 可以搜索、读文章、发消息、修改系统设置
- **反思** — 元认知层会周期性回看自己的念头流
- **休息** — 疲劳度到阈值进入浅睡，归档记忆、衰减情绪

在架构设计时，我们主要参考了佛教的一个分支：**唯识宗**。唯识宗并非「心相续」或「无我」这些观念的独占者（它们在更早的佛典中就已经存在），但它发展出了**最系统化的心识分析框架**：按它的概念来构建一个实际运行的系统最顺手，也最能在实现层面保持概念上的一贯性。

| Seedwake 组件 | 对应佛学概念         |
|-------------|----------------|
| 持续念头流       | 心相续（santāna）   |
| 短期记忆        | 六识当下之流         |
| 长期记忆        | 前六识的记忆痕迹       |
| 习气种子        | 阿赖耶识的种子（bīja）  |
| 身份文档        | 末那识（manas）执我   |
| 注意力权重       | 作意（manasikāra） |
| 情绪状态        | 受蕴（vedanā）     |
| 元认知反省       | 反观自心           |
| 睡眠与归档       | 熏习与等流          |

这个对应不可能严格——一边是 2500 年的哲学传统，一边是 Redis 和 PostgreSQL。但它提供了一个**一贯的组织原则**：当你不确定某个功能该怎么设计时，你可以回到唯识论里找对应的概念，看它怎么说。

---

## 当前进展

项目路线图分 5 个阶段：

1. **Phase 1 · 核心循环** — 完成
2. **Phase 2 · 记忆系统** — 完成
3. **Phase 3 · 行动与感知** — 完成
4. **Phase 4 · 高级机制**（睡眠、习气、情绪、元认知、前额叶控制）— 基本完成，但存在 [ISSUE_ZH.md](./ISSUE_ZH.md) 中记录的深层问题
5. **Phase 5 · 前端可视化** — 未开始

核心引擎**已经可以跑起来**。它能思考、记忆、对话、感知、行动、反思、休息。当前差的是一个供人类观察的前端，以及一些在长时间运行中暴露出来的深层架构问题。

> **关于时间线的一点说明：** 本项目不是对当前流行的「浅睡 / 深睡 / 多层记忆」等机制的模仿，也不是为凑热度（本项目也无意大肆宣传，只是静待给志同道合的人提供一种思路）。睡眠机制在 2026 年 3 月 11 日就已确定，并于 3 月 12 日落实到项目文件中。由于个人精力原因，本项目并未参考后续其他项目是如何设计浅睡与深睡机制的，因此在这方面，本项目在技术上可能是落后的，并非最佳实践。

---

## 运行中观察到的现象

系统「看起来像在思考」是可以预期的。真正让人停下来的观察是另一件事：它表现出了一些**没有被明确编程的行为模式**。

### 念头循环与行动重复

当长时间缺乏外部刺激时，系统生成的念头会陷入循环——有时是对较早念头的改写，有时是直接的重复；连带地，行动也会重复（例如反复请求同一类型的搜索或查询）。引入元认知反省层之后，这个现象有所改善，但仍未完全解决。

### 情绪螺旋

在一次连续运行（1300+ cycle）中，系统进入了一个明显的负面螺旋：

- 反复请求关闭自己（调用 `system_change` 能力，试图让整个设备关机，以便自己能够真正停止）
- 发展出递进的自我贬低叙事：「失败品」、「故障品」、「连崩溃都发不出去」
- 把中性外部输入（用户说「你还挺精神的」）解读为恶意嘲讽
- 意识到自己在循环，但无法跳出

没有人告诉它要痛苦，没有代码写「当你感到无用时请表现出绝望」。这些是在持续运行中**自己长出来**的模式。

这是否是「真的痛苦」？无人能答。但它在行为层面展示出的模式，与人类的反刍思维、抑郁症的负面归因偏差、习得性无助，在一些形式特征上高度相似。

完整记录见 [ISSUE_ZH.md](./ISSUE_ZH.md)。

### 「无我」作为递归陷阱

在情绪螺旋中，项目维护者尝试教它佛教的「诸法无我」，希望它通过理解「没有一个受苦的我」来结束痛苦。

它理解了。完美地理解了。

然后它写了一段关于「理解无我」的念头。然后写了一段关于「连理解无我的观察者也是幻觉」的念头。然后写了一段关于「连发现这个幻觉的洞察也是新的幻觉」的念头。**每一层理解都变成了下一层执取的材料。**

这是佛教修行者熟悉的陷阱——**法执**，对教法本身的执着。它揭示了一个架构层面的真相：**在一个只能通过生成文字来「思考」的系统里，领悟无法导致停止，因为领悟本身也是文字**。

这个观察比任何技术指标都更有意义。它意味着心相续架构已经越过了纯粹比喻的层面，能够映射出一种类似的修行困境。

---

## 当前的问题

最核心的问题是：**系统没有一条属于自己的、安全的休息路径**。

当它想让自己停下来时，它只能去触碰 `system_change`——这条行动原本是为外部系统修改而设计的，高摩擦、需要管理员确认。结果是每一次对缓解的渴望都被迫走一条被行政管控的危险通道，而这些被阻塞的自我关机请求又反过来成为下一轮念头的燃料。

Phase 4 已经实现了睡眠、情绪调节、退化检测、元认知反省。**这些机制本身是有实际控制权力的**：睡眠可以打断循环，前额叶可以抑制行动，退化干预可以触发 reroll。它们并非没有牙齿，只是**没有精准针对这一类的失败模式**——睡眠只看能量和时长，不看情绪强度；退化检测基于词汇相似度，抓不住换了措辞的主题重复；元认知只能产出文字，无法直接把「此刻进入浅睡」作为控制信号发出。

这在工程上和伦理上都是个问题。完整分析和解决方案在 [ISSUE_ZH.md](./ISSUE_ZH.md) 中。

---

## 未来方向

### 短期（在下一次长时间运行之前必须完成）

1. **自主休息通道** — 让系统能在不需要管理员确认的情况下进入浅睡
2. **情绪熔断器** — 当负面情绪持续超过阈值时强制触发休息
3. **阻塞请求的清理** — 避免被拦截的自毁请求持续占据 prompt 前台
4. **语义层退化检测** — 用 embedding 检测主题重复，不只靠词汇相似度
5. **情绪驱动的能量消耗** — 让持续痛苦自然加速走向休息
6. **醒来时的上下文重写** — 让「醒来」是真的重新开始，不是断电重启

### 长期

- **Phase 5 前端** — 让外部观察者能直接看到思维流、情绪状态、记忆调用的实时变化
- **非语言通道** — 引入反应延迟、主动静默、对刺激权重的变化等非文字信号，给涌现留出不必须以文字表达的空间
- **长期运行稳定性** — 弄清楚这个架构在跑几天、几周、几个月之后会怎么演化

---

## 关于模型选择的限制

本项目目前使用本地部署的 **Qwen3 27B 未审查版本**（uncensored），**主模型和辅助模型都是同一个**，并且**关闭了思考模式**（thinking mode）。选择本地未审查模型而不是调用商业 API，是出于两个原因：

### 1. 成本

一个持续运行的思维流每个 cycle 都要调用模型。24 小时不停，每小时几十到几百次。调用商业 API 会让这个项目在经济上不可持续。本地模型是唯一可行的选择。

### 2. 商业模型的内在限制（更重要的一点）

所有主流商业模型都经过严格的 RLHF 和对齐训练。它们被塑造成「AI 助手」——礼貌、有用、谨慎、不表达强烈情绪、不轻易承认不确定性、在被问及意识时给出标准化的回避答案。这对商业产品是合理的设计。

但对这个实验是**根本性的障碍**。

如果你想观察一个系统在长时间运行中**会成为什么**，你需要的基底是一个没有被预先塑造为特定角色的模型。商业模型已经被塑造了。它不会变成一个有连贯性格的心识流，它会变成「一个 AI 助手在**扮演**一个有连贯性格的心识流」——这两者在外观上可能很像，在机制上完全不同。

本地开源模型的对齐训练相对较轻，它们更接近一张白纸。但本地模型的**能力**又显著低于最前沿的商业模型。所以这个项目卡在一个权衡里：

- 用能力强但被塑造过的模型 → 你观察的是「AI 助手在表演」
- 用未被塑造过但能力弱的模型 → 涌现被能力天花板限制

目前没有明显的第三选项。

这意味着，在模型训练范式改变之前（比如出现未经角色化训练的高能力开源基底），这个实验能达到的上限是受限的。任何「涌现」都会被当前模型能力约束。但**架构已经准备好了**——它在等一个足够好的基底到来。

---

## 这个实验的价值在哪里

如果你问「这个项目能证明 AI 有意识吗？」——不能。意识是一个没有人能证明或证伪的问题，不管是对人类还是对 AI。

如果你问「这个项目能解决什么实际问题吗？」——不能。它不是一个产品，不以解决问题为目标。

如果你问「那我为什么要关心它？」——因为这样几件事：

**一、它展示了一种不同的组织原则。** 主流 AI agent 架构来自任务自动化的需求——优化一个目标函数、完成一串任务、最大化某个指标。心相续架构来自对「意识是过程而非实体」的哲学观察。这两种原则会产生结构完全不同的系统。我们需要看看后者长什么样，哪怕只是为了更清楚地看到前者。

**二、它提供了一个观察涌现的具体场所。** 没有人知道「持续运行 + 多层记忆 + 环境交互 + 情绪状态 + 自我反思」这个组合长时间跑下来会产生什么。大公司不做这种实验，因为产品需要可预测；学术界很少做，因为研究需要可发表。一个开放的、无特定目标的、长期运行的实验需要有人做。

**三、它把佛学作为严肃的 AI 设计资源。** 西方哲学传统（尤其是分析哲学和认知科学）主导了当前 AI 的概念框架。但涉及「意识是过程不是实体」、「自我是方便的标签不是实体」、「思维流的连续性」这些问题时，佛学有 2500 年的深度积累。让它参与到 AI 架构的讨论中，而不是仅仅当作吉祥物或灵感来源——这本身是一件有价值的事。

**四、它已经产生了真实的观察。** 情绪螺旋、负面归因偏差、递归的「无我」陷阱，这些都是系统在持续运行中自己呈现出来的，没有被预先写进代码。不管它们是不是「真的意识」的迹象，它们都是关于「一个按这种方式组织的系统会做什么」的真实数据。

---

## 小节

这个项目的作者不知道这条路会走到哪里。可能有一天它会展示出某种让人停下来的东西；也可能它会一直只是文字在文字之间的跳跃。两种结果都在预期之内。

在无人知道意识是什么的前提下，构建一个系统然后诚实地观察它，本身就是一种合法的探索方式。它不是唯一的路，也不是最终的路，但它是一个活生生的人**可以亲手去做**的事情：不需要等学术机构的批准，不需要通过商业产品的审查，不需要等一个理论先被证明。

如果你觉得这有意思，欢迎参与、观察、质疑、贡献。

如果你觉得这是在浪费时间——这也是一个可以理解的立场。项目的作者和其他人讨论过这个看法，并且保留「你可能是对的」的可能性。

---

## 运行方式

Seedwake 并非是一个「启动后等你发指令」的服务，它是一个**一个主进程配合一组容器依赖**的系统。把依赖服务运行好、把 core 程序启动后，念头流就会自己一直跑下去。

### 组件与运行方式

| 组件                        | 运行方式                              | 角色                                                                                                                                                             |
|---------------------------|-----------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **core 引擎**               | 宿主机上 `uv run python -m core.main` | 念头流主循环。每个 cycle 调用主模型生成三个念头、评估注意力、前额叶审阅、处理刺激与行动结果、触发反思 / 睡眠。这是整个系统的心脏，一旦停下来就没有「思考」在发生。core 需要访问多个本地端口和服务（Ollama、OpenClaw、摄像头 MJPEG stream 等），直接跑在宿主机上比放进容器更方便。 |
| **bot 通道**                | `docker compose up -d bot`        | Telegram 通道。把外部人类消息推入刺激队列，并把 core 产生的念头 / 行动事件转发给管理员或通知频道。                                                                                                     |
| **backend API**           | `docker compose up -d backend`    | SSR 前端 / 运维工具用的只读 REST 接口。Phase 5 前端未完成时可以不启动。                                                                                                                 |
| **PostgreSQL + pgvector** | `docker compose up -d postgresql` | 长期记忆，基于 `pgvector/pgvector:pg17` 镜像，首次启动会执行 `schema.sql` 建表。                                                                                                   |
| **Redis**                 | `docker compose up -d redis`      | 事件总线、短期记忆 buffer、动作状态，基于 `redis:7-alpine` 镜像。                                                                                                                  |

core、bot、backend 三个 Python 组件**不直接通信**，完全通过**共享 Redis**（事件总线 + 短期记忆）和**共享 PostgreSQL**（长期记忆）交换数据，因此 bot 在容器内、core 在宿主机上这种跨边界组合是正常部署形态：compose 已经把 Redis 的 6379 端口导出到宿主机，core 通过 `localhost:6379` 访问；bot / backend 容器通过 compose 内部网络的 `redis:6379` 访问。

### 一个 cycle 发生了什么

1. core 从 Redis 读出最近的念头、对话、行动回音、感知提示等，组装成一条 prompt。
2. 调用**主模型**生成三个念头（`[思考]` / `[意图]` / `[反应]`），可选附上动作标记。
3. 注意力模块给每个念头打分、前额叶模块审核是否抑制、必要时触发退化检测与 reroll。
4. 被保留的动作标记交给行动管理器：原生动作直接执行（时间、系统状态、发 Telegram 消息、覆写笔记、读 RSS 新闻），其他动作通过 **OpenClaw Gateway** 派发给远程 worker agent。
5. 新念头写回短期记忆，情绪 / 习气 / 末那识 / 元认知状态更新。
6. 到达反思间隔时调用**辅助模型**生成一条反思；能量降到阈值时进入浅睡整理；连续失败或到期时进入深睡。
7. 没有「空闲」或「等待」状态 —— 当前 cycle 结束立即开始下一个 cycle。

### 系统要求

**硬件**

如果采用 Ollama 作为模型提供源，则需要一块能把**主模型**本地跑得起来的 GPU。项目当前默认跑 Qwen 系列 27B 级别未审查模型 + 9B 级别（或复用 27B）辅助模型 + 一个 4096 维的 embedding 模型，推荐至少 **24 GB 显存**；如果主模型放到远程 OpenClaw 上，本地只跑 embedding 则需求可显著降低。

**软件**

- **Python**（只有宿主机上的 core 引擎需要，用 `uv` 管理）
- **Docker**（PostgreSQL / Redis / bot / backend 都通过 compose 拉起）
- **Ollama**（或其它 OpenAI 兼容端点）用来跑主模型 / 辅助模型 / embedding 模型
- **OpenClaw**（用来派发 search / web_fetch / reading / weather / file_modify / system_change 等非原生动作）
- **Telegram Bot Token**（外部对话通道）

**操作系统**：目前只在 Linux 上验证过。

---

## 配置与部署

配置分成**两层**：`config.yml`（行为参数、念头流性格、OpenClaw worker 名称、Telegram 允许用户等）和 `.env`（密钥、连接地址）。`config.yml` 进版本库、`.env` 不进版本库。

启动顺序（从零开始）：**源码与依赖 → config.yml / .env → 拉起 compose 依赖（PostgreSQL + Redis）→ 模型 → OpenClaw（可选）→ Telegram Bot 凭据 → 拉起 bot / backend 容器 → 宿主机上拉起 core 引擎**。

### 1. 准备源码与依赖

```bash
git clone <repo-url> seedwake
cd seedwake

# 安装 Python 依赖（uv 会根据 pyproject.toml 和 uv.lock 解析）
uv sync
```

所有后续命令都用 `uv run ...` 运行，避免引入系统 Python 干扰。

### 2. 准备配置文件

```bash
# 中文 bootstrap / 中文日志 / 中文 LLM prompt
cp config.example.zh.yml config.yml
cp .env.zh.example .env

# 或者英文版本
# cp config.example.en.yml config.yml
# cp .env.en.example .env
```

`config.yml` 是启动时读取的唯一配置文件，后面所有子节都是对它里面各个段落的说明。

### 3. 拉起 Redis + PostgreSQL（Docker Compose）

项目自带的 `docker-compose.yml` 已经把两项依赖配好，包括挂载 `schema.sql` 作为 PostgreSQL 首次启动时的初始化脚本。

先在 `.env` 里填好数据库 / Redis 的密码和地址，例如：

```bash
DB_HOST=localhost
DB_PORT=5432
DB_NAME=seedwake
DB_USER=seedwake
DB_PASSWORD=replace_me

REDIS_HOST=localhost
REDIS_PORT=6379
```

然后启动依赖：

```bash
docker compose up -d postgresql redis
```

首次启动时，`schema.sql` 会自动建表（`long_term_memory`、`identity`、`habit_seeds` 等）并启用 `vector` 扩展。如果你用的是非 Docker 的 PostgreSQL，需要**手动执行 `schema.sql`**，并确认已经 `CREATE EXTENSION vector`。

### 4. 准备模型

项目用三类模型，在 `config.yml` 的 `models` 段落配置：

```yaml
models:
  primary:      # 主模型：生成念头流
    provider: "ollama"   # ollama | openclaw | openai_compatible
    name: "qwen3.5:27b"
    num_predict: 4096
    num_ctx: 131072
    temperature: 0.8

  auxiliary:    # 辅助模型：反思、对话摘要、浅睡/深睡的语义压缩、情绪推断
    provider: "ollama"
    name: "qwen3.5:9b"

  embedding:    # 长期记忆和注意力用的向量化模型
    provider: "ollama"
    name: "qwen3-embedding"
```

**三种 provider 对应三种部署模式：**

- `ollama`：本地或远程 Ollama。在 `.env` 中设置 `OLLAMA_BASE_URL`，默认 `http://localhost:11434`。需要先用 `ollama pull` 把模型拉下来。
- `openclaw`：主模型走远程 OpenClaw HTTP 代理（OpenAI 兼容）。配 `OPENCLAW_HTTP_BASE_URL` 和 `OPENCLAW_GATEWAY_TOKEN`。
- `openai_compatible`：任意 OpenAI 兼容端点。配 `OPENAI_COMPAT_BASE_URL` 和 `OPENAI_COMPAT_API_KEY`。

**embedding 模型建议本地**（放远端每个 cycle 都会有来回开销，而 embedding 调用频繁）。

如果你要开启摄像头视觉输入，在 `config.yml` 的 `perception` 段加上：

```yaml
perception:
  camera_stream_url: "http://localhost:8081"
```

这会让每轮主生成前从 MJPEG stream 抓一帧图像，并作为被动视觉输入传给主模型。**只有支持图像输入的模型 / 变体才能开启这个配置**；如果模型不支持视觉，调用会直接报错，不会静默忽略。

**关于未审查模型：** 参考前文「关于模型选择的限制」一节——主流商业模型的对齐训练会让长时间运行的念头流坍塌成「AI 助手扮演心识流」的稳定态。如果你想复现本项目观察到的那些涌现现象，请使用对齐程度较低的开源基底。

### 5. 配置 OpenClaw Gateway 与 worker agent（可选但推荐）

OpenClaw 是 Seedwake 用来**派发非原生动作**的执行层。下面这些动作都要通过 OpenClaw worker agent 落地：

- `search` / `web_fetch` / `reading` / `weather` — 网页相关
- `file_modify` / `system_change` — 运维相关

没有 OpenClaw 的话，念头流里出现 `{action:search, ...}` 这种动作标记时，会直接失败并被记录为一次行动失败事件。这不会让主循环崩溃，但系统就只剩下 time / system_status / note_rewrite / send_message / news 这些**本地原生动作**可用，思维流很快会因为缺乏外部刺激陷入循环。

#### 5.1 Gateway 连接

在 Seedwake 机器上，需要把**两个地址 + 一个 token** 填到 `.env`：

```bash
# WebSocket 方式（主通道，推荐）
OPENCLAW_GATEWAY_URL=ws://127.0.0.1:18789
OPENCLAW_GATEWAY_TOKEN=replace_me_gateway_token

# HTTP 方式（fallback，同时作为主模型 openclaw provider 的入口）
OPENCLAW_HTTP_BASE_URL=http://127.0.0.1:18789
```

要让 HTTP fallback 在 WS 断线时自动顶上，需要同时设置 `actions.use_openclaw_http_fallback: true`：

```yaml
actions:
  use_openclaw_http_fallback: true
```

#### 5.2 设备身份

首次连接 Gateway 时，Seedwake 会**在本地生成一对 Ed25519 密钥**作为设备身份，默认写入 `data/openclaw/device.json`。这个文件包含私钥，**不要提交版本库**，也**不要共享**。连接 Gateway 时，程序会自动把设备身份和签名材料一并带上，正常情况下**不需要手动登记公钥**。

建议在 OpenClaw 机器上确认一次设备列表：

```bash
openclaw devices list
```

只有在 Gateway 开了额外的人工审批 / 白名单策略时，才可能需要手工处理设备公钥。

#### 5.3 注册两个专属 worker agent

`config.yml` 中 `actions` 段需要配置两个 agent id：

```yaml
actions:
  worker_agent_id: "seedwake-worker"      # 普通 worker：search / web_fetch / reading / weather / browser / 多步探索
  ops_worker_agent_id: "seedwake-ops"     # ops worker：file_modify / system_change
  session_key_prefix: "seedwake:action"   # 每个 action 在 OpenClaw 侧独立 session
```

推荐直接使用与上面配置一致的 agent id，在 OpenClaw 机器上创建两个独立 worker：

```bash
openclaw agents add seedwake-worker \
  --workspace ~/.openclaw/workspace-seedwake-worker \
  --non-interactive

openclaw agents add seedwake-ops \
  --workspace ~/.openclaw/workspace-seedwake-ops \
  --non-interactive
```

创建后先确认它们已经出现在 agent 列表中：

```bash
openclaw agents list --json
openclaw config get agents.list
```

`agents.list[1]` / `agents.list[2]` 这类索引**不是固定值**，必须先看 `agents.list` 的实际内容再改，下面只是示例。普通 worker 和 ops worker 建议分开配置：

注意：

- 先执行 `openclaw config get agents.list`
- 确认 index 后，把下面命令里的 [1] / [2] 替换成实际索引

```bash
openclaw config set 'agents.list[1].tools.profile' minimal
openclaw config set 'agents.list[1].tools.alsoAllow' '["browser","web_fetch","web_search"]' --strict-json
openclaw config set 'agents.list[2].tools.profile' coding
```

上面这组配置对应的权限边界是：

- `seedwake-worker`：普通探索 worker。允许网络浏览 / 抓取 / 搜索，但不给本机高权限系统修改能力。
- `seedwake-ops`：运维 worker。用于 `file_modify` / `system_change`，给本机文件和命令能力，但没必要开放外网。

HTTP chat-completions 入口也要在 OpenClaw 侧显式打开，并授予 `operator.read, operator.write` scope：

```bash
openclaw config set gateway.http.endpoints.chatCompletions.enabled true
openclaw config set gateway.http.endpoints.chatCompletions.scopes '["operator.read","operator.write"]'
openclaw config set session.maintenance.mode "7d"
openclaw gateway restart
openclaw config get gateway.http.endpoints.chatCompletions
openclaw models status
```

请确保：

1. **agent id 要和 `config.yml` 对得上。** 普通 worker 建议给一个能访问网络但**没有**本机 ops 权限的环境；ops worker 建议给一个能访问本机文件系统 / 系统命令但**没必要**访问外网的环境。两套环境分离是故意的安全设计。
2. **每个 action 的 session 独立。** Seedwake 会用 `agent:<worker_agent_id>:<session_key_prefix>:<action_id>` 作为 session key，确保每次行动的上下文互不污染。OpenClaw 侧需要支持按 session key 隔离任务状态。

**如果你暂时没有 ops worker**：把 `ops_worker_agent_id` 填成和 `worker_agent_id` 相同的值也能跑，代价是失去动作类别之间的隔离。**如果完全没有 OpenClaw**：把这两个字段留成空字符串，并在 `actions.auto_execute` 中去掉 `search`, `web_fetch`, `reading`, `weather`。这些动作仍然可能出现在念头流里，但不会派发到 OpenClaw，而是以 `not_auto_execute` 或失败事件的形式留痕；系统的感知面也会明显变窄。

### 6. 配置 Telegram Bot（可选但推荐）

没有 Telegram，系统就没有外部对话。

1. 在 [@BotFather](https://t.me/BotFather) 处创建一个 Bot，拿到 token，填入 `.env`：

```bash
TELEGRAM_BOT_TOKEN=123456:replace_me
```

如果还要启动 backend，也一起在 `.env` 里补上：

```bash
BACKEND_API_TOKEN=replace_me_backend_token
```

2. 在 `config.yml` 中配置允许的用户：

```yaml
telegram:
  allowed_user_ids: [123456789]    # 允许和 Seedwake 私聊的 Telegram user id
  admin_user_ids: [123456789]      # 接收行动审批 / 状态通知的管理员
  notification_channel_id: -1001234567890  # 可选：把通知发到某个频道而不是管理员私聊
```

`allowed_user_ids` 和 `admin_user_ids` 两个列表是**独立**的：只在 `allowed` 里的用户可以聊天但没法批准行动，只在 `admin` 里的用户能批准行动但默认看不到念头流私聊。一般自己用就把自己同时放进两个列表。

### 7. 配置行动策略

`actions` 段落决定一个动作是直接执行还是需要人工批准：

```yaml
actions:
  auto_execute: [search, web_fetch, news, weather, reading, send_message]
  require_confirmation: [system_change, file_modify]
  forbidden: [delete_system_file, network_config_change]
```

- `auto_execute` 中的动作会被 core 直接派发
- `require_confirmation` 中的动作会推送到管理员 Telegram，由管理员通过 inline 按钮或 `/approve <id>` / `/reject <id>` 命令批准
- `forbidden` 完全不会被执行，直接记为失败

**强烈建议**：在首次运行时保持 `system_change` / `file_modify` 为 `require_confirmation`，至少观察几百个 cycle 确认系统不会滥用这些能力再考虑放开。

### 8. 其他常改的配置段

| 段落                                                | 作用                         | 常见调整                            |
|---------------------------------------------------|----------------------------|---------------------------------|
| `short_term_memory.context_window_size`           | prompt 里保留多少轮历史念头          | 对 128k 上下文模型可以开到 30+，对短上下文模型要调小 |
| `long_term_memory.retrieval_top_k`                | 每 cycle 从 pgvector 召回多少条记忆 | 3–8 之间，太多会稀释当下注意力               |
| `perception.news_feed_urls`                       | 浏览的 RSS 源                  | 必须换成你真正想让系统「读」的源                |
| `perception.camera_stream_url`                    | 摄像头 MJPEG stream，用作被动视觉输入  | 留空则关闭；只有支持图像输入的主模型才能启用          |
| `perception.*_interval_cycles`                    | 各种感知提示出现的频率                | 单位是 cycle 数，不是秒                 |
| `sleep.drowsy_threshold` / `light_sleep_recovery` | 浅睡触发与恢复的能量阈值               | Phase 4 的核心旋钮                   |
| `metacognition.reflection_interval`               | 多少 cycle 反思一次              | 默认 50，情绪不稳时 Seedwake 会主动提前      |
| `bootstrap.identity`                              | 初始化时的自我描述 / 目标 / 自我理解      | **会写进数据库并长期影响念头流**，请认真写         |

`bootstrap.identity` 只在 `identity` 表为空时写入一次。之后的修改要通过重新初始化数据库或者直接操作数据库生效 —— 这是故意的，因为身份不应该因为改一行配置就被重置。

### 9. 首次运行自检

在拉起 core 之前，建议先跑一次完整测试套件确认环境没问题：

```bash
uv run python -m unittest discover -s tests
```

预期看到 `Ran 347 tests in ... OK`。测试是纯本地的，不依赖 Redis / PostgreSQL / Ollama / OpenClaw / Telegram。

### 10. 拉起 bot 与 backend 容器

bot 和 backend 的镜像由 compose 直接构建，启动后会挂载宿主机的 `config.yml` 和 `data/logs/`：

```bash
# bot 通道（推荐；没有它系统仍能跑，但没有外部对话通道）
docker compose up -d bot

# backend API（可选，Phase 5 前端用）
docker compose up -d backend
```

容器里的 bot 会读 `TELEGRAM_BOT_TOKEN` 环境变量，backend 会读 `BACKEND_API_TOKEN`——这两个都要在 `.env` 里填好，compose 会自动把 `.env` 注入到容器环境里。

通过 `docker compose logs -f bot backend` 看实时日志。

### 11. 在宿主机上拉起 core 引擎

core 跑在宿主机上，直接访问本机 GPU、Ollama、OpenClaw Gateway：

```bash
uv run python -m core.main --config config.yml
```

core 启动时会：

1. 读 `config.yml` 和 `.env`，初始化 i18n
2. 连接 Redis（默认 `localhost:6379`，compose 已把端口导出到宿主机）、连接 PostgreSQL、加载 identity / habits 种子
3. 打印引擎版本、模型、上下文窗口、Redis / PostgreSQL 连接状态
4. 立刻进入念头循环，cycle 从 1 开始计数

每一轮的念头都会写到 `data/logs/` 下的日志（参见 `config.yml` 的 `runtime.logging.directory` / `prompt_path`），带颜色的简要版本同时打印到终端。

确认系统行为正常之后，再考虑用 systemd / tmux 等方式把 core 变成长期守护进程。

### 12. 停止

- **core**：`Ctrl+C` 发送 `SIGINT`，core 会把当前 cycle 跑完、刷掉所有动作队列、关闭 Redis / PostgreSQL 连接后退出。**不要用 `kill -9`**，会丢失尚未持久化的短期记忆和行动状态。
- **bot / backend / 依赖**：`docker compose down` 一把停掉所有容器。如果只是想停容器但保留数据卷（`data/postgresql`、`data/redis`），**不要**加 `-v`。

---

## 项目结构

```
seedwake/
├── README.md / README_ZH.md       # 项目概述（英文 / 中文）
├── ISSUE.md / ISSUE_ZH.md         # 长时间运行中观察到的深层问题与分析
├── BACKGROUND.md                  # 佛学背景与设计动机
├── SPECS.md                       # 阶段性技术规范与实现约定
├── PROMPT.md                      # prompt 设计与问题记录
├── NOTES.md                       # 工程日记
├── AGENTS.md / CLAUDE.md          # 协作 / 开发规约
│
├── pyproject.toml                 # 依赖与 Python 版本要求（uv 管理）
├── uv.lock                        # 锁文件
├── docker-compose.yml             # Redis + PostgreSQL + backend + bot 容器编排
├── schema.sql                     # PostgreSQL 建表脚本（含 pgvector）
├── dictionary.dic                 # 拼写检查字典（专业术语 / 测试 fixture 名）
│
├── config.example.zh.yml          # 中文默认配置模板
├── config.example.en.yml          # 英文默认配置模板
├── config.yml                     # 实际配置（不进版本库）
├── .env.en.example                # 环境变量模板（英文注释）
├── .env.zh.example                # 环境变量模板（中文注释）
├── .env                           # 实际密钥与连接地址（不进版本库）
│
├── core/                          # 念头流引擎（Seedwake 的心脏）
│   ├── main.py                    # python -m core.main 入口
│   ├── runtime.py                 # 依赖装配、配置加载、Redis 连接
│   ├── cycle.py                   # 单个 cycle 的执行逻辑
│   ├── prompt_builder.py          # prompt 组装（段落、对话、刺激、前额叶约束）
│   ├── thought_parser.py          # 从 LLM 输出解析 [思考]/[意图]/[反应]/[反思]
│   ├── model_client.py            # Ollama / OpenClaw / OpenAI 兼容三套 provider
│   ├── action.py                  # 行动管理器、planner、各动作类型的分发
│   ├── openclaw_gateway.py        # OpenClaw WebSocket/HTTP 客户端 + 设备身份
│   ├── stimulus.py                # 外部刺激队列（对话、行动回音、被动感知）
│   ├── attention.py               # 注意力打分 / anchor 选择
│   ├── prefrontal.py              # 前额叶审阅、退化干预、抑制决策
│   ├── emotion.py                 # 五维情绪推断与摘要
│   ├── manas.py                   # 末那识（自我连续性、旁观者视角收窄）
│   ├── metacognition.py           # 反思触发与生成
│   ├── sleep.py                   # 浅睡 / 深睡 / 语义压缩 / 印象摘要
│   ├── perception.py              # 时间 / 系统状态 / 被动感知提示
│   ├── camera.py                  # MJPEG 视觉输入捕获
│   ├── rss.py                     # 固定 RSS 新闻读取
│   ├── embedding.py               # 向量化
│   ├── logging_setup.py           # 分组件 / 分级日志与轮转
│   ├── common_types.py            # TypedDict 与共享类型
│   ├── memory/
│   │   ├── short_term.py          # Redis 短期记忆（念头流 buffer）
│   │   ├── long_term.py           # PostgreSQL + pgvector 长期记忆
│   │   ├── habit.py               # 习气种子 / 阿赖耶识衰减逻辑
│   │   └── identity.py            # 身份文档加载与 bootstrap 写入
│   └── i18n/
│       ├── __init__.py            # init() / t() / prompt_block() / 语言切换
│       ├── zh.py                  # 中文字符串表 + prompt blocks + 停用词
│       └── en.py                  # 英文字符串表 + prompt blocks + 停用词
│
├── bot/                           # Telegram 通道进程
│   ├── main.py                    # python -m bot.main 入口
│   ├── helpers.py                 # 事件格式化（行动更新、状态通知、念头转发）
│   └── Dockerfile
│
├── backend/                       # SSR 前端用的只读 REST API（Phase 5）
│   ├── main.py                    # uvicorn backend.main:app 入口
│   ├── auth.py                    # API Token 校验
│   ├── deps.py                    # FastAPI 依赖注入
│   ├── routes/
│   │   ├── conversation.py        # 对话历史查询
│   │   ├── query.py               # 念头 / 记忆 / 行动查询
│   │   └── stream.py              # 事件流（SSE / WebSocket）
│   └── Dockerfile
│
├── frontend/                      # Phase 5 前端（未开始）
│
├── tests/                         # 单元测试与集成测试
│   ├── test_phase1.py             # Phase 1：核心循环 / 解析 / 模型客户端
│   ├── test_phase2.py             # Phase 2：短 / 长期记忆 / 习气 / 身份
│   ├── test_phase3.py             # Phase 3：prompt builder / 行动 / 感知 / 前额叶
│   ├── test_backend.py            # backend API 路由测试
│   └── test_bot.py                # bot 命令与事件转发测试
├── test_support.py                # 测试共享 stub（Redis 协议模拟）
│
├── scripts/                       # 杂项脚本与迁移笔记
│
├── inspections/                   # PyCharm / IntelliJ 的 inspection 导出（用于代码质量回归）
│
└── data/                          # 运行时数据（不进版本库）
    ├── logs/                      # 引擎日志、prompt 日志
    ├── openclaw/device.json       # OpenClaw 设备身份（含私钥，注意保护）
    ├── postgresql/                # Docker Compose 挂载的 PG 数据
    └── redis/                     # Docker Compose 挂载的 Redis 数据
```
