# 架构细节

[回到主线使用手册](../README.md) · 本页供理解实现和扩展接口时查阅，使用步骤以主线为准。

<a id="layers"></a>

## 三层职责与代码入口

| 层 | 权限与职责 | 代码 |
|---|---|---|
| 人 / 上层 LLM | 规约、固定目标、假设、不变式、引理及显式语义重规划 | 输入 DSL；可选 `planner/` |
| VeriRuntime | 能力筛选、工具选择、执行顺序、并行 / 回退、预算、取消、缓存、证据归并 | `optimizer.py`、`runtime/`、`service.py` |
| Infrastructure backend | 运行已经选好的命令；执行身份、指标、生命周期与清理 | `execution/`；当前 LocalExecutionBackend |

凡是改变命题的动作都属于上层。Runtime 不生成子目标、不添加假设、不修改源码，也不在 UNKNOWN 后调用 LLM 写不变式。未来 Kubernetes 可以决定某个已选 execution 放在哪个 node，不能决定选哪个验证器或缓存是否有效。

| 文件 / 目录 | 入口和职责 |
|---|---|
| [`model.py`](../veriruntime/model.py) | LogicalGoal / VerificationTask、Workflow、快照、要求及结果记录 |
| [`dsl/`](../veriruntime/dsl/) | 严格 JSON Schema、单任务 / 工作流加载 |
| [`plan.py`](../veriruntime/plan.py)、[`optimizer.py`](../veriruntime/optimizer.py) | 物理 AST、能力筛选与历史成本启发式 |
| [`service.py`](../veriruntime/service.py)、[`runtime/engine.py`](../veriruntime/runtime/engine.py) | 工作流门控、目标调度与 reconcile |
| [`runtime/process.py`](../veriruntime/runtime/process.py) | attempt 编排、资源策略、backend 调用与记录 |
| [`execution/`](../veriruntime/execution/) | 后端契约与 POSIX 本地执行实现 |
| [`tools/`](../veriruntime/tools/) | 探测、能力声明、命令生成、结果解析及工具工件 |
| [`store.py`](../veriruntime/store.py)、[`artifacts.py`](../veriruntime/artifacts.py)、[`cache.py`](../veriruntime/cache.py) | SQLite 来源、内容寻址工件、精确缓存 |
| [`planner/`](../veriruntime/planner/)、[`proofs/`](../veriruntime/proofs/) | 可选上层规划、独立 Rocq 工件检查 |

<a id="dsl"></a>

## 逻辑目标与不可变输入

`VerificationTask` 也称 `LogicalGoal`；`VerificationWorkflow` 包含固定目标、显式无环控制依赖和 metadata。单任务 JSON 是一个单目标工作流的简写。逻辑 IR 中不出现 Run / Parallel 等物理算子。

Schema 拒绝工具名称、执行策略、未知字段和重复 JSON key。源文件路径相对 DSL 文件解析；捕获所有 translation units 及递归的字面量本地头文件。无法忠实重放的 macro / absolute / symlink includes、依赖宿主环境的预定义宏等输入会被拒绝。系统头文件属于已记录的工具链环境。

`semantic_key` 包含源码逻辑路径与内容 hash、translation-unit 列表、语言、入口、属性、C 语义和确认要求。任务名称、预算、工具选择和 advisory hints 不改变命题身份。当前 hints 被记录但不参与策略或正确性判断。

工作流边要求前置目标返回指定 SAFE / UNSAFE 且满足确认要求，才允许后续目标执行。失败时后续目标 BLOCKED / UNKNOWN。就绪目标当前串行执行，目标内的验证器可以并行。G1 SAFE + G2 SAFE 不自动证明 G3；控制依赖不会传播假设。

<a id="planning"></a>

## 物理计划与真实执行顺序

`optimize(LogicalPlan, RuntimeContext) → OptimizationResult` 的接口可替换。当前按工具可用性、语言、属性、C 标准 / 数据模型、程序形状和估计内存筛选候选；用平滑确定率 / 历史中位耗时排序，没有历史时使用先验。统计按工具版本和配置身份隔离，受到选择与取消偏差影响，不是理论最优策略。

| 物理算子 | 含义 |
|---|---|
| Run | 一个已选工具及时间片 |
| Parallel | 同阶段并发；嵌套计划也受目标级全局 semaphore 约束 |
| Sequence | 未达到要求时继续下一阶段，保留回退时间 |
| CacheLookup | 有效精确缓存；同时保存无效时执行的 fallback |

每个固定目标依次经过：

1. 工作流前置结果及确认数门控。
2. 筛选、评分、装箱，生成候选物理计划。
3. **候选计划生成后**核验缓存；有效命中替换为 CacheLookup。
4. MISS 时解释 AST，约束并发与共享 deadline；adapter 生成已固定输入的 argv / workspace。
5. ExecutionBackend 负责 start / wait / cancel / metrics / cleanup。已有 Docker transport 的资源预备也必须在取消后完成，再清理，避免迟到的 create 留下容器。
6. adapter 解释原始输出；reconcile 归并完成证据；保存来源、工件和反馈。

达到确认要求后可以取消未完成 peer；每一批完成项先归并再取消。要求一个确认时，尚未完成的 peer 被取消，无法判断它本来是否会产生冲突。提高确认要求会使更多独立家族实际完成。

<a id="results"></a>

## 结果、状态与信任边界

`Verdict` 为 SAFE / UNSAFE / UNKNOWN / CONFLICT。`ExecutionStatus` 为 COMPLETED / TIMEOUT / CANCELLED / ERROR / OOM / START_FAILED；状态与命题结论分开，COMPLETED / UNKNOWN 是有效组合。

只有 COMPLETED 的确定 SAFE / UNSAFE 贡献证据，同一家族只计一次。相反确定证据优先返回 CONFLICT，没有多数投票。同一精确目标的历史中出现相反确定证据也会使旧缓存失效；清缓存不会删除历史。

C 断言验证针对声明数据模型下良定义的执行，信任支持的验证器与工具链。家族不同不等于数学上独立的信任根，例如 ESBMC 起源于 CBMC 的早期分支。普通 C adapter 的报告不是独立内核检查证书；Rocq 的证据边界见 [高级验证](advanced.md#rocq-proof)。

反馈含 goal identity、status、verdict、confirmations、artifacts、diagnostics、optimizer_summary、failure_reasons。常见诊断包括 timeout、insufficient_unwinding、out_of_memory、unsupported_property、no_compatible_tool、conflicting_verdict 和 verifier_error。上层根据反馈显式提交新请求。

<a id="cache"></a>

## 精确缓存与证据来源

当前缓存 contract 为 `definitive-cache-v3`，只缓存达到要求的确定结果。UNKNOWN、CONFLICT、取消、超时和错误不提供确定缓存。预算影响执行机会，但不能降低确认要求来换取结果。

命中需要同时满足：相同 semantic key；来源结果和 completed attempts 一致；足够独立家族；无相反证据；版本 / config_id 非空且匹配当前工具；工件 hash 完整；SQLite 的逻辑、结果和 attempt metadata 与不可变 JSON 工件相符。Docker config identity 包含精确镜像身份；环境变更不能复用旧证据。

HIT 仍建立新的 execution ID，零 attempts，并通过 source_execution_id 连接来源。版本 / 能力探测不算验证命令。当前没有工作流级缓存、部分确认复用、witness / invariant 输入复用、checkpoint 或增量证明；工件被保存为证据，不自动作为其他工具的新假设。

<a id="execution-backend"></a>

## Execution Backend Abstraction

公共异步契约为 `start(spec) → handle`、`wait(handle) → outcome`、`cancel(handle, reason)`、`collect_metrics(handle)`、`cleanup(handle)`。handle 是不透明身份；Runtime 不读取 PID。重复 wait 返回同一 outcome，cancel / cleanup 幂等。

`ExecutionSpec` 携带 argv、工作目录、环境、剩余 wall limit、CPU / 内存请求、执行 / 尝试 ID、semantic key 和工具路径 / 版本 / 配置身份。`ExecutionOutcome` 包含退出码、stdout / stderr、起止时间、耗时、状态、终止原因、指标和 backend diagnostics，**不包含 verifier verdict**。

```python
from veriruntime.execution import LocalExecutionBackend
from veriruntime.service import VerificationService
service = VerificationService(".veriruntime", execution_backend=LocalExecutionBackend())
```

当前只有 POSIX `LocalExecutionBackend`。产品 subprocess、PID 与进程组操作集中在 `execution/local.py`；探测与编译驱动使用同一实现边界，内层命令继承外层监督的进程组。wall timeout 从启动开始计时，TERM 后升级 KILL，正常退出也清理组内残留并回收父进程。主动逃离进程组的进程在本地保证范围外。

Local 的 CPU / 每执行 memory request 仅为 metadata；Runtime 实施 aggregate sampled RSS 策略，可能漏掉尖峰。指标覆盖本地进程树；Docker 模式下它们覆盖桥接树，不能当作工具容器用量。Docker worker 的硬 cgroup 限额与 OOM 状态是另一条证据。未提供指标以 null / unavailable 报告。

`VRUN_BACKEND=native|docker` 选择既有 tool transport；`execution_backend=` 选择基础设施执行对象，两者不同。原生模式无需 Docker；本机 Windows 使用已有 Linux 控制容器和可选 Docker adapter transport。

每次尝试保存 execution-spec.json、backend-outcome.json、命令、原始输出和 attempt；spec 环境回执只保留白名单，避免复制宿主无关凭证。后端状态不成功或清理失败，即使输出包含 SAFE，也不能贡献确定证据。

未来 KubernetesExecutionBackend 需要实现 workspace staging / 回收、环境资格验证、准确工具身份、资源保证和启动取消竞态；不能静默替换工具链或系统头文件。start 应返回已拥有资源的 handle，wait 负责等待执行。集群专有配置属于 backend 对象 / 部署配置，不进入 DSL 或 Physical Plan。当前没有 Kubernetes、manifest、Helm 或分布式调度实现。

<a id="adapter"></a>

## Adapter 与扩展

adapter 负责探测、声明能力、接受固定目标、物化快照、生成无 shell 插值的 argv、解析终端记录和收集工件，不负责组合策略。保持命题等价的编译展开允许执行，例如 CPAchecker / Ultimate 的 assert macro 与 reachability property；生成头文件、属性及预处理命令必须保存。

加入新工具需要 adapter、profile 和注册，不需要把工具名写进 DSL。上层 planner 的 proposal 先通过完整语义校验，再调用 VerificationService；模型解释不是证明证据。独立 proof_check 也使用相同 backend 生命周期，具体 DSL 和完整 C / VC / Rocq 设计在 [高级验证](advanced.md)。
