# VeriRuntime semantic contract

VeriRuntime is a declarative multi-verifier execution runtime. A caller describes
**what to verify**. The optimizer and scheduler decide **how to verify it**.

## Two planning levels and the runtime boundary

An upstream LLM Semantic Planner answers: which logical propositions should be
proved to answer the user's question? VeriRuntime's per-goal Physical Optimizer
answers: how should a fixed proof obligation be executed economically?

Anything that changes a proposition belongs upstream: goal decomposition,
assumptions, invariants, auxiliary lemmas, logical dependencies, and replanning
after UNKNOWN. VeriRuntime never silently creates those. It may change tools,
order, concurrency, fallback, resource allocation, cancellation and cache lookup.
Hints are advisory preferences, never correctness assumptions. The optimizer
may ignore them, and they are excluded from semantic identity.

VerificationTask is also named LogicalGoal. VerificationWorkflow contains fixed
goals, explicit acyclic control dependencies and inert metadata. A single task
JSON is backward-compatible shorthand for a one-goal workflow. An edge explicitly
requires a predecessor's requested SAFE/UNSAFE result and satisfied confirmation
requirement before its successor can run. No assertion is inherited as an
assumption; G1 SAFE + G2 SAFE does not prove G3. Workflow scheduling can remain
thin and sequential while physical plans within each goal run in parallel.

The result API includes goal identity, verdict, lifecycle status, confirmations,
artifacts, structured diagnostics, optimizer summary and failure codes. An
upstream planner can consume UNKNOWN with `timeout`, `insufficient_unwinding`,
`out_of_memory`, `no_compatible_tool` or other codes, then submit a new explicit
workflow. No LLM is implemented inside the runtime.

```mermaid
flowchart TD
    User[User requirement] --> Semantic[Upstream LLM Semantic Planner]
    Semantic --> DSL
    DSL[JSON Verification DSL] --> Parser[Parser and validator]
    Parser --> Workflow[Logical Workflow IR: fixed goals and explicit dependencies]
    Workflow --> Logical[Ready Logical Goal]
    Logical --> Optimizer[Per-Goal Physical Optimizer]
    Registry[Tool Registry] --> Optimizer
    History[Execution History] --> Optimizer
    Cache[Semantic Cache] --> Optimizer
    Optimizer --> Physical[Physical Plan AST]
    Physical --> Runtime[Runtime Scheduler]
    Runtime --> Adapters[Tool Adapters: command and result interpretation]
    Adapters --> Backend[ExecutionBackend: selected command lifecycle]
    Runtime --> Backend
    Backend --> Verifiers[Real Software Verifiers]
    Verifiers --> Store[Execution and Artifact Store]
    Store --> History
    Store --> Cache
    Store --> Feedback[Structured goal feedback]
    Feedback -. explicit semantic replanning .-> Semantic
```

## Semantic transparency

For a DSL request D, every returned SAFE or UNSAFE must refer to the immutable
program snapshot, property, entry point, C semantics, and trust requirements of
D. Different physical plans may change time, cost, selection, and order. UNKNOWN
may change with resource budgets. Optimization must never weaken the proposition
or confirmation requirement in order to produce a definitive result.

Assertions refer to well-defined executions under the declared C data model.
Unsupported language constructs, incomplete bounded proofs, or inconclusive
tool output must not become SAFE. The prototype trusts supported verifier
versions and their verdicts; it does not check proof certificates independently.

## Boundaries and records

| Layer | Responsibility | Main records / interface |
|---|---|---|
| DSL | Stable JSON, validation, no tool names or execution operators | schema, loader |
| Logical IR | Verification proposition and immutable inputs | VerificationTask, ProgramSnapshot, VerificationProperty, Semantics, Requirements, Budget, LogicalPlan |
| Optimizer | Capability filtering, history and cost heuristic, cache decision | optimize(logical_plan, context), OptimizationResult |
| Physical plan | Structured implementation of a logical request | RunPlan, SequencePlan, ParallelPlan, CacheLookupPlan |
| Scheduler | Global deadline, concurrency, fallback, cancellation, reconciliation | Runtime |
| Tool adapter | Detection, capability declaration, argv construction, output parsing, artifact collection | ToolAdapter, ToolProfile |
| Tool registry | Discovery, lookup and compatible candidates | ToolRegistry |
| Infrastructure execution | Start/wait/cancel, process or future job ownership, metrics and cleanup | ExecutionBackend, ExecutionSpec, ExecutionHandle, ExecutionOutcome |
| Execution store | Tasks, plans, attempts, results, history and events | SQLite |
| Artifact store | Immutable content-addressed raw output and evidence | Artifact, filesystem |
| Semantic cache | Exact completed definitive results meeting trust requirements | semantic key, evidence references |

The logical IR contains no physical operators. Adapters contain no global
scheduling decisions. The runtime contains no backend-name branches. An
optimizer can be replaced without changing an adapter or the DSL.

## Execution Backend Abstraction

Three scheduling responsibilities remain separate. An upstream LLM decides
**what should be proved**; VeriRuntime decides **how a fixed goal should be
executed**; an infrastructure backend decides **where/how the selected command
physically runs**. A backend does not select CBMC versus CPAchecker, build a
portfolio, validate cache evidence or change a proposition.

`veriruntime/execution/` defines the backend-neutral `ExecutionSpec`, opaque
`ExecutionHandle`, `ExecutionMetrics` and `ExecutionOutcome`. The asynchronous
contract is `start`, `wait`, `cancel`, `collect_metrics`, `cleanup`. A spec carries
adapter-generated argv, workspace, environment, remaining wall time, CPU/memory
requests and provenance metadata. Outcomes carry raw outputs, exit code, timing,
termination, metrics and backend diagnostics; they contain no verifier verdict.
Adapters alone interpret output, and Runtime alone reconciles evidence.

The current implementation is **LocalExecutionBackend**, using POSIX process
groups. All product subprocess/PID/signal operations, including version probes,
compiler drivers and the optional upstream planner's CLI, are concentrated in
`execution/local.py`. Nested driver commands inherit the owning execution's
process group rather than escaping supervision. Synchronous probe/helper calls
use the same implementation boundary; they do not decide scheduling policy.

Runtime and VerificationService accept `execution_backend=` injection. The
existing native registry is the default and needs no Docker dependency. The M9
Docker registry remains an optional adapter transport: the local backend runs its
bridge, while the existing transport applies worker cgroups and mount isolation.
`VRUN_BACKEND=native|docker` selects that existing tool transport, not a new DSL
directive or an infrastructure-backend name. This preserves the already qualified
Linux reproduction rather than rolling it back.

Local enforces wall timeout and owned-process-group TERM/KILL cleanup. CPU and
per-execution memory requests are **metadata only**. Runtime applies aggregate
sampled RSS policy using backend metrics; samples can miss spikes. With the Docker
bridge these local samples cover the bridge tree, not the verifier container;
worker cgroup state is separate evidence. `backend-outcome.json` reports these
guarantees explicitly. Attempt records include backend identity and metrics, while
`execution-spec.json` stores requests and an allowlisted environment receipt.

A future **KubernetesExecutionBackend** would map an already selected spec to a
Job/Pod, stage the workspace, return artifacts to it, enforce requested resources,
and own startup/cancel/cleanup races. Startup must return ownership without waiting
indefinitely for a scheduled workload. It must execute the qualified tool identity
in spec metadata, or report failure; silently changing toolchains/system headers
would invalidate evidence and caching. Remote staging, environment qualification
and lifecycle implementation remain future work. Verification DSL, Logical IR,
optimizer interfaces, physical-plan semantics, registry and basic adapter contract
remain unchanged. Kubernetes-specific settings belong to backend configuration,
never RunPlan or the verification DSL. **Kubernetes is intentionally out of scope
for M0–M7**, and no Kubernetes implementation, manifests or deployment dependencies
are introduced in this follow-up.

## Verdict and execution status

Verdict: SAFE (proved property), UNSAFE (property violation), UNKNOWN
(insufficient evidence), CONFLICT (opposing definitive evidence).

ExecutionStatus: COMPLETED, TIMEOUT, CANCELLED, ERROR, OOM, START_FAILED.
COMPLETED/UNKNOWN and TIMEOUT/UNKNOWN are valid combinations. Only
COMPLETED attempts may contribute definitive evidence. Conflicting completed
evidence yields CONFLICT; there is no majority vote. Confirmations count distinct
verifier families, not repeated runs or versions of the same verifier.

A VerificationTask is immutable and reusable. An ExecutionAttempt is one
particular invocation, with its own identity, version, argv, timing, exit code,
status, verdict, termination reason, and evidence. VerificationResult reconciles
attempts and evaluates requirements; it does not hide unsuccessful attempts.

## Identity and evidence

The semantic key hashes the program's logical paths and content hashes, source
translation-unit list, language, entry, property, semantics and requirements.
Task labels, budgets and tool choices do not change the proposition. Tool version
and configuration belong to provenance and tool-specific artifact validity.
Local source dependencies must be snapshotted or rejected, never read live after
task creation. System headers are part of the recorded verifier environment.

The initial cache is exact. UNKNOWN, CONFLICT, timeout, cancellation and errors
cannot supply definitive cache hits. Evidence must remain linked to its original
execution and input snapshot. Resource enforcement on a portable subprocess
backend is best effort, not a claim of cgroup-level isolation.

## M0 API review

YES: logical records depend only on semantic inputs; execution records explicitly
separate verdict, lifecycle, task and attempt identity. Physical operators belong
in a separate plan module. One adapter protocol and one optimizer interface are
sufficient; no plugin framework or speculative proof-reuse abstraction is needed.

## M1 review

YES: strict schema rejects physical directives; the parser needs no verifier.
Program content is captured before execution; local quoted headers participate
in identity, while macro/absolute/symlink includes are rejected when replay is
unsupported. The IR supports multiple translation units. `memory_safety` is a
valid proposition but must be filtered out by adapters lacking that capability.

## M2 review

YES: tool-specific flags and result interpretation live only in adapters; the
registry performs capability lookup. Actual command, version, output, and exit
code were retained during smoke verification. Installed backends run real tests;
missing backends explicitly skip only integration tests. No runtime fake backend
or DSL tool directive was introduced.

## Execution lifecycle

```mermaid
stateDiagram-v2
    [*] --> Pending
    Pending --> START_FAILED: preparation or spawn error
    Pending --> Running: compatible tool and budget admission
    Running --> COMPLETED: normal recognized termination
    Running --> ERROR: abnormal exit or malformed result
    Running --> TIMEOUT: wall deadline
    Running --> OOM: aggregate sampled RSS exceeds budget
    Running --> CANCELLED: user cancellation or requirements satisfied
    COMPLETED --> Reconcile
    ERROR --> Reconcile
    TIMEOUT --> Reconcile
    OOM --> Reconcile
    CANCELLED --> Reconcile
    START_FAILED --> Reconcile
    Reconcile --> [*]
```

Sequence falls back until requirements are met or budget is exhausted. Parallel
starts backend executions, subject to one global concurrency bound even in nested
plans. Every batch of completed attempts is reconciled before early cancellation.
For one confirmation, an unfinished peer may be cancelled; only observed completed
evidence can be checked for conflict. Requiring two confirmations keeps peers
running. A conflict overrides all counts.

POSIX processes have independent sessions; termination signals the whole process
group, escalates to SIGKILL, and reaps the direct process. A process that deliberately
escapes the group is outside this portable backend's isolation guarantees. Sampled
RSS includes active verifier trees, but can miss short spikes, does not limit virtual
memory or CPU, and is not strict memory isolation. No BenchExec/cgroup claims apply.

## M3 review

YES: physical operators are typed AST nodes; the interpreter depends on adapter
interfaces, never tool names. Cancellation and failures yield UNKNOWN attempts,
and reconciliation counts distinct families. Tests exercise real concurrency and
process-group cleanup, and a real two-verifier SAFE cross-check. Raw inputs, plans,
events, timing and attempt records are retained before the SQLite layer arrives.

## Physical optimizer and workflow scheduler

`optimize(LogicalPlan, RuntimeContext) -> OptimizationResult` produces one physical
plan per fixed goal. Candidates are filtered by availability, language, property,
C standard/data model and memory estimate. The initial cost heuristic ranks by a
smoothed definitive rate divided by historical median time, with version/config
scoped priors. Memory estimates and max_parallel pack candidates into parallel
stages; sequential fallback stages receive time slices under the global deadline.
Infeasible trust requirements remain explicit and yield UNKNOWN, never a lowered
confirmation count. Hints are recorded and currently ignored.

`VerificationService.verify(VerificationWorkflow | VerificationTask)` is the future
LLM boundary. The thin DAG scheduler executes ready goals sequentially and checks
explicit required predecessor results. Each ready goal is independently optimized
and executed via `Runtime.execute_goal`. Blocked successors receive structured
`dependency_not_satisfied` feedback. No workflow-level theorem verdict is inferred.

## M4 review

YES: optimizer replacements need only the optimize interface. Tool adapters do
not receive semantic planning authority. Real history is persisted in SQLite and
influences candidate ordering. Plans honor memory estimates, concurrency and
confirmation opportunity; runtime enforces the shared wall deadline and records
uncertainty. Single goals and explicit control DAGs use the same per-goal engine.

## Per-goal cache validity and provenance

SQLite persists workflows, logical tasks, executions with both plans and optimizer
decisions, attempts, results, runtime statistics, artifact references and cache
entries. Raw stdout/stderr, argv/environment, input snapshots, events and result
JSON are content-addressed immutable files. A cache hit records a new execution
with zero attempts and a link to the source execution; history is never fabricated.

Cache validity requires exact semantic goal identity (including requirements),
a completed definitive source result, enough matching distinct verifier families,
nonempty version/config provenance, no opposing completed attempt, and all original
artifact hashes still intact. Unknowns, conflicts and unsuccessful attempts never
contribute cache evidence. Labels, hints, budgets and physical plan identity are
excluded from the semantic key. Tool-specific artifacts record version/config;
they are stored as evidence and are not reused as solver inputs across versions.
The cache format carries its own contract version and must be advanced if evidence
interpretation changes. Workflow-level caching and partial confirmation reuse are
not implemented. Clearing cache deletes selected entries only, not artifacts/history.
Opposing completed evidence in the same exact goal's history also yields CONFLICT
and evicts a previous definitive cache entry. Clearing cache cannot erase this
recorded disagreement; it remains auditable in execution history.
Cache revalidation also compares stored logical/result metadata and per-attempt
provenance against their immutable JSON artifacts, so a metadata count or family
change cannot fabricate a confirmation. M7 advanced the cache contract to v2 after
qualifying ESBMC's unwinding property-table classification.

## M5 review

YES: the physical optimizer emits CacheLookup with an auditable fallback; execution
revalidates evidence before returning a hit. Cache-disabled operation remains valid.
Source execution and all evidence stay linked to the immutable logical snapshot.
Tests verify zero verifier starts on a hit, strict requirements, corruption misses,
conflict exclusion and scoped deletion.

## EXPLAIN and structured feedback

EXPLAIN separates the supplied Logical Workflow from optimized Physical Execution
for each goal. Candidate filtering, scores, history, hints, resource packing, cache
state and fallback plans are machine-readable. `--analyze` executes and presents
attempts, verdict/status, timing, versions, termination, artifacts and source
provenance. An explain-only call launches no verifier execution (version/help
discovery probes may still run). Goals with dependencies are optimized again when
ready because history/cache can change while predecessors execute.

EventLog emits structured append-only JSONL independently of the CLI renderer.
SQLite `history` and `show` expose real execution records; cache-hit show also
includes original source provenance. Unknown results provide codes for filtering,
timeouts, insufficient unfolding, memory limits, errors and unmet dependencies.

## M6 review

YES: user-facing rendering is separate from structured internal events; EXPLAIN
does not conflate the supplied logical workflow and generated physical plans.
The demo asserts a real UNSAFE miss followed by a hit with zero attempts. Demo
cache clearing is scoped to known example keys and retains all other data.

## Adapter contract and compilation layer

An adapter detects/version-probes its real CLI, declares capabilities, accepts a
fixed task, materializes its snapshot, builds argv without shell=True, parses
recognized terminal records and collects evidence. It has no permission to select
other goals, create assumptions or interpret a predecessor as a proof of its input.
Equivalent syntax/property elaboration is permitted: CPAchecker's assertion macro
and reachability specification preserve the same assertion obligation. Generated
inputs/configs and preprocessing/verifier argv are retained as artifacts.

Adding Ultimate requires an adapter/profile and registration, not a DSL change.
A learned optimizer replaces the optimize interface without modifying adapters.
The service remains operational with no LLM, with cache disabled, or with one
compatible verifier. An upstream planner can submit new workflows after UNKNOWN;
M8 adds an optional Codex-backed upstream planner. The runtime itself does not call
an LLM, and an HTTP server remains unimplemented.

## M7 final architecture review

YES: single-task shorthand and multi-goal control DAGs share one fixed-goal API.
The optimizer never creates subgoals. No runtime/optimizer/cache code branches on
backend names, and test fixtures are confined to tests. Three genuine tools were
qualified with real safe/unsafe runs and multi-source examples. Cross-checks require
two distinct matching families. Opposing completed evidence returns CONFLICT.
Verdict/status and task/attempt remain separate. All real launches record versions,
outer argv, and any compilation-layer argv. Exact cache validation excludes
incomplete evidence and requires intact, consistent provenance. The actual demo
and three-family mini benchmark verify the intended semantic-transparent behavior.

## M8 upstream semantic planner and bounded experiments

`veriruntime/planner/` composes the public VerificationService API; runtime,
optimizer, cache and adapters do not import or invoke it. CodexPlanner manages a
real CLI process, stdout JSONL/final JSON, usage, deadlines, cancellation and group
cleanup. ExperimentRunner owns explicit planning rounds, candidate validation,
feedback handoff and bounded stopping. Neither model output nor rationale is
treated as verifier evidence.

The bridge starts with existing fixed goals as an input manifest. Captured files
are copied into a stable allowlisted namespace. Every generated goal must keep
one complete supplied source set, entry, property and C semantics; each input is
covered once. Confirmation requirements cannot decrease; caller budgets cannot
increase. The versioned workflow DSL validates the actual submitted document,
including its DAG. Unsupported semantic edits must be authored as new explicit
inputs outside this first bridge. Logical dependencies are proposed upstream and
recorded; the service only obeys them.

After unresolved results a new, recorded Codex invocation receives goal_id,
status, verdict, confirmations, diagnostics, artifacts and optimizer summaries.
An unchanged workflow is stopped before another verification; CONFLICT is returned
for review, and bounded rounds prevent an unending replanning loop. Model failure,
malformed output or an invalid workflow starts no verification for that proposal.
Completed refers to satisfying submitted goal trust requirements, not proving that
the user request has been formalized correctly. Natural-language interpretation
and unbounded invariant/decomposition generation remain research scope.

The configured sol default is selected without changing user settings or storing
credentials. CLI per-call overrides disable tools/plugins/user MCP, and the child
uses a read-only sandbox; unexpected model tool actions reject the proposal. Only
the host runtime starts actual verifier commands. Experiment records and Codex
messages are separate from semantic proof/cache evidence. See
[llm-experiments.md](llm-experiments.md) for real acceptance and limits.
