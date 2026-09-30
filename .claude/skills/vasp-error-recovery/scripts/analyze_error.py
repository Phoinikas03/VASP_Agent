#!/usr/bin/env python3
import argparse
import contextlib
import importlib.util
import io
import json
import os
import sys
import time
from pathlib import Path

STATE_FILE_NAME = ".vasp_run_state.json"


def load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def tail_lines(path: Path, n: int = 40) -> list[str]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-n:]


def file_age_seconds(path: Path) -> float | None:
    if not path.exists():
        return None
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def load_check_convergence_module(repo_root: Path):
    module_path = repo_root / ".claude/skills/run-vasp/scripts/check_convergence.py"
    spec = importlib.util.spec_from_file_location("run_vasp_check_convergence", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load module spec: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def detect_issue(conv: dict, state: dict | None, combined_text: str, stall_seconds: float) -> tuple[str, list[str]]:
    text = combined_text.lower()
    suggestions: list[str] = []

    if "wavecar: reading failed" in text:
        suggestions = [
            "Make sure ENCUT, KSPACING, or KPOINTS in the continuation stage exactly match the previous stage.",
            "If parameters have changed, delete the old WAVECAR, set ISTART to 0, and restart this stage.",
        ]
        return "wavecar_mismatch", suggestions

    if "zbrent" in text:
        suggestions = [
            "First check whether vasprun.xml and OUTCAR have been fully written; if so, this can usually be treated as recoverable or ignorable.",
            "If a rerun is still needed, reduce POTIM, e.g. to 0.1, and consider switching to damped dynamics with IBRION = 3.",
            "Check whether the initial structure is too aggressive; if needed, pre-optimize with more conservative relaxation settings first.",
        ]
        return "zbrent_recoverable", suggestions

    if conv.get("reached_nelm") or "edddav" in text or "sub-space-matrix is not hermitian" in text:
        suggestions = [
            "Increase NELM, e.g. to 100 or 200.",
            "Set ALGO to All or another more robust setting; if needed, tighten/adjust the mixing parameters AMIX and BMIX.",
            "For semiconductors/insulators, check ISMEAR and SIGMA; for metals, SIGMA can be increased moderately.",
            "For relaxations, also check the structure for atoms too close together or abnormal symmetry.",
        ]
        return "scf_not_converged", suggestions

    if "lapack" in text or "zpotrf failed" in text or "allocation failed" in text or "oom" in text:
        suggestions = [
            "Suspect insufficient memory first: reduce k-point density, NBANDS, or concurrency.",
            "For GPU/HSE runs, re-evaluate KPAR, NCORE, PRECFOCK, and the number of GPUs.",
            "If running concurrently on a shared node, reduce the number of simultaneously submitted tasks.",
        ]
        return "memory_or_lapack_failure", suggestions

    if "fatal error" in text or "segmentation fault" in text or conv.get("fatal_error_detected"):
        suggestions = [
            "First use the logs to tell whether this is a numerical, memory, or input-file problem, then decide whether to rerun.",
            "If the current process is still alive, stop the current task first, then modify parameters and rerun.",
        ]
        return "fatal_runtime_error", suggestions

    if "vrhfin" in text or "potcar" in text and "match" in text:
        suggestions = [
            "Check whether the element order in POTCAR exactly matches the element order on line 6 of POSCAR.",
            "If the order is inconsistent, regenerate POTCAR and then resubmit.",
        ]
        return "potcar_order_mismatch", suggestions

    if stall_seconds > 0:
        suggestions = [
            "The current run appears to have produced no new output for a long time; first verify that the log, OUTCAR, and OSZICAR have really stopped updating.",
            "If it is confirmed stuck and the process is still alive, stop it precisely with terminate.py first, then rerun according to the diagnosis.",
        ]
        return "stalled_run", suggestions

    if conv.get("status") == "incomplete_postprocess":
        suggestions = [
            "The electronic steps have most likely finished, but vasprun.xml is incomplete; first check whether OUTCAR and OSZICAR are sufficient for post-processing.",
            "If needed, rerun only the post-processing steps instead of immediately redoing the whole calculation.",
        ]
        return "incomplete_postprocess", suggestions

    if conv.get("status") == "unconverged":
        suggestions = [
            "No clear convergence signal detected; first check NELM, ALGO, ISMEAR, SIGMA, and structure quality.",
            "If it is an ionic-step problem, check NSW, POTIM, IBRION, and whether a continuation run is needed.",
        ]
        return "generic_unconverged", suggestions

    return "unknown", ["No clear pattern matched; manually inspect OUTCAR, OSZICAR, and the end of the main log."]


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze VASP failures/stalls and suggest recovery actions")
    parser.add_argument("--work-dir", type=str, default=".")
    parser.add_argument(
        "--stall-minutes",
        type=float,
        default=30.0,
        help="If the run is still marked running and no fresh output appears for this many minutes, mark as stalled",
    )
    args = parser.parse_args()

    work_dir = Path(args.work_dir).resolve()
    repo_root = Path(__file__).resolve().parents[4]
    state_path = work_dir / STATE_FILE_NAME
    state = load_json(state_path)

    check_module = load_check_convergence_module(repo_root)
    with contextlib.redirect_stdout(io.StringIO()):
        conv = check_module.check_convergence(str(work_dir))

    log_candidates: list[Path] = []
    if state and state.get("log_path"):
        log_candidates.append(Path(state["log_path"]))
    log_candidates.extend(
        [
            work_dir / "vasp.out",
            work_dir / "vasp_run.log",
            work_dir / "vasp_pbe.log",
            work_dir / "vasp_hse.log",
            work_dir / "vasp_relax.log",
            work_dir / "OUTCAR",
            work_dir / "OSZICAR",
        ]
    )

    seen: set[str] = set()
    deduped_logs: list[Path] = []
    for path in log_candidates:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        if path.exists():
            deduped_logs.append(path)

    freshest_age = None
    freshest_path = None
    for path in deduped_logs:
        age = file_age_seconds(path)
        if age is None:
            continue
        if freshest_age is None or age < freshest_age:
            freshest_age = age
            freshest_path = path

    running_status = str((state or {}).get("status", "")).lower()
    stall_seconds = 0.0
    if running_status in {"running", "submitted", "launched"} and freshest_age is not None:
        if freshest_age >= max(0.0, args.stall_minutes) * 60.0:
            stall_seconds = freshest_age

    snippets = []
    combined_chunks = []
    for path in deduped_logs[:4]:
        lines = tail_lines(path, 30)
        snippets.append({"path": str(path), "tail": lines})
        combined_chunks.extend(lines)
    combined_text = "\n".join(combined_chunks)

    issue, suggestions = detect_issue(conv, state, combined_text, stall_seconds)
    recommend_terminate = False
    if running_status in {"running", "submitted", "launched"} and issue in {
        "fatal_runtime_error",
        "memory_or_lapack_failure",
        "wavecar_mismatch",
        "stalled_run",
    }:
        recommend_terminate = True

    result = {
        "workdir": str(work_dir),
        "state_file_found": state is not None,
        "state": state,
        "check_convergence": conv,
        "detected_issue": issue,
        "suggested_actions": suggestions,
        "freshest_output_path": str(freshest_path) if freshest_path else None,
        "freshest_output_age_seconds": freshest_age,
        "stalled_for_seconds": stall_seconds if stall_seconds > 0 else None,
        "recommend_terminate_current_run": recommend_terminate,
        "rerun_requires_termination_first": recommend_terminate,
        "evidence": snippets,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
