"""Keep concrete auxiliary fallback keys subject to native model cooldowns."""

from typing import Any, Optional


def available_pool_fallback_token(provider: str, token: Any, model: Optional[str]) -> Any:
    """Reject only a known key benched for this model; preserve distinct fallback keys.

    Lazy API-key providers retain their existing SDK-owned evaluation semantics.
    """
    if not isinstance(token, str) or not token or not model:
        return token
    from agent.credential_pool import load_pool

    return None if load_pool(provider).token_is_blocked(token, model=model) else token
