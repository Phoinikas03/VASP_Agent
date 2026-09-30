# Experiments

One directory per experiment: `scripts/` (drivers and evaluation) and
`results/` (tables).
Structures are in `../data/`. Raw VASP outputs and session logs are available
from the corresponding authors upon reasonable request; `<archive>` below
denotes a local copy of them, with top-level directories named as the ones
here.

| Directory | Manuscript | Supplementary | Regenerate `results/` |
| --- | --- | --- | --- |
| `01_structural_relaxation` | Table 2 | Tables S8, S12 | `scripts/evaluate_relax.py`, `scripts/deviation_evidence.py` with `--raw-dir <archive>/01_structural_relaxation` |
| `02_bandgap` | Table 3 | Tables S9, S12 | `scripts/evaluate_bandgap.py`, `scripts/deviation_evidence.py` with `--raw-dir <archive>/02_bandgap` |
| `03_sol27lc_dreams` | Table 4 | Table S10 | values reported by the agent; DREAMS from arXiv:2507.14267, SI Table 1 |
| `04_co_pt111_adsorption` | Table 5 | Table S11 | `scripts/compute_adsorption.py --raw-dir <archive>/04_co_pt111_adsorption` |
| `05_failure_recovery` | Fig. 3 | Tables S6, S13; Fig. S1 | `scripts/recovery_trace.py --raw-dir <archive>/05_failure_recovery/lifepo4_vasp_agent`; `scripts/generate_rule_based_incars.py` |
| `06_sol27lc_deepseek_v4_repeats` | Table 4 | Tables S23–S24 | `scripts/collect_repeats.py --runs-dir <archive>/06_sol27lc_deepseek_v4_repeats/runs` |
| `07_atomate2_comparison` | Tables 6–7 | Tables S14–S16; Figs. S2–S3 | `scripts/collect_comparison.py` (arguments in its docstring) |
| `08_skill_ablation` | Table 8 | Tables S20–S22; Fig. S5 | `scripts/collect_ablation.py`, `scripts/audit_resources.py`, `scripts/api_usage.py` |
| `09_mgo_phonon` | Fig. 4 | Tables S17–S19; Fig. S4 | `scripts/phonon_frequencies.py --check`; `scripts/extract_evidence.py --raw-dir <archive>/09_mgo_phonon --check` |

Each script's docstring gives its definitions, the raw-output layout it expects,
and its outputs. `common/eos_fit.py` is the equation-of-state fit used for all
lattice constants of 06–08. The drivers that ran 06–08 are
`06/scripts/run_batch.sh`, `07/scripts/run_batch.sh`, and
`08/scripts/run_batch.sh`.

