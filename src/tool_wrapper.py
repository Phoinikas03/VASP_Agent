from .tool import duckduckgo_search_impl, visit_webpage_impl, google_search_impl, arxiv_search_impl, setup_vasp_inputs_impl, semanticscholar_search_impl
from claude_agent_sdk import tool
from typing import Dict, Any, Optional

# ==========================================
# VASP input file generation tool
# ==========================================
def setup_vasp_inputs_tool(workspace_dir: str, default_kpoints_density: int = 100):
    """Closure: inject workspace_dir and the default grid density config and return the assembled Tool"""
    
    @tool(
        name="setup_vasp_inputs", 
        description=(
            "Generates VASP inputs (POTCAR, POSCAR copy, INCAR) in the workspace. "
            "If the INCAR contains KSPACING, k-points come from INCAR only and no KPOINTS file is written. "
            "Otherwise writes KPOINTS via automatic_density; kpoints_density defaults to 100. "
            'Optional potcar_overrides maps POSCAR element symbols to exact PBE POTCAR symbols as a JSON object '
            '(for example {"Cr": "Cr_pv"}). Pass this when the user explicitly requests a POTCAR variant; '
            "explicit overrides are validated and do not fall back. JSON object strings are accepted for compatibility. "
            "Optional work_dir writes the generated files into a subdirectory of the workspace "
            "(for example 'configs/ontop_upright'); use it for per-configuration workflows such as "
            "adsorption stages or EOS volume points instead of generating POTCAR by hand."
        ),
        input_schema={
            "poscar_path": str,
            "incar_path": str,
            "kpoints_density": Optional[int],  # lets the LLM adjust the K-point density as needed
            "potcar_overrides": Optional[Dict[str, str]],
            "work_dir": Optional[str],  # target subdirectory within the workspace; defaults to the workspace root
        }
    )
    async def setup_vasp_inputs(args: Dict[str, Any]) -> Dict[str, Any]:
        # Use `or` rather than `get(k, default)`: when the model explicitly passes null, get returns None and int(None) raises TypeError
        density = int(args.get("kpoints_density") or default_kpoints_density)
        potcar_overrides = args.get("potcar_overrides") or None

        # Only responsible for argument extraction and forwarding
        return await setup_vasp_inputs_impl(
            poscar_path=args["poscar_path"],
            incar_path=args["incar_path"],
            workspace_dir=workspace_dir,
            kpoints_density=density,
            potcar_overrides=potcar_overrides,
            work_dir=args.get("work_dir") or None,
        )
            
    return setup_vasp_inputs


# ==========================================
# DuckDuckGo search tool
# ==========================================
def duckduckgo_search_tool(max_results: int = 10):
    """Closure: inject the max_results config and return the assembled Tool"""
    
    @tool(
        name="duckduckgo_search",
        description="Performs a DuckDuckGo web search based on your query and returns the top search results.",
        input_schema={"query": str},
    )
    async def duckduckgo_search(args: Dict[str, Any]) -> Dict[str, Any]:
        # Only responsible for argument extraction and forwarding
        return await duckduckgo_search_impl(
            query=args["query"], 
            max_results=max_results
        )

    return duckduckgo_search


# ==========================================
# Google search tool
# ==========================================
def google_search_tool(provider: str = "serper"):
    """Closure: inject the provider config and return the assembled Tool"""
    
    @tool(
        name="google_search", 
        description="Performs a Google web search for your query and returns a string of the top search results.", 
        input_schema={"query": str}
    )
    async def google_search(args: Dict[str, Any]) -> Dict[str, Any]:
        # Only responsible for argument extraction and forwarding
        return await google_search_impl(
            query=args["query"], 
            provider=provider
        )
            
    return google_search


# ==========================================
# Web browsing tool
# ==========================================
def visit_webpage_tool(max_output_length: int = 40000):
    """Closure: inject the max_output_length config and return the assembled Tool"""
    
    @tool(
        name="visit_webpage", 
        description="Visits a webpage at the given url and reads its content as a markdown string. Use this to browse webpages.", 
        input_schema={"url": str}
    )
    async def visit_webpage(args: Dict[str, Any]) -> Dict[str, Any]:
        # Only responsible for argument extraction and forwarding
        return await visit_webpage_impl(
            url=args["url"], 
            max_output_length=max_output_length
        )

    return visit_webpage

# ==========================================
# Tool wrapper: arXiv academic literature search
# ==========================================
def arxiv_search_tool(max_results: int = 5):
    """Closure: inject the max_results config and return the assembled academic search Tool"""
    
    @tool(
        name="arxiv_search", 
        description=(
            "Search for open-access academic papers, especially in computational materials, physics, and computer science. "
            "Returns the paper title, abstract, and a direct PDF download link."
        ), 
        input_schema={"query": str}
    )
    async def arxiv_search(args: Dict[str, Any]) -> Dict[str, Any]:
        return await arxiv_search_impl(
            query=args["query"], 
            max_results=max_results
        )

    return arxiv_search

# ==========================================
# Tool wrapper: Semantic Scholar academic literature search
# ==========================================
def semanticscholar_search_tool(max_results: int = 5):
    """Closure: inject the max_results config and return the assembled Semantic Scholar search Tool"""
    
    @tool(
        name="semanticscholar_search", 
        description=(
            "Search for academic papers across all scientific fields using the Semantic Scholar API. "
            "Supports advanced search syntax (e.g., '\"exact phrase\" +required -excluded'). "
            "Returns the paper title, publication year, abstract, and an open-access PDF download link if available."
        ), 
        input_schema={"query": str}
    )
    async def semanticscholar_search(args: Dict[str, Any]) -> Dict[str, Any]:
        # Only responsible for argument extraction and forwarding
        return await semanticscholar_search_impl(
            query=args["query"], 
            max_results=max_results
        )

    return semanticscholar_search
