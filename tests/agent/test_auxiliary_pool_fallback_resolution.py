"""Real-import auxiliary resolution cannot reissue a model-benched fallback key."""

import base64
import json
import time
from types import SimpleNamespace

import pytest

from agent import auxiliary_client as aux
from agent.credential_pool import load_pool

MODEL = "requested-model"
OTHER = "other-model"


def _token(account):
    def segment(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return ".".join((segment({"alg": "none"}), segment({
        "exp": int(time.time() + 86400), "sub": account,
        "https://api.openai.com/auth": {"chatgpt_account_id": account},
    }), "synthetic-signature"))


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("OPENROUTER_API_KEY", "HERMES_CODEX_BASE_URL", "OPENAI_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    built = []

    def client(**kwargs):
        built.append(kwargs)
        return SimpleNamespace(**kwargs)

    # Only client construction is replaced: config, pool, singleton and env reads are real.
    monkeypatch.setattr(aux, "_create_openai_client", client)
    return home, built


def _store(home, provider, cooldown_model, *, distinct=False, env=None):
    first = _token("synthetic-a") if provider == "openai-codex" else "synthetic-openrouter-a"
    second = _token("synthetic-b") if provider == "openai-codex" else "synthetic-openrouter-b"
    until = time.time() + 3600
    row = {
        "id": "pooled", "label": "synthetic", "priority": 0, "source": "manual",
        "auth_type": "oauth" if provider == "openai-codex" else "api_key",
        "access_token": first, "expires_at_ms": 4_000_000_000_000,
        "model_cooldowns": {cooldown_model: until},
    }
    if provider == "openai-codex" and not distinct:
        row["source"] = "device_code"
    elif provider == "openrouter" and env is not None:
        row["source"] = "env:OPENROUTER_API_KEY"
    rows = [row]
    state = {"version": 1, "credential_pool": {provider: rows}}
    fallback = second if distinct else first
    if provider == "openai-codex":
        state["providers"] = {provider: {"tokens": {
            "access_token": fallback, "refresh_token": "synthetic-refresh-token",
        }}}
        if distinct:
            # Force the real singleton fallback, not an available newly seeded pool row.
            rows.append({
                "id": "singleton", "label": "synthetic-b", "source": "device_code", "priority": 1,
                "auth_type": "oauth", "access_token": fallback, "expires_at_ms": 4_000_000_000_000,
                "last_status": "exhausted", "last_error_code": 401, "last_status_at": time.time(),
            })
    elif env is not None:
        env.setenv("OPENROUTER_API_KEY", fallback)
    (home / "auth.json").write_text(json.dumps(state), encoding="utf-8")
    (home / "config.yaml").write_text(json.dumps({
        "model": {"provider": provider, "default": MODEL},
        "auxiliary": {"free_only": False, "openrouter_model": MODEL},
    }), encoding="utf-8")
    return first, fallback, until


def _assert_cooldown(home, provider, token, model, until):
    rows = json.loads((home / "auth.json").read_text(encoding="utf-8"))["credential_pool"][provider]
    assert any(row.get("id") == "pooled" and row.get("model_cooldowns", {}).get(model) == until for row in rows)
    assert load_pool(provider).token_is_blocked(token, model=model)


@pytest.mark.parametrize("provider", ["openai-codex", "openrouter"])
@pytest.mark.parametrize("route", ["explicit", "fallback"])
def test_other_model_cooldown_allows_real_resolution(home, monkeypatch, provider, route):
    root, built = home
    key, _, until = _store(root, provider, OTHER, env=monkeypatch)
    pool = load_pool(provider)
    assert pool.select() is None
    assert pool.select(model=MODEL) is not None
    if route == "fallback":
        client, model = aux._resolve_fallback_entry({"provider": provider, "model": MODEL})
    else:
        client, model = aux.resolve_provider_client(provider, model=MODEL)
    assert client is not None and model == MODEL
    assert built[-1]["api_key"] == key
    _assert_cooldown(root, provider, key, OTHER, until)


@pytest.mark.parametrize("provider", ["openai-codex", "openrouter"])
@pytest.mark.parametrize("route", ["explicit", "fallback"])
def test_native_rejection_cannot_reuse_same_fallback_key(home, monkeypatch, provider, route):
    root, built = home
    key, fallback, until = _store(root, provider, MODEL, env=monkeypatch)
    assert fallback == key
    assert load_pool(provider).select(model=MODEL) is None
    if route == "fallback":
        result = aux._resolve_fallback_entry({"provider": provider, "model": MODEL})
    else:
        result = aux.resolve_provider_client(provider, model=MODEL)
    assert result == (None, None)
    assert built == []
    _assert_cooldown(root, provider, key, MODEL, until)


@pytest.mark.parametrize("raw_codex", [False, True])
def test_distinct_codex_singleton_fallback_keeps_endpoint_authority(home, monkeypatch, raw_codex):
    root, built = home
    key, fallback, until = _store(root, "openai-codex", MODEL, distinct=True)
    monkeypatch.setenv("HERMES_CODEX_BASE_URL", "https://synthetic-gateway.example/v1")
    assert load_pool("openai-codex").select(model=MODEL) is None
    client, model = aux.resolve_provider_client("openai-codex", model=MODEL, raw_codex=raw_codex)
    assert client is not None and model == MODEL
    assert built[-1]["api_key"] == fallback != key
    assert built[-1]["base_url"] == "https://synthetic-gateway.example/v1"
    _assert_cooldown(root, "openai-codex", key, MODEL, until)


@pytest.mark.parametrize("same_key", [True, False])
def test_openrouter_explicit_fallback_obeys_model_and_endpoint(home, same_key):
    root, built = home
    key, fallback, until = _store(root, "openrouter", MODEL, distinct=not same_key)
    assert load_pool("openrouter").select(model=MODEL) is None
    client, model = aux.resolve_provider_client(
        "openrouter", model=MODEL, explicit_api_key=fallback,
        explicit_base_url="https://synthetic-router.example/v1",
    )
    if same_key:
        assert client is None and built == []
    else:
        assert client is not None and model == MODEL
        assert built[-1]["api_key"] == fallback != key
        assert built[-1]["base_url"] == "https://synthetic-router.example/v1"
    _assert_cooldown(root, "openrouter", key, MODEL, until)


@pytest.mark.parametrize("route", ["explicit", "fallback"])
@pytest.mark.parametrize("same_key", [True, False])
def test_anthropic_explicit_key_obeys_native_requested_cooldown(home, monkeypatch, route, same_key):
    root, built = home
    key, fallback, until = _store(root, "anthropic", MODEL, distinct=not same_key)

    def client(token, base_url):
        built.append({"api_key": token, "base_url": base_url})
        return SimpleNamespace()

    monkeypatch.setattr("agent.anthropic_adapter.build_anthropic_client", client)
    pool = load_pool("anthropic")
    assert pool.select(model=MODEL) is None
    assert pool.token_is_blocked(key, model=MODEL)
    if route == "fallback":
        result = aux._resolve_fallback_entry({"provider": "anthropic", "model": MODEL, "api_key": fallback})
    else:
        result = aux.resolve_provider_client("anthropic", model=MODEL, explicit_api_key=fallback)
    if same_key:
        assert result == (None, None) and built == []
    else:
        assert result[0] is not None and result[1] == MODEL
        assert built[-1]["api_key"] == fallback != key
    _assert_cooldown(root, "anthropic", key, MODEL, until)


@pytest.mark.parametrize("route", ["explicit", "fallback"])
@pytest.mark.parametrize("same_key", [True, False])
@pytest.mark.parametrize("override", ["", "https://explicit-router.example/v1"])
def test_openrouter_explicit_key_keeps_its_own_endpoint(home, route, same_key, override):
    root, built = home
    key, other_key, until = _store(root, "openrouter", OTHER, distinct=True)
    store = json.loads((root / "auth.json").read_text(encoding="utf-8"))
    relay = "https://pool-a-relay.example/v1"
    store["credential_pool"]["openrouter"][0]["base_url"] = relay
    (root / "auth.json").write_text(json.dumps(store), encoding="utf-8")
    assert load_pool("openrouter").select(model=MODEL).runtime_api_key == key
    explicit_key = key if same_key else other_key
    if route == "fallback":
        client, model = aux._resolve_fallback_entry({
            "provider": "openrouter", "model": MODEL, "api_key": explicit_key, "base_url": override,
        })
    else:
        client, model = aux.resolve_provider_client(
            "openrouter", model=MODEL, explicit_api_key=explicit_key, explicit_base_url=override or None,
        )
    assert client is not None and model == MODEL
    assert built[-1]["api_key"] == explicit_key
    assert built[-1]["base_url"] == (override or (relay if same_key else aux.OPENROUTER_BASE_URL))
    _assert_cooldown(root, "openrouter", key, OTHER, until)


@pytest.mark.parametrize("provider", ["openai-codex", "openrouter", "anthropic"])
@pytest.mark.parametrize("route", ["explicit", "fallback"])
@pytest.mark.parametrize("same_key", [True, False])
@pytest.mark.parametrize("fingerprint_only", [True, False])
def test_malformed_pool_priority_preserves_distinct_fallback_and_known_rejection(
    home, monkeypatch, provider, route, same_key, fingerprint_only,
):
    from agent.credential_persistence import fingerprint_secret_value

    root, built = home
    key, fallback, until = _store(root, provider, MODEL, distinct=not same_key, env=monkeypatch)
    store = json.loads((root / "auth.json").read_text(encoding="utf-8"))
    rows = store["credential_pool"][provider]
    if fingerprint_only:
        rows[0].pop("access_token")
        rows[0]["secret_fingerprint"] = fingerprint_secret_value(key)
    rows.append({
        "id": "malformed", "source": "manual", "priority": "not-a-number",
        "auth_type": "api_key", "access_token": "synthetic-unrelated-key",
    })
    (root / "auth.json").write_text(json.dumps(store), encoding="utf-8")

    def client(token, base_url):
        built.append({"api_key": token, "base_url": base_url})
        return SimpleNamespace()

    monkeypatch.setattr("agent.anthropic_adapter.build_anthropic_client", client)
    # The real first selection already handles this store error without raising.
    with pytest.raises(TypeError):
        load_pool(provider)
    assert aux._select_pool_entry(provider, model=MODEL) == (False, None)
    if route == "fallback":
        entry = {"provider": provider, "model": MODEL}
        if provider == "anthropic":
            entry["api_key"] = fallback
        result = aux._resolve_fallback_entry(entry)
    else:
        result = aux.resolve_provider_client(
            provider, model=MODEL, explicit_api_key=fallback if provider == "anthropic" else None,
        )
    if same_key:
        assert result == (None, None) and built == []
    else:
        assert result[0] is not None and result[1] == MODEL
        assert built[-1]["api_key"] == fallback != key
    after = json.loads((root / "auth.json").read_text(encoding="utf-8"))["credential_pool"][provider]
    assert next(row for row in after if row["id"] == "pooled")["model_cooldowns"] == {MODEL: until}
    assert next(row for row in after if row["id"] == "malformed")["priority"] == "not-a-number"


@pytest.mark.parametrize("route", ["explicit", "fallback"])
@pytest.mark.parametrize("same_key", [True, False])
@pytest.mark.parametrize("cooldown_model", [MODEL, OTHER])
@pytest.mark.parametrize("malformed", [False, True])
def test_ranked_fingerprint_cooldown_matches_concrete_key(home, route, same_key, cooldown_model, malformed):
    from agent.credential_persistence import fingerprint_secret_value

    root, built = home
    key, other_key, until = _store(root, "openrouter", cooldown_model, distinct=True)
    store = json.loads((root / "auth.json").read_text(encoding="utf-8"))
    rows = store["credential_pool"]["openrouter"]
    rows[0].pop("access_token")
    rows[0]["secret_fingerprint"] = fingerprint_secret_value(key)
    rows.append({
        "id": "unrelated", "source": "manual", "priority": "not-a-number" if malformed else 1,
        "auth_type": "api_key", "access_token": "synthetic-unrelated-key",
    })
    (root / "auth.json").write_text(json.dumps(store), encoding="utf-8")
    if malformed:
        with pytest.raises(TypeError):
            load_pool("openrouter")
        assert aux._select_pool_entry("openrouter", model=MODEL) == (False, None)
    else:
        selected = load_pool("openrouter").select(model=MODEL)
        assert selected is not None and selected.runtime_api_key == "synthetic-unrelated-key"
    explicit_key = key if same_key else other_key
    if route == "fallback":
        result = aux._resolve_fallback_entry({"provider": "openrouter", "model": MODEL, "api_key": explicit_key})
    else:
        result = aux.resolve_provider_client("openrouter", model=MODEL, explicit_api_key=explicit_key)
    if same_key and cooldown_model == MODEL:
        assert result == (None, None) and built == []
    else:
        assert result[0] is not None and result[1] == MODEL
        assert built[-1]["api_key"] == explicit_key
    after = json.loads((root / "auth.json").read_text(encoding="utf-8"))["credential_pool"]["openrouter"]
    known = next(row for row in after if row["id"] == "pooled")
    assert known["model_cooldowns"] == {cooldown_model: until}
    assert known["secret_fingerprint"] == fingerprint_secret_value(key)
    assert not known.get("access_token")
