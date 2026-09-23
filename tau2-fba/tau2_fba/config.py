"""Build the prototype's Config for one tau2 simulation.

Single source of truth is the prototype's own ``config/agent.yaml`` (and the
``prompts.yaml`` it references). Only the keys tau2 must own are overridden --
see the table in ../misc/prototypes/text-frontend-backend-agent-tau2-integration-plan.md
section 6.1. Prompts, history windows and delegation limits stay exactly as shipped.
"""

import copy
import hashlib
import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import yaml

from tau2_fba.prototype_import import ensure_importable

MODE_PAIRED = "frontend_backend"
MODE_BACKEND_ONLY = "backend_only"
MODES = (MODE_PAIRED, MODE_BACKEND_ONLY)

DOMAINS_FILE = Path(__file__).with_name("domains.yaml")

#: LiteLLM provider prefix for an OpenAI-compatible endpoint (Inference Hub).
LITELLM_PREFIX = "openai/"


def default_config_path() -> Path:
    """The prototype's shipped ``config/agent.yaml``."""
    return ensure_importable() / "config" / "agent.yaml"


@lru_cache(maxsize=8)
def _read_yaml(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: config root must be a mapping")
    return raw


def load_raw_config(path: Optional[str | Path] = None) -> tuple[dict, Path]:
    """Read and env-interpolate ``agent.yaml`` exactly as the prototype does.

    Returns a deep copy, so callers may mutate it, plus the directory relative
    prompt paths resolve against.
    """
    ensure_importable()
    from prototypes.text_frontend_backend_agent.config import interpolate_env

    resolved = Path(path).expanduser().resolve() if path else default_config_path()
    if not resolved.is_file():
        raise FileNotFoundError(f"FBA config not found: {resolved}")
    raw = interpolate_env(copy.deepcopy(_read_yaml(str(resolved))))
    return raw, resolved.parent


def load_domain_profile(domain: str) -> dict:
    """Persona and capability list for ``domain`` (see domains.yaml)."""
    profiles = _read_yaml(str(DOMAINS_FILE))
    if domain not in profiles:
        raise ValueError(
            f"No FBA profile for domain {domain!r}. The frontend needs a capability "
            f"list for every domain it serves; add one to {DOMAINS_FILE} "
            f"(configured: {sorted(profiles)})."
        )
    return copy.deepcopy(profiles[domain])


def build_fba_config(
    raw: dict,
    source_dir: Path,
    *,
    mode: str,
    domain_policy: str,
    profile: dict,
    frontend_model: Optional[str] = None,
    backend_model: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
):
    """Apply the tau2 overrides to a raw ``agent.yaml`` mapping and validate it.

    Model ids are Inference Hub ids as the prototype's YAML writes them (no LiteLLM
    prefix); :func:`litellm_model` adds the prefix at call time.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    ensure_importable()
    from prototypes.text_frontend_backend_agent.config import build_config

    raw = copy.deepcopy(raw)
    agent = raw.setdefault("agent", {})
    agent["mode"] = mode
    agent["name"] = profile.get("name") or agent.get("name")
    agent["persona"] = profile.get("persona") or agent.get("persona")

    raw.setdefault("frontend", {})
    backend = raw.setdefault("backend", {})
    backend["stateful"] = "auto"
    tools = backend.setdefault("tools", {})
    tools["execution"] = "external"  # tau2's environment executes every tool
    tools["on_incomplete_results"] = "error"
    tools["on_user_message_while_pending"] = "error"

    for role, model in (("frontend", frontend_model), ("backend", backend_model)):
        llm = raw[role].setdefault("llm", {})
        if model:
            llm["model"] = model
        if base_url:
            llm["base_url"] = base_url
        if api_key is not None:
            llm["api_key"] = api_key

    raw["domain"] = {
        "policy": domain_policy,
        "capabilities": list(profile.get("capabilities") or []),
        "unsupported_reply": profile.get("unsupported_reply")
        or (raw.get("domain") or {}).get("unsupported_reply"),
    }
    # The adapter injects its own sink; these two must be on because the filler
    # latency is read off the `delegation` event and the filler off `filler`.
    logging = raw.setdefault("logging", {})
    logging["event_sink"] = "none"
    logging["log_filler_text"] = True
    logging["log_delegation_query"] = True

    # Default the catalog path; build_config resolves it against source_dir.
    prompts = raw.setdefault("prompts", {})
    prompts.setdefault("path", "prompts.yaml")
    return build_config(raw, source_dir=source_dir)


def litellm_model(model: str) -> str:
    """Hub model id -> LiteLLM model string (``openai/`` provider prefix)."""
    return model if model.startswith(LITELLM_PREFIX) else LITELLM_PREFIX + model


def passes_explicit_key(llm_config: Any) -> bool:
    """Whether the key must travel as a per-call ``api_key`` kwarg.

    tau2's generate() writes every call's kwargs, verbatim, into the llm_debug logs
    when --verbose-logs is on -- an explicit api_key lands on disk. LiteLLM's
    ``openai/`` provider falls back to $OPENAI_API_KEY by itself, so when the key
    *is* that variable it is not passed at all.
    """
    return bool(llm_config.api_key) and llm_config.api_key != os.environ.get(
        "OPENAI_API_KEY"
    )


def litellm_kwargs(llm_config: Any, extra: Optional[dict] = None) -> dict:
    """Map a prototype ``LLMConfig`` onto kwargs for ``tau2.utils.llm_utils.generate``."""
    kwargs: dict[str, Any] = {
        "api_base": llm_config.base_url,
        "temperature": llm_config.temperature,
        "max_tokens": llm_config.max_tokens,
        "timeout": llm_config.timeout_seconds,
    }
    if passes_explicit_key(llm_config):
        kwargs["api_key"] = llm_config.api_key
    if llm_config.extra_body:
        kwargs["extra_body"] = copy.deepcopy(llm_config.extra_body)
    kwargs.update(extra or {})
    return kwargs


def resolve_api_key(api_key_env: Optional[str]) -> Optional[str]:
    """Read the key from the named variable; ``None`` keeps the YAML's own ``${VAR}``."""
    if not api_key_env:
        return None
    value = os.environ.get(api_key_env, "")
    if not value:
        raise ValueError(
            f"${api_key_env} is empty. Export the key first: export {api_key_env}='sk-...'"
        )
    return value


def sha256_of(value: Any) -> str:
    """Stable digest of a file path's bytes or of a JSON-serializable value."""
    if isinstance(value, Path):
        data = value.read_bytes()
    else:
        data = json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class RoleSummary:
    """What one role runs on, for provenance (never includes the API key)."""

    model: str
    base_url: str
    temperature: float
    max_tokens: int
    extra_body: dict

    @classmethod
    def of(cls, llm_config: Any) -> "RoleSummary":
        return cls(
            model=llm_config.model,
            base_url=llm_config.base_url,
            temperature=llm_config.temperature,
            max_tokens=llm_config.max_tokens,
            extra_body=dict(llm_config.extra_body),
        )
