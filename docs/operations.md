# 部署与运维

[回到主线使用手册](../README.md) · 本页合并环境、验证器、命令、证据和验收资料，按目录查需要的部分。

<a id="install"></a>

## 安装与构建

Windows 使用 PowerShell 7 和 Docker Desktop 的 Linux amd64 engine。Docker Engine 客户端使用 API 1.45，工作挂载需要 volume subpath 支持。控制镜像、CBMC、ESBMC 为最小组合，CPAchecker 和扩展 lab 按需构建。

```powershell
# 最小组合：下载、hash 核验、构建、单元测试、真实验收
pwsh -NoProfile -File scripts/docker-demo.ps1
# 本机 Docker DNS 有问题时：仅为本次构建生成临时 extra_hosts
pwsh -NoProfile -File scripts/docker-demo.ps1 -BuildDns 1.1.1.1
# 额外构建 CPAchecker；已有镜像则用 -SkipBuild
pwsh -NoProfile -File scripts/docker-demo.ps1 -WithCPAchecker -BuildDns 1.1.1.1
pwsh -NoProfile -File scripts/docker-demo.ps1 -WithCPAchecker -SkipBuild
```

归档保存在忽略的 `docker/downloads/`，安装脚本逐个验证 SHA-256；CPAchecker / Frama-C 大归档使用有界分段下载。Linux 网络正常时可直接 `docker compose build runtime cbmc esbmc`，构建阶段也能下载校验缺失的基础归档。不要把未经重新资格验证的最新版替换进锁文件。

本机已构建七个镜像，但基础运行不要求构建全部扩展。镜像显示大小约 runtime 226 MB、CBMC 522 MB、ESBMC 714 MB、CPAchecker 1.65 GB、Ultimate 1.07 GB、Frama-C 1.01 GB、Rocq 858 MB；含共享层，不能直接相加当作独占磁盘占用，构建缓存和下载另计。

<a id="tools"></a>

## 工具版本与支持范围

| 工具 | 已资格验证的版本 | 当前用途与边界 |
|---|---|---|
| CBMC | 6.11.0 | C99 / C11，LP64 / ILP32，断言安全；JSON 属性记录与 cProverStatus、退出码共同解释 |
| ESBMC | 8.5.0 | 同上；属性表和终端记录可能在 stderr；unwinding assertion 失败返回 UNKNOWN |
| CPAchecker | 4.2.2；Linux Java 21、原生 macOS Java 24 | valueAnalysis + Princess；确认反例后才给 UNSAFE；浮点和不支持输入返回 UNKNOWN |
| Ultimate Automizer | 0.3.1 / 35a84365 | 可选 C adapter；当前单 translation unit、main、无浮点；反例尚未独立 witness-check |
| Frama-C | 33.0 Arsenic | 可选 Eva adapter；完整、零警报且断言已证明才 SAFE，抽象警报为 UNKNOWN；原断言 DSL 拒绝源 ACSL，避免引入其他家族未采用的假设 |
| WP + Z3 / CVC5 | Frama-C 33.0，Z3 4.13.3 / CVC5 1.1.2 | 独立 lab 实验，共享 WP 编码；solver_report 不计成两个 C 家族，不等于内核检查证书 |
| Rocq | 9.1.1 + OCaml 5.3.0 | 独立 proof_check；有 Corelib，未带完整 Stdlib / Why3-Rocq 支撑库，不计 C 确认票 |

来源、版本和归档 hash 见 [Linux 锁文件](../docker/verifiers-linux.lock.json)、[扩展锁文件](../docker/experiments.lock.json)、[原生锁文件](../verifiers.lock.json)。扩展的实际矩阵和信任边界见 [高级验证](advanced.md#lab)。

CPAchecker 的默认 MathSAT 在原 ARM64 环境不可用，最终使用 Java Princess 配置。其编译层以等价 assert macro 处理标准断言：只求值一次、遵守 NDEBUG、跳转到私有失败标签；拒绝忽略未知调用。生成头文件、属性、预处理命令、配置及反例均保留。Ultimate 使用同类等价 reachability 展开，并把 vendor 的 15 GB JVM heap 请求限定为 768 MB。

<a id="commands"></a>

## CLI 命令与结果查看

下面以主线脚本的 `$dataDir` 为数据目录；可从 `.veriruntime/quickstart/latest.json` 取值：

```powershell
$record = Get-Content .veriruntime/quickstart/latest.json -Raw | ConvertFrom-Json
$dataDir = $record.data_dir
$goalId = $record.first.goals[0].report.result.execution_id
docker compose run --rm runtime show $goalId --data-dir $dataDir --json
```

| 命令 | 用途 |
|---|---|
| `doctor --json` | 实际版本、可用性、能力、配置身份 |
| `validate TASK` / `parse TASK` | 校验或显示 DSL / 逻辑 IR |
| `explain TASK --data-dir DIR` | 查看逻辑目标、候选排序、物理 AST 与缓存决策；没有证明命令，可能有探测 |
| `explain TASK --analyze --data-dir DIR` | 真实执行并显示计划与统计 |
| `verify TASK --explain --data-dir DIR` | 执行固定目标或工作流；`--no-cache` 禁用复用，`--json` 导出结构化结果 |
| `history --data-dir DIR --limit 10` | 近期执行 ID、目标与结果 |
| `show ID --data-dir DIR --json` | 来源、原始 attempts、工件与缓存来源执行 |
| `cache clear TASK --data-dir DIR` | 只清该任务的缓存键，保留历史和工件；`--demo` 只清已知示例键 |
| `check-proof TASK --json` | Rocq 工件检查，见 [证明 DSL](advanced.md#rocq-proof) |
| `plan` / `experiment` | 可选上层规划，见 [LLM planner](advanced.md#llm-planner) |

verify / explain 的 exit 0 只表示操作完成；看 verdict / requirement_satisfied / diagnostics。非法输入或访问错误 exit 2。check-proof 则 VERIFIED exit 0，其余 exit 2。SIGINT / SIGTERM 请求监督取消与清理。

<a id="data"></a>

## 数据与证据位置

Docker 模式的 `--data-dir` 必须放在命名卷 `veriruntime-data` 的 `/data/` 内；不要用额外挂载替换 compose 的数据卷。源码 checkout 挂载在 `/workspace`，只读，新增 C / DSL 文件会直接可见。

| 位置 | 内容 |
|---|---|
| `/data/quickstart-<id>/` | 每次主线例子的独立 SQLite、执行、缓存和工件 |
| `.veriruntime/quickstart/latest.json` | 主线脚本导出的两次真实结果与数据目录 |
| `<data-dir>/executions/<goal execution id>/` | logical / physical / optimizer、事件、结果，以及每次 attempt 的 workspace |
| attempt workspace | stdout / stderr、command、execution-spec、backend-outcome、native-command、container-state、工具配置 / 反例 |
| ArtifactStore | SHA-256 内容寻址对象；SQLite 保存工件类型、大小、hash 和来源关联 |
| `/data/proofs/<id>/` | 独立 Rocq 命题 / 证明快照、编译模块、类型绑定、假设审计和内核复查 |
| `/data/acceptance/latest/` | 基础验收；导出到 `.veriruntime/docker-acceptance/` |
| `/data/verifier-lab/` | 扩展实验；导出到 `.veriruntime/verifier-lab/lab.json` |

HIT 建立新的执行记录，attempts 为空，source_execution_id 指向原始结果。`show` 会展示原始来源。删除 Docker 数据卷会失去这些本机记录；仅删除文档不会改变验证数据。

<a id="isolation"></a>

## 隔离和资源保证

控制层使用 Python / SQLite / 标准库 Docker Engine 客户端，384 MiB / 1 CPU，无网络、只读根目录。它持有本机 Docker socket，面向可信本机 CLI；不运行 Docker-in-Docker、HTTP 服务或验证器常驻服务。

每次工具尝试创建独立容器，UID 10001，禁网、根目录只读、cap_drop ALL、no-new-privileges、128 PID 上限、64 MiB tmpfs。仅将当前尝试的 volume subpath 挂到 `/work`；输入只读，HOME / TMPDIR / XDG 独立；工具不能读取 peer、SQLite、源 checkout 或 socket。

容器内存为 `memory_mb // max_parallel`，至少 16 MiB；MemorySwap 等于 Memory，额外 swap 被禁用；每工具 1 CPU。当前保守固定份额不会在独占阶段借用其他份额；控制层、daemon、探测和证据存盘 / 清理有额外成本。共享宿主内核不等于虚拟机隔离。

LocalExecutionBackend 的 CPU / memory request 只是 metadata，RSS 采样不是硬隔离；Docker cgroup 是单独的保证。桥接进程指标不等于工具容器用量。接口与后端记录见 [架构细节](architecture.md#execution-backend)。

<a id="native"></a>

## 原生 macOS / Linux

原 bootstrap 资格验证于 macOS ARM64 Tahoe，Python 3.11+、Command Line Tools 与 Homebrew；CPAchecker 另需 Java 21+，原开发环境为 24。工具和库安装到仓库本地，不用 sudo，不升级系统包。

```sh
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
./scripts/bootstrap_verifiers.sh
# 按需添加第三家族：./scripts/bootstrap_verifiers.sh --cpachecker
vrun doctor
vrun verify examples/tasks/safe_assert_crosscheck.json --data-dir .veriruntime/my-demo --explain
```

原生 Linux 可安装锁定的官方工具并放入 PATH，或设置 VRUN_CBMC / VRUN_ESBMC / VRUN_CPACHECKER 为绝对可执行路径。native 是默认 registry；Windows 原生进程后端未实现，使用前面的 Docker Linux 路径。原生 toolchain 位于 `.veriruntime/toolchains/`；若 Homebrew bottle 已变化，bootstrap 拒绝 hash 不匹配，应有意更新并重新资格验证，不能跳过校验。

<a id="validation"></a>

## 测试和真实验收

最近的框架回归是 M11：**128 passed**，包含 16 个真实软件集成测试；仅单元测试 **112 passed, 16 deselected**。常规 pytest 不调用上层真实模型。

```powershell
docker compose run --rm --entrypoint python runtime -m pytest -q --basetemp=/data/tests -o cache_dir=/tmp/pytest-cache
docker compose run --rm --entrypoint python runtime scripts/docker_acceptance.py --with-cpachecker
pwsh -NoProfile -File scripts/verifier-lab.ps1 -SkipBuild
```

pytest 临时目录必须在 `/data`，以便独立 worker 通过 subpath 读取当前尝试；pytest cache 则放在 `/tmp`。真实验收验证三家族 SAFE / UNSAFE、零证明命令缓存重复、DAG、unsupported property、timeout、cgroup OOM、挂载隔离和无遗留容器。六案例基础基准为双家族 12 次、三家族 18 次，4 SAFE / 2 UNSAFE；扩展 lab 为 16 次 C 验证、3 次 Rocq 检查、2 次 WP 运行。

实际记录为 [dispatch-evidence.json](dispatch-evidence.json) 和 [verifier-lab.json](verifier-lab.json)。本次文档整理的演示结果保存到 `.veriruntime/quickstart/latest.json`；以脚本断言和结果字段验收，而非仅检查 CLI 退出码。

<a id="visuals"></a>

## 可视化维护与回执

[调度回放](runtime-explorer.html) 离线展示已完成的五个场景、八个步骤，不执行新的证明；数据是 dispatch-evidence.json，模板是 runtime-explorer.template.html。更新已有验收数据后：

```powershell
docker compose run --rm --entrypoint python runtime scripts/export_dispatch_evidence.py
docker compose run --rm -v "${PWD}/docs:/export" --entrypoint python runtime -c 'import shutil; shutil.copyfile("/data/acceptance/latest/dispatch-evidence.json","/export/dispatch-evidence.json")'
python scripts/build_runtime_explorer.py
```

已有两张 archify workflow 图保持原始交付字节，showcase 9/9、零错误 / 警告。内置 visual-check 在 Windows 失败（spawn UNKNOWN），没有算作通过；随后用 Playwright 对同一 HTML 测量 1440×900、1600×1000、1920×1080、2048×1320，无页面溢出，深浅截图已人工检查。回放页面另验五案例 / 八步骤和 390 窄屏。

| 图 / 规范 | 字节数 | SHA-256 |
|---|---:|---|
| dispatch.workflow.json | 3661 | f7316d26e82183991f2ecf271a506463cb2f802078ad93e76ab909ec753d1d02 |
| dispatch.html | 632300 | e59bd9b9ba0ee0b2427304c516375ff8e02fa3c8eaaba28353f6f2359f378836 |
| strong-verification.workflow.json | 3589 | 2e0839900a5cc1cac1611315fca594a0b3add48f6ceac1cc4df1fc8abdea2aef |
| strong-verification.html | 632465 | 04034fe71a3402fcdc472554b3ceea458987e6dd61bd7904697a047fca2b779a |

<a id="history"></a>

## 版本沿革

旧阶段手册的重复安装 / 架构 / 验收内容已合并到现在的三份参考文档；旧全文仍可从 Git 历史取回，没有重写历史。以下仅保留定位信息，不要求新用户逐阶段阅读。

| 节点 | 提交 | 内容 |
|---|---|---|
| M0–M7 | b7edebb → 5717c28 | DSL / IR、真实工具、运行时、优化器、缓存、可观测性与 macOS 原型 |
| M8 | 4a86a7e | 可选上层 planner 与有界反馈实验 |
| M9 | 7cb58ed | Docker Linux 复现和每尝试隔离 |
| M10 | afbe9d8 | Ultimate / Eva / WP 实验、独立 Rocq 证明 DSL 与强证明设计 |
| M11 | a49f8a5 | ExecutionBackend 抽象、Local 后端、资源回执 |

例如 `git show a49f8a5:docs/execution-backends.md` 可取回后端原验收说明；`git show 5717c28:docs/delivery-report.md` 可取回 M0–M7 原报告。

<a id="faq"></a>

## 常见问题

| 现象 | 处理 |
|---|---|
| Docker 无法连接 / 不是 Linux engine | 启动 Docker Desktop，并确认 Linux containers；先运行 doctor |
| 找不到 CBMC / ESBMC 镜像 | 运行安装段的 docker-demo.ps1；已有镜像用 -SkipBuild 避免下载 |
| 构建解析到不可达 127 地址 | 用本机已验证的 -BuildDns 1.1.1.1；不修改全局 DNS |
| 数据目录 / pytest 放在 `/tmp` 后 worker 找不到文件 | 执行 store 和测试 workspace 放在 `/data` 命名卷，且不覆盖该卷 |
| 首次演示已经 HIT | 用 quickstart 脚本或主线的随机数据目录重新演示；不必删全局缓存 |
| UNKNOWN | 检查 diagnostics；增加预算不保证成功，展开 / 属性不支持需要不同执行手段 |
| 重建镜像后旧缓存 MISS | 正常：镜像身份变了，旧环境证据不再匹配 |
