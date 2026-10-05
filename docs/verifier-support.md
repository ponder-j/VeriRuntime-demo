# Real verifier support

The development host is macOS ARM64. CBMC 6.11.0 and ESBMC 8.5.0 have both
verified the actual safe and unsafe assertion examples. These are independent
verifier families for the confirmation policy (distinct current implementations
and solving engines); ESBMC originated as an old CBMC fork, so this is not a
claim of mathematically independent proof trust.

`scripts/bootstrap_verifiers.sh` installs pinned, hash-checked packages in
`.veriruntime/toolchains/`. It uses the official ESBMC release and Homebrew bottles
for CBMC and native libraries. The bootstrap currently targets macOS ARM64 Tahoe;
Linux users can install official CBMC/ESBMC releases and expose them in PATH or
set `VRUN_CBMC` and `VRUN_ESBMC` to absolute executable paths. No sudo is used.
The adapters also search repository-local and user-local executables.

CBMC uses JSON property records and cProverStatus with validated exit codes.
ESBMC uses the real property table and terminal verification record, which may
be written to stderr. Both enable bounded unfolding with unwinding assertions.
Unwinding failures and non-assertion failures yield UNKNOWN for assertion_safety.
Both support C99/C11 and LP64/ILP32. Memory safety is a valid DSL proposition but
is not yet supported by these profiles, and cannot produce a definitive result.

CPAchecker 4.2.2 has been downloaded and its version/help probed using a local
Bash 5. Its standard configuration needs native SMT libraries not supplied for
macOS ARM64. A Java-solver configuration is under evaluation for the optional
third backend; it is not advertised as a supported verifier until real smoke
tests succeed.

Official sources: [CBMC releases](https://github.com/diffblue/cbmc/releases),
[ESBMC releases](https://github.com/esbmc/esbmc/releases/tag/v8.5),
[Homebrew formulae](https://github.com/Homebrew/homebrew-core),
[CPAchecker installation](https://gitlab.com/sosy-lab/software/cpachecker/-/blob/main/INSTALL.md).
