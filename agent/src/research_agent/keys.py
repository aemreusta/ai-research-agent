"""Provider keys: where they come from, how they are stored, and how they stay out of sight.

The flow (architecture v0.6 §15.3 / D18): the browser keeps keys in `sessionStorage` and sends
them when starting a run; `api` encrypts them into `run_secrets` with `APP_SECRET_KEY`; the agent
decrypts them when it picks the run up; the row is deleted when the run finishes. The Go
dispatcher never reads that table, so the control plane holds no secrets at all.

Two habits enforced here rather than hoped for:

* `ProviderKeys` has a `__repr__` that shows provider names and nothing else, because key
  material leaks through `repr()` far more often than through a deliberate log line;
* only `sources()` is ever persisted or returned by the API - which provider came from the UI
  and which from the environment, never the value.
"""

from __future__ import annotations

import os
from enum import StrEnum
from typing import Final

from cryptography.fernet import Fernet, InvalidToken

from research_agent.errors import AgentError, AgentException, ErrorCode


class Provider(StrEnum):
    GEMINI = "gemini"
    OPENAI = "openai"
    TAVILY = "tavily"
    BRAVE = "brave"
    OLLAMA = "ollama"

    @property
    def env_var(self) -> str:
        return _ENV_VARS[self]

    @property
    def is_secret(self) -> bool:
        """Ollama takes a base URL, not a key: it is configuration, not something to hide."""
        return self is not Provider.OLLAMA


_ENV_VARS: Final[dict[Provider, str]] = {
    Provider.GEMINI: "GEMINI_API_KEY",
    Provider.OPENAI: "OPENAI_API_KEY",
    Provider.TAVILY: "TAVILY_API_KEY",
    Provider.BRAVE: "BRAVE_API_KEY",
    Provider.OLLAMA: "OLLAMA_BASE_URL",
}


class KeySource(StrEnum):
    UI = "ui"
    ENV = "env"


class KeyError_(AgentException):
    """Named with a trailing underscore to leave the builtin `KeyError` alone."""

    def __init__(self, decision: str, detail: str) -> None:
        super().__init__(
            AgentError(
                code=ErrorCode.CONFIG_INVALID, node="keys", decision=decision, outcome=detail
            )
        )


class SecretBox:
    """Symmetric encryption for values on their way into `run_secrets`.

    Fernet rather than a hand-rolled scheme: authenticated, versioned, and rotation is a
    `MultiFernet` away when this grows up (v0.6 §19 moves it to a KMS).
    """

    def __init__(self, secret_key: str | None = None) -> None:
        key = secret_key if secret_key is not None else os.environ.get("APP_SECRET_KEY", "")
        if not key:
            raise KeyError_(
                "refuse to start",
                "APP_SECRET_KEY is not set; generate one with "
                '`python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"`',
            )
        try:
            self._fernet = Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise KeyError_(
                "refuse to start", f"APP_SECRET_KEY is not a valid Fernet key: {exc}"
            ) from exc

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken:
            raise KeyError_(
                "fail the run",
                "stored provider key cannot be decrypted; APP_SECRET_KEY changed since it "
                "was written",
            ) from None

    def __repr__(self) -> str:
        return "SecretBox(<key hidden>)"


class ProviderKeys:
    """The keys one run may use, with where each came from."""

    def __init__(self, keys: dict[Provider, tuple[str, KeySource]]) -> None:
        self._keys = keys

    @classmethod
    def resolve(cls, supplied: dict[Provider, str] | None = None) -> ProviderKeys:
        """UI values first, environment defaults second; blank means "not supplied"."""
        resolved: dict[Provider, tuple[str, KeySource]] = {}
        given = supplied or {}
        for provider in Provider:
            from_ui = (given.get(provider) or "").strip()
            if from_ui:
                resolved[provider] = (from_ui, KeySource.UI)
                continue
            from_env = (os.environ.get(provider.env_var) or "").strip()
            if from_env:
                resolved[provider] = (from_env, KeySource.ENV)
        return cls(resolved)

    def get(self, provider: Provider) -> str | None:
        entry = self._keys.get(provider)
        return entry[0] if entry else None

    def require(self, provider: Provider) -> str:
        value = self.get(provider)
        if value is None:
            raise KeyError_(
                "fail the run",
                f"no key for {provider.value}: set it in the UI or as {provider.env_var}",
            )
        return value

    def available(self) -> frozenset[Provider]:
        return frozenset(self._keys)

    def sources(self) -> dict[str, str]:
        """What goes into `runs.key_sources` and out through the API: names only."""
        return {provider.value: source.value for provider, (_, source) in self._keys.items()}

    def encrypted(self, box: SecretBox) -> dict[Provider, str]:
        """Ciphertexts for the secret providers, ready to insert into `run_secrets`."""
        return {
            provider: box.encrypt(value)
            for provider, (value, _) in self._keys.items()
            if provider.is_secret
        }

    @classmethod
    def from_ciphertexts(
        cls, box: SecretBox, rows: dict[Provider, str], *, source: KeySource = KeySource.UI
    ) -> ProviderKeys:
        """Rebuild inside the agent from `run_secrets`, then fill the gaps from the environment."""
        decrypted = {provider: (box.decrypt(value), source) for provider, value in rows.items()}
        merged = dict(cls.resolve()._keys)
        merged.update(decrypted)
        return cls(merged)

    def __repr__(self) -> str:
        listed = ", ".join(
            f"{provider.value}={source.value}" for provider, (_, source) in self._keys.items()
        )
        return f"ProviderKeys({listed})"

    __str__ = __repr__
