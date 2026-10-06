# M9 — 本机 Docker Linux 复现与验收

2026-10-06，在 Windows 的 Docker Desktop Linux amd64 引擎上复现。最终完整测试 **113 passed**；三家族 SAFE / UNSAFE、零验证执行缓存命中、工作流依赖与六案例基准均通过。原有 macOS 引导和原生 adapter 保留。

## 复现命令

本机使用 PowerShell 7，已构建四个镜像。重复验收无需重新下载：

```powershell
./scripts/docker-demo.ps1 -WithCPAchecker -SkipBuild
```

新环境默认只构建控制镜像、CBMC 和 ESBMC；第三家族按需加入：

```powershell
./scripts/docker-demo.ps1
./scripts/docker-demo.ps1 -WithCPAchecker
```

本机 Docker 默认 DNS 把公共域名解析到不可达的 127 段地址；构建时使用独立解析生成临时 `build.extra_hosts`，不修改 Windows 或 Docker 全局配置：

```powershell
./scripts/docker-demo.ps1 -BuildDns 1.1.1.1
./scripts/docker-demo.ps1 -WithCPAchecker -BuildDns 1.1.1.1
```

脚本预下载官方归档并核验 SHA-256，CPAchecker 使用八路有界分段下载。归档保存在被 Git 忽略的 `docker/downloads/`，不会进入最终运行镜像。Linux 主机在网络正常时可直接 `docker compose build runtime cbmc esbmc`；构建阶段也会下载并校验缺失归档。Docker Engine 要求支持 API 1.45 的 volume subpath（26+）；本机实际为 Docker Desktop 4.91.0 / Engine 29.8.0。

```powershell
docker compose run --rm runtime doctor
docker compose run --rm runtime validate examples/tasks/unsafe_assert_crosscheck.json
docker compose run --rm runtime verify examples/tasks/unsafe_assert_crosscheck.json --data-dir /data/review --explain
docker compose run --rm runtime verify examples/tasks/unsafe_assert_crosscheck.json --data-dir /data/review --explain
docker compose run --rm runtime verify examples/tasks/assertion_workflow.json --data-dir /data/workflow --json
docker compose run --rm --entrypoint python runtime -m pytest -q --basetemp=/data/tests -o cache_dir=/tmp/pytest-cache
```

验证结果查看 `verdict` / `requirement_satisfied`；CLI exit 0 只表示操作完成，不能把 UNKNOWN 当作证明。`--data-dir` 必须位于 `/data` 命名卷内，才能提供独立的工具挂载。源仓库挂载在 `/workspace`，只读；可从中读取新增的 DSL 与 C 输入。

## 轻量化与隔离

控制容器只运行 Python、SQLite 和标准库 Docker Engine 客户端，不安装 Docker CLI、不运行 Docker-in-Docker。每个工具按需创建短生命周期容器，结束后删除；HTML 无服务器、无外部 CDN。默认没有 Java 服务或 HTTP 服务。

| 层 | 内容与隔离 |
|---|---|
| 控制容器 | `python:3.11-slim` digest 固定；384 MiB / 1 CPU；只读根目录、无网络；持有本机 Docker socket |
| CBMC / ESBMC | 分别使用官方固定版本；只保留 CBMC 主程序及 ESBMC 主程序、头文件和许可；去除调试符号；共享 Python / GCC 基础层 |
| CPAchecker（可选） | 独立 Java 21 / Clang 镜像；继续使用 valueAnalysis + Princess，不向其他工具注入库路径或 Java 配置 |
| 每次尝试 | UID 10001、独立进程 namespace、禁网、只读根目录、`cap_drop=ALL`、`no-new-privileges`、128 PID 上限、64 MiB 临时文件系统 |
| 输入与输出 | 只挂载该尝试的卷 subpath 到 `/work`；输入设为只读；HOME / TMPDIR / XDG 目录独立；工具看不到 peer 尝试、SQLite、源码 checkout 或 socket |
| 内存与 CPU | 按 `memory_mb // max_parallel` 分配每容器内存（最少 16 MiB）；`MemorySwap == Memory` 禁用额外 swap；每容器 1 CPU |

Docker Desktop 当次镜像显示大小约为 runtime **226 MB**、CBMC **522 MB**、ESBMC **714 MB**、可选 CPAchecker **1.65 GB**。这些大小含共享层，不能直接相加当作独占磁盘占用；CPAchecker 的 Java/Clang 是主要额外开销，因此默认不构建。构建缓存和下载归档另计。

控制容器的 socket 可控制本机 Docker，因此控制层面向本机可信 CLI；工具容器不持有它。隔离针对依赖、文件、缓存和资源互相污染；不把共享宿主内核描述为虚拟机隔离。

## DSL 到调度的真实顺序

1. `load_workflow` 校验严格 Schema，快照源文件与递归字面量本地头文件；单任务包装为工作流。
2. `VerificationService.verify` 检查 DAG 前置结果及确认数；失败的依赖写 BLOCKED / UNKNOWN。当前就绪目标串行执行。
3. `CostAwareOptimizer.optimize` 按语言、属性、C 语义、程序形状和预算筛选工具；按历史确定率 / 耗时排序，生成 Run / Parallel / Sequence。
4. **候选计划生成后**，`SemanticCache.lookup` 核验来源、工件和当前工具身份；有效命中将计划替换为带 fallback 的 CacheLookup。
5. Cache MISS 进入 `Runtime.execute_goal`：共享 deadline、全局 semaphore、阶段时间片；目标内工具可并行。
6. DockerAdapter 预备容器，等待预备完成后才启动监督桥接进程。即使此时协程被取消，也等待预备结束再清理，避免 HTTP create 迟到后留下容器。
7. 容器内原生 adapter 接收相同不可变任务并生成命令。Docker cgroup OOM 映射 UNKNOWN / OOM；deadline 或早停取消后清理进程组，再删除命名容器。
8. `reconcile` 只计 COMPLETED 的确定证据，按家族去重；冲突优先于确认数。保存 SQLite 来源和 SHA-256 工件，再返回结构化反馈。

缓存 contract 升级到 `definitive-cache-v3`。Docker配置身份包含镜像身份、adapter 配置和 transport contract；工具家族、版本或身份变化时不命中旧证据。原有 v2 缓存不再复用。默认 Linux 命名卷与原来 macOS 工作目录的证据分离。

## 验收记录

| 检查 | 实际结果 |
|---|---|
| 完整 Linux 测试 | **113 passed**，含 15 项真实工具集成测试 |
| SAFE 三家族 | CBMC 6.11.0、ESBMC 8.5.0、CPAchecker 4.2.2 一致 SAFE，确认数 3 |
| UNSAFE 三家族 | 三工具一致 UNSAFE，确认数 3；CPAchecker 反例由 Princess 确认 |
| 重复请求 | SAFE / UNSAFE 均 CacheLookup 命中，attempts 为空，验证执行数 0 |
| 工作流 | G1 SAFE 后才执行 G2，返回 UNSAFE；不进行假设传播或定理组合 |
| 不支持属性 | memory_safety 返回 UNKNOWN / unsupported_property，无工具执行 |
| 超时 / OOM | 小时间预算返回 UNKNOWN 并清理；真实 64 MiB cgroup OOM 及 adapter 的 UNKNOWN / OOM 映射通过 |
| 文件隔离 | 工具内无法读取其他尝试、`/data` 或 Docker socket；自己的输出可回收 |
| 清理 | 创建期间取消回归通过；完整测试结束后受管理工具容器数为 0 |
| 六案例基准 | 双家族 12 次、三家族 18 次真实执行；4 SAFE、2 UNSAFE，全部符合预期 |

脚本创建新 store，不依赖已有缓存，导出到 `.veriruntime/docker-acceptance/acceptance.json`；原始命令、输出、attempt / container-state、SQLite 和工件保留在 `veriruntime-data` 卷的 `/data/acceptance/latest/<运行ID>/`。基准记录在 `/data/benchmark-linux[-three]/`。缓存 HIT 的版本/能力探测可能创建 probe 容器，“零执行”指未运行验证命令。

## 可视化与再生成

[DSL 调度观察台](runtime-explorer.html) 独立离线运行，包含真实请求 JSON、物理计划、五种场景、八步说明、原生命令、容器配置及事件。它回放已完成执行，不模拟新证明。数据在 [dispatch-evidence.json](dispatch-evidence.json)，模板和生成脚本可继续维护。

[流程全景](dispatch.html) 使用 archify workflow 生成。9/9 showcase 检查通过，0 errors / warnings；四个桌面尺寸 1440×900、1600×1000、1920×1080、2048×1320 无页面溢出，深浅主题已实际截图检查。archify 内置 visual-check 在该 Windows 主机报告 `spawn UNKNOWN`，随后通过 bundled Playwright 对同一 HTML 测量和截图；没有把失败的工具回执当作通过。

流程图固定的交付摘要：specification SHA-256 `f7316d26e82183991f2ecf271a506463cb2f802078ad93e76ab909ec753d1d02`（3661 bytes）；artifact SHA-256 `e59bd9b9ba0ee0b2427304c516375ff8e02fa3c8eaaba28353f6f2359f378836`（632300 bytes）；`visual_review: passed`，`correction_rounds: 2`。

```powershell
# 新验收完成后从卷导出经过裁剪的真实证据
docker compose run --rm --entrypoint python runtime scripts/export_dispatch_evidence.py
docker compose run --rm -v "${PWD}/docs:/export" --entrypoint python runtime -c 'import shutil; shutil.copyfile("/data/acceptance/latest/dispatch-evidence.json", "/export/dispatch-evidence.json")'
python scripts/build_runtime_explorer.py
```

当前仍只覆盖 assertion_safety，BMC 展开上限 64；不足展开返回 UNKNOWN。容器内存采用保守固定份额，独占阶段不会借用其他份额；控制层和 Docker daemon 开销不计入任务 memory_mb。版本发现、证据存盘与清理有额外时间。上层 Codex 规划桥接仍是可选功能，最小镜像未安装 Codex 或复制账户认证，本节点验收直接提交 DSL。
