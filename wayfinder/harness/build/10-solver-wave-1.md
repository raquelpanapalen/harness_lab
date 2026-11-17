# 10: Solver framework and wave 1

**What to build:** Solvers plug into SOLVE: two-part recognition (intake family + preconditions), answer + evidence handles + confidence, partial facts seeding the loop on failure. Wave 1: compromised agents, patient zero, timeline. A solver is enabled only if it beats the loop by Score on its family's development tasks.

**Blocked by:** 05, 06, 09

**Status:** ready-for-agent

- [ ] Per-family Score: solver vs. loop on development runs, recorded
- [ ] Enabled solvers raise the run Score; disabled ones are listed with the reason
- [ ] A forced precondition failure falls back to the loop and still submits

Spec: [SPEC.md](../SPEC.md)
