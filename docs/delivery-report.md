# VeriRuntime M0–M7 交付记录

验收环境：2026-10-06，macOS ARM64 Tahoe，Python 3.11.15，Java 24.0.2。
这是可运行的研究原型；所有现场 verdict 来自真实验证器。
本记录中的耗时是本机测量值，不是性能保证。

## 1. Milestone history

仓库最初为空且没有 Git 历史。初始化后沿用已有用户身份，每个 milestone
单独提交。M3 开发期间收到 Workflow 架构微调，增量实现，没有 reset、rebase、
rewrite、amend 或覆盖已完成提交。

```text
b7edebb feat(m0): establish verification runtime architecture
d1a2413 feat(m1): add declarative verification DSL and IR
5b0059a feat(m2): integrate real verification backends
2f99c3d feat(m3): implement verification execution runtime
1eeea63 feat(m4): add automatic physical plan optimizer
5b111d0 feat(m5): add semantic cache and artifact store
22f08dc feat(m6): add explainability and runtime observability
HEAD    feat(m7): complete end-to-end declarative verification prototype
```

M7 的实际提交 ID 可用 `git log --oneline --reverse` 查看。
各阶段的审查与验证记录见 [milestones.md](milestones.md)。

## 2. Repository structure

```text
veriruntime/
  model.py                 固定逻辑目标、Workflow、不可变源代码快照和证据记录
  dsl/                     严格 JSON 校验、单任务兼容和 Workflow IR
  plan.py                  Run / Parallel / Sequence / CacheLookup 物理 AST
  optimizer.py             每个 goal 的可替换物理优化器和历史成本启发式
  runtime/                 AST 执行、并发、超时、取消、进程组和 RSS 监督
  service.py               ready-goal 调度、依赖检查和持久化生命周期
  tools/                   注册表、三个真实 adapter、CPAchecker 编译驱动
  store.py                 SQLite 执行、任务、历史、统计和 provenance
  artifacts.py             内容寻址的只读 artifact 对象
  cache.py                 每个 goal 的 exact semantic cache
  observability.py         结构化事件和独立人类可读渲染
  cli.py                   validate/parse/doctor/verify/explain/history/show/cache
schemas/                   指向打包 schema 的链接
examples/c/, tasks/         SAFE、UNSAFE、双确认和小 Workflow 示例
examples/benchmark/        六个 independently meaningful C 验证案例
scripts/                   固定版本安装、真实 demo 和 mini benchmark
tests/                     单元、解析 fixture、真实集成和完整执行测试
docs/                      架构、工具支持、milestone 审查和本交付记录
verifiers.lock.json        官方下载与 SHA-256 锁定
```

`.veriruntime/` 保存本机 toolchain、SQLite、日志、artifact 和实验结果，
`.venv/` 保存 Python 环境；两者均被 Git 忽略。

## 3. Architecture and responsibility boundary

```text
User / Natural Language
  -> upstream LLM Semantic Planner (接口边界，当前未实现 LLM)
  -> VerificationWorkflow: fixed LogicalGoals + explicit dependencies
  -> Logical Workflow IR
  -> per-goal Physical Optimizer
  -> Workflow Scheduler / Goal Runtime
  -> Physical Plans -> real verifiers
  -> structured results / diagnostics / artifacts / cache
  -> upstream explicit replanning
```

LLM 决定 WHAT SHOULD BE PROVED；VeriRuntime 决定 HOW A FIXED PROOF
OBLIGATION SHOULD BE EXECUTED。Runtime 不生成 subgoal，不添加 assumption、
invariant 或 lemma，不改变 property，也不根据 UNKNOWN 偷偷降低要求。

`VerificationTask` 保留，`LogicalGoal` 是兼容别名，单任务自动包成一节点
`VerificationWorkflow`。Workflow 验证 DAG，按显式 required_verdict 和确认要求
决定后继是否 ready；前置条件不满足时后继产生 BLOCKED/UNKNOWN。示例
`assertion_workflow.json` 是 G1 SAFE 后启动独立 G2 的控制依赖，没有证明组合。
当前 ready goals 顺序执行，单个 goal 内的工具可并发。

DSL 拒绝工具名和 run/parallel/sequence 等物理指令。Semantic hints 是可忽略的
偏好，当前记录但不应用，不参与 correctness assumption 或缓存身份。
结构化结果包含 goal_id、status、verdict、confirmations、artifacts、diagnostics、
optimizer_summary、failure_reasons。EXPLAIN 分 Logical Workflow / Physical Execution。

## 4. Real verifier support

| Verifier | 实际检测版本 | 支持 property | 真实验收 |
|---|---|---|---|
| CBMC | 6.11.0 (cbmc-6.11.0) | assertion_safety | SAFE / UNSAFE、多源链接、完整/不足 unwinding |
| ESBMC | 8.5.0 64-bit aarch64 macos | assertion_safety | SAFE / UNSAFE、多源链接、完整/不足 unwinding |
| CPAchecker | 4.2.2，Java 24.0.2，Princess 2025-06-25 | assertion_safety | SAFE / 经 counterexample check 的 UNSAFE、多源链接 |

本机检测路径：

```text
/Users/ponder/Codes/VeriRuntime/.veriruntime/toolchains/cbmc/cbmc/6.11.0/bin/cbmc
/Users/ponder/Codes/VeriRuntime/.veriruntime/toolchains/esbmc/release/bin/esbmc
/Users/ponder/Codes/VeriRuntime/.veriruntime/toolchains/cpachecker/CPAchecker-4.2.2-unix/bin/cpachecker
```

安装只写仓库本地，未 sudo 或升级系统软件。官方来源、CLI qualification、
参数和 CPAchecker assertion elaboration 的边界见 [verifier-support.md](verifier-support.md)。
CPAchecker 是可选第三工具；本机解决了默认 MathSAT 动态库和 SDK assert 头解析问题，
使用 Java Princess 和等价 assertion reachability elaboration。生成的头、spec、
config、预处理 argv、实际 CPAchecker argv 和输出都保存为 artifacts。

“独立”采用 distinct verifier family 的工程确认策略；它不是数学上的信任独立性保证。

## 5. Actual demo commands

在仓库根目录使用已安装的 `.venv`：

```sh
source .venv/bin/activate
./scripts/bootstrap_verifiers.sh --cpachecker
vrun doctor --json
./scripts/demo.sh
./scripts/run_mini_benchmark.sh --confirmations 3
pytest -q -ra
```

现场脚本执行了 validate、两层 explain、CACHE MISS verify、CACHE HIT analyze/verify、
SAFE、两个双确认示例、history 和 show。可直接手动复现核心流程：

```sh
vrun cache clear examples/tasks/unsafe_assert.json
vrun verify --explain examples/tasks/unsafe_assert.json
vrun verify --explain examples/tasks/unsafe_assert.json
vrun verify --explain examples/tasks/unsafe_assert_crosscheck.json
vrun verify --explain examples/tasks/assertion_workflow.json
```

Demo 使用独立 `.veriruntime/demo/` store，清理仅限明确列出的例子缓存 key，
保留历史与证据。安装脚本重复执行、最终 demo 和最终 benchmark 均成功退出。

## 6. First run: CACHE MISS

相同 `unsafe_assert.json` 的 goal execution ID：`5fecf8ab21c94e2ea816e8c681d8f109`。
DSL 不含验证器名字，要求一个确认。优化器根据 registry、历史和预算生成：

```text
Sequence
  Parallel(max_parallel=2)
    Run(CBMC, time_slice=15s)
    Run(ESBMC, time_slice=15s)
  Run(CPAchecker, time_slice=15s)
```

| 实际 attempt | Status | Verdict | Wall time |
|---|---|---|---|
| CBMC | COMPLETED | UNSAFE | 0.193727s |
| ESBMC | CANCELLED | UNKNOWN | 0.197858s |
| CPAchecker fallback | 未启动 | 无 attempt | — |

满足 min_confirmations=1 后取消尚未完成的 ESBMC；保留取消状态，未伪造第二个确认。
最终 UNSAFE，confirmations=1，goal wall time **0.204931s**。
该耗时从 goal runtime 开始测量，不包含 CLI 启动与 discovery probes。

## 7. Second run: CACHE HIT

完全相同 DSL，goal execution ID：`9cb251ada2dc4e6593e65fe0bf0c0db9`。
物理计划改为 `CacheLookup`，链接上次 source execution。
最终 UNSAFE，confirmations=1，**verifier executions=0**，wall time **0.012975s**。
这里的零执行指无 verification command；版本/help discovery probes 仍会发生。

Cache key 是源代码快照、entry、property、C semantics 和 trust requirements 的
语义身份；工具、物理策略、预算、goal 标签和 hints 不参与身份。命中还检查
原始实际 attempts、工具版本/config、distinct families、artifact 哈希和 SQLite
与不可变 JSON 的一致性。UNKNOWN/TIMEOUT/CONFLICT 不缓存；跨执行矛盾证据会失效缓存。

## 8. Cross-check and benchmark

`min_confirmations=2` 自动得到多工具物理计划，不降低确认数，也不通过 DSL 选工具：

| Goal | CBMC | ESBMC | 最终 verdict / confirmations | Goal wall time |
|---|---|---|---|---|
| unsafe-assert-crosscheck | UNSAFE, 0.191908s | UNSAFE, 0.301014s | UNSAFE / 2 | 0.308810s |
| safe-assert-crosscheck | SAFE, 0.195672s | SAFE, 0.297236s | SAFE / 2 | 0.305157s |

两例均 CACHE MISS 并真实等待两个完成；CPAchecker fallback 无需启动。
六案例三确认基准进一步执行了 **18 次真实验证**，三个 family 每例一致：
assertion、bounded loop、branch、array assertion、arithmetic 共 **4 SAFE、2 UNSAFE**，
0 UNKNOWN、0 CONFLICT、0 unexpected results。案例小，只证明端到端机制，
不支持工具性能排名或理论最优性结论。

本机完整证据（未提交二进制或运行数据）：

- `.veriruntime/demo/demo-record.json` 与 `demo-final-console.txt`
- `.veriruntime/demo/executions/<goal-execution-id>/` 的 logical/physical/result/events
- `.veriruntime/benchmark/benchmark-results.json` 和 `benchmark-results.csv`
- `.veriruntime/bootstrap-final.log`

## 9. Tests and package qualification

最终命令 `pytest -q -ra`：**82 passed，0 skipped，0 failed，19.88s**。
其中 **15 项 integration tests** 使用真实安装的工具：六个工具 SAFE/UNSAFE、
真实双确认 runtime、两个 cache miss/hit end-to-end、真实 DAG、多源链接和
两个不足 unwinding 回归。缺少真实工具的其它环境会明确 skip 对应项。

单元测试覆盖 DSL 禁用物理指令、快照/semantic key、DAG/cycle、资源预算、超时、
取消和子进程清理、真实并行、fallback、distinct family/conflict、历史成本、
缓存禁用/清理/损坏/元数据篡改/跨执行冲突、provenance、两层 EXPLAIN 和包装资源。
Fake adapter/测试子进程仅存在测试中，生产路径及 demo/benchmark 不使用 mock verdict。

`uv build --wheel` 成功；wheel 在新独立 venv 中安装后，从 `/tmp` 调用
`vrun validate` 成功验证原始单任务与两节点 Workflow，确认 schema/entry point
已随 package 分发。各 milestone 及最终 staged diff 均执行 `git diff --check`。

最终审查修复了 ESBMC 将 unwinding obligation 标成 `main.assertion.N` 的情况，
以真实 property 描述区分 UNKNOWN，增加真实长循环回归，并升级 adapter/cache
解释契约以避免旧证据误用。公共 Python API 也检查 min_confirmations 等基本约束。

## 10. Current limitations

- Exact per-goal cache；没有 workflow cache、partial/evidence cache、witness reuse。
- 没有 invariant/lemma 生成、theorem composition、LLM 实现或 HTTP server。
- Ready goals 顺序运行；无 checkpoint、增量验证、动态 CPU 分配。
- 成本模型是小型历史启发式，可能受 early cancellation 和选择偏差影响。
- RSS 是采样的进程树监督，未提供严格容器/cgroup 内存隔离；逃离进程组的子进程不在保证范围。
- 安装脚本只完成 macOS ARM64 Tahoe qualification；Linux 手动工具发现可用，Windows 未实现。
- BMC 固定 unwind=64 并检查完整性；不足时返回 UNKNOWN，不声称 unbounded proof。
- 当前只支持 assertion_safety；memory_safety schema/IR 可表达但无兼容 adapter。
- CPAchecker 的当前 Princess 配置拒绝浮点。源依赖捕获保守拒绝无法忠实重放的输入。
- SAFE 信任验证器的 C 语义与实现，假设 well-defined C；未独立检查 proof certificate。

## 11. Research extension points

A. **Cost-based verification planning**：在 Optimizer 接口中替换启发式，建模预算、
trust feasibility、工具成功概率和执行成本。

B. **Runtime-adaptive scheduling**：利用事件与 attempts 调整尚未开始的执行策略、
time slices 和并发，保持同一逻辑目标。

C. **Evidence-aware optimization**：明确 evidence 的可用性与 trust 组合规则，
扩展当前 exact cache，不能把 UNKNOWN 当成确认。

D. **Witness / invariant reuse**：对工具产物建立验证过的重用契约；若改变假设或
证明命题，必须通过上层显式 Workflow 重新提交。

E. **Incremental verification**：建立源码变化与逻辑义务映射、证据失效和增量重证接口。

F. **Learned verifier selection**：在版本/config scoped 历史上训练选择模型，
处理取消/选择偏差，评估 held-out workloads，保留可解释计划与回退。

当前原型验证了 DSL 不变而 physical plan 可从真实 portfolio 变为零执行缓存的机制；
不声称这些研究方向的理论 novelty 或性能最优性。
