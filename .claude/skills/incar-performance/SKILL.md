---
name: "incar-performance"
description: "Performance tuning and parallel parameter settings for VASP INCAR files. Trigger when the user mentions performance optimization, parallelization strategy, KPAR, NCORE, NPAR, CPU/GPU resource mapping, single/multiple GPUs, or workstation or node hardware, and wants the INCAR adjusted for the device. Only modifies performance-related INCAR tags; actually running VASP is still handed to run-vasp."
version: "1.0.0"
---

# VASP Performance Tuning Skill

The goal of this skill is not to launch VASP, but to safely adjust the performance-related parameters in `INCAR` according to the user's hardware **before the actual run**, so that the agent does not write `KPAR`, `NCORE`, or `NPAR` from guesswork.

## When it must be used

This skill must be loaded in any of the following situations:

- The user asks to "optimize performance", "run parallel tests", "make it faster", or "adapt the INCAR to the machine"
- The user explicitly mentions `KPAR`, `NCORE`, `NPAR`, `NSIM`, the number of GPUs, or the number of CPU cores
- The user asks for separate recommended settings for CPU-only, single-GPU, multi-GPU, or multi-node scenarios
- An upstream skill needs to rewrite `INCAR` according to the compute device before the production run

This skill is responsible for **rewriting input parameters and explaining the strategy**; actually executing VASP should still be handed to `run-vasp`.

## Mandatory procedure

### 1. Ask about the device first

Before modifying `INCAR`, you must first confirm the device the user actually uses. If the conversation does not state it explicitly, you must ask the user in plain text and wait for the reply.

Confirm at least the core items of the following:

- **CPU-only** or **GPU-accelerated**
- If GPU: how many GPUs
- If CPU: how many CPU cores per job
- Single node or multiple nodes

If the user has already given this information earlier, go directly to the next step; do not ask again.

### 2. Only change performance-related tags

This skill is only responsible for the following kinds of parameters:

- `KPAR`
- `NCORE`
- `NPAR` (only when clearly needed)
- `NSIM` (only when clearly needed)
- a very small number of auxiliary tags that strongly affect performance but do not change physical results

Do not use performance optimization as an excuse to casually change core parameters that affect physical comparability, such as:

- `ENCUT`
- `KSPACING`
- `ISMEAR`
- `SIGMA`
- `AEXX`
- `HFSCREEN`

unless the user explicitly asks for a benchmark or a quick trial run, and you state clearly in your reply that this is a "performance/test setting" rather than a production setting.

### 3. Order of priority

Decide the parallel parameters in this order:

1. First determine **CPU-only** vs **GPU**
2. Then determine single job vs multiple concurrent jobs
3. Then set `KPAR` according to the number of devices
4. Only then consider `NCORE` / `NPAR`

### 4. Explain after modifying

After every `INCAR` change, briefly explain to the user:

- the hardware scenario identified
- which parallel parameters you changed
- why you changed them that way
- which parameters you **deliberately left unchanged**

## Recommended rules

See `references/parallel_settings.md` for the detailed mapping table. In practice, apply the following concise rules first.

### A. CPU-only single node

- Prefer writing `NCORE`
- Usually do not write `KPAR` proactively, unless the user explicitly wants k-point parallelization
- Choose `NCORE` as a natural core block within each NUMA domain or socket, e.g. `4`, `8`, `16`
- Avoid writing `NCORE` and `NPAR` together haphazardly

Rules of thumb:

- `<= 8` cores: `NCORE` may be omitted, or set `NCORE = 2/4`
- `16–32` cores: usually start from `NCORE = 4/8`
- `>= 48` cores: usually start from `NCORE = 8/12/16`

If the user just wants a safe default, prefer:

- small to medium CPU jobs: `NCORE = 4`
- larger single-node CPU jobs: `NCORE = 8`

### B. Single GPU

- Usually write `KPAR = 1` first
- **Do not write `NCORE` by default**
- Avoid keeping old CPU parallel parameters that pollute the GPU job

If `INCAR` already contains old `NCORE` / `NPAR` and the user now explicitly wants a GPU run, these tags should usually be removed, unless the user asks to keep them for comparison.

### C. Single job on multiple GPUs

- Align `KPAR` with the number of GPUs first: `1/2/4/8 ...`
- Usually set `KPAR ≈ number of GPUs`
- **Do not write `NCORE` by default**

But note:

- `KPAR` should not be much larger than the number of k-point groups that can run in parallel
- If the system has very few irreducible k-points, an overly large `KPAR` may bring no benefit
- For HSE, hybrid functionals, and static runs with many k-points, multiple GPUs + `KPAR` are often especially effective

### D. Multiple concurrent jobs occupying multiple GPUs

If the user actually has "several directories each using 1 GPU" rather than "one job using 8 GPUs at once", do not change a single `INCAR` to `KPAR = 8`.

In this case, distinguish clearly:

- **Single job, multiple GPUs**: consider `KPAR = number of GPUs`
- **Multiple concurrent jobs, one GPU each**: each job usually still uses `KPAR = 1`

### E. Multi-node CPU / GPU

In multi-node scenarios, job scripts and `mpirun`/scheduler resource mapping are handled primarily by `run-vasp`.

This skill's principles for multi-node `INCAR`:

- Do not casually increase `KPAR` just because the number of nodes increases
- Do not guess `NCORE` when information is insufficient
- If the user only says "multiple nodes" without giving cores/GPUs per node, keep asking first

## Explicit don'ts

- Do not carry over an old `NCORE` as the default configuration in GPU scenarios
- Do not write `KPAR`, `NCORE`, and `NPAR` together without justification
- Do not mistake "multiple concurrent jobs" for "a single job on multiple GPUs"
- Do not set `KPAR` very large by default just because the machine is large
- Do not conflate performance tuning with convergence of physical parameters

## Working with other skills

- When VASP actually needs to be launched, hand over to `run-vasp`
- Skills such as `workflow-electronic-structure`, `workflow-convergence`, `workflow-relax`, and `workflow-eos-lattice-constant` should use this skill first, then move on to the run stage, when the user explicitly asks to optimize `INCAR` for the hardware
- If the user only asks "how to make it faster" without asking to run, you may only modify/suggest the `INCAR` without calling `run-vasp`

## Required final output

After completing this skill, you should do at least one of the following:

- directly rewrite the `INCAR` in the workspace
- or give an explicit patch showing how the `INCAR` should be rewritten

and include in your reply:

- the identified hardware
- the parallelization strategy adopted
- the key parameters after modification

If information is insufficient, ask the device questions first, stop generating, and wait for the user's reply.
