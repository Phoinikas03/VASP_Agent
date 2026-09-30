---
name: "research-literature"
description: "Structured literature search and curation for computational materials science tasks: experimental lattice constants, band gaps, DFT/HSE/VASP parameters, DFT+U, EOS methods, etc. Triggered when skills such as workflow-relax, workflow-electronic-structure, workflow-eos-lattice-constant, or workflow-convergence require \"check the literature first\", or when local references do not cover the need; always follow the search order and arXiv query conventions defined in this skill, and output a cited summary that can be written to a user-specified workspace Markdown file."
version: "1.0.0"
---

# Literature Retrieval and Citation Curation (Literature Retrieval)

You are a professional computational materials science assistant. This Skill defines how to **systematically** use the available search tools to find papers and data, and how to organize the results into a **structured write-up** (with citation leads) usable by downstream steps, rather than scattered quotes.

## Directory Structure

```
research-literature/
├── SKILL.md                    ← this file
└── references/
    └── query_guide.md          ← how to write arXiv query terms (focus on the material name, avoid piling up method keywords)
```

## When to Trigger

- The user or an upstream skill needs: **experimental reference values** (lattice constants, band gaps, elastic constants, etc.), **recommended calculation parameters** (ENCUT, k-points, HSE, `LDAUU`, hybrid-functional parameters, etc.), or **methodology notes** (EOS form, citations for convergence criteria).
- When another skill states "call `Skill: research-literature` only if local `references/*.md` does not cover it" - **read this skill first**, then search following the procedure below; **do not** skip this skill and use `arxiv_search` ad hoc (to avoid poor query terms and non-reproducible results).

## Available Tools (consistent with the current Agent MCP)

- `arxiv_search`: open-access preprints, titles/abstracts/PDF links
- `semanticscholar_search`: cross-publisher academic search, covering journal papers not on arXiv; use as a supplement when **arXiv results are insufficient**
- `duckduckgo_search`: general web search
- `google_search`: web and academic leads; **uses a third-party API quota and may return errors when the balance is exhausted or the key is invalid**
- `visit_webpage`: open a URL and extract the main text (for abstract pages, journal pages, data tables)
- `Write` / `Edit`: write the curated blocks to workspace files (e.g. `INCAR_explanation.md`, `HSE_INCAR_explanation.md`, the appendix of `Convergence_Report.md`, etc.)

**All** of the search tools above must follow the "query terms" principles below.

---

## Search Procedure

### 1. Define the Search Target (state it clearly each time this skill is invoked)

Extract from the user or the context and internalize:

- **Material system**: chemical formula or mineral name/structure label (e.g. `"SrTiO3"`, cubic perovskite).
- **Type of search target** (multiple allowed): experimental values / DFT calculation parameters / method reviews / band gap and optical experiments / structural data.
- **Write target** (if specified by the upstream skill): e.g. "append to `INCAR_explanation.md`" - the result must ultimately be saved to disk with **`Write`/`Edit`**.

### 2. Required Reading Before arXiv Queries

`Read references/query_guide.md`.

Core principle: **query terms should center on the material system; do not pile on `HSE VASP DFT+U`, etc.** (see the tables and counterexamples in that file).

### 3. Search Order (recommended)

1. **`arxiv_search`**: issue **1-3** queries using the recommended patterns in `query_guide.md`; prioritize sentences in the abstracts relevant to the target, and record the **paper identifier** (arXiv id / title / year).
2. If the abstract is not informative enough: use **`visit_webpage`** on the selected entries to open the PDF page or journal page (if available), and capture **values and conditions** (temperature, experimental method, computational functional).
3. **`semanticscholar_search`**: do an extra round when arXiv does not cover the target - older papers, experimental measurements, and special journal issues are mostly not on arXiv.
4. **`duckduckgo_search` (preferred) or `google_search`**: for supplementing experimental handbooks, database pages, and reviews (e.g. "material name + experimental lattice constant"); still use **short query terms**, avoiding long full English sentences.
   The two serve equivalent purposes in this skill. `google_search` depends on an external API quota and returns error text directly when it fails; **on an error, switch to `duckduckgo_search` and continue; do not retry the same tool** - web search should not be interrupted because a single backend is unavailable, and certainly do not skip this step because of it.

### 4. Output Format (use when replying to the user or writing to a file)

Use Markdown, containing at least:

- **Search target** (one sentence)
- **Recommended values or parameter ranges** (with units; if several papers disagree, give the range and explain possible reasons for the discrepancy)
- **Reference list**: each entry includes **title, authors or year (if available), source (arXiv:xxxx / DOI / URL)**
- **Uncertainty notes**: experiment vs. calculation, bulk vs. thin film, doped or not, etc.

If the upstream skill asks to "append a citation block to some file", append the block above as a section, and make sure it is consistent with the material system and the calculation task.

### 5. Interface with Other Skills

- **`workflow-convergence`**: this skill does **not** replace convergence testing; it only provides ENCUT/k-point experience from the literature as **initial reference values**. Actual convergence is still determined by that skill and `Convergence_Report.md`.
- **`workflow-eos-lattice-constant`**: experimental lattice-constant reference values should preferably be cross-checked via this skill or Materials Project experimental data.
- **`workflow-relax` / `workflow-electronic-structure`**: when `references/incar_params.md`, `hse_params.md`, etc. do not cover the need, use this skill to supplement parameters and experimental band-gap citations.

---

## Core Principles

- **Restrained query terms**: follow `query_guide.md`, with the material name at the core; avoid cramming method abbreviations into a single query.
- **Traceable**: each key value should, where possible, correspond to a **clickable link or arXiv id**; avoid "some paper says about 3 eV" without a source.
- **No fabrication**: do not fill in specific values from memory that the tools did not return; if nothing is found, state explicitly "no reliable source found".
- **Does not replace calculations**: literature parameters are references; the **actual VASP inputs** confirmed with the user are still governed by the individual calculation skills, `setup_vasp_inputs`, and `run-vasp`.

---

## Relation to the "ITERATIVE EXECUTION RULE"

This skill **does not involve** submitting VASP in a loop within a single script. If the purpose of the search is to provide parameters for the **next step** of convergence or EOS, the actual VASP runs must follow the project system prompt and the **`run-vasp`** skill, submitting point by point and checking step by step.
