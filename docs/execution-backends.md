# M11 — ExecutionBackend 架构补充

本节点参考补充 prompt 整理执行层，沿用已完成的 M0–M10 历史。原 prompt 针对 M0–M7 的“不引入 Docker”按不增加基础依赖处理；当前用户已经明确要求并验收的可选 Docker Linux 路径继续保留。没有安装 Kubernetes / kind / minikube，没有编写集群 manifest / Helm，也没有增加部署服务。

## 当前职责和接口

| 层 | 决定什么 | 当前代码 |
|---|---|---|
| 上层人 / LLM | 规约、目标、假设、不变式、引理、逻辑依赖及显式重规划 | 上层输入、可选 `planner/`；运行时不自动编写证明 |
| VeriRuntime | 固定目标的工具选择、顺序、并行、回退、预算、取消、缓存与证据归并 | `optimizer.py`、`runtime/`、`service.py` |
| Infrastructure backend | 已选命令的物理运行、执行身份、进程 / 未来任务生命周期、指标和清理 | `execution/`，当前只有 `LocalExecutionBackend` |

公共接口为 `start(spec) → handle`、`wait(handle) → outcome`、`cancel(handle, reason)`、`collect_metrics(handle)`、`cleanup(handle)`，均为异步方法。handle 是不透明身份，Runtime 不读取 PID，也不调用进程创建 / 信号 API。

`ExecutionSpec` 携带 argv、工作目录、环境、剩余 wall limit、CPU / 内存请求和执行元数据；后者包括 goal semantic key、执行 / 尝试 ID，以及已资格验证的工具路径、版本和配置身份。`ExecutionOutcome` 返回退出码、stdout / stderr、开始 / 结束时间、耗时、终止原因、指标和后端诊断，不返回验证结论。工具 adapter 负责原来的输出解析，runtime 负责确认数及冲突。

DSL、Logical Goal / Workflow、优化器核心接口、Physical Plan 算子和 Tool Registry / Adapter 基本契约未改变。资源需求放在下层生成的 ExecutionSpec 中；没有向 RunPlan 或上层 DSL 添加 namespace / pod / nodeSelector 等集群字段。

```python
from veriruntime.execution import LocalExecutionBackend
from veriruntime.service import VerificationService

service = VerificationService(
    data_dir=".veriruntime",
    execution_backend=LocalExecutionBackend(),
)
```

所有产品中的 subprocess、PID 和进程组操作集中在 `execution/local.py`。工具版本 / 帮助探测、macOS SDK 探测、CPA / Ultimate 预处理驱动、Eva 驱动、Rocq 编译 / 检查，以及可选上层 Codex CLI 都使用同一实现边界。同步 helper 只是探测或既定命令的执行；编译驱动的子命令继承外层已经监督的进程组，不能通过新建 session 逃离取消。

## 本地保证与可选隔离

| 能力 | LocalExecutionBackend | 已有可选 Docker transport |
|---|---|---|
| wall timeout | 启动即建立 watchdog；等待调用延迟也不延长期限。清理 / 回收有额外耗时 | 本地 runtime 控制桥接期限，并清理工作容器 |
| cancel / cleanup | 独立 POSIX session；TERM 后 KILL，回收父进程；正常退出也清理组内残留 | 预备完成后按确定名称删除容器，保留 M9 创建期间取消修复 |
| CPU request | 仅 metadata；不声称限制 CPU | 工具容器现有 1 CPU 限额 |
| memory request | 仅 metadata；Runtime 基于后端指标实施 aggregate sampled RSS 策略，可能漏过瞬时尖峰 | 工具容器现有硬内存 / swap 限额；OOM 用独立 cgroup 状态解释 |
| 指标范围 | 本地进程树采样；不能当作容器或远端工具用量；未提供指标以 null / unavailable 报告 | 容器状态与本地桥接指标分别记录 |
| 文件 / 网络隔离 | 本地独立 HOME / TMPDIR，不能声称 namespace 隔离；主动逃离组的进程在保证范围外 | 继续使用独立卷 subpath、禁网、只读根目录和非 root worker |

`VRUN_BACKEND=native|docker` 保留原含义：选择工具 adapter transport。它与 Python API 的 `execution_backend=` 参数是不同配置层。原生模式和新抽象无需 Docker；Windows 本机验收继续在已有 Linux 容器中运行 POSIX backend。

每次尝试新增 `execution-spec.json` 和 `backend-outcome.json` 工件；attempt 记录 backend 名称及指标，命题 identity 不因此变化。规范工件只保留环境白名单，不复制宿主无关凭证；stdin 记录字节数，上层请求已有自己的文件。内核检查的独立 Rocq 接口也保存相同 backend 工件。后端输出即使包含 SAFE，超时、取消或清理失败的尝试仍只能贡献 UNKNOWN，不能进入确定结果缓存。

## 未来基础设施扩展

未来的 KubernetesExecutionBackend 可以把已选 spec 映射成 Job / Pod。它仍需实现工作目录 staging / 回收、准确工具链身份、环境资格验证、资源保证、启动取消竞态和任务清理。start 应尽快返回已拥有资源的 handle，不能等待无限的基础设施排队；wait 才负责等待执行。backend 专有配置放在部署配置或 backend 对象内部，不能进入验证 DSL。

换机器不能静默换验证环境。backend 必须兑现 spec 中记录的工具 / 配置身份；无法兑现时返回执行失败。原缓存仍按工具版本 / 配置及工件核验，Docker 身份仍包含镜像 digest。这里提供扩展边界，不宣称完整远端 staging 和环境管理已实现。

## 验收

测试涵盖：不带 PID 的替换后端、命题与资源请求传递、backend 状态与 verifier verdict 分离、清理失败不能贡献确定证据、重复 wait / 幂等 cleanup、启动期间两级取消、主动 deadline、超时输出保留、探测子进程清理，以及产品进程 API 只能位于后端模块的架构回归。

完整 Linux 测试 **128 passed**；仅运行非 integration 测试为 **112 passed, 16 deselected**。九个新增后端测试均为本节点的真实生命周期或接口替换检查。

重新构建已有七个 Linux 镜像后，三家族 SAFE / UNSAFE 均达到确认数 3，重复请求 Cache HIT 且零验证执行；DAG、不支持属性、超时、cgroup OOM、卷挂载隔离和容器清理通过。扩展 lab 的 16 次 C 验证、3 次 Rocq 检查与 2 次 WP 运行均符合 M10 的预期，结束时遗留工具容器为 0。新执行的后端字段和工件见 [调度证据](dispatch-evidence.json) 与 [扩展实验记录](verifier-lab.json)。

HTML 回放已实际检查五个案例、八个步骤、深浅主题和 1440 / 1920 / 390 宽度；没有 JavaScript 错误或横向溢出。后端参数与 `metadata only` 资源保证在真实记录中可见。

```powershell
docker compose run --rm --entrypoint python runtime -m pytest -q --basetemp=/data/backend-final-tests -o cache_dir=/tmp/pytest-cache
docker compose run --rm --entrypoint python runtime scripts/docker_acceptance.py --with-cpachecker
./scripts/verifier-lab.ps1 -SkipBuild
```

当前调度回放页面同时展示 ExecutionSpec、backend outcome 摘要、原生命令和容器记录。见 [DSL 调度观察台](runtime-explorer.html)；三层职责的英文架构说明见 [Execution Backend Abstraction](architecture.md#execution-backend-abstraction)。
