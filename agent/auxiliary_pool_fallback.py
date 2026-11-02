"""Keep auxiliary fallback keys subject to native model cooldowns and route ownership."""

from typing import Any, Optional


def available_pool_fallback_token(provider: str, token: Any, model: Optional[str]) -> Any:
    """Reject only a known key benched for this model; preserve distinct fallback keys.

    Lazy API-key providers retain their existing SDK-owned evaluation semantics.
    """
    if not isinstance(token, str) or not token or not model:
        return token
    from agent.credential_pool import load_pool

    return None if load_pool(provider).token_is_blocked(token, model=model) else token


def pool_fallback_base_url(token: Any, entry: Any, default: str) -> str:
    """A selected pool row's endpoint belongs only to that row's credential."""
    from agent.auxiliary_client import _pool_runtime_api_key, _pool_runtime_base_url

    if token != _pool_runtime_api_key(entry):
        return default
    return _pool_runtime_base_url(entry, default) or default
