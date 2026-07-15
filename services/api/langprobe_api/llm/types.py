"""Domain types for the LLM dispatch gateway.

We intentionally do NOT import litellm here — these types are the
contract between callers and the gateway. The gateway module imports
litellm; nobody else does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ProviderName = Literal["anthropic", "openai", "gemini", "mistral", "deepseek", "groq"]
SurfaceName = Literal["playground", "comparisons", "studio", "luna", "eval", "poll"]
ErrorCode = Literal[
    "no_credential",
    "provider_error",
    "bad_model",
    "timeout",
    "ceiling_exceeded",
]

SUPPORTED_PROVIDERS: frozenset[str] = frozenset(
    ["anthropic", "openai", "gemini", "mistral", "deepseek", "groq", "stub"]
)


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str


@dataclass(frozen=True)
class DispatchResult:
    text: str
    prompt_tokens: int | None
    completion_tokens: int | None
    cost_usd: float | None
    provider: str
    model: str
    raw: dict[str, Any] = field(default_factory=dict)


class DispatchError(Exception):
    """Domain error from the gateway. Caller surfaces translate this to
    their own row-failure shape."""

    def __init__(
        self,
        code: ErrorCode,
        provider: str | None,
        detail: str,
    ) -> None:
        super().__init__(f"[{code}] {provider or '-'}: {detail}")
        self.code: ErrorCode = code
        self.provider = provider
        self.detail = detail


def provider_from_model(model: str) -> str:
    """Extract the provider from a `<provider>/<model>` string.

    Raises `DispatchError(code='bad_model')` for unknown prefixes or
    unprefixed models. Surface tables stamp the prefix at write time;
    we never guess.
    """
    if "/" not in model:
        raise DispatchError("bad_model", None, f"model must be '<provider>/<id>', got {model!r}")
    provider = model.split("/", 1)[0]
    if provider not in SUPPORTED_PROVIDERS:
        raise DispatchError("bad_model", None, f"unsupported provider {provider!r}")
    return provider
