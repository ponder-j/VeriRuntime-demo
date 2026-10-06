# Milestone reviews

Existing repository: empty directory without Git history. Git was initialized;
the existing configured user identity was retained. No reset, cleanup, force push,
rebase or commit rewriting was performed. The workflow architecture adjustment
arrived during M3 and was integrated in M3–M7 without modifying M0–M2 history.

| Milestone | Reviewed result | Automated checks and real smoke |
|---|---|---|
| M0 | Semantic contracts, records and WHAT/HOW boundaries | 2 tests; no verifier required |
| M1 | Strict JSON schema, immutable multi-source snapshot, semantic identity | 18 tests; validate and canonical parse |
| M2 | Real registry, CBMC/ESBMC adapters, pinned local bootstrap | 28 tests; both tools actually returned SAFE and UNSAFE |
| M3 | Process-group supervision, physical AST, thin workflow IR and diagnostics | 45 tests; timeout/cancellation/cleanup/concurrency plus real two-family proof |
| M4 | Per-goal cost heuristic, SQLite history, explicit DAG control scheduling | 49 tests; automatic real safe/unsafe portfolios |
| M5 | Persistent executions/artifacts, exact cache, evidence checks | 56 tests; real miss followed by zero-verifier hit |
| M6 | Two-level EXPLAIN/ANALYZE, structured events, history/show and acceptance script | 59 tests; real demo script passed |
| M7 | Third real verifier, cross-checks, six-case benchmark, final soundness review and docs | Final test counts and evidence in delivery report |
| M8 | Optional upstream Codex planner, validated Workflow export and bounded feedback experiments | Real default-sol DAG, two-family miss/hit and honest UNKNOWN feedback; final checks in llm-experiments.md |

Every milestone was reviewed for module boundaries, DSL tool leakage, logical versus
physical planning, semantic cache validity, fixture isolation, status definitions
and extension points. Tests and real smoke were followed by `git diff --check` and
staged diff inspection before the milestone commit. M2 bootstrap repeatability
repairs were carried forward in M3; existing commits were not rewritten.

The final audit specifically caught ESBMC 8.5's use of `main.assertion.N` for
unwinding assertions. Both real long-loop regression and saved-output regression
now classify these as UNKNOWN/insufficient_unwinding; the cache contract advanced
to prevent reuse of evidence under older interpretation rules. Public Python
goal constructors also reject invalid confirmation requirements, preventing
schema-bypassing clients from weakening the trust contract.

The optional third backend was only registered after actual safe/unsafe execution
succeeded. Failed CPAchecker default MathSAT and SDK-header attempts were replaced
with a documented equivalent assertion elaboration and Princess counterexample
checking, with all generated files retained. No mocked verdict filled this gap.
