"""Keep auxiliary fallback keys subject to native model cooldowns and route ownership."""

from typing import Any, Optional, cast


def available_pool_fallback_token(provider: str, token: Any, model: Optional[str]) -> Any:
    """Reject only a known key benched for this model; preserve distinct fallback keys.

    Lazy API-key providers retain their existing SDK-owned evaluation semantics.
    """
    if not isinstance(token, str) or not token or not model:
        return token
    from agent.credential_pool import load_pool

    try:
        pool = load_pool(provider)
    except TypeError:
        # A malformed priority can break ranking, not invalidate a distinct fallback key.
        return _available_unranked_fallback_token(provider, token, model)
    return None if pool.token_is_blocked(token, model=model) else _available_unranked_fallback_token(provider, token, model)


def _available_unranked_fallback_token(provider: str, token: str, model: str) -> Optional[str]:
    """Keep known cooldowns enforceable even when the pool cannot rank its rows."""
    from agent.credential_persistence import fingerprint_secret_value
    from agent.credential_pool import PooledCredential
    from agent.credential_pool_model_cooldowns import model_cooldown_until
    from hermes_cli.auth import read_credential_pool

    fingerprint = fingerprint_secret_value(token)
    for payload in cast(list[dict[str, Any]], read_credential_pool(provider)):
        entry = PooledCredential.from_dict(provider, payload)
        # Env/borrowed rows persist fingerprints instead of plaintext credentials.
        matches = entry.runtime_api_key == token or entry.extra.get("secret_fingerprint") == fingerprint
        if matches and model_cooldown_until(entry, model) is not None:
            return None
    return token


def pool_fallback_base_url(token: Any, entry: Any, default: str) -> str:
    """A selected pool row's endpoint belongs only to that row's credential."""
    from agent.auxiliary_client import _pool_runtime_api_key, _pool_runtime_base_url

    if token != _pool_runtime_api_key(entry):
        return default
    return _pool_runtime_base_url(entry, default) or default
