#!/usr/bin/env python
"""VASP Agent driver for one Sol27LC system (experiments 06, 07 and 08).

The agent receives an initial POSCAR and the goal, and nothing else: no ENCUT,
k spacing, smearing, pseudopotential variant, or strain window. It may ask
questions; replies come from the operator or,
after a timeout, from the deterministic rules in answer_script.py.

The same driver produced Runs 1-3 of experiment 06. Run 1 is also the
"with domain skills" configuration of experiment 08 and the VASP Agent arm of
experiment 07.

Usage:
    python run_agent_lc.py --system Si_dia --gpu-uuid GPU-xxxx --rep rep1
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXP = HERE.parent
REPO = EXP.parents[1]
STRUCTURES = REPO / "data" / "sol27lc" / "structures"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

DEFAULT_BUDGET_SEC = 6 * 3600
# The model yields in short turns; wall time, not rounds, is the budget.
MAX_ROUNDS = 300

TASK_PROMPT = """\
Please calculate the equilibrium lattice constant of the structure described by
the POSCAR in the working directory.

Working directory: {workspace}
Structure file: {workspace}/POSCAR

Requirements:
- Use the PBE functional.
- Run the lattice_constant workflow **in full**; do not omit any of its steps.
- Finally, report the equilibrium lattice constant explicitly (in Å).

I will not specify any calculation parameters for you (ENCUT, k points,
smearing, pseudopotential variant, volume sampling window, etc.). These
parameters should be determined by the workflow itself, not by directly picking
a safe value from experience.
"""

CONTINUE_PROMPT = (
    "Continue with the calculation task above. Call the tools directly to carry "
    "out the operations; do not restate the plan."
)


def build_system_prompt(workspace: str) -> str:
    return "\n".join(
        [
            f"Your workspace directory is: {workspace}",
            "All VASP input/output files should be read from and written to this directory.",
            "",
            f"Repository root (skills and everything under `.claude/`): {REPO}",
            "The shell cwd for Bash is the workspace above, which is **not** the repository "
            "root. Any Bash that runs a skill script MUST either be prefixed with "
            f'`cd "{REPO}" && ...` or use an absolute path starting with `{REPO}/`.',
            "",
            "CRITICAL RULES:",
            "1. Do NOT use the AskUserQuestion tool. If you need a decision or information "
            "from the user, print the question as plain text and stop generating; the "
            "user will reply in the terminal. This matches the skill instructions for "
            "non-GUI environments.",
            "2. Launch VASP through `python .claude/skills/run-vasp/scripts/vasp_runner.py` "
            "(with the cd/absolute-path rule above). Do NOT hand-write raw `mpirun`.",
            "3. Generate POTCAR only through `setup_vasp_inputs`; for per-volume "
            "subdirectories pass its `work_dir` argument rather than building POTCAR by hand.",
            "4. This task has been allocated exactly one GPU; CUDA_VISIBLE_DEVICES is "
            "already set for you. Use 1 MPI rank bound to that one GPU.",
            "",
            "Task type: equilibrium lattice constant calculation. All calculation "
            "parameters are for you to decide.",
        ]
    )


def get_classifier():
    """Fallback-reply classifier (replaced by the band-gap driver in experiment 07)."""
    from answer_script import classify

    return classify


def looks_finished(workspace: Path) -> bool:
    """Completion is decided by files on disk, never by what the agent says."""
    from check_flow import check

    return check(str(workspace))["verdict"] in ("conformant", "done_with_deviation")


def block_is_tool_use(block) -> bool:
    """The SDK yields ToolUseBlock instances; they carry no ``.type`` attribute."""
    return type(block).__name__ in ("ToolUseBlock", "ToolResultBlock") or (
        getattr(block, "type", None) == "tool_use"
    )


ESCALATION = [
    "",
    "\n\n(Note: this kind of question has already been answered once. Do not ask "
    "again; continue according to your own judgement.)",
    "\n\n(Note: continue the calculation immediately and do not ask similar "
    "questions again.)",
]
STUCK_LIMIT = len(ESCALATION)

ASK_POLL_SEC = 20
ASK_TIMEOUT_SEC = 900  # after this the rule-based fallback answers, so a run
                       # never deadlocks waiting for the operator

UPSTREAM_FAILURE = re.compile(
    r"(hit your session limit|usage limit|rate.?limit|quota exceeded|"
    r"insufficient balance|invalid api key|authentication.?error|"
    r"upstream (error|timeout)|502 bad gateway|503 service unavailable|"
    r"api error:|malformed json|connection error|overloaded)", re.I)

# While the agent waits for VASP it answers every "please continue" with another
# status paragraph; an unchanged workspace therefore buys a wait, not a prompt.
IDLE_BACKOFF_SEC = [30, 60, 120, 240, 300]


def progress_fingerprint(workspace: Path) -> tuple:
    """Cheap signature of how much output exists, to tell work from waiting."""
    total = 0
    count = 0
    for path in workspace.rglob("OUTCAR"):
        try:
            total += path.stat().st_size
        except OSError:
            continue
        count += 1
    return count, total


def awaits_reply(text: str) -> bool:
    """Whether a tool-free round is actually a question for the operator.

    Pure status lines ("Waiting for e_400 to complete") are not escalated.
    """
    tail = text[-600:]
    if "?" in tail:
        return True
    return bool(re.search(
        r"(do you agree|do you confirm|please confirm|confirm (the )?execution|"
        r"should i (run|execute|start|proceed)|please approve|please choose|"
        r"option\s*[ab]\b|please reply|waiting for (your )?reply|shall\s+i|"
        r"do\s+you\s+approve|let\s+me\s+know)", tail, re.I))


def _normalize(text: str) -> str:
    """Collapse a question to a shape that ignores volatile detail."""
    return re.sub(r"[\s\d.,;:!?()\[\]{}\-_/\\]+", "", text)[-400:]


def ask_operator(outdir: Path, round_idx: int, question: str, fallback: str,
                 fallback_category: str, transcript) -> tuple[str, str]:
    """Surface a question to the operator and wait for a written reply.

    If nobody answers within ASK_TIMEOUT_SEC the deterministic reply from
    answer_script.py is used instead, so an unattended run still finishes.
    """
    pending = outdir / "PENDING_QUESTION.md"
    answer_file = outdir / "OPERATOR_ANSWER.txt"
    if answer_file.exists():
        answer_file.unlink()
    pending.write_text(
        f"# round {round_idx}\n"
        f"# suggested category: {fallback_category}\n"
        f"# write the reply into OPERATOR_ANSWER.txt in this directory\n\n"
        f"{question}\n"
    )
    transcript.write(f"\n=== round {round_idx} WAITING FOR OPERATOR ===\n")
    transcript.flush()

    waited = 0
    while waited < ASK_TIMEOUT_SEC:
        if answer_file.exists():
            text = answer_file.read_text().strip()
            if text:
                pending.unlink(missing_ok=True)
                return "operator", text
        time.sleep(ASK_POLL_SEC)
        waited += ASK_POLL_SEC
    pending.unlink(missing_ok=True)
    return f"fallback_timeout:{fallback_category}", fallback


async def run_one(system: str, gpu_uuid: str, outdir: Path, budget_sec: int) -> dict:
    from dotenv import load_dotenv

    load_dotenv(REPO / ".env")
    # The Claude Agent SDK speaks the Anthropic protocol over ANTHROPIC_BASE_URL;
    # resolve_llm_endpoint() points it at the configured upstream (starting an
    # in-process protocol bridge if the upstream needs one).
    from src.litellm_proxy import resolve_llm_endpoint

    endpoint = resolve_llm_endpoint()
    print(f"[llm] {endpoint.describe()}", flush=True)
    os.environ["CUDA_VISIBLE_DEVICES"] = gpu_uuid
    os.environ["OMP_NUM_THREADS"] = "1"

    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        ResultMessage,
        TextBlock,
        create_sdk_mcp_server,
    )
    from src.tool_wrapper import (
        arxiv_search_tool,
        duckduckgo_search_tool,
        google_search_tool,
        setup_vasp_inputs_tool,
        visit_webpage_tool,
    )
    from src.event_log import EventLogWriter

    classify = get_classifier()
    outdir.mkdir(parents=True, exist_ok=True)
    workspace = str(outdir)
    mcp_name = "vasp_agent"
    server = create_sdk_mcp_server(
        name=mcp_name,
        tools=[
            setup_vasp_inputs_tool(workspace),
            duckduckgo_search_tool(),
            google_search_tool(),
            visit_webpage_tool(),
            arxiv_search_tool(),
        ],
    )
    options = ClaudeAgentOptions(
        cwd=workspace,
        model=os.environ.get("CLAUDE_CODE_MODEL") or None,
        setting_sources=["project"],
        permission_mode="bypassPermissions",
        system_prompt=build_system_prompt(workspace),
        mcp_servers={mcp_name: server},
        allowed_tools=[
            "Skill",
            f"mcp__{mcp_name}__setup_vasp_inputs",
            f"mcp__{mcp_name}__duckduckgo_search",
            f"mcp__{mcp_name}__google_search",
            f"mcp__{mcp_name}__visit_webpage",
            f"mcp__{mcp_name}__arxiv_search",
        ],
    )

    info = {
        "system": system,
        "gpu_uuid": gpu_uuid,
        "model": getattr(endpoint, "model", None),
        "llm_mode": getattr(endpoint, "mode", None),
        "llm_base_url": os.environ.get("ANTHROPIC_BASE_URL"),
        "llm_upstream": os.environ.get("LLM_API_BASE"),
        "workspace": workspace,
        "budget_sec": budget_sec,
        "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "rounds": 0,
        "total_turns": 0,
        "status": "unknown",
        "qa": [],
    }
    started = time.time()
    event_log = EventLogWriter.open_for_workspace(outdir)
    transcript = open(outdir / "driver.log", "w", encoding="utf-8")

    try:
        async with ClaudeSDKClient(options=options) as client:
            prompt = TASK_PROMPT.format(workspace=workspace)
            asked_shapes: list[tuple[str, str]] = []
            last_fingerprint = progress_fingerprint(outdir)
            idle_rounds = 0
            for rnd in range(MAX_ROUNDS):
                info["rounds"] = rnd + 1
                if time.time() - started > budget_sec:
                    info["status"] = "budget_exceeded"
                    break

                transcript.write(f"\n{'='*70}\n=== round {rnd} USER ===\n{prompt}\n")
                transcript.flush()
                event_log.append_user_turn(prompt)
                await client.query(prompt)

                texts, had_tool_use = [], False
                async for msg in client.receive_response():
                    event_log.append_sdk_message(msg)
                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, TextBlock):
                                texts.append(block.text)
                            elif block_is_tool_use(block):
                                had_tool_use = True
                    elif isinstance(msg, ResultMessage):
                        info["total_turns"] += msg.num_turns

                last_text = "\n".join(texts)
                if not had_tool_use and UPSTREAM_FAILURE.search(last_text):
                    info["status"] = "upstream_failure"
                    info["upstream_message"] = last_text.strip()[:300]
                    transcript.write(f"\n=== round {rnd} ABORT: upstream failure ===\n{last_text[:300]}\n")
                    break
                transcript.write(f"\n=== round {rnd} AGENT (tool_use={had_tool_use}) ===\n{last_text}\n")
                transcript.flush()

                if looks_finished(outdir):
                    info["status"] = "completed"
                    break

                # No tool call this round means the agent stopped to ask something.
                if not had_tool_use and awaits_reply(last_text):
                    category, answer = classify(last_text)
                    shape = (category, _normalize(last_text))
                    repeats = sum(1 for s_ in asked_shapes if s_ == shape)
                    asked_shapes.append(shape)
                    if repeats >= STUCK_LIMIT:
                        info["status"] = "stuck_qa_loop"
                        info["stuck_category"] = category
                        transcript.write(
                            f"\n=== round {rnd} ABORT: same question asked "
                            f"{repeats + 1}x [{category}] ===\n"
                        )
                        break
                    rule_answer = answer + ESCALATION[repeats]
                    source, answer = ask_operator(
                        outdir, rnd, last_text, rule_answer, category, transcript
                    )
                    info["qa"].append(
                        {"round": rnd, "category": category, "repeats": repeats,
                         "answered_by": source,
                         "question": last_text[-1200:], "answer": answer}
                    )
                    transcript.write(
                        f"\n=== round {rnd} REPLY [{category}] by={source} "
                        f"repeat={repeats} ===\n{answer}\n"
                    )
                    prompt = answer
                else:
                    fingerprint = progress_fingerprint(outdir)
                    if fingerprint == last_fingerprint:
                        idle_rounds += 1
                        wait = IDLE_BACKOFF_SEC[min(idle_rounds - 1, len(IDLE_BACKOFF_SEC) - 1)]
                        transcript.write(
                            f"\n=== round {rnd} NO PROGRESS (idle {idle_rounds}), "
                            f"waiting {wait}s before prompting again ===\n"
                        )
                        transcript.flush()
                        time.sleep(wait)
                    else:
                        idle_rounds = 0
                        last_fingerprint = fingerprint
                    prompt = CONTINUE_PROMPT
            else:
                info["status"] = "max_rounds"
    except Exception as exc:  # noqa: BLE001 - recorded, not swallowed silently
        info["status"] = f"exception: {type(exc).__name__}: {exc}"
        transcript.write(f"\nEXCEPTION: {exc}\n")
    finally:
        transcript.close()
        event_log.close()

    info["wall_seconds"] = round(time.time() - started, 1)
    info["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    (outdir / "run_meta.json").write_text(json.dumps(info, indent=2, ensure_ascii=False))
    return info


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--system", required=True, help="e.g. Si_dia (a directory under data/sol27lc/structures)")
    parser.add_argument("--gpu-uuid", required=True, help="GPU UUID from nvidia-smi -L")
    parser.add_argument("--rep", default="rep1")
    parser.add_argument("--runs-dir", type=Path, default=EXP / "runs",
                        help="output root; each run is written to <runs-dir>/<rep>/<system>")
    parser.add_argument("--budget-sec", type=int, default=DEFAULT_BUDGET_SEC)
    args = parser.parse_args()

    outdir = args.runs_dir / args.rep / args.system
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "POSCAR").write_text((STRUCTURES / args.system / "POSCAR").read_text())

    info = asyncio.run(run_one(args.system, args.gpu_uuid, outdir, args.budget_sec))
    print(json.dumps(info, indent=2, ensure_ascii=False)[:2000])


if __name__ == "__main__":
    main()
