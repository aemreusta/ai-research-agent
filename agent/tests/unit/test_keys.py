"""Provider key handling: encrypted at rest, never logged, UI beats environment."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from research_agent.errors import ErrorCode
from research_agent.keys import KeyError_, KeySource, Provider, ProviderKeys, SecretBox

SECRET = Fernet.generate_key().decode()


def test_round_trip() -> None:
    box = SecretBox(SECRET)
    assert box.decrypt(box.encrypt("tvly-secret-value")) == "tvly-secret-value"


def test_ciphertext_does_not_contain_the_plaintext() -> None:
    box = SecretBox(SECRET)
    assert "tvly-secret-value" not in box.encrypt("tvly-secret-value")


def test_two_encryptions_of_the_same_key_differ() -> None:
    """Fernet includes a nonce, so equal ciphertexts would not reveal equal keys."""
    box = SecretBox(SECRET)
    assert box.encrypt("same") != box.encrypt("same")


def test_a_missing_app_secret_key_is_a_config_error() -> None:
    with pytest.raises(KeyError_) as caught:
        SecretBox("")
    assert caught.value.error.code is ErrorCode.CONFIG_INVALID


def test_a_malformed_app_secret_key_is_a_config_error() -> None:
    with pytest.raises(KeyError_):
        SecretBox("not-a-fernet-key")


def test_decrypting_with_the_wrong_key_fails_loudly() -> None:
    ciphertext = SecretBox(SECRET).encrypt("value")
    with pytest.raises(KeyError_):
        SecretBox(Fernet.generate_key().decode()).decrypt(ciphertext)


def test_ui_keys_win_over_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-from-env")
    keys = ProviderKeys.resolve(supplied={Provider.TAVILY: "tvly-from-ui"})
    assert keys.get(Provider.TAVILY) == "tvly-from-ui"
    assert keys.sources()[Provider.TAVILY.value] == KeySource.UI.value


def test_environment_is_the_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-from-env")
    keys = ProviderKeys.resolve(supplied={})
    assert keys.get(Provider.GEMINI) == "AIza-from-env"
    assert keys.sources()[Provider.GEMINI.value] == KeySource.ENV.value


def test_a_provider_with_no_key_anywhere_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    for provider in Provider:
        monkeypatch.delenv(provider.env_var, raising=False)
    keys = ProviderKeys.resolve(supplied={})
    assert keys.get(Provider.OPENAI) is None
    assert Provider.OPENAI.value not in keys.sources()


def test_blank_supplied_values_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty field in the UI form must not shadow a working key from `.env`."""
    monkeypatch.setenv("BRAVE_API_KEY", "BSA-from-env")
    keys = ProviderKeys.resolve(supplied={Provider.BRAVE: "   "})
    assert keys.get(Provider.BRAVE) == "BSA-from-env"


def test_sources_metadata_carries_no_key_material(monkeypatch: pytest.MonkeyPatch) -> None:
    """`runs.key_sources` is written to the database and shown in the UI (v0.6 §15.3)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-abcdefghijklmnop")
    keys = ProviderKeys.resolve(supplied={Provider.TAVILY: "tvly-ui-abcdefghijk"})
    serialised = repr(keys.sources())
    assert "sk-env-abcdefghijklmnop" not in serialised
    assert "tvly-ui-abcdefghijk" not in serialised


def test_repr_never_leaks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keys end up inside exception messages and event payloads by accident; not from here."""
    keys = ProviderKeys.resolve(supplied={Provider.TAVILY: "tvly-ui-abcdefghijk"})
    assert "tvly-ui-abcdefghijk" not in repr(keys)
    assert "tvly-ui-abcdefghijk" not in str(keys)


def test_ollama_base_url_is_configuration_not_a_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama:11434")
    keys = ProviderKeys.resolve(supplied={})
    assert keys.get(Provider.OLLAMA) == "http://ollama:11434"
