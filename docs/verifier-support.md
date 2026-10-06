# Real verifier support

The development host is macOS ARM64 Tahoe. CBMC 6.11.0, ESBMC 8.5.0 and CPAchecker
4.2.2 have all verified the actual safe and unsafe assertion examples. These are independent
verifier families for the confirmation policy (distinct current implementations
and solving engines); ESBMC originated as an old CBMC fork, so this is not a
claim of mathematically independent proof trust.

The Docker Linux amd64 port is also qualified on this Windows host: CBMC 6.11.0,
ESBMC 8.5.0 and CPAchecker 4.2.2 / OpenJDK 21.0.12.1 pass SAFE/UNSAFE, multi-source
inputs and the six-case benchmark. Linux archives and SHA-256 are recorded in
`docker/verifiers-linux.lock.json`. Separate attempt containers isolate native
dependencies, outputs, HOME, temporary files and resource limits. See
[Linux reproduction](linux-docker.md) and [the dispatch explorer](runtime-explorer.html).

`scripts/bootstrap_verifiers.sh` installs pinned, hash-checked packages in
`.veriruntime/toolchains/`. It uses the official ESBMC release and Homebrew bottles
for CBMC and native libraries. The bootstrap currently targets macOS ARM64 Tahoe;
Linux users can install official CBMC/ESBMC releases and expose them in PATH or
set `VRUN_CBMC` and `VRUN_ESBMC` to absolute executable paths. No sudo is used.
The adapters also search repository-local and user-local executables.

| Verifier | Version | Property | Qualified execution |
|---|---|---|---|
| CBMC | 6.11.0 | assertion_safety | SAFE / UNSAFE, loops, multi-source linking, incomplete unfolding |
| ESBMC | 8.5.0 | assertion_safety | SAFE / UNSAFE, loops, multi-source linking, incomplete unfolding |
| CPAchecker | 4.2.2 + Java 24.0.2; Princess 2025-06-25 | assertion_safety | SAFE / confirmed UNSAFE, loops, multi-source linking |

`vrun doctor --json` returns actual detected paths, versions and capabilities.
The local layouts are `cbmc/cbmc/6.11.0/bin/cbmc`, `esbmc/release/bin/esbmc` and
`cpachecker/CPAchecker-4.2.2-unix/bin/cpachecker` underneath `.veriruntime/toolchains/`.

CBMC uses JSON property records and cProverStatus with validated exit codes.
ESBMC uses the real property table and terminal verification record, which may
be written to stderr. Both enable bounded unfolding with unwinding assertions.
Unwinding failures and non-assertion failures yield UNKNOWN for assertion_safety.
Both support C99/C11 and LP64/ILP32. Memory safety is a valid DSL proposition but
is not yet supported by these profiles, and cannot produce a definitive result.

CPAchecker is optional in bootstrap and registered as a real backend. The Unix
launcher requires Bash 5, installed locally. Attempts with the default native
MathSAT configuration were not usable on this ARM64 host, and the SDK's assert
header exposed parser-incompatible attributes. The final adapter uses value analysis,
rejects ignored unknown calls, and confirms counterexamples with the bundled pure
Java Princess solver. Floating-point theory is explicitly unsupported; no float
approximation is used. Unsupported inputs or configurations return UNKNOWN.

The compilation layer provides a generated standard assert macro that evaluates
its argument once, honors NDEBUG, and routes failure to a fresh private reachability
label. This is an equivalent assertion property elaboration, not a new assumption
or subgoal. A generated specification observes only that label. Both header and
property files are saved as artifacts. A supervised Python driver preprocesses with
clang and invokes the actual CPAchecker command in the same process group. The
outer argv, all preprocessor argv and inner verifier argv are recorded. FALSE is
accepted as UNSAFE only when CPAchecker reports successful counterexample checking.
Raw logs, used configuration, counterexamples and reports are collected.

The six-case benchmark with three confirmations exercised all 18 invocations:
each family agreed on four SAFE and two UNSAFE cases. CPAchecker remains optional
because installing Java/Bash and its configuration-specific limitations should not
prevent using the simpler two-family prototype.

Official sources: [CBMC releases](https://github.com/diffblue/cbmc/releases),
[ESBMC releases](https://github.com/esbmc/esbmc/releases/tag/v8.5),
[Homebrew formulae](https://github.com/Homebrew/homebrew-core),
[CPAchecker installation](https://gitlab.com/sosy-lab/software/cpachecker/-/blob/main/INSTALL.md).
