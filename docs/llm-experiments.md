# M8: Codex Semantic Planner 与真实实验

在 M0–M7 之上增量增加上层 planner；既有提交、单任务 DSL、工具 adapter、物理优化器、
Runtime 与缓存接口保持工作。LLM 不进入 Runtime 内部，也不能通过 Workflow 指定工具。

## 使用

本机已登录 Codex CLI 0.157.1，配置默认模型为 `gpt-6.1-sol`。读取现有 sol 配置，
未配置 sol 时回退到这个模型；可显式 `--model`。沿用 CLI 认证及 provider 配置，
不读取认证文件、不记录凭据，也不修改用户配置。

```sh
source .venv/bin/activate
vrun plan examples/tasks/assertion_workflow.json \
  --request '检查两个已有断言目标；仅当第一个获得 SAFE 后才执行第二个。'
vrun experiment examples/tasks/assertion_workflow.json \
  --request '检查两个已有断言目标；仅当第一个获得 SAFE 后才执行第二个。' \
  --max-rounds 2 --json
./scripts/run_llm_experiment.sh
```

`plan` 只调用模型并导出可重载的 `workflow.json`。`experiment` 自动调用模型、校验
Workflow、调用 VerificationService，再将未解决结果交给下一轮模型。成功 SAFE 和
UNSAFE 都是有效回答；UNKNOWN 保持 UNKNOWN。默认最多 2 轮，允许 1–8 轮，单次模型
默认超时 180 秒。SIGINT/SIGTERM 使用取消 token 并清理进程组。

当前输入上限是 8 个已有目标、128 KiB 源码和 32 KiB 自然语言要求。源快照与输入集
冻结；目标不能遗漏或复制，entry/property/C semantics 不变，确认数不能降低，预算
不能超过调用者上限。LLM 可创建命名与明确的控制依赖；新 assumption、invariant、
lemma、源码 instrumentation 或任意自然语言规约转换尚未实现。

## Process contract 与 provenance

采用官方 [非交互接口](https://learn.chatgpt.com/docs/non-interactive-mode)：
`codex exec --json --output-schema ... --output-last-message ... -`，prompt 走 stdin。
逐次覆盖使用 read-only sandbox、ephemeral 会话，关闭 shell、subagent、app/plugin、
web search 和用户配置中的 MCP，仅影响该子进程。相关设置见官方
[配置说明](https://learn.chatgpt.com/docs/config-file/config-basic) 和
[配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)。

调用以 argv 创建，无 shell 插值；模型进程超时、取消、启动错误、非零退出、非法
JSON/schema、重复 JSON key、非预期工具动作等分别报告。传输 schema 是严格对象；
最终 Workflow 还通过完整 DSL/schema、来源、快照、预算、确认数和 DAG 校验。
失败 proposal 不进入 verifier Runtime。模型的文本解释不构成 proof evidence。

每次实验在 `<data-dir>/experiments/<id>/` 保存：

- `request.txt`、`seed.json`、`inputs.json` 和捕获的源码。
- 每轮 `prompt.txt`、`response.schema.json`、`command.json`、`codex.events.jsonl`、
  `codex.stderr.txt`、`codex-run.json`、`answer.json`。
- 通过校验的 `workflow.json`、包含 rationale/limitations 的 proposal、实际执行、
  `feedback.json` 和 `round.json`。
- 最终 `experiment.json`：状态、停止原因、轮数、诊断与真实 verifier execution 数。

源码使用稳定 input namespace；导出的 Workflow DSL 与实际执行使用同一个 snapshot。
命名空间可能与原 seed 的逻辑路径不同，因此不承诺命中 seed 的既有缓存；相同输入
顺序的重复实验可以复用 per-goal exact cache。Codex 日志与验证器 artifacts 分开，
不把 LLM 解释、假定结果或 token usage 纳入 definitive cache。

## 2026-10-06 真实验收

`./scripts/run_llm_experiment.sh` 自动创建新的 store，保存旧实验，顺序启动 **4 次真实
gpt-6.1-sol Codex 规划进程**。模型规划耗时分别为 26.634、25.041、27.182、27.100 秒。
这些是本机测量值；模型规划耗时与 verifier wall time 分别记录。

| 实验 | 实际结果 |
|---|---|
| 第一轮规划并执行 | 模型生成 G1 SAFE 后才执行 G2 的 DAG；4 次真实 verifier execution |
| G1 | CBMC + ESBMC 均 SAFE，confirmations=2，goal wall time 0.307 秒 |
| G2 | CBMC + ESBMC 均 UNSAFE，confirmations=2，goal wall time 0.292 秒 |
| 相同输入/要求再次规划 | 两个 goal 均 CACHE HIT，0 次 verifier execution；SAFE / UNSAFE 不变 |
| memory_safety 不支持案例 | UNKNOWN，0 confirmations，unsupported_property + no_compatible_tool；0 次 verifier execution |
| UNKNOWN 第二轮反馈 | 模型明确保留 memory_safety、不替换为 assertion_safety；无允许范围内的有效修订，INCOMPLETE/unchanged_workflow 停止 |

首次 experiment ID：`56237a737d8842f49d6cf3df39448787`。
缓存重复 experiment ID：`544ab1c09adb41b793dd5a0aaefeb3d5`。
UNKNOWN experiment ID：`7f27dab00f46475cb81c4f77ebf39cac`。

本机完整结果：
`.veriruntime/llm-demo/0be7d0d492a041e6ad92310b547b912a/llm-experiment-results.json`；
`.veriruntime/llm-demo/latest.json` 指向最近一次成功实验。
源码、二进制、运行数据与账号配置不会作为实验日志提交到 Git。

初始 schema 与 CLI MCP dotted override 的兼容问题由真实调用暴露，修复后完成上述
完整验收；失败记录也保留在对应实验目录，没有用 mock 补齐结果。

## 自动测试与范围

新增测试检查默认 sol、严格传输 schema、plan-only、非法/弱化目标拒绝、快照篡改、
真实进程 stdin/output 协议、token usage、超时含阻塞 stdin、取消、非法 JSON 与缺少
Codex。上层反馈测试显式修改控制依赖，并由既有 Runtime 遵守；无进展测试确保不
重复执行相同 UNKNOWN goal。测试替身只在 tests 中，现场脚本使用真实模型和工具。

最终 `pytest -q -ra`：**105 passed，0 skipped，0 failed，22.92 秒**，含新增 23 项
上层 planner/进程协议测试以及既有 15 项真实 verifier integration tests。真实 LLM
验收由独立脚本执行，普通 pytest 不自动消耗模型账号额度。

wheel 构建并独立安装后，从 `/tmp` 使用安装包的 `vrun plan` 再次真实调用默认 sol，
生成 PLANNED Workflow，verifier executions=0；最终源码补充 CRLF 字节保真回归。

当前 bridge 限定为已有 formal goals 的规划与编排，不宣称实现通用自然语言验证、
任意 subgoal 分解或 invariant synthesis。`COMPLETED` 只表示提交的 goals 满足确认
要求，不代表自然语言需求的形式化完整性已自动证明。Codex 使用现有账号配额；轮数
和墙钟上限不是 token/费用上限。运行环境的用户/系统 instruction 和受管 policy
仍可能影响模型，需要保留实际 prompt、输出和进程日志进行实验评估。
