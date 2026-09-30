#!/usr/bin/env python
"""Band-gap extraction from vasprun.xml, shared by both arms of experiment 07.

Both arms are read the same way, straight from vasprun.xml, so the comparison
does not depend on whether a workflow writes a particular summary file.
"""
from __future__ import annotations

import glob
import gzip
import os
import re
import shutil
import tempfile


def _incar_text(job_dir: str) -> str:
    for name in ("INCAR", "INCAR.gz"):
        path = os.path.join(job_dir, name)
        if os.path.exists(path):
            opener = gzip.open if name.endswith(".gz") else open
            with opener(path, "rt", errors="replace") as fh:
                return fh.read()
    return ""


def is_hybrid(job_dir: str) -> bool:
    return bool(re.search(r"^\s*LHFCALC\s*=\s*\.?T", _incar_text(job_dir), re.M | re.I))


def _vasprun_path(job_dir: str) -> str | None:
    for name in ("vasprun.xml", "vasprun.xml.gz"):
        path = os.path.join(job_dir, name)
        if os.path.exists(path):
            return path
    return None


def read_gap(job_dir: str) -> dict | None:
    """Return gap/VBM/CBM/direct for one finished VASP directory, or None."""
    from pymatgen.io.vasp.outputs import Vasprun

    path = _vasprun_path(job_dir)
    if path is None:
        return None
    tmp = None
    try:
        if path.endswith(".gz"):
            tmp = tempfile.NamedTemporaryFile(suffix=".xml", delete=False)
            with gzip.open(path, "rb") as src:
                shutil.copyfileobj(src, tmp)
            tmp.close()
            path = tmp.name
        run = Vasprun(path, parse_dos=False, parse_projected_eigen=False,
                      parse_potcar_file=False, exception_on_bad_xml=False)
        gap, cbm, vbm, direct = run.eigenvalue_band_properties
        return {
            "gap_eV": float(gap),
            "vbm_eV": float(vbm),
            "cbm_eV": float(cbm),
            "direct": bool(direct),
            "nkpoints": len(run.actual_kpoints),
            "converged_electronic": bool(run.converged_electronic),
            "incar_encut": run.incar.get("ENCUT"),
            "incar_algo": run.incar.get("ALGO"),
            "incar_ismear": run.incar.get("ISMEAR"),
            "incar_aexx": run.incar.get("AEXX"),
            "incar_hfscreen": run.incar.get("HFSCREEN"),
            "incar_ispin": run.incar.get("ISPIN"),
            "hybrid": bool(run.incar.get("LHFCALC")),
        }
    except Exception:
        return None
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass


def hybrid_gap_dirs(root: str) -> list[str]:
    """Every directory under root that ran a hybrid calculation."""
    hits = []
    for path in glob.glob(os.path.join(root, "**", "INCAR*"), recursive=True):
        job_dir = os.path.dirname(path)
        if job_dir in hits or not is_hybrid(job_dir):
            continue
        if _vasprun_path(job_dir):
            hits.append(job_dir)
    return sorted(hits)


def best_hybrid_gap(root: str) -> dict | None:
    """The hybrid gap for a workspace: prefer the densest converged sampling."""
    best = None
    for job_dir in hybrid_gap_dirs(root):
        res = read_gap(job_dir)
        if res is None or not res["converged_electronic"]:
            continue
        res["dir"] = os.path.relpath(job_dir, root)
        if best is None or res["nkpoints"] > best["nkpoints"]:
            best = res
    return best
