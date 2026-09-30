---
name: simple-skill-creator
description: >
  Meta-agent for creating, editing, or improving AI Skills. This skill MUST be triggered (do not handle these yourself) when:
  1. The user asks to create a new skill;
  2. The user asks to open the web editor to view or edit a SKILL.md (the only way is to run serve_skill.py; the Agent must not merely describe the content or ask the user to open it themselves);
  3. The user asks to improve an existing skill based on a task trajectory (launch diff_skill.py for item-by-item review);
  4. The user mentions "skill editor", "preview skill", "edit SKILL.md", etc.;
  5. The Agent itself, after finishing a task, decides that the experience should be consolidated - whether or not the task used an existing skill.
  In particular: if no skill covered the task and the Agent completed it by combining existing skills or figuring it out on its own,
  it should consolidate that trajectory into a new skill (Path C). This case is initiated by the Agent proactively; the user does not need to ask.
---

# Agile Skill Creator

> **Important**: If the user asks to "open the web editor" or "view/edit a SKILL.md", **you must directly execute step A4 of Path A (run serve_skill.py)**. Do not merely describe the SKILL.md content and leave the rest to the user.

You are a meta-agent that helps build custom Skills. Three workflows are supported:

- **Path A - New Skill (conversation-driven)**: draft through conversation -> confirm in the web editor -> done
- **Path B - Improve an existing Skill (trajectory-driven)**: analyze the task trajectory -> generate an improved version -> review item by item -> write back
- **Path C - Consolidate a new Skill from a trajectory (self-distillation)**: task finished and no skill covers it -> compress the trajectory ->
  draft a new skill -> review item by item -> write back. **Initiated by the Agent proactively**; this is the core path of self-evolution

How to choose: if the target skill exists, choose B; if it does not exist and you just finished a reusable task, choose C; for pure requirements discussion, choose A.

## Directory Structure

```
simple-skill-creator/
├── SKILL.md
└── scripts/
    ├── quick_validate.py       ← format validation (name/description/frontmatter validity)
    ├── serve_skill.py          ← web editor (edit on the left + preview on the right + save)
    ├── diff_skill.py           ← change reviewer (per-item Accept/Reject + write back)
    └── extract_trajectory.py   ← trajectory compressor (log.jsonl -> summary + failure list)
```

## Where Trajectories Come From

The full execution record of each workspace is in `<workspace>/log.jsonl` (older sessions may still use `log.txt`, which is also readable).
**Do not Read it directly** - a single workspace easily reaches several hundred KB and contains many repeatedly injected SKILL bodies. Compress it first:

```bash
cd "<repo_root>"
python .claude/skills/simple-skill-creator/scripts/extract_trajectory.py \
  <workspace_dir> -o /tmp/traj.md
```

In practice this compresses to 5-13% of the original size. Add `--errors-only` to see only the overview and the failure list, which are the main basis for improving a skill.

---

## Path A: Create a New Skill

### A1. Requirements Exploration

Do not write the SKILL right away. First gather through conversation:
1. What should this Skill enable Claude to do? What are the inputs and expected outputs?
2. When should it be triggered?
3. Does it depend on specific tools, MCP servers, or execution environments?
4. If the requirements are vague, ask 1-2 targeted questions and wait for the user's confirmation before moving on

### A2. Draft SKILL.md (Progressive Loading Structure)

**Metadata (frontmatter)**
- `name`: kebab-case, ≤ 64 characters
- `description`: when to trigger + what it does, ≤ 1024 characters, no `<>`. Be specific so Claude knows exactly when to invoke it

**Core Logic (body, ≤ 500 lines)**
- Write instructions in the imperative; explain the "why" instead of piling up MUST/NEVER
- Define a clear output format (with templates or examples)

**Bundled Resources (loaded on demand)**
- `scripts/`: deterministic scripts (avoid rewriting them every time)
- `references/`: large reference documents
- `templates/`: INCAR templates, document templates, etc.

### A3. Format Validation

After writing the draft to a file, first quickly validate that the format is legal:

```bash
python scripts/quick_validate.py <skill_dir>
```

If it outputs a `✗` error, fix it according to the message (common problems: name contains uppercase letters or spaces, description exceeds 1024 characters, frontmatter contains disallowed fields). Launch the editor only after validation passes.

### A4. Launch the Web Editor

After validation passes, **you must actually call the Bash tool to run the following commands; do not assume they have run or claim the editor is up without doing so**.

`BASE_DIR` comes from the first line shown when the Skill is loaded: `Base directory for this skill: ...`.

```bash
BASE_DIR=<Base directory read from the load message>
TARGET_SKILL_DIR=<target skill directory path>
nohup python "$BASE_DIR/scripts/serve_skill.py" "$TARGET_SKILL_DIR" > /tmp/skill_view.log 2>&1 &
sleep 2 && grep '^URL=' /tmp/skill_view.log
```

**You must verify**: if grep prints output (e.g. `URL=http://...`), the launch succeeded; give that URL to the user. If the output is empty, the launch failed; run `cat /tmp/skill_view.log` to diagnose and retry.

Explain to the user:
- Markdown can be edited directly on the left, with a live preview on the right
- **Ctrl+S** or clicking "Save" writes back to the file
- When done editing, tell the Agent, which can then read the latest version

> **SSH remote users**: first set up local port forwarding
> `ssh -L 8700:localhost:8700 user@server`, then visit `http://localhost:8700`

Wait for user feedback, make adjustments based on the written feedback, then ask whether testing is needed. If not, skip to A5.

### A5. (Optional) Testing

If the user wants to test: ask the user for a real test scenario, execute it following the SKILL instructions, show the output to the user, collect feedback, and repeat until satisfied.

### A6. Wrap-up

```bash
# Stop the editor service
kill $(grep '^PID=' /tmp/skill_view.log | cut -d= -f2) 2>/dev/null
```

Tell the user where the final SKILL is located.

---

## Path B: Improve an Existing Skill Based on a Task Trajectory

Use case: the user completed a task with some Skill and wants to add the problems encountered, workarounds, and caveats from that run into the Skill.

### B1. Read the Old SKILL and the Trajectory

```bash
SKILL_DIR=<skill directory path>
TRAJ_FILE=<trajectory file path>   # e.g. logs/20260306_192442.txt
```

Read `$SKILL_DIR/SKILL.md` and the contents of the trajectory file.

### B2. Analyze the Trajectory and Generate an Improved SKILL

Focus on:
- **Errors and retries**: Which steps failed? Why? How can they be avoided?
- **Implicit knowledge**: things the Agent "discovered" during execution that the original SKILL did not state
- **Redundant steps**: things the Agent has to do from scratch every time; can they be abstracted into a script in `scripts/`?

Improvement principles:
- Generalize rather than overfit (do not target the specific values of this one task)
- Be concise: remove instructions that had no actual effect during execution
- Explain the reasons: turn "lessons learned the hard way" into a "why" the Skill can understand

Snapshot the old version and write the improved version to SKILL.md:

```bash
SNAPSHOT=/tmp/SKILL_snapshot_$(date +%s).md
cp "$SKILL_DIR/SKILL.md" "$SNAPSHOT"
echo "Snapshot: $SNAPSHOT"
# Then write the new version to $SKILL_DIR/SKILL.md
```

### B3. Launch the Change Reviewer

**You must actually call the Bash tool to run the following commands; do not assume they have run or claim the reviewer is up without doing so**.

`BASE_DIR` comes from the first line shown when the Skill is loaded: `Base directory for this skill: ...`.

```bash
BASE_DIR=<Base directory read from the load message>
nohup python "$BASE_DIR/scripts/diff_skill.py" \
  --old "$SNAPSHOT" \
  --new "$SKILL_DIR/SKILL.md" \
  --trajectory "$TRAJ_FILE" \
  > /tmp/skill_diff.log 2>&1 &
sleep 2 && grep '^URL=' /tmp/skill_diff.log
```

**You must verify**: if grep prints output, the launch succeeded; if the output is empty, run `cat /tmp/skill_diff.log` to diagnose and retry.

Give the URL to the user and explain how to use the review interface:
- **📝 Change Review** tab: view changes one by one (green = added, red = deleted)
  - Each change defaults to "Accept" (green frame); click "✗ Reject" to reject it
  - Click "Apply accepted changes" to write the result back to SKILL.md
- **📜 Execution Trajectory** tab: view the full execution process to help judge whether a change is reasonable

Wait for the user to finish reviewing and click Apply.

### B4. Confirm the Result

After the user applies, read the latest `$SKILL_DIR/SKILL.md` to confirm it was written correctly:

```bash
kill $(grep '^PID=' /tmp/skill_diff.log | cut -d= -f2) 2>/dev/null
```

Report an improvement summary to the user: how many changes were accepted, how many rejected, and the final SKILL file location.

---

## Path C: Consolidate a New Skill from an Execution Trajectory (Self-Distillation)

Use case: you just completed a task, and **no existing skill covers it** - you did it by combining existing skills,
consulting the literature, or figuring it out yourself. If the knowledge in this trajectory is not consolidated, it disappears with the session.

You decide when to trigger this; the user does not need to ask. Do it only if **all** of the following conditions hold:

1. The task is truly finished and produced a valid result (not abandoned midway, and not still running);
2. None of the skills in the existing skill list covers this kind of task;
3. This kind of task will recur - consolidating it is useful in the future, not a one-off ad-hoc request.

If the conditions are not met, do not do it. Building a skill for a one-off task only pollutes the skill library.

### C1. Compress the Trajectory

```bash
cd "<repo_root>"
python .claude/skills/simple-skill-creator/scripts/extract_trajectory.py \
  <current_workspace> -o /tmp/traj_$(date +%s).md
```

Note down the output file path; you will need it in C4.

### C2. Distill from the Trajectory

Read through the summary and focus on answering four questions; the answers form the body of the new skill:

- **What is the stable procedure**: which steps are needed every time for this kind of task? What are their order and dependencies?
- **What pitfalls were hit**: look at the "Failure list". Each failure corresponds to a preventive instruction that should go into the skill,
  with an explanation of the reason - writing the "why" is more useful than piling up MUST/NEVER.
- **Which steps should be fixed as scripts**: if you wrote some piece of logic on the spot and would have to rewrite it next time,
  put it into the new skill's `scripts/`; do not count on improvising it again next time.
- **Which existing skills it depends on**: state explicitly in the body "this skill must load Y before executing X",
  consistent with how the repository's existing workflow skills are written.

**Generalize, do not overfit**: the specific material names, paths, and parameter values in the trajectory are incidental to this one run.
Write down how to do this class of task, not a log of this particular run.

### C3. Draft and Validate the Format

Write `SKILL.md` under `.claude/skills/<new_name>/`, then:

```bash
python .claude/skills/simple-skill-creator/scripts/quick_validate.py .claude/skills/<new_name>
```

VASP / materials-computation skills must also satisfy requirements such as the ITERATIVE EXECUTION RULE in "General Writing Guidelines" at the end of this document.

### C4. Item-by-Item Review

For a new skill, **omit `--old`**; the entire content is presented as additions (green) for per-item Accept/Reject:

```bash
BASE_DIR=<Base directory read from the Skill load message>
nohup python "$BASE_DIR/scripts/diff_skill.py" \
  --new .claude/skills/<new_name>/SKILL.md \
  --trajectory /tmp/traj_<the one you noted in C1>.md \
  > /tmp/skill_new.log 2>&1 &
sleep 2 && grep '^URL=' /tmp/skill_new.log
```

**You must verify**: the launch succeeded only if grep prints output; if empty, run `cat /tmp/skill_new.log` to diagnose and retry.
Give the URL to the user, and explain that the "📜 Execution Trajectory" tab lets them check each instruction against the original execution to judge whether it holds up.

### C5. Wrap-up

After the user applies, read `SKILL.md` back to confirm, then stop the service:

```bash
kill $(grep '^PID=' /tmp/skill_new.log | cut -d= -f2) 2>/dev/null
```

Report to the user: the location of the new skill, what tasks it covers, and which lessons from the trajectory it absorbed.

> **When a new skill takes effect**: the skill list is fixed at session start. A newly written skill usually does not appear
> in the invocable list until the next session. Mention this in the report; do not claim it can be invoked immediately in the current session.

---

## General Writing Guidelines

- **Generalizable**: a good Skill handles a class of problems, not just the user's current example
- **The description is the key to triggering**: be specific but not verbose, so Claude can invoke it automatically at the right time
- If several test cases independently produced similar helper scripts, extract them into `scripts/` for unified maintenance
- **VASP / materials-computation Skills**: the body must state the **ITERATIVE EXECUTION RULE** consistent with the project system_prompt (no `for`/`while` loops or monolithic scripts that submit multiple points, stages, or directories of VASP at once; check each step individually before continuing), and align with the probe, **STRICT HARDWARE ALIGNMENT**, and `vasp_runner.py --dirs` batching strategy of the Skill **`run-vasp`**. For wording, refer to the existing "Execution" and "Core Principles" sections of `workflow-relax`, `workflow-electronic-structure`, `workflow-eos-lattice-constant`, `research-literature`, etc. in the same repository.
