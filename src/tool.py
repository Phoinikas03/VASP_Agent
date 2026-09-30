import ast
import json
import os
import asyncio
import time
from pathlib import Path
from typing import Dict, Any, Optional

def _ok(text: str) -> Dict[str, Any]:
    """Success return. Empty results (e.g. "no papers found") also go through here -- that is a legitimate result, not a failure."""
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> Dict[str, Any]:
    """Failure return.

    Must carry ``is_error``: the host uses ``ToolResultBlock.is_error`` to decide whether a tool failed,
    and drives UI markers and the error fallback text from it. If only "Error: ..." is written into the body, the failure shows
    green in the UI and the trajectory has no machine-readable failure marker.
    """
    return {"content": [{"type": "text", "text": text}], "is_error": True}


# ==========================================
# Set up VASP input files (POTCAR recommended pseudopotentials + smart fallback)
# ==========================================
def _configure_repo_potcar_dir() -> None:
    """Prefer the repository-local POTCAR library when no valid env path exists."""
    repo_potcar_dir = Path(__file__).resolve().parents[1] / "POTCAR_dir"
    current = os.environ.get("PMG_VASP_PSP_DIR")
    if repo_potcar_dir.is_dir() and (not current or not Path(current).exists()):
        os.environ["PMG_VASP_PSP_DIR"] = str(repo_potcar_dir)


def _normalize_potcar_overrides(potcar_overrides: Optional[Any]) -> Dict[str, str]:
    """Return a validated element-to-POTCAR override map.

    Some model/tool transports occasionally send nested objects as JSON strings.
    Accept that form here so explicit pseudopotential requests still go through
    setup_vasp_inputs instead of encouraging manual POTCAR generation.
    """
    if potcar_overrides is None:
        return {}

    if isinstance(potcar_overrides, str):
        text = potcar_overrides.strip()
        if not text:
            return {}
        try:
            potcar_overrides = json.loads(text)
        except json.JSONDecodeError:
            try:
                potcar_overrides = ast.literal_eval(text)
            except (SyntaxError, ValueError) as exc:
                raise ValueError(
                    'potcar_overrides must be a mapping such as {"Cr": "Cr_pv"} '
                    "or a JSON object string."
                ) from exc

    if potcar_overrides is None:
        return {}

    if not isinstance(potcar_overrides, dict):
        raise ValueError(
            'potcar_overrides must map POSCAR element symbols to PBE POTCAR symbols, '
            'for example {"Cr": "Cr_pv"}.'
        )

    normalized: Dict[str, str] = {}
    for element, symbol in potcar_overrides.items():
        element_key = str(element).strip()
        potcar_symbol = str(symbol).strip()
        if not element_key or not potcar_symbol:
            raise ValueError("potcar_overrides cannot contain empty element names or POTCAR symbols.")
        normalized[element_key] = potcar_symbol
    return normalized


def _setup_vasp_inputs_sync(
    poscar_path: Path,
    incar_path: Path,
    work_dir: Path,
    kpoints_density: int,
    potcar_overrides: Optional[Any] = None,
) -> str:
    """(Sync function) The blocking task that actually does file I/O, Pymatgen object instantiation, and file generation"""
    _configure_repo_potcar_dir()

    from pymatgen.core import Structure
    from pymatgen.io.vasp import Incar, Kpoints, Poscar, Potcar, PotcarSingle
    
    # Make sure the target working directory exists
    work_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. Read the original structure and custom INCAR
    structure = Structure.from_file(str(poscar_path))
    incar = Incar.from_file(str(incar_path))
    
    # 2. K-points: if the INCAR already contains KSPACING, VASP generates the mesh from the INCAR and no KPOINTS file is written
    use_kspacing = incar.get("KSPACING") is not None
    
    # 3. Automatically generate POTCAR (using the pymatgen / Materials Project recommended pseudopotentials)
    # The element order must come from the POSCAR actually written (site order), not from types_of_specie
    # (which is sorted by electronegativity). When the two differ, VASP pairs POTCAR entries with the POSCAR
    # atom counts by position, silently computing the wrong elements without any error.
    species = list(Poscar(structure).site_symbols)

    # PBE pseudopotential variants recommended by pymatgen MPRelaxSet: for transition metals/alkali/alkaline earth/heavy elements, etc.,
    # it recommends the semicore-containing _pv/_sv/_d (e.g. Ti_pv, Fe_pv, Mn_pv, Li_sv, Ba_sv, Sn_d).
    # This stays consistent with the reference data (expert/workflow) and keeps the energy reference zero comparable.
    try:
        from pymatgen.io.vasp.sets import _load_yaml_config
        recommended_map = _load_yaml_config("MPRelaxSet")["POTCAR"]
    except Exception:
        recommended_map = {}

    normalized_overrides = _normalize_potcar_overrides(potcar_overrides)
    unknown_override_elements = sorted(set(normalized_overrides) - set(species))
    if unknown_override_elements:
        raise ValueError(
            "POTCAR override provided for element(s) not present in POSCAR: "
            + ", ".join(unknown_override_elements)
        )

    potcar_symbols = []
    for sym in species:
        best_sym = None
        override_sym = normalized_overrides.get(sym)
        if override_sym:
            try:
                potcar_single = PotcarSingle.from_symbol_and_functional(override_sym, "PBE")
            except Exception as exc:
                raise ValueError(
                    f"Explicit POTCAR override for element '{sym}' requested '{override_sym}', "
                    "but that PBE POTCAR could not be loaded. No fallback was used."
                ) from exc
            if str(potcar_single.element) != sym:
                raise ValueError(
                    f"Explicit POTCAR override for element '{sym}' requested '{override_sym}', "
                    f"but that POTCAR is for element '{potcar_single.element}'."
                )
            best_sym = override_sym
            potcar_symbols.append(best_sym)
            continue

        # Prefer the recommended variant; if the local POTCAR library lacks it, fall back in order of increasing cost
        trials = []
        rec = recommended_map.get(sym)
        if rec:
            trials.append(rec)
        trials += [f"{sym}{suffix}" for suffix in ["", "_pv", "_sv", "_d", "_h"]]
        for test_sym in dict.fromkeys(trials):  # deduplicate while preserving order
            try:
                # Try to read the POTCAR for this symbol
                PotcarSingle.from_symbol_and_functional(test_sym, "PBE")
                best_sym = test_sym
                break
            except Exception:
                continue

        if not best_sym:
            raise ValueError(f"Cannot find any PBE POTCAR for element '{sym}' (tried recommended '{rec}' then '', '_pv', '_sv', etc.). Please check PMG_VASP_PSP_DIR.")

        potcar_symbols.append(best_sym)
        
    potcar = Potcar(symbols=potcar_symbols, functional="PBE")
    
    # 4. Write all files to the target working directory
    structure.to(fmt="poscar", filename=str(work_dir / "POSCAR"))

    # Post-write check: the POTCAR element order must correspond one-to-one with the element line of the POSCAR written.
    # VASP pairs the two by position without validation; a mismatch only shows up as wrong physical results.
    written_symbols = list(Poscar.from_file(str(work_dir / "POSCAR")).site_symbols)
    potcar_elements = [str(p.element) for p in potcar]
    if written_symbols != potcar_elements:
        raise ValueError(
            "POTCAR/POSCAR element order mismatch — refusing to write inconsistent inputs. "
            f"POSCAR element line: {written_symbols}; POTCAR order: {potcar_elements}. "
            "VASP pairs these positionally and would silently assign the wrong "
            "pseudopotential to each element."
        )

    incar.write_file(str(work_dir / "INCAR"))
    kpath = work_dir / "KPOINTS"
    if use_kspacing:
        if kpath.exists():
            kpath.unlink()
        k_mesh_info = f"K mesh from INCAR KSPACING={incar.get('KSPACING')} (no KPOINTS file)"
    else:
        kpoints = Kpoints.automatic_density(structure, kpoints_density)
        kpoints.write_file(str(kpath))
        k_mesh_info = f"KPOINTS from automatic_density={kpoints_density}"
    potcar.write_file(str(work_dir / "POTCAR"))

    message = (
        f"Successfully generated POSCAR, INCAR, and POTCAR in {work_dir}\n"
        f"{k_mesh_info}\n"
        f"(Used POTCARs: {', '.join(potcar_symbols)})"
    )
    # Structured provenance: these fields are the input end of the "input choice -> final convergence/failure" supervision chain;
    # written only into prose they cannot be read by machines.
    detail = {
        "work_dir": str(work_dir),
        "element_order": written_symbols,
        "potcar_symbols": potcar_symbols,
        "potcar_overrides": normalized_overrides or None,
        "kspacing": incar.get("KSPACING") if use_kspacing else None,
        "kpoints_density": None if use_kspacing else kpoints_density,
        "encut": incar.get("ENCUT"),
        "n_sites": len(structure),
        "formula": structure.composition.reduced_formula,
    }
    return message, detail


async def setup_vasp_inputs_impl(
    poscar_path: str,
    incar_path: str,
    workspace_dir: str,
    kpoints_density: int = 100,
    potcar_overrides: Optional[Any] = None,
    work_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """(Async interface) Core logic called by the Tool: automatically generate the full set of VASP input files from a custom INCAR and POSCAR

    ``work_dir`` is an optional target subdirectory, used for workflows with one directory per case such as three-stage relaxations and
    multi-volume EOS. Defaults to writing to the workspace root. It must be inside ``workspace_dir`` -- otherwise this tool would
    become a channel for writing files to arbitrary locations outside the workspace.
    """
    workspace_root = Path(workspace_dir).resolve()
    if work_dir:
        candidate = Path(work_dir)
        target = (candidate if candidate.is_absolute() else workspace_root / candidate).resolve()
        if target != workspace_root and workspace_root not in target.parents:
            return _err(
                f"Error: work_dir must stay inside the workspace ({workspace_root}); got {target}"
            )
        work_dir_path = target
    else:
        work_dir_path = workspace_root
    poscar_file = Path(poscar_path).resolve()
    incar_file = Path(incar_path).resolve()

    from src.event_log import append_tool_record

    started = time.monotonic()
    args_record = {
        "poscar_path": str(poscar_file),
        "incar_path": str(incar_file),
        "kpoints_density": kpoints_density,
        "potcar_overrides": potcar_overrides,
        "work_dir": work_dir,
    }

    def _record(ok: bool, detail: dict | None = None, error: str | None = None) -> None:
        append_tool_record(
            workspace_root,
            tool="setup_vasp_inputs",
            ok=ok,
            duration_ms=int((time.monotonic() - started) * 1000),
            args=args_record,
            detail=detail,
            error=error,
        )

    if not poscar_file.exists():
        msg = f"Error: The source POSCAR file was not found at {poscar_file}"
        _record(False, error=msg)
        return _err(msg)
    if not incar_file.exists():
        msg = f"Error: The source INCAR file was not found at {incar_file}"
        _record(False, error=msg)
        return _err(msg)

    try:
        success_msg, detail = await asyncio.to_thread(
            _setup_vasp_inputs_sync,
            poscar_file,
            incar_file,
            work_dir_path,
            kpoints_density,
            potcar_overrides,
        )
        _record(True, detail=detail)
        return _ok(success_msg)

    except Exception as e:
        error_msg = f"Error during VASP input generation: {str(e)}"
        if "No POTCAR" in str(e) or "VASP_PSP_DIR" in str(e):
            error_msg += "\n(Hint: Ensure PMG_VASP_PSP_DIR is configured in your environment or ~/.pmgrc.yaml)"
        _record(False, error=str(e))
        return _err(error_msg)


# ==========================================
# DuckDuckGo search
# ==========================================
async def duckduckgo_search_impl(query: str, max_results: int = 10) -> Dict[str, Any]:
    try:
        from ddgs import DDGS
        def _run_search():
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=max_results))
        results = await asyncio.to_thread(_run_search)
        if not results:
            return _ok("No results found! Try a less restrictive/shorter query.")
        postprocessed_results = [f"[{res['title']}]({res['href']})\n{res['body']}" for res in results]
        final_text = "## Search Results\n\n" + "\n\n".join(postprocessed_results)
        return _ok(final_text)
    except ImportError:
        return _err("Error: You must install `ddgs` (pip install ddgs).")
    except Exception as e:
        return _err(f"Error: {str(e)}")

# ==========================================
# Google search
# ==========================================
def _google_search_sync(query: str, provider: str, api_key: str) -> dict:
    import requests
    if provider == "serpapi":
        base_url = "https://serpapi.com/search.json"
        params = {"q": query, "api_key": api_key, "engine": "google", "google_domain": "google.com"}
        response = requests.get(base_url, params=params, timeout=15)
    else:
        # serper.dev requires POST + the X-API-KEY header. A key in the URL query would be carried verbatim by the requests
        # exception message, then written via _err() into log.jsonl and the trajectory digest -- the key would leak all the way
        # into the inputs distilled into skills. Putting it in a header keeps it out of the URL in exceptions.
        base_url = "https://google.serper.dev/search"
        response = requests.post(
            base_url,
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query},
            timeout=15,
        )
    response.raise_for_status()
    return response.json()


def _redact(text: str, *secrets: str) -> str:
    """Scrub secrets from outbound text. Exception messages may carry keys from the URL, headers, or body."""
    for secret in secrets:
        if secret and len(secret) >= 8:
            text = text.replace(secret, f"{secret[:4]}…[redacted]")
    return text

async def google_search_impl(query: str, provider: str = "serper") -> Dict[str, Any]:
    api_key = os.getenv(f"{provider.upper()}_API_KEY")
    if not api_key:
        return _err(f"Error: Missing API key. Make sure {provider.upper()}_API_KEY is in your env variables.")
    try:
        results = await asyncio.to_thread(_google_search_sync, query, provider, api_key)
        organic_key = "organic_results" if provider == "serpapi" else "organic"
        if organic_key not in results or len(results[organic_key]) == 0:
            return _ok(f"No results found for '{query}'. Try with a more general query.")
        web_snippets = []
        for idx, page in enumerate(results[organic_key]):
            title = page.get("title", "No Title")
            link = page.get("link", "")
            snippet = page.get("snippet", "")
            web_snippets.append(f"{idx + 1}. [{title}]({link})\n{snippet}")
        final_text = "## Search Results\n\n" + "\n\n".join(web_snippets)
        return _ok(final_text)
    except Exception as e:
        return _err(f"Error: {_redact(str(e), api_key)}")

# ==========================================
# Web browsing tool
# ==========================================
def _assert_public_http_url(url: str) -> None:
    """Reject URLs that point to the internal network / localhost.

    Without this check, the agent could make this tool read ``http://127.0.0.1:<litellm port>``,
    LAN devices, or the cloud metadata address (169.254.169.254) -- this is SSRF, and the trigger is the agent
    itself, which does not become safe just because it is deployed on an internal network.
    """
    import ipaddress
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Only http/https URLs are allowed, got scheme {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise ValueError("URL has no host")
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise ValueError(f"Cannot resolve host {host!r}: {exc}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local        # includes the 169.254.169.254 cloud metadata address
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError(
                f"Refusing to fetch {url}: {host} resolves to non-public address {ip}. "
                "This tool may only reach public internet hosts."
            )


def _visit_webpage_sync(url: str) -> str:
    import requests
    import re
    from markdownify import markdownify

    _assert_public_http_url(url)
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    # verify=True: disabling TLS verification would strip the fetched content of any provenance guarantee, while literature search results go straight
    # into scientific decisions. A bad certificate should raise an error, not be silently accepted.
    response = requests.get(url, headers=headers, timeout=20)
    response.raise_for_status()
    markdown_content = markdownify(response.text).strip()
    markdown_content = re.sub(r"\n{3,}", "\n\n", markdown_content)
    return markdown_content

async def visit_webpage_impl(url: str, max_output_length: int = 40000) -> Dict[str, Any]:
    try:
        import markdownify  
    except ImportError:
        return _err("Error: You must install `markdownify` (pip install markdownify).")
    import requests
    try:
        markdown_content = await asyncio.to_thread(_visit_webpage_sync, url)
        if len(markdown_content) > max_output_length:
            markdown_content = markdown_content[:max_output_length] + \
                f"\n\n..._This content has been truncated to stay below {max_output_length} characters_...\n"
        return _ok(markdown_content)
    except requests.exceptions.Timeout:
        return _err("Error: The request timed out. Please try again later or check the URL.")
    except Exception as e:
        return _err(f"Error fetching the webpage: {str(e)}")

# ==========================================
# Academic open literature search (arXiv API)
# ==========================================
def _arxiv_search_sync(query: str, max_results: int) -> list:
    import requests
    import xml.etree.ElementTree as ET
    base_url = "http://export.arxiv.org/api/query"
    params = {
        "search_query": query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "relevance",
        "sortOrder": "descending"
    }
    response = requests.get(base_url, params=params, timeout=15)
    response.raise_for_status()
    root = ET.fromstring(response.text)
    ns = {'atom': 'http://www.w3.org/2005/Atom'}
    papers = []
    for entry in root.findall('atom:entry', ns):
        title = entry.find('atom:title', ns).text.replace('\n', ' ').strip()
        summary = entry.find('atom:summary', ns).text.replace('\n', ' ').strip()
        pdf_link = ""
        for link in entry.findall('atom:link', ns):
            if link.attrib.get('title') == 'pdf':
                pdf_link = link.attrib.get('href')
                break
        papers.append({
            "title": title,
            "summary": summary,
            "pdf_link": pdf_link
        })
    return papers

async def arxiv_search_impl(query: str, max_results: int = 5) -> Dict[str, Any]:
    try:
        papers = await asyncio.to_thread(_arxiv_search_sync, query, max_results)
        if not papers:
            return _ok(f"No open-access papers found for query: '{query}'")
        snippets = []
        for idx, paper in enumerate(papers):
            pdf_url = paper["pdf_link"] + ".pdf" if paper["pdf_link"] else "No PDF available"
            snippets.append(
                f"### {idx + 1}. {paper['title']}\n"
                f"**PDF Download Link**: {pdf_url}\n"
                f"**Abstract**: {paper['summary'][:500]}...\n"
            )
        final_text = "## Academic Search Results (Open Access)\n\n" + "\n\n".join(snippets)
        return _ok(final_text)
    except Exception as e:
        return _err(f"Error searching academic papers: {str(e)}")

# ==========================================
# Semantic Scholar academic literature search
# ==========================================
import requests

def _semanticscholar_search_sync(query: str, max_results: int) -> list:
    base_url = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"
    params = {
        "query": query,
        "fields": "title,abstract,year,openAccessPdf"
    }
    headers = {}
    response = requests.get(base_url, params=params, headers=headers, timeout=15)
    response.raise_for_status()
    data = response.json()
    papers = []
    for item in data.get('data', []):
        if len(papers) >= max_results:
            break
        title = item.get('title') or "Untitled"
        abstract = item.get('abstract') or "No abstract available"
        year = item.get('year') or "Unknown year"
        pdf_link = ""
        oa_pdf = item.get('openAccessPdf')
        if oa_pdf and isinstance(oa_pdf, dict):
            pdf_link = oa_pdf.get('url', "")
        papers.append({
            "title": title,
            "abstract": abstract,
            "year": year,
            "pdf_link": pdf_link
        })
    return papers

async def semanticscholar_search_impl(query: str, max_results: int = 5) -> Dict[str, Any]:
    try:
        papers = await asyncio.to_thread(_semanticscholar_search_sync, query, max_results)
        if not papers:
            return _ok(f"No papers found for query: '{query}'")
        snippets = []
        for idx, paper in enumerate(papers):
            pdf_url = paper["pdf_link"] if paper["pdf_link"] else "No open-access PDF available"
            abstract_text = paper['abstract']
            if len(abstract_text) > 500:
                abstract_text = abstract_text[:500] + "..."
            snippets.append(
                f"### {idx + 1}. {paper['title']} ({paper['year']})\n"
                f"PDF Download Link: {pdf_url}\n"
                f"Abstract: {abstract_text}\n"
            )
        final_text = "## Semantic Scholar Bulk Search Results\n\n" + "\n\n".join(snippets)
        return _ok(final_text)
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 429:
            error_msg = "Rate limit exceeded (429). You are sharing the unauthenticated API pool. Please configure an API Key (1 req/sec limit)."
        elif e.response.status_code == 400:
            error_msg = f"Bad Request (400): Check your search query parameters. Details: {e.response.text}"
        else:
            error_msg = f"HTTP Error {e.response.status_code}: {str(e)}"
        return _err(error_msg)
    except Exception as e:
        return _err(f"Error searching Semantic Scholar: {str(e)}")
