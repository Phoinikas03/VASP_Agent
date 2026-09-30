"""LLM access: connect the Claude Agent SDK to the user-supplied upstream, translating the protocol in-process when needed.

The user only supplies **one** set of credentials (base URL + API key + model name). Three fully automatic steps follow:

1. **Probe** -- hit the upstream Anthropic ``/v1/messages`` with these credentials.
   401/403 is classified separately: that is a key problem, not a protocol problem, so it raises an error instead of falling back to the bridge.
2. **Connect directly if possible** -- the SDK points straight at the upstream, no proxy is started.
3. **Otherwise translate in-process** -- when the upstream only speaks OpenAI ``/v1/chat/completions``, start
   a very thin HTTP endpoint that listens on loopback only, on a system-assigned port, and calls ``litellm.anthropic_messages()``
   internally for the conversion. It lives and dies with the agent process, is shared by all sessions in the process, and writes no config file.

Configuration (``.env`` or command line), in order of priority:

- Command line ``--api-base`` / ``--api-key`` / ``--model``
- ``LLM_API_BASE`` / ``LLM_API_KEY`` / ``LLM_MODEL``
- Legacy names ``UPSTREAM_API_BASE`` / ``UPSTREAM_API_KEY`` / ``UPSTREAM_MODEL`` (still supported)

``ANTHROPIC_BASE_URL`` / ``ANTHROPIC_API_KEY`` are **output** by this module to the SDK;
the user does not need to set them.
"""

from __future__ import annotations

import importlib.util
import json
import os
import queue
import re
import socket
import sys
import threading
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parent.parent
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0", ""})

_KNOWN_PROVIDERS = frozenset(
    {
        "openai", "azure", "anthropic", "vertex_ai", "vertex_ai_beta", "gemini",
        "bedrock", "ollama", "huggingface", "deepseek", "mistral", "cohere",
        "openrouter", "databricks",
    }
)


@dataclass(frozen=True)
class LLMEndpoint:
    """Resolution result. ``base_url`` / ``api_key`` have already been written to the environment for the SDK."""

    base_url: str
    api_key: str
    model: str | None
    mode: str      # "direct" | "bridge"
    detail: str

    def describe(self) -> str:
        label = "direct to upstream" if self.mode == "direct" else "converted via in-process protocol bridge"
        return f"{label}  {self.base_url}  model={self.model or '(default)'}  [{self.detail}]"


# --------------------------------------------------------------------------
# Basic utilities
# --------------------------------------------------------------------------

def _is_local_host(host: str | None) -> bool:
    if not host:
        return False
    h = host.lower()
    return h in _LOCAL_HOSTS or h.startswith("127.")


def host_port_from_base_url(base_url: str) -> tuple[str | None, int]:
    try:
        u = urlparse(base_url)
    except ValueError:
        return None, 80
    return u.hostname, u.port or (443 if (u.scheme or "http") == "https" else 80)


def port_is_listening(port: int, host: str = "127.0.0.1", timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _merge_no_proxy(base_url: str) -> None:
    """Add local addresses to NO_PROXY so requests are not hijacked by the system proxy."""
    with suppress(ValueError):
        host = urlparse(base_url).hostname
    parts = [p.strip() for p in (os.environ.get("NO_PROXY") or "").split(",") if p.strip()]
    for h in ("127.0.0.1", "localhost", "0.0.0.0", host):
        if h and h not in parts:
            parts.append(h)
    merged = ",".join(parts)
    os.environ["NO_PROXY"] = merged
    os.environ["no_proxy"] = merged


def normalize_api_base(api_base: str) -> str:
    """Strip the trailing ``/v1``. LiteLLM and direct connections both append the path themselves; leaving it in yields ``/v1/v1/...``."""
    u = api_base.strip().rstrip("/")
    while u.lower().endswith("/v1"):
        u = u[:-3].rstrip("/")
    return u


def _infer_provider(model_id: str) -> str:
    s = model_id.strip().lower()
    if "gemini" in s:
        return "gemini"
    if "claude" in s:
        return "anthropic"
    return "openai"


def litellm_model_id(model: str) -> str:
    """Complete ``provider/model``: keep a known prefix, otherwise infer from the model ID."""
    m = model.strip()
    if not m:
        return m
    if "/" in m:
        prov, rest = m.split("/", 1)
        rest = rest.strip()
        if not rest:
            return m
        prov_l = prov.strip().lower()
        return f"{prov_l if prov_l in _KNOWN_PROVIDERS else _infer_provider(rest)}/{rest}"
    return f"{_infer_provider(m)}/{m}"


def bare_model_id(model: str) -> str:
    """Strip the provider prefix: ``anthropic/glm-5`` -> ``glm-5``. The SDK needs the bare name for direct connections."""
    m = model.strip()
    return m.split("/", 1)[1].strip() or m if "/" in m else m


#: litellm providers build URLs differently, so the version segment ``api_base`` should carry differs too:
#: openai/openrouter only append ``/chat/completions`` and gemini only ``/models/<m>:generateContent``,
#: so the base must include the version segment itself; anthropic appends ``/v1/messages`` on its own, so the base must not.
_API_BASE_SUFFIX = {"openai": "/v1", "openrouter": "/v1", "gemini": "/v1beta"}


def bridge_model_id(model: str) -> str:
    """litellm model ID for the protocol bridge only.

    Reaching the bridge means the upstream just **rejected** Anthropic ``/v1/messages``. If we then inferred the
    ``anthropic/`` provider from the word ``claude``, litellm would speak the Anthropic protocol to
    a gateway that does not understand it -- guaranteed failure. So inside the bridge, bare names are always treated as OpenAI-compatible.

    Two exceptions: if the user explicitly wrote a provider prefix, honor it; the ``gemini`` API is not
    OpenAI-shaped (``/models/<m>:generateContent``) and still goes through litellm's gemini provider.
    """
    m = model.strip()
    if "/" in m and m.split("/", 1)[0].strip().lower() in _KNOWN_PROVIDERS:
        return litellm_model_id(m)          # the user named a provider; respect it
    resolved = litellm_model_id(m)
    prov, _, rest = resolved.partition("/")
    return resolved if prov == "gemini" else f"openai/{rest}"


def litellm_api_base(model: str, api_base: str) -> str:
    """Adjust ``api_base`` to the form litellm expects, per provider.

    Stripping ``/v1`` in ``normalize_api_base()`` is right for direct connections and the anthropic provider,
    but without it the openai provider would hit ``/chat/completions`` (gateways mostly answer 405).
    """
    base = normalize_api_base(api_base)
    provider = litellm_model_id(model).split("/", 1)[0]
    suffix = _API_BASE_SUFFIX.get(provider, "")
    if suffix and not base.lower().endswith(suffix):
        base += suffix
    return base


# --------------------------------------------------------------------------
# Upstream protocol probing
# --------------------------------------------------------------------------

def _short_error(text: str, limit: int = 160) -> str:
    """Compress a gateway error response into one line. Some gateways return a whole HTML page on 404, which is unreadable if logged verbatim."""
    s = re.sub(r"<[^>]+>", " ", text)           # strip tags so an HTML error page leaves only its body text
    s = " ".join(s.split())
    return s if len(s) <= limit else s[:limit] + "…"


@dataclass(frozen=True)
class ProbeResult:
    """Result of the Anthropic ``/v1/messages`` probe.

    ``ok`` and ``auth_failed`` must be considered separately: 401/403 means **this key was not accepted**,
    which says nothing about whether the upstream supports the Anthropic protocol. Early versions conflated the two, so as soon as a key expired
    they misjudged it as "upstream does not support Anthropic" and started the bridge, which uses the same key and
    would just fail again in a more roundabout way.
    """

    ok: bool
    auth_failed: bool
    detail: str


def probe_anthropic_messages(
    *, base_url: str, api_key: str, model: str, timeout: float = 20.0
) -> ProbeResult:
    """Probe whether the upstream can handle Anthropic ``/v1/messages`` directly.

    Both auth headers are tried (``Authorization: Bearer`` and ``x-api-key``); relay gateways use either.
    It is judged a credential problem only when **every attempt is 401/403** -- if even one is another error
    (404/405, etc.), the routing layer does not recognize this endpoint, i.e. the protocol is unsupported.
    """
    endpoint = normalize_api_base(base_url) + "/v1/messages"
    body = json.dumps(
        {"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "ping"}]}
    ).encode("utf-8")
    base_headers = {"content-type": "application/json", "anthropic-version": "2023-06-01"}
    attempts = [
        ("Bearer", {**base_headers, "Authorization": f"Bearer {api_key}"}),
        ("x-api-key", {**base_headers, "x-api-key": api_key}),
    ]
    failures: list[str] = []
    codes: list[int | None] = []
    for label, headers in attempts:
        req = Request(endpoint, data=body, method="POST", headers=headers)
        try:
            with urlopen(req, timeout=timeout) as resp:
                status = int(getattr(resp, "status", 200))
                if 200 <= status < 300:
                    return ProbeResult(True, False, f"HTTP {status} ({label})")
                codes.append(status)
                failures.append(f"{label}: HTTP {status}")
        except HTTPError as e:
            detail = ""
            with suppress(Exception):
                detail = _short_error(e.read(2048).decode("utf-8", errors="replace"))
            codes.append(e.code)
            failures.append(f"{label}: HTTP {e.code}{': ' + detail if detail else ''}")
        except (URLError, OSError) as e:
            codes.append(None)
            failures.append(f"{label}: {type(e).__name__}: {e}")
    auth_failed = bool(codes) and all(c in (401, 403) for c in codes)
    return ProbeResult(False, auth_failed, "; ".join(failures))


def probe_openai_chat(
    *, base_url: str, api_key: str, model: str, timeout: float = 20.0
) -> tuple[bool, str]:
    """Probe the upstream OpenAI ``/v1/chat/completions`` to tell whether the key itself is good.

    Only needed when the Anthropic probe failed with 401/403 -- if both sides reject the key, it is the key's problem;
    if this side accepts it and the other does not, only the auth method of ``/v1/messages`` differs and the bridge can still be used.
    """
    endpoint = normalize_api_base(base_url) + "/v1/chat/completions"
    body = json.dumps(
        {"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "ping"}]}
    ).encode("utf-8")
    req = Request(
        endpoint,
        data=body,
        method="POST",
        headers={"content-type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            status = int(getattr(resp, "status", 200))
            return 200 <= status < 300, f"HTTP {status}"
    except HTTPError as e:
        # 404/405 = no such endpoint, but at least the credentials were not rejected
        return False, f"HTTP {e.code}"
    except (URLError, OSError) as e:
        return False, f"{type(e).__name__}: {e}"


# --------------------------------------------------------------------------
# In-process protocol bridge: Anthropic /v1/messages -> upstream OpenAI /v1/chat/completions
# --------------------------------------------------------------------------
#
# When the upstream only speaks the OpenAI protocol, something must translate the SDK's Anthropic requests.
# That is delegated to ``litellm.anthropic_messages()`` -- LiteLLM's **library interface**,
# which does not need the LiteLLM proxy server. We only add a very thin HTTP endpoint, because the SDK can only
# talk HTTP via ``ANTHROPIC_BASE_URL``.
#
# Compared with spawning a subprocess running `litellm --config`, this avoids: writing a config file to disk (with a plaintext key),
# port scanning and reservation, cross-process instance registration, lifecycle cleanup of detached subprocesses, and monkeypatching
# private LiteLLM methods. The bridge lives and dies with the agent process and leaves no orphans.

#: Anthropic Messages parameters passed through to litellm. A whitelist rather than forwarding everything,
#: so the upstream does not error on unknown fields.
_PASSTHROUGH_KEYS = (
    "messages", "system", "max_tokens", "stop_sequences", "stream",
    "temperature", "top_k", "top_p", "tools", "tool_choice", "metadata", "thinking",
)

_bridge_url: str | None = None
_bridge_lock = threading.Lock()


def _make_bridge_app(model: str, api_base: str, api_key: str):
    from aiohttp import web
    import litellm

    # Make litellm send the Anthropic request to /v1/chat/completions, instead of /v1/responses,
    # which most relays do not support.
    litellm.use_chat_completions_url_for_anthropic_messages = True
    # The SDK sends Anthropic thinking, which litellm maps to OpenAI reasoning_effort;
    # the generic openai provider does not recognize this parameter for arbitrary models and raises UnsupportedParamsError.
    # The bridge faces all kinds of compatible gateways with uneven capabilities; dropping unrecognized parameters beats failing the whole request.
    litellm.drop_params = True
    target_model = bridge_model_id(model)
    target_api_base = litellm_api_base(target_model, api_base)

    async def handle(request):
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"type": "error", "error": {"type": "invalid_request_error",
                                                                 "message": "malformed JSON body"}},
                                     status=400)
        kwargs = {k: body[k] for k in _PASSTHROUGH_KEYS if k in body}
        kwargs.setdefault("max_tokens", 4096)
        kwargs["model"] = target_model      # ignore the model name in the request; use the configured one
        kwargs["api_base"] = target_api_base
        kwargs["api_key"] = api_key
        streaming = bool(kwargs.get("stream"))

        try:
            result = await litellm.anthropic_messages(**kwargs)
        except Exception as exc:
            return web.json_response(
                {"type": "error", "error": {"type": "api_error", "message": f"{type(exc).__name__}: {exc}"}},
                status=502,
            )

        if not streaming:
            payload = result if isinstance(result, dict) else result.model_dump()
            return web.json_response(payload)

        resp = web.StreamResponse(
            status=200,
            headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"},
        )
        await resp.prepare(request)
        try:
            async for chunk in result:
                await resp.write(chunk if isinstance(chunk, (bytes, bytearray)) else str(chunk).encode())
        except Exception as exc:
            # The stream has already started, so we can only finish with an SSE error event
            err = json.dumps({"type": "error",
                              "error": {"type": "api_error", "message": str(exc)}})
            with suppress(Exception):
                await resp.write(f"event: error\ndata: {err}\n\n".encode())
        await resp.write_eof()
        return resp

    app = web.Application()
    app.router.add_post("/v1/messages", handle)
    return app


def start_protocol_bridge(model: str, api_base: str, api_key: str) -> str | None:
    """Start, in a background thread, a protocol bridge that listens on loopback only on a system-assigned port, and return its base URL.

    Singleton: shared by all sessions in the same process. The bridge is stateless, so concurrent requests are fine.
    """
    global _bridge_url
    with _bridge_lock:
        if _bridge_url:
            return _bridge_url
        if importlib.util.find_spec("litellm") is None:
            print("[llm] litellm is not installed; cannot convert the protocol. Please `pip install litellm`",
                  file=sys.stderr, flush=True)
            return None

        ready: queue.Queue = queue.Queue(maxsize=1)

        def _serve() -> None:
            import asyncio

            from aiohttp import web

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                app = _make_bridge_app(model, api_base, api_key)
                runner = web.AppRunner(app)
                loop.run_until_complete(runner.setup())
                site = web.TCPSite(runner, "127.0.0.1", 0)  # let the system assign the port
                loop.run_until_complete(site.start())
                port = site._server.sockets[0].getsockname()[1]
                ready.put(port)
                loop.run_forever()
            except Exception as exc:
                ready.put(exc)

        threading.Thread(target=_serve, name="llm-protocol-bridge", daemon=True).start()
        try:
            got = ready.get(timeout=30)
        except queue.Empty:
            print("[llm] protocol bridge startup timed out", file=sys.stderr, flush=True)
            return None
        if isinstance(got, Exception):
            print(f"[llm] protocol bridge failed to start: {got}", file=sys.stderr, flush=True)
            return None
        _bridge_url = f"http://127.0.0.1:{got}"
        return _bridge_url


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------

def _first(*values: str | None) -> str:
    for v in values:
        if v and str(v).strip():
            return str(v).strip()
    return ""


def _publish(base_url: str, api_key: str, model: str) -> None:
    """Write the resolution result to the environment for the Claude Agent SDK and build_options to read.

    Setting it all here avoids guessing elsewhere how UPSTREAM_* should be converted into the form the SDK needs.
    """
    os.environ["ANTHROPIC_BASE_URL"] = base_url
    os.environ["ANTHROPIC_API_KEY"] = api_key
    if model:
        os.environ.setdefault("CLAUDE_CODE_MODEL", model)
    _merge_no_proxy(base_url)


def resolve_llm_endpoint(
    *,
    api_base: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    force_litellm: bool = False,
) -> LLMEndpoint:
    """Resolve the address the SDK should connect to, automatically starting/reusing LiteLLM if needed.

    Side effects: sets ``ANTHROPIC_BASE_URL`` / ``ANTHROPIC_API_KEY`` and ``NO_PROXY``.
    """
    base = _first(api_base, os.environ.get("LLM_API_BASE"), os.environ.get("UPSTREAM_API_BASE"))
    key = _first(api_key, os.environ.get("LLM_API_KEY"), os.environ.get("UPSTREAM_API_KEY"))
    mdl = _first(model, os.environ.get("LLM_MODEL"), os.environ.get("UPSTREAM_MODEL"))

    if not (base and key):
        raise SystemExit(
            "[llm] Missing upstream configuration. Set LLM_API_BASE / LLM_API_KEY in .env "
            "(optionally LLM_MODEL), or pass --api-base / --api-key."
        )

    base = normalize_api_base(base)
    direct_model = bare_model_id(mdl) if mdl else ""

    # 1) If the upstream natively supports the Anthropic protocol, connecting directly is simplest and has one fewer failure point
    if not force_litellm and direct_model:
        res = probe_anthropic_messages(base_url=base, api_key=key, model=direct_model)
        if res.ok:
            _publish(base, key, direct_model)
            return LLMEndpoint(base, key, direct_model, "direct", res.detail)

        if res.auth_failed:
            # 401/403 does not mean the upstream lacks Anthropic support. First check whether this key is also
            # rejected on the OpenAI route -- if both reject it, it is a credential problem, and starting the bridge would only wrap the same error in a more confusing one.
            key_ok, chat_detail = probe_openai_chat(base_url=base, api_key=key, model=direct_model)
            if not key_ok:
                raise SystemExit(
                    f"[llm] The upstream rejected these credentials (not a protocol problem).\n"
                    f"      /v1/messages:        {res.detail}\n"
                    f"      /v1/chat/completions: {chat_detail}\n"
                    f"      Neither route accepts this key, and the protocol bridge would use it too; first check whether LLM_API_KEY "
                    f"has expired, the quota is used up, or LLM_API_BASE is wrong."
                )
            print(
                "[llm] /v1/messages rejected this key, but /v1/chat/completions accepts it; "
                "switching to the in-process protocol bridge",
                flush=True,
            )
        else:
            print(
                f"[llm] Upstream does not support Anthropic /v1/messages; switching to the in-process protocol bridge: {res.detail}",
                flush=True,
            )

    # 2) Otherwise start a protocol bridge in-process to translate
    if not mdl:
        raise SystemExit("[llm] A model must be specified when protocol conversion is needed (LLM_MODEL or --model).")
    local = start_protocol_bridge(mdl, base, key)
    if local is None:
        raise SystemExit("[llm] Cannot establish a connection to the upstream: direct connection is unsupported and the protocol bridge failed to start.")

    resolved_model = direct_model or bare_model_id(mdl)
    _publish(local, key, resolved_model)
    return LLMEndpoint(local, key, resolved_model, "bridge", local)
