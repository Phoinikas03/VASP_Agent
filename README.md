# VASP Agent

This repository is the public companion repository for the manuscript:

**VASP Agent: An Agentic Framework for Autonomous First-principles Calculations**

It contains the VASP Agent runtime code, the skill library, the inputs of all
reported experiments, and for each experiment its scripts and results. Raw
VASP outputs and agent session logs are available from the corresponding
authors upon reasonable request.

## Repository Layout

```text
main.py             CLI/Web entry point
batch_runner.py     Batch runner for the structural-relaxation and band-gap tasks
src/                Agent runtime, tools, scheduler, and LiteLLM bridge
webui/              Local web interface
tools/              Repository export helper
.claude/skills/     VASP Agent skill library
data/               Experiment inputs: task tables, initial structures, reference values
experiments/        One directory per experiment: scripts and results
```

## Experiments

`experiments/README.md` maps every experiment to its manuscript tables and
figures and to the scripts that regenerate its results. Experiments 01–05 were
reported in the original submission; experiments 06–09 were added in the
revision:

| Directory | Experiment |
| --- | --- |
| `01_structural_relaxation` | 40 relaxations vs. LLM-based workflow baselines |
| `02_bandgap` | 24 PBE-to-HSE band gaps vs. LLM-based workflow baselines |
| `03_sol27lc_dreams` | Sol27LC lattice constants vs. DREAMS |
| `04_co_pt111_adsorption` | CO/Pt(111) site preference |
| `05_failure_recovery` | Failure taxonomy and LiFePO4 recovery case |
| `06_sol27lc_deepseek_v4_repeats` | Three repeated Sol27LC runs with DeepSeek v4 |
| `07_atomate2_comparison` | Comparison with atomate2 and custodian |
| `08_skill_ablation` | Sol27LC with and without domain skills |
| `09_mgo_phonon` | MgO phonon workflow and generation of `workflow-phonon` |

## Installation

```bash
conda create -n vasp-agent python=3.10 -y
conda activate vasp-agent
pip install -U pip
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` for the model endpoint and local scientific resources. VASP
pseudopotentials are not redistributed; set `PMG_VASP_PSP_DIR` to a local
licensed POTCAR library before running VASP tasks.

## Running Tasks

List the structural-relaxation and band-gap tasks:

```bash
python batch_runner.py --tasks relax bandgap --dry-run
```

Run selected tasks after configuring the model endpoint, VASP executable, and
POTCAR path:

```bash
python batch_runner.py --tasks relax --materials Al AlN
python batch_runner.py --tasks bandgap --materials Si GaN
```

Interactive runs can be launched with:

```bash
python main.py --mode web
```

Drivers for the other experiments are documented in the corresponding
`experiments/` directories.

