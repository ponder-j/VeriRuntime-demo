# 高级验证

[回到主线使用手册](../README.md) · 本页集中保留扩展实验、上层证明和未来集成设计。

<a id="lab"></a>

## 新验证器实验

基础断言路径不要求这些镜像。需要实验时才构建 Ultimate、Frama-C、Rocq；已有镜像可直接复验：

```powershell
pwsh -NoProfile -File scripts/verifier-lab.ps1 -BuildDns 1.1.1.1
pwsh -NoProfile -File scripts/verifier-lab.ps1 -SkipBuild
docker compose run --rm -e VRUN_EXPERIMENTAL_TOOLS=ultimate,framac runtime doctor
docker compose run --rm -e VRUN_EXPERIMENTAL_TOOLS=ultimate,framac runtime verify examples/tasks/safe_assert_crosscheck.json --data-dir /data/extended --explain
docker compose run --rm runtime check-proof examples/proofs/check.json --json
```

扩展工具沿用独立尝试容器与环境锁。Frama-C 官方包的 Electron GUI 已移除；Rocq 是多阶段 release build，运行镜像不装 OCaml 编译工具链或 GUI，仅带 Corelib。OCaml 5.x 上游仍标为实验性，不承诺所有第三方库兼容。[Rocq 构建说明](https://github.com/rocq-prover/rocq/blob/V9.1.1/INSTALL.md) 版本 / 镜像规模见 [运维工具表](operations.md#tools)。

六个原基准以调用者显式请求的 `min_confirmations=1` 分别测试一个家族，运行时没有降低原双家族请求。单家族实验矩阵不能被解释为满足双确认要求。

| 实验 | 已完成的真实结果 |
|---|---|
| Ultimate 六案例 | 4 SAFE / 2 UNSAFE；有反例 / witness，尚未独立检查，归为 verifier_report |
| Eva 六案例 | 4 SAFE / 2 UNKNOWN；仅完整、零警报且报告断言已证明时 SAFE；抽象警报不当作具体反例 |
| 100 次循环 | CBMC / ESBMC 固定展开 64 返回 UNKNOWN；Ultimate 约 34 秒、Eva 约 1.5 秒 SAFE，耗时是本机测量 |
| WP + Z3 / CVC5 | 两个独立运行各 4/4 报告义务通过，含返回值非负、有符号溢出与 assigns；非 Qed 义务实际调用求解器 |
| Rocq 正确代码 | 编译、类型绑定、假设审计、内核复查后 VERIFIED，code_verdict=null |
| Rocq 错配命题 / Admitted | 分别 REJECTED / proof_type_mismatch、REJECTED / untrusted_axioms_or_extensions |

WP 两次运行共享编码来源，不能计为两个 C 验证家族。报告保留 Skipped RTE guards 警告；当前 square 标量例子没有指针操作，只声明记录的义务通过，不宣称覆盖任意程序的所有运行时错误。WP 模型、算术与内存语义属于证明语境。[Frama-C WP 手册](https://www.frama-c.com/download/frama-c-wp-manual.pdf)

实际计划、尝试、原始报告和工件摘要在 [verifier-lab.json](verifier-lab.json)。[强证明流程图](strong-verification.html) 展示下面的设计，**完整 C → Frama-C VC → Rocq → C 契约覆盖链尚未实现**；独立数学证明通过或 SMT 成功不代表闭包已完成。

<a id="rocq-proof"></a>

## 可执行的上层证明 DSL

当前 `version=0.2` 的 `proof_check` 接收**已有**命题源文件和**已有**证明源文件。声明源文件、导出符号、证据要求及预算，不传工具命令、脚本执行策略或 `coqc` 参数：

```json
{
  "version": "0.2",
  "goal": {
    "id": "identity-proof",
    "kind": "proof_check",
    "namespace": "VRGoal",
    "statement": {"file":"Expected.v", "symbol":"VRGoal.Expected.obligation"},
    "proof": {"language":"Rocq", "files":["Correct.v"], "symbol":"VRGoal.Correct.discharge"}
  },
  "requirements": {"evidence":"kernel_checked", "axioms":[]},
  "budget": {"wall_time_sec":30, "memory_mb":512, "max_parallel":1}
}
```

上层人 / LLM 编写 `Correct.v`；输入快照封存其内容。下层通过 `rocq dep` 派生模块编译次序，编译提供的代码，再生成一个**类型绑定**：

```coq
Definition required_statement : Prop := VRGoal.Expected.obligation.
Definition bound_proof : required_statement := VRGoal.Correct.discharge.
```

绑定不是新的证明。错误命题的证明无法赋给它。之后单独加载已编译模块，审计假设闭包，再由 `rocq check` 重新检查 Binding 及依赖库。当前原型采取严格闭合策略，拒绝用户 Axiom / Parameter / Admitted、外部 ML 扩展和关闭关键类型检查的代码；不加载 `.coqrc`，不复用输入 `.vo`。假设审计和编译库复查是不同检查，保留各自日志。[Rocq 命令说明](https://rocq-prover.org/doc/V9.1.1/refman/practical-tools/coq-commands.html)、[假设检查说明](https://rocq-prover.org/doc/V9.1.1/refman/proof-engine/vernacular-commands.html)

检查结果位于 `/data/proofs/<execution_id>/`，有源快照、精确镜像身份、命题与证明符号、每条命令、编译对象、日志和 SHA-256 记录。此接口暂不加入 C 家族的确认计数，也不进入 C 结果缓存；`VERIFIED` 仅表示提交证明建立了提交命题。

<a id="framac-rocq-design"></a>

## Frama-C + Rocq 应如何组合

以下是依据实验提出的架构，不能直接把两个现成 adapter 串联后计两票。上层请求的是某个 **C 源快照在指定语义下满足某个契约**；Rocq 检查的是这个目标派生的具体逻辑义务。需要显式的工件依赖和覆盖闭包。

推荐在原有每目标 portfolio 旁增加 `ArtifactGoal` / `ObligationBundle` 类型，物理计划增加 `GenerateObligations`、`DischargeAll`、`CheckProofArtifact`、`LinkCoverage`。这些是下层生成的 AST 算子，不写入上层 DSL。原有 DAG 的 SAFE 前置边只表达控制关系，不能自动担当逻辑定理依赖。

1. **冻结目标。** 校验 C、ACSL、入口、整数 / 内存语义、证据要求；上层提供前置条件、后置条件、loop invariant 等内容。下层不增加假设、不修改契约。对于指定函数契约，结论必须显式保留其前置条件；证明某个函数在 P 下正确，不等于证明全部调用者满足 P。
2. **生成并封存全部义务。** Frama-C / WP 生成 VC；清单同时记录源 / 契约 digest、生成器镜像、模型、变换链、依赖理论及每个 VC 的原始命题 digest。契约、RTE、frame、invariant 初始化 / 保持、按需 termination 都必须纳入请求覆盖。不能只取“剩余困难义务”而丢掉 Qed / CFG 简化过的义务或跳过不支持的 guards。
3. **依证据要求选择物理执行。** solver_report 模式可用自动求解器组合；kernel_checked 模式下，普通 unsat / valid 报告不足以结案。求解器可用于先行筛选、生成候选证书或向上层展示难点，最终仍要有可核验的证明工件。共享 WP 编码的 Z3 / CVC5 结果标记同一来源链。
4. **等待上层补证。** 没有满足要求的证明时返回 `NEEDS_INPUT`，附完整 bundle、具体未完成 VC、Rocq / Why3 模块骨架、环境锁和 diagnostics。人 / LLM 编写 `.v` 与必要辅助引理，作为**下一次显式请求**的 proof_assets 提交；运行时不调用 LLM 写证明，不替换未完成证明为 Axiom。
5. **严格绑定后检查。** proof_asset 要引用已封存 bundle 和目标 VC 的 digest。声明变更、义务过期、导出符号错配、未覆盖新增 VC 均拒绝。生成的 Binding 引用运行时保存的权威命题，而不是相信上层自行重抄的“同名定理”。再做编译、精确类型绑定、假设闭包、内核复查。
6. **闭包与反馈。** 只在每个请求覆盖的义务都有足够证据、所有依赖已验证且没有 unsupported / stale 项时返回 `CONTRACT_VALID`。SMT 未证明、错误 invariant 或不完整证明返回 UNKNOWN / NEEDS_INPUT；要给出 C 的 UNSAFE，还需要绑定并检查真实 C 反例。保存义务与最终目标之间的来源链接。

下层调度可以并行处理不同 VC、按历史选择求解器、优先复查已有 `.vo`、对超时任务回退；同一模块的依赖编译按 DAG 排序。等待人工 / LLM 期间释放容器，不占用验证并发槽。恢复时创建新执行记录，通过 bundle digest 链接原始义务。

## 建议的契约 DSL

下面是 `0.2-proposal`，**现有 `vrun verify` 不接受它**。完整请求保存在 `examples/experiments/strong-contract.proposed.json`：

```json
{
  "version":"0.2-proposal",
  "goal": {
    "id":"square-contract", "kind":"c_contract",
    "program":{"language":"C","entry":"square","sources":["square.c"]},
    "contract":{"format":"ACSL","sources":["square.c"],"scope":"function_contract"},
    "semantics":{"c_standard":"c11","data_model":"LP64","integer_arithmetic":"bounded_c","memory":"iso_c_objects"}
  },
  "requirements":{"evidence":"kernel_checked","coverage":["contract","runtime_errors"],"user_axioms":[]},
  "proof_assets":[],
  "budget":{"wall_time_sec":120,"memory_mb":2048,"max_parallel":2}
}
```

首轮 proof_assets 为空，下层导出义务并报告 NEEDS_INPUT。上层第二轮保留相同 goal，添加绑定已有 bundle 的证明附件，例如：

```json
{
  "format":"Rocq",
  "files":["SquareProof.v"],
  "bundle":{"artifact_id":"<内容寻址的 bundle>"},
  "bindings":[{
    "obligation_id":"typed_square_ensures",
    "statement_sha256":"<该权威命题的 digest>",
    "proof_symbol":"SquareProof.square_nonnegative"
  }]
}
```

`format=Rocq` 表示附件语言；不会让上层控制物理工具顺序。`requirements.evidence` 和 `coverage` 表达证明强度，取代用“多投几票”模拟更强证据。只有一个义务被证明时，不能得出整个 bundle 完成。

## 落地路线与可信边界

近期可采用 **WP → Why3 → Rocq 命题模块 → 上层 `.v` → 类型 / 内核检查**。需要补齐版本匹配的 Why3-Rocq 支撑库、真实命题导出、bundle seal 和覆盖 linker。当前 Rocq 镜像只有 Corelib，原型的闭合假设策略也不能直接用于所有 WP 生成理论；后者可能有显式模型参数 / 公理，必须通过受信理论清单及 digest 管理，不能笼统允许用户 Axiom。

证书路线可以另行探索 SMTCoq 等受支持的求解器证书检查；在完成证书格式、理论覆盖和版本资格验证前，普通 SMT 报告保持 solver_report 标签。多阶段计划的证据类型必须可组合：生成器证明的是“C 到该 VC 的关系”，Rocq 证明的是“该 VC 在这些依赖下成立”，coverage linker 证明的是“目标需要的 VC 全部已覆盖”。

Rocq 内核能减少对战术和搜索代码的信任，但 C 前端、WP 生成器、语义模型与允许的理论仍属于可信链。[Rocq 内核与战术边界](https://rocq-prover.org/doc/V9.1.1/refman/language/core/index.html) 因此本项目应报告具体 assurance profile 和依赖闭包，而不是使用单一“强工具成功”布尔值。

缓存应分别作用于 C goal、生成的 VC bundle、证明工件和最终覆盖结果。键至少包含源码 / 契约 / 语义、目标命题、完整覆盖集合、生成器与模型、Rocq / 依赖库身份、已核验的证明 digest 和信任策略。更改内存模型、加入假设、漏掉新义务或换用 solver_report 都不能命中 kernel_checked 结果；过期绑定必须重新生成反馈。


<a id="llm-planner"></a>

## 可选上层 LLM planner

原生环境需要已登录的 Codex CLI；轻量 Docker 镜像未安装它，也不复制账号认证。当前 bridge 只编排已有形式化目标，不生成任意源码 / 假设 / 不变式。



M8 资格验证使用 Codex CLI 0.157.1、`gpt-6.1-sol`。实现读取现有 sol 配置，
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

### Process contract 与 provenance

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


### 历史验收与边界

M8 在原生环境、Codex CLI 0.157.1 完成四次真实 gpt-6.1-sol 规划：首次生成 G1 SAFE 后执行 G2 的 DAG，CBMC / ESBMC 给出两家族 SAFE / UNSAFE，共四次验证命令；重复规划两个目标均缓存命中，零验证命令；memory_safety 保持 UNKNOWN，第二轮没有允许范围内的新进展，以 unchanged_workflow 停止。普通 pytest 的模型进程替身仅用于协议测试，不会自动消耗模型配额。

原始历史记录可用 `git show 4a86a7e:docs/llm-experiments.md` 取回，现场数据位于原环境的 `.veriruntime/llm-demo/`。原实验 ID 分别为 `56237a737d8842f49d6cf3df39448787`、`544ab1c09adb41b793dd5a0aaefeb3d5`、`7f27dab00f46475cb81c4f77ebf39cac`；不要求当前 Windows 环境具有这些旧目录。

当前桥接没有通用自然语言验证、任意 subgoal 分解、invariant synthesis 或源码编辑。COMPLETED 表示提交目标得到满足确认要求的回答，可以包含 UNSAFE；不是形式化完整性已被自动证明。模型解释、token usage 与 planner 日志不会进入证明缓存；轮数和 wall timeout 也不是 token / 费用上限。需要使用已有账号的模型配额，并保存实际 prompt / proposal / feedback 供评估。

完整框架与当前实验验收、可视化 SHA-256 回执统一见 [部署与运维](operations.md#validation)，不再为每个阶段另立手册。
