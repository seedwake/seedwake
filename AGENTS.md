## 编码准则

如无必要，勿增实体。英文注释，中文回复，言简意赅。

## 八荣八耻

1. 以暗猜接口为耻，以认真查阅为荣
2. 以模糊执行为耻，以寻求确认为荣
3. 以盲想业务为耻，以人类确认为荣
4. 以创造接口为耻，以复用现有为荣
5. 以跳过验证为耻，以主动测试为荣
6. 以破坏架构为耻，以遵循规范为荣
7. 以假装理解为耻，以诚实无知为荣
8. 以盲目修改为耻，以谨慎重构为荣


## 代码规范

- 函数排布遵循浏览顺序：主调用函数在前，被调用函数定义在后。
- 参数、函数使用类型标注，可以直接使用内置容器类型，如无必要不引用 typing 模块。

## 工作检查清单

- 环境相关验证优先使用 `uv run`；不要用裸 `python` 推断项目实际解释器与依赖环境。
- 不要让两个独立句柄写同一个日志文件；统一走 logging，额外纯文本日志必须写到不同文件。
- 不要偷懒使用宽泛类型，如 `dict[str, object]`、`dict[str, Any]`、`dict[Any, Any]`；应收紧到明确结构。
- 对 inspection / Sonar / 联调报错，必须按行号回到对应文件核对，不要凭印象判断“应该已经修了”。
- `except Exception` 只允许出现在真正的边界层，并且必须记录日志；普通业务逻辑优先捕获明确异常。
- 能抽成公开纯函数的就抽到 helper；不该公开的内部函数保持私有，测试侧局部 suppress，不要为过 inspection 乱改 API 边界。
- 发现真实联调 bug 后，必须补对应回归测试，避免“线上修了，测试没锁住”。
- 对 spell checking，专业术语优先加入 `dictionary.dic`，不要为消告警而硬改正确术语。
- 对 inspection 里的内嵌 Lua / JSON / YAML 片段，先区分是 Python 代码还是 DSL / 字典项；正确术语加词典，别误改协议或脚本语义。
- 遇到重复代码、过长参数列表、过高复杂度时，优先抽 helper / dataclass / callbacks，避免继续堆条件分支。
- 任何涉及阶段边界的改动，先核对 `SPECS.md` 与 `BACKGROUND.md`；不要擅自把后续阶段内容提前落到当前阶段。

## The Zen of Python

- Beautiful is better than ugly.
- Explicit is better than implicit.
- Simple is better than complex.
- Complex is better than complicated.
- Flat is better than nested.
- Sparse is better than dense.
- Readability counts.
- Special cases aren't special enough to break the rules.
- Although practicality beats purity.
- Errors should never pass silently.
- Unless explicitly silenced.
- In the face of ambiguity, refuse the temptation to guess.
- There should be one-- and preferably only one --obvious way to do it.
- Although that way may not be obvious at first unless you're Dutch.
- Now is better than never.
- Although never is often better than *right* now.
- If the implementation is hard to explain, it's a bad idea.
- If the implementation is easy to explain, it may be a good idea.
- Namespaces are one honking great idea -- let's do more of those!
