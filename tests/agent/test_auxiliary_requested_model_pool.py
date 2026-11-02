"""Auxiliary selection must scope shared credentials to the resolved request model."""

import time
from unittest.mock import Mock

import pytest

from agent import auxiliary_client as aux
from agent.credential_pool import CredentialPool, PooledCredential


@pytest.fixture
def shared_pool(monkeypatch):
    """Real native selection, synthetic entries only; no credential discovery or HTTP."""
    pools = {}

    def make(provider, cooldowns=None):
        entry = PooledCredential(
            id="synthetic-seat", provider=provider, label="synthetic-seat",
            auth_type="api_key", source="manual", priority=0,
            access_token="synthetic-test-key", model_cooldowns=cooldowns,
        )
        pool = CredentialPool(provider, [entry])
        spy = Mock(wraps=pool.select)
        monkeypatch.setattr(pool, "select", spy)
        pools[provider] = pool
        return pool, entry, spy

    monkeypatch.setattr(aux, "load_pool", lambda provider: pools.get(provider))
    monkeypatch.setattr(aux, "_read_codex_singleton_token", lambda: None)
    monkeypatch.setattr(aux, "_codex_base_url_override", lambda: "")
    monkeypatch.setattr(aux, "_create_openai_client", lambda **kwargs: Mock(**kwargs))
    monkeypatch.setattr(aux, "_get_aux_model_for_provider", lambda provider: "requested-model")
    monkeypatch.setattr("hermes_cli.config.load_config_readonly", lambda: {})
    monkeypatch.setattr("agent.anthropic_credentials.resolve_anthropic_token", lambda **kwargs: None)
    monkeypatch.setattr("agent.anthropic_adapter.build_anthropic_client", lambda *args: Mock())
    monkeypatch.setattr(aux, "_aux_openrouter_settings", lambda: (False, "requested-model"))
    return make


@pytest.mark.parametrize("provider", ["anthropic", "openai-codex", "openrouter"])
def test_unrelated_model_cooldown_does_not_block_request(shared_pool, provider):
    pool, entry, spy = shared_pool(provider, {"other-model": time.time() + 3600})
    # Native conservative unscoped selection explains the original failure.
    assert pool.select() is None
    spy.reset_mock()
    present, selected = aux._select_pool_entry(provider, model="requested-model")
    assert present and selected is not None and selected.id == entry.id
    spy.assert_called_once_with(model="requested-model")
    assert entry.model_cooldowns["other-model"] > time.time()


@pytest.mark.parametrize("provider", ["anthropic", "openai-codex", "openrouter"])
@pytest.mark.parametrize("route", ["explicit", "fallback", "default"])
def test_resolved_model_reaches_pool_select(shared_pool, provider, route):
    _, _, spy = shared_pool(provider)
    if route == "fallback":
        client, model = aux._resolve_fallback_entry({"provider": provider, "model": "requested-model"})
    else:
        client, model = aux.resolve_provider_client(
            provider, model=None if route == "default" else "requested-model",
        )
    assert client is not None
    assert model == "requested-model"
    spy.assert_called_once_with(model="requested-model")


def test_raw_codex_model_reaches_pool_select(shared_pool):
    _, _, spy = shared_pool("openai-codex", {"other-model": time.time() + 3600})
    client, model = aux.resolve_provider_client("openai-codex", model="requested-model", raw_codex=True)
    assert client is not None and model == "requested-model"
    spy.assert_called_once_with(model="requested-model")


@pytest.mark.parametrize("provider", ["anthropic", "openai-codex", "openrouter"])
def test_requested_model_cooldown_remains_enforced(shared_pool, provider):
    _, entry, spy = shared_pool(provider, {"requested-model": time.time() + 3600})
    present, selected = aux._select_pool_entry(provider, model="requested-model")
    assert present and selected is None
    spy.assert_called_once_with(model="requested-model")
    assert entry.model_cooldowns["requested-model"] > time.time()


@pytest.mark.parametrize("provider", ["anthropic", "openai-codex"])
def test_requested_model_cooldown_blocks_client(shared_pool, provider):
    shared_pool(provider, {"requested-model": time.time() + 3600})
    assert aux.resolve_provider_client(provider, model="requested-model") == (None, None)


@pytest.mark.parametrize("provider", ["openai", "gemini"])
def test_ordinary_unscoped_provider_selection_preserved(shared_pool, provider):
    _, entry, spy = shared_pool(provider)
    present, selected = aux._select_pool_entry(provider)
    assert present and selected is not None and selected.id == entry.id
    spy.assert_called_once_with()


def test_anthropic_legacy_resolver_receives_requested_model(shared_pool, monkeypatch):
    resolver = Mock(return_value=None)
    monkeypatch.setattr("agent.anthropic_credentials.resolve_anthropic_token", resolver)
    assert aux.resolve_provider_client("anthropic", model="requested-model") == (None, None)
    resolver.assert_called_once_with(model="requested-model")
