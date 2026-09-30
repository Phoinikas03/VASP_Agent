# VASP parallel parameter quick reference

This file is read by the `incar-performance` skill when needed. The values here are **recommended starting points**, not absolute optima.

## 1. CPU-only scenarios

| Scenario | Recommended start | Notes |
|---|---|---|
| Single node, up to 8 cores | `NCORE = 2` or `4` | avoid over-splitting small jobs |
| Single node, 16–32 cores | `NCORE = 4` or `8` | most common safe default |
| Single node, 48–64 cores | `NCORE = 8` | common starting point |
| Very high core-count CPU node | `NCORE = 8/12/16` | take socket/NUMA layout into account |

In CPU-only scenarios:

- usually try `NCORE` first
- do not also write `KPAR` by default
- add `KPAR` only if the user explicitly wants k-point parallelization

## 2. GPU scenarios

| Device usage | Recommended `KPAR` | `NCORE` | Notes |
|---|---:|---|---|
| Single job, 1 GPU | `1` | omit | safest default |
| Single job, 2 GPUs | `2` | omit | suits static/HSE runs with many k-points |
| Single job, 4 GPUs | `4` | omit | common multi-GPU benchmark starting point |
| Single job, 8 GPUs | `8` | omit | confirm there are enough k-points |
| Concurrent jobs, 1 GPU each | `1` | omit | do not mistakenly set `KPAR = total GPU count` |

In GPU scenarios:

- default to `KPAR ≈ number of GPUs`
- by default remove or omit `NCORE`
- if an old `INCAR` still has `NCORE` / `NPAR`, it should usually be cleaned up

## 3. Rules of thumb for HSE / hybrid functionals

For expensive jobs such as HSE, PBE0, or EXX:

- on multiple GPUs, try `KPAR = number of GPUs` first
- when benchmarking, fix the other parameters and scan only `KPAR`
- if the user's goal is "performance testing", you may moderately lower `ENCUT` or limit the number of electronic steps after stating in the explanation that this is a benchmark setting
- if the user's goal is "production calculations", do not lower physical accuracy parameters in the name of performance optimization

## 4. When to remove parameters

Old parameters should usually be removed in these cases:

- the user switches from CPU-only to GPU
- the old `INCAR` contains `NCORE`, but a GPU benchmark is now required
- the origin of an old `NPAR` is unknown and there is no clear reason to keep it

## 5. Suggested output template

When you modify an `INCAR` following this skill, prefer an explanation structured like this:

1. User device: e.g. "single job using 4 GPUs"
2. Changes: e.g. "removed `NCORE`, set `KPAR = 4`"
3. Reason: e.g. "GPU jobs parallelize over k-points first; avoids interference from old CPU parallel parameters"
4. Unchanged: e.g. "did not change `ENCUT/KSPACING`, because they affect physical results rather than only performance"
