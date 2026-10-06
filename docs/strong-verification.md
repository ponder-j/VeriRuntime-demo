# M10 — 新验证器实验与 Frama-C / Rocq 集成设计

基础 Docker 复现节点为 `7cb58ed`。本节点增加 **Ultimate Automizer**、**Frama-C Eva** 的可调度 C adapter，并实测 **Frama-C WP + Z3 / CVC5**。另有可执行的 **Rocq 9.1.1 proof_check DSL**，检查上层提交的证明代码。

当前已实现的是新 C 家族实验和独立 Rocq 证明工件检查。**完整的 C → Frama-C VC → Rocq → C 契约结论闭包仍是下面的设计方案**；不会把独立数学命题证明通过或 SMT 求解成功包装成已经完成该闭包。

打开 [强证明调度设计页面](strong-verification.html) 查看义务生成、等待上层补证、Rocq 检查及覆盖闭包的关系；[现有 DSL 调度回放](runtime-explorer.html) 展示当前可执行路径。真实实验记录见 [verifier-lab.json](verifier-lab.json)，包含实际计划、命令、镜像身份、原始报告和证明工件摘要。

## 复现实验

```powershell
# 已有基础镜像时，构建三个额外的可选镜像并运行实验
./scripts/verifier-lab.ps1 -BuildDns 1.1.1.1
# 本机已构建，可直接复验
./scripts/verifier-lab.ps1 -SkipBuild

# 新 C 工具显式启用；默认环境保持三个原工具
docker compose run --rm -e VRUN_EXPERIMENTAL_TOOLS=ultimate,framac runtime doctor
docker compose run --rm -e VRUN_EXPERIMENTAL_TOOLS=ultimate,framac runtime verify examples/tasks/safe_assert_crosscheck.json --data-dir /data/extended --explain

# 独立检查上层已经写好的 .v
docker compose run --rm runtime check-proof examples/proofs/check.json --json
```

归档来源、版本与 SHA-256 记录在 `docker/experiments.lock.json`。新工具沿用 M9 的独立容器、卷 subpath、禁网、只读根文件系统及硬内存限额。Frama-C 官方包中的 Electron GUI 已从最终镜像移除；Rocq 使用多阶段 release build，最终镜像不安装 OCaml 编译工具链或 GUI。它带 Corelib，并未安装完整 Stdlib / Why3-Rocq 支撑库。Rocq 编译器实际为 9.1.1 + OCaml 5.3.0；上游仍把 OCaml 5.x 支持标为实验性，当前仅记录本机已通过的检查，不承诺所有第三方库兼容。[Rocq 构建说明](https://github.com/rocq-prover/rocq/blob/V9.1.1/INSTALL.md)

## 已完成的真实实验

六个原基准以调用者明确给出的 `min_confirmations=1` 分别测试单家族；运行时没有降低原请求的确认数。实验脚本记录新请求、实际计划、尝试、原始输出和工件；不能把单家族矩阵解读为满足原来的双家族请求。

| 实验 | 结果与含义 |
|---|---|
| Ultimate 0.3.1 / 35a84365 | 六案例 4 SAFE、2 UNSAFE；产生真实反例 / witness。尚未用独立 witness checker 复查，因此归为 verifier_report |
| Frama-C 33.0 Eva | 六案例 4 SAFE、2 UNKNOWN；可能警报不当作具体反例；只接受完整分析、零警报且所报告断言调用已验证的结果 |
| 100 次循环 | CBMC / ESBMC 的固定展开 64 返回 UNKNOWN；Ultimate 约 35 秒给出 SAFE；Eva 约 1.2 秒给出 SAFE |
| WP + Z3 4.13.3 | `square` 的四个 JSON 报告义务通过，包括返回值非负和有符号溢出保护；三个非 Qed 义务实际调用 Z3 |
| WP + CVC5 1.1.2 | 同一固定契约及 RTE 义务，通过另一求解器的独立运行验证。仍共享 WP 编码，不能计成两个 C 验证家族 |
| Rocq 正确代码 | 先编译上层模块，再做指定命题的类型绑定、假设审计和独立 `rocq check`；VERIFIED，`code_verdict=null` |
| Rocq 证明别的命题 | `.v` 自身可以编译，但绑定失败；REJECTED / proof_type_mismatch |
| Rocq Admitted | REJECTED / untrusted_axioms_or_extensions |

WP 的运行报告仍保留了 “Skipped RTE guards” 警告，当前标量例子没有指针相关操作。实验只声明所记录义务通过，不宣称该选项覆盖任意 C 程序的所有运行时错误。未证完、假设不满足或不支持的义务必须留在未完成清单中。WP 是针对 C 契约生成证明义务的平台，内存与算术模型属于其证明语境。[Frama-C WP 手册](https://www.frama-c.com/download/frama-c-wp-manual.pdf)

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

## 验收与可视化回执

完整 Linux 测试为 119 passed，包含真实 Rocq 类型绑定与拒绝检查。实验脚本完成 16 次 C 家族验证、3 次 Rocq 检查、2 次 WP 求解器运行，结束时没有遗留验证容器。证明进程中断或 JSON 报告截断返回 UNKNOWN，不能接受单独留下的 VERIFIED 字段。

```powershell
docker compose run --rm --entrypoint python runtime -m pytest -q --basetemp=/data/tests-m10-final -o cache_dir=/tmp/pytest-cache
```

测试临时文件必须位于命名数据卷的 `/data` 下，这样独立验证容器才能通过卷 subpath 访问当前尝试。不要用额外挂载替换 compose 的数据卷；缓存文件则放在控制容器的 `/tmp`。

`strong-verification.html` 为 workflow 类型，最终交付校验为 showcase 9/9，零错误、零警告。规范为 `strong-verification.workflow.json`，3589 字节，SHA-256 `2e0839900a5cc1cac1611315fca594a0b3add48f6ceac1cc4df1fc8abdea2aef`；HTML 为 632465 字节，SHA-256 `04034fe71a3402fcdc472554b3ceea458987e6dd61bd7904697a047fca2b779a`。规范和 HTML 在校验后保持原始字节。

Archify 内置 visual-check 在本机 Windows 启动 Chromium 时失败（`spawn UNKNOWN`），该命令不算通过。使用已安装的 Playwright Chromium 对同一交付 HTML 测量 1440×900、1600×1000、1920×1080、2048×1320，四种尺寸均无横向或纵向溢出；已人工检查最小 / 最大尺寸与深色截图，文字、连线和层次清晰。截图与测量位于忽略的 `.veriruntime/strong-*` 文件中。现有回放页面另验五个案例、八个步骤、深浅主题及窄屏宽度。
