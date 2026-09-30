#!/usr/bin/env python3
"""Batch VASP Agent runner

Drives ClaudeSDKClient with a multiprocessing pool to run structure relaxation
and band gap calculations for the materials under data/relax_40 and data/bandgap_24
(data/relax and data/bandgap in the development workspace). Each worker is bound to one GPU (CUDA_VISIBLE_DEVICES).

Usage examples:
    python batch_runner.py --gpus 0,1,2,3 --tasks relax bandgap
    python batch_runner.py --data-root data --dry-run
    python batch_runner.py --api-base https://host/v1 --model glm-5   # override the upstream from .env
    python batch_runner.py --gpus 0,1 --tasks relax --materials Al AlN
    python batch_runner.py --dry-run          # list tasks without running them

Environment: the upstream only needs LLM_API_BASE / LLM_API_KEY / LLM_MODEL in .env. At startup the main process
calls resolve_llm_endpoint() to probe it: if the upstream supports the Anthropic protocol it is used directly, otherwise a
protocol bridge is started **inside the main process**; workers connect through the inherited ANTHROPIC_BASE_URL, so the
main process must stay alive until all workers finish (this script joins them anyway).

Note: the worker prompts in this script must also follow the repository conventions:
- Formal VASP submissions go through `python .claude/skills/run-vasp/scripts/vasp_runner.py` first
- Do not steer the agent to run `bash run_vasp.sh` directly or hand-write `mpirun`
"""

from __future__ import annotations

import os
import sys
import json
import time
import asyncio
import argparse
import multiprocessing as mp
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

DATA_ROOT = SCRIPT_DIR / "data"
# Input subdirectory for each task type; take the first one that exists:
# the public repo uses data/relax_40 and data/bandgap_24; the development workspace and legacy data/datasets use relax and bandgap
TASK_SUBDIRS = {
    "relax": ("relax_40", "relax"),
    "bandgap": ("bandgap_24", "bandgap"),
}
RUNS_ROOT = SCRIPT_DIR / "runs"
LOG_DIR = SCRIPT_DIR / "logs" / "batch"
MAX_CONTINUATIONS = 15
TASK_TIMEOUT_SEC = 3600  # 1 hour per task

# ───────────────────────── prompt templates ──────────────────────────

RELAX_PROMPT = """\
Run a structure relaxation calculation for material {material}.

Data directory: {data_dir}
Run directory: {workspace_dir}
The data directory provides the public POSCAR; restricted VASP files are not distributed with the repository.

Steps:
1. Inspect the POSCAR in the data directory
2. Create an INCAR file in the run directory (recommended parameters: IBRION=2, ISIF=3, NSW>=30, EDIFFG=-0.02, choose a suitable ENCUT based on the POTCAR)
3. Call the setup_vasp_inputs tool with the POSCAR from the data directory and the INCAR from the run directory to generate POSCAR, KPOINTS and POTCAR in the run directory; the POTCAR must come from the locally configured PMG_VASP_PSP_DIR or POTCAR_dir
4. Run VASP through `python .claude/skills/run-vasp/scripts/vasp_runner.py`; do not execute `bash run_vasp.sh` and do not hand-write `mpirun`
5. After VASP finishes, check OSZICAR to confirm convergence
6. Extract the final optimized energy and report it

Key requirements:
- You must complete all steps in one go and finally output the optimized total energy value
- Execute directly; do not ask the user questions or wait for confirmation
- If an error occurs, diagnose and fix it, then retry
"""

BANDGAP_PROMPT = """\
Run a band gap calculation for material {material}.

Data directory: {data_dir}
Run directory: {workspace_dir}
The data directory provides the public POSCAR; restricted VASP files are not distributed with the repository.

Steps:
1. Inspect the POSCAR in the data directory
2. Create an INCAR file in the run directory (the HSE06 hybrid functional is required; adjust HFSCREEN, AEXX, ALGO and other parameters appropriately)
3. Call the setup_vasp_inputs tool with the POSCAR from the data directory and the INCAR from the run directory to generate POSCAR, KPOINTS and POTCAR in the run directory; the POTCAR must come from the locally configured PMG_VASP_PSP_DIR or POTCAR_dir
4. Run VASP through `python .claude/skills/run-vasp/scripts/vasp_runner.py`; do not execute `bash run_vasp.sh` and do not hand-write `mpirun`
5. After VASP finishes, check OSZICAR/OUTCAR to confirm the calculation completed normally
6. Extract the band gap value and report it (including the gap value and type)

Key requirements:
- You must complete all steps in one go and finally output the band gap value
- Execute directly; do not ask the user questions or wait for confirmation
- If an error occurs, diagnose and fix it, then retry
"""

CONTINUE_PROMPT = "Continue the calculation task above. Call the tools directly to carry out the operations; do not repeat the plan."


DIGEST_DIR = SCRIPT_DIR / "runs" / ".distill_candidates"


def write_trajectory_digest(
    workspace_dir: Path, *, task_type: str, material: str, info: dict[str, Any]
) -> Path:
    """Compress the trajectory of one batch task into a digest and write it to the candidate pool for later review.

    Unattended batch runs produce the most trajectories, but they cannot go through the interactive skill review flow.
    This only produces material (digest + failure list) and does **not** write to ``.claude/skills/`` automatically:
    writing automatically would let unreviewed content into the skill library.
    """
    skill_scripts = SCRIPT_DIR / ".claude/skills/simple-skill-creator/scripts"
    for path in (SCRIPT_DIR, skill_scripts):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    from extract_trajectory import extract, to_markdown  # type: ignore

    from src.event_log import resolve_log_path

    log_path = resolve_log_path(workspace_dir)
    data = extract(log_path)
    header = (
        f"<!-- task_type={task_type} material={material} "
        f"status={info.get('status')} rounds={info.get('rounds')} -->\n\n"
    )
    DIGEST_DIR.mkdir(parents=True, exist_ok=True)
    out = DIGEST_DIR / f"{task_type}_{material}.md"
    out.write_text(header + to_markdown(data), encoding="utf-8")
    return out


def _build_system_prompt(workspace: str, task_type: str) -> str:
    repo_root = str(SCRIPT_DIR)
    lines = [
        f"Your workspace directory is: {workspace}",
        f"All VASP input/output files should be read from and written to this directory.",
        "",
        f"Repository root (skills and everything under `.claude/`): {repo_root}",
        "The shell cwd for Bash is the workspace above, which is **not** the repository "
        "root. Any Bash that runs a skill script MUST either be prefixed with "
        f'`cd \"{repo_root}\" && ...` or use an absolute path starting with `{repo_root}/`. '
        "A bare relative `.claude/skills/...` path will not resolve.",
        "",
        "CRITICAL RULES:",
        "1. You MUST NOT use the AskUserQuestion tool. Never ask for confirmation.",
        "2. You MUST complete the entire computation in one session — create INCAR, "
        "run VASP, extract results. Do NOT stop after merely describing your plan.",
        "3. Prefer running `python .claude/skills/run-vasp/scripts/vasp_runner.py` "
        "(with the cd/absolute-path rule above) to launch VASP. Do NOT use "
        "`bash run_vasp.sh`, and do NOT hand-write raw `mpirun` commands as the assistant.",
        "4. Generate POTCAR only through `setup_vasp_inputs`. For per-case directories "
        "(EOS volume points, adsorption stages) pass its `work_dir` argument instead of "
        "building POTCAR by hand.",
        "",
    ]
    if task_type == "relax":
        lines.append("Task type: structure relaxation; the goal is to obtain the optimized total energy.")
    else:
        lines.append("Task type: band gap calculation; the goal is to obtain an accurate band gap value.")
    return "\n".join(lines)


# ───────────────────────── single task (async) ───────────────────────

async def run_single_task(
    task_type: str,
    material: str,
    data_dir: Path,
    workspace_dir: Path,
    log_path: Path,
    gpu_id: str,
    max_continuations: int,
) -> dict[str, Any]:
    from dotenv import load_dotenv
    load_dotenv(SCRIPT_DIR / ".env")

    from claude_agent_sdk import (
        create_sdk_mcp_server, ClaudeAgentOptions, ClaudeSDKClient,
        AssistantMessage, ResultMessage, TextBlock,
    )
    from src.tool_wrapper import (
        setup_vasp_inputs_tool,
        duckduckgo_search_tool, google_search_tool,
        visit_webpage_tool, arxiv_search_tool,
    )
    from src.result_message import result_message_indicates_failure

    workspace_dir.mkdir(parents=True, exist_ok=True)
    workspace = str(workspace_dir)
    mcp_name = "vasp_agent"
    mcp_server = create_sdk_mcp_server(
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
        system_prompt=_build_system_prompt(workspace, task_type),
        mcp_servers={mcp_name: mcp_server},
        allowed_tools=[
            "Skill",
            f"mcp__{mcp_name}__setup_vasp_inputs",
            f"mcp__{mcp_name}__duckduckgo_search",
            f"mcp__{mcp_name}__google_search",
            f"mcp__{mcp_name}__visit_webpage",
            f"mcp__{mcp_name}__arxiv_search",
        ],
    )

    template = RELAX_PROMPT if task_type == "relax" else BANDGAP_PROMPT
    initial_prompt = template.format(
        material=material,
        data_dir=data_dir,
        workspace_dir=workspace_dir,
    )

    info: dict[str, Any] = {
        "task_type": task_type,
        "material": material,
        "data_dir": str(data_dir),
        "workspace_dir": str(workspace_dir),
        "gpu": gpu_id,
        "status": "unknown",
        "total_turns": 0,
        "rounds": 0,
        "agent_text_tail": "",
    }

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "w", encoding="utf-8")
    # The structured trajectory goes to the same place as in interactive mode (log.jsonl in the workspace), so extract_trajectory.py
    # and the self-distillation flow also work for batch runs.
    from src.event_log import EventLogWriter

    event_log = EventLogWriter.open_for_workspace(workspace_dir)

    try:
        async with ClaudeSDKClient(options=options) as client:
            prompt = initial_prompt
            ever_had_tool_use = False

            for rnd in range(max_continuations + 1):
                info["rounds"] = rnd + 1
                log_file.write(f"\n{'='*60}\n=== Round {rnd}  prompt={'(initial)' if rnd == 0 else '(continue)'}\n{'='*60}\n")
                if rnd == 0:
                    log_file.write(prompt + "\n\n")
                event_log.append_user_turn(prompt)
                log_file.write(
                    "\n--- Waiting for LLM response (if nothing follows for a long time, check the upstream with scripts/test_env_llm_connection.py) ---\n"
                )
                log_file.flush()

                await client.query(prompt)

                round_tool_use = False
                round_texts: list[str] = []

                async for msg in client.receive_response():
                    log_file.write(repr(msg) + "\n")
                    log_file.flush()
                    event_log.append_sdk_message(msg)

                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, TextBlock):
                                round_texts.append(block.text)
                            elif getattr(block, "type", None) == "tool_use":
                                round_tool_use = True

                    elif isinstance(msg, ResultMessage):
                        info["total_turns"] += msg.num_turns
                        if result_message_indicates_failure(msg):
                            info["status"] = "error"

                if round_tool_use:
                    ever_had_tool_use = True

                last_text = "\n".join(round_texts)
                info["agent_text_tail"] = last_text[-2000:]
                lt = last_text.lower()

                relax_kw = ["total energy", "总能量", "final energy",
                            "optimized total energy", "e0=", "ev"]
                bandgap_kw = ["band gap", "带隙", "bandgap", "band_gap", "ev"]

                has_result_kw = any(
                    k in lt for k in (relax_kw if task_type == "relax" else bandgap_kw)
                )

                # Round 0 counts as done only if there is a tool_use; in later rounds, a keyword hit
                # with no tool_use this round (plain-text repetition) means the task is really done
                done = False
                if has_result_kw and ever_had_tool_use:
                    done = True
                if has_result_kw and rnd > 0 and not round_tool_use:
                    done = True

                if done:
                    info["status"] = "completed"
                    break
                if info["status"] == "error":
                    break
                if rnd >= max_continuations:
                    info["status"] = "max_rounds"
                    break

                prompt = CONTINUE_PROMPT

    except Exception as exc:
        info["status"] = f"exception: {exc}"
        log_file.write(f"\nEXCEPTION: {exc}\n")
    finally:
        log_file.close()
        event_log.close()

    if info["status"] == "unknown":
        info["status"] = "completed"

    # Unattended mode cannot go through the distillation flow of "open a web editor and wait for the user to confirm"; instead the compressed
    # trajectory digest is written to the candidate pool for later batch review. This only produces material and does not write .claude/skills/.
    try:
        info["trajectory_digest"] = str(
            write_trajectory_digest(workspace_dir, task_type=task_type, material=material, info=info)
        )
    except Exception as exc:  # a failed digest must not affect the task result itself
        info["trajectory_digest"] = f"failed: {exc}"

    return info


# ───────────────────────── worker process ────────────────────────────

def _worker_entry(
    gpu_id: str,
    task_queue: mp.Queue,
    result_queue: mp.Queue,
    log_root: Path,
    max_continuations: int,
):
    os.environ["CUDA_VISIBLE_DEVICES"] = gpu_id
    # ANTHROPIC_* is set by the main process's resolve_llm_endpoint() and inherited (direct address or bridge address);
    # do not guess the port here. If it is missing, the main process failed to resolve it; returning early beats letting the SDK hit a 404.
    if not os.environ.get("ANTHROPIC_BASE_URL"):
        print(f"[GPU-{gpu_id}] ANTHROPIC_BASE_URL not inherited; did LLM resolution fail in the main process?", file=sys.stderr, flush=True)
        return
    os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost,0.0.0.0")

    tag = f"GPU-{gpu_id}"
    print(f"[{tag}] worker started", flush=True)

    while True:
        task = task_queue.get()
        if task is None:
            break

        task_type, material, data_dir = task
        label = f"{task_type}/{material}"
        workspace_dir = RUNS_ROOT / task_type / material
        log_path = log_root / f"gpu{gpu_id}" / f"{task_type}_{material}.txt"

        print(f"[{tag}] ▶ {label}", flush=True)
        t0 = time.monotonic()

        info = asyncio.run(
            run_single_task(
                task_type,
                material,
                data_dir,
                workspace_dir,
                log_path,
                gpu_id,
                max_continuations,
            )
        )
        elapsed = time.monotonic() - t0
        info["elapsed_sec"] = round(elapsed, 1)
        result_queue.put(info)

        sym = "✓" if info["status"] == "completed" else "✗"
        print(
            f"[{tag}] {sym} {label}  status={info['status']}  "
            f"turns={info['total_turns']}  rounds={info['rounds']}  "
            f"time={info['elapsed_sec']}s",
            flush=True,
        )

    print(f"[{tag}] worker exited", flush=True)


# ───────────────────────── task discovery ────────────────────────────

def discover_tasks(
    data_root: Path,
    task_types: list[str],
    materials_filter: list[str] | None,
) -> list[tuple[str, str, Path]]:
    tasks = []
    for tt in task_types:
        candidates = [data_root / name for name in TASK_SUBDIRS[tt]]
        task_dir = next((c for c in candidates if c.is_dir()), None)
        if task_dir is None:
            print(f"Warning: directory does not exist {' or '.join(map(str, candidates))}", file=sys.stderr)
            continue
        for d in sorted(task_dir.iterdir()):
            if not d.is_dir():
                continue
            if not (d / "POSCAR").exists():
                continue
            if materials_filter and d.name not in materials_filter:
                continue
            tasks.append((tt, d.name, d))
    return tasks


# ───────────────────────── main ──────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Batch VASP Agent runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  python batch_runner.py --gpus 0,1,2,3\n"
               "  python batch_runner.py --data-root data --dry-run\n"
               "  python batch_runner.py --api-base https://host/v1 --model glm-5\n"
               "  python batch_runner.py --gpus 0 --tasks relax --materials Al AlN\n"
               "  python batch_runner.py --dry-run\n",
    )
    parser.add_argument(
        "--gpus",
        default=os.environ.get("CUDA_VISIBLE_DEVICES", "0"),
        help="list of GPU ids, comma-separated (default: CUDA_VISIBLE_DEVICES or '0')",
    )
    parser.add_argument(
        "--tasks",
        nargs="+",
        choices=["relax", "bandgap"],
        default=["relax", "bandgap"],
        help="task types (default: run both)",
    )
    parser.add_argument(
        "--materials",
        nargs="*",
        default=None,
        help="run only the given material names (default: all)",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=DATA_ROOT,
        help=f"data root directory (default {DATA_ROOT})",
    )
    parser.add_argument(
        "--max-continuations",
        type=int,
        default=MAX_CONTINUATIONS,
        help=f"max automatic continuation rounds per task (default {MAX_CONTINUATIONS})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list tasks only, do not run them",
    )
    parser.add_argument(
        "--api-base",
        default=None,
        help="upstream API address; default: LLM_API_BASE from .env (UPSTREAM_API_BASE also accepted)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="upstream API key; default: LLM_API_KEY from .env (UPSTREAM_API_KEY also accepted)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="model name; default: LLM_MODEL from .env (UPSTREAM_MODEL also accepted)",
    )
    parser.add_argument(
        "--force-litellm",
        action="store_true",
        help="skip the direct-connection probe and force conversion through the in-process protocol bridge (use when the probe misjudges the upstream)",
    )
    args = parser.parse_args()

    data_root = args.data_root
    if not data_root.is_absolute():
        data_root = (SCRIPT_DIR / data_root).resolve()

    gpu_list = [g.strip() for g in args.gpus.split(",") if g.strip()]
    if not gpu_list:
        print("Error: no GPU specified", file=sys.stderr)
        sys.exit(1)

    all_tasks = discover_tasks(data_root, args.tasks, args.materials)
    if not all_tasks:
        print("No matching tasks found", file=sys.stderr)
        sys.exit(1)

    print(f"{len(all_tasks)} tasks, GPUs: [{', '.join(gpu_list)}]  ({len(gpu_list)} workers)")
    for tt, mat, ddir in all_tasks:
        print(f"  {tt:>7s} / {mat:<20s}  {ddir}")

    if args.dry_run:
        print("\n(dry-run mode, not executing)")
        return

    # Resolve before forking workers: with a direct connection each worker talks to the upstream itself; with a bridge they share the one in the main process.
    from dotenv import load_dotenv
    load_dotenv(SCRIPT_DIR / ".env")
    from src.litellm_proxy import resolve_llm_endpoint

    endpoint = resolve_llm_endpoint(
        api_base=args.api_base,
        api_key=args.api_key,
        model=args.model,
        force_litellm=args.force_litellm,
    )
    print(f"[llm] {endpoint.describe()}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_root = LOG_DIR / timestamp
    log_root.mkdir(parents=True, exist_ok=True)

    task_queue: mp.Queue = mp.Queue()
    result_queue: mp.Queue = mp.Queue()

    for t in all_tasks:
        task_queue.put(t)
    for _ in gpu_list:
        task_queue.put(None)

    workers: list[mp.Process] = []
    for gid in gpu_list:
        p = mp.Process(
            target=_worker_entry,
            args=(gid, task_queue, result_queue, log_root, args.max_continuations),
        )
        p.start()
        workers.append(p)

    for p in workers:
        p.join()

    results: list[dict] = []
    while not result_queue.empty():
        results.append(result_queue.get_nowait())

    summary_path = log_root / "summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)

    ok = sum(1 for r in results if r["status"] == "completed")
    err = len(results) - ok
    print(f"\n{'='*60}")
    print(f"All done  succeeded: {ok}  failed/incomplete: {err}  total: {len(results)}")
    print(f"Log directory: {log_root}")
    print(f"Summary file: {summary_path}")

    if err:
        print("\nFailed tasks:")
        for r in results:
            if r["status"] != "completed":
                print(f"  {r['task_type']}/{r['material']}  status={r['status']}")


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    main()
