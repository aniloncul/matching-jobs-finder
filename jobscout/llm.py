"""Provider-agnostic chat transport for every LLM call in JobScout.

Everything above this module speaks one dialect: an OpenAI-style request
payload in, an OpenAI-style response dict out.  A provider module converts
that payload to the native wire format and back, so the cover letter, the CV
pipeline and the fit scorer never learn what vendor they are talking to.

Security rule taken from the benchmark repos: a *local-model* provider
(ollama) has no authentication and trusts whatever it talks to.  Pointing it
at a non-loopback address sends the candidate's whole profile somewhere else,
so that needs the explicit opt-in ``OLLAMA_ALLOW_REMOTE`` /
``COVER_LETTER_ALLOW_REMOTE``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse

import httpx

PROVIDERS = ("openai", "ollama", "anthropic", "gemini")

ANTHROPIC_BASE_URL = "https://api.anthropic.com"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
ANTHROPIC_VERSION = "2023-06-01"

# "ollama" counts as local: it is the docker-compose service name, and that
# network stays on this machine like a loopback address does.
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "ollama"}


class LLMError(Exception):
    """Raised when a model request fails or returns something unusable."""


@runtime_checkable
class LLMClient(Protocol):
    """The one method every provider transport must implement."""

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send an OpenAI-style chat payload, return an OpenAI-style body."""
        ...


def is_loopback(base_url: str) -> bool:
    try:
        host = urlparse(base_url).hostname or ""
    except ValueError:
        return False
    return host in _LOOPBACK_HOSTS or host.startswith("127.")


def detect_provider(base_url: str, provider: str = "auto") -> str:
    """Map ``auto`` onto a concrete provider from the base URL."""
    provider = (provider or "auto").strip().lower()
    if provider in PROVIDERS:
        return provider
    lowered = (base_url or "").lower()
    if "api.anthropic.com" in lowered:
        return "anthropic"
    if "generativelanguage.googleapis.com" in lowered:
        return "gemini"
    if any(host in lowered for host in _LOOPBACK_HOSTS):
        return "ollama"
    return "openai"


def _http_error(exc: httpx.HTTPError, timeout: float) -> LLMError:
    if isinstance(exc, httpx.TimeoutException):
        return LLMError(
            f"Model request timed out after {int(timeout)}s. "
            "Increase COVER_LETTER_TIMEOUT and retry."
        )
    return LLMError(f"Model request failed: {exc}")


def _parse_json_body(response: httpx.Response, label: str) -> dict[str, Any]:
    if response.status_code >= 400:
        raise LLMError(f"{label} returned {response.status_code}: {response.text[:600]}")
    try:
        data = response.json()
    except ValueError as exc:
        raise LLMError(f"{label} returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise LLMError(f"{label} returned an unexpected payload")
    return data


@dataclass
class OpenAICompatTransport:
    """Ollama, OpenAI, NVIDIA NIM and every other /chat/completions server."""

    base_url: str
    api_key: str
    timeout_seconds: float
    provider: str = "openai"
    allow_remote: bool = False

    def __post_init__(self) -> None:
        self.base_url = (self.base_url or "").rstrip("/")
        if self.provider == "ollama" and not is_loopback(self.base_url):
            if not self.allow_remote:
                raise LLMError(
                    f"Ollama base URL {self.base_url} is not a local address. "
                    "An unauthenticated ollama server sends the whole profile "
                    "there; set OLLAMA_ALLOW_REMOTE=1 if that is really intended, "
                    "or use provider=openai with an API key."
                )

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key or 'ollama'}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/chat/completions"
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(url, headers=headers, json=payload)
                if response.status_code >= 400 and (
                    "response_format" in payload
                    or "options" in payload
                    or "chat_template_kwargs" in payload
                ):
                    fallback = dict(payload)
                    fallback.pop("response_format", None)
                    fallback.pop("options", None)
                    fallback.pop("chat_template_kwargs", None)
                    response = client.post(url, headers=headers, json=fallback)
        except httpx.HTTPError as exc:
            raise _http_error(exc, self.timeout_seconds) from exc
        return _parse_json_body(response, "Model API")


def _split_system(payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    system_parts: list[str] = []
    chat: list[dict[str, Any]] = []
    for message in payload.get("messages", []):
        role = str(message.get("role", "user"))
        content = str(message.get("content") or "")
        if role == "system":
            system_parts.append(content)
        else:
            chat.append({"role": role, "content": content})
    return "\n\n".join(part for part in system_parts if part), chat


def _as_openai(content: str, finish_reason: str) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ]
    }


@dataclass
class AnthropicTransport:
    """Anthropic Messages API with the same OpenAI-shaped contract."""

    base_url: str
    api_key: str
    timeout_seconds: float

    def __post_init__(self) -> None:
        self.base_url = (self.base_url or ANTHROPIC_BASE_URL).rstrip("/")

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        system, chat = _split_system(payload)
        system_text = system
        if payload.get("response_format"):
            system_text = (
                system_text
                + "\n\nRespond with a single valid JSON object and nothing else."
            )
        body = {
            "model": payload.get("model", ""),
            "max_tokens": int(payload.get("max_tokens", 2048)),
            "temperature": float(payload.get("temperature", 0.2)),
            "messages": chat,
        }
        if system_text:
            body["system"] = system_text
        headers = {
            "x-api-key": self.api_key or "",
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(f"{self.base_url}/v1/messages", headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise _http_error(exc, self.timeout_seconds) from exc
        data = _parse_json_body(response, "Anthropic")
        blocks = data.get("content") or []
        text = "".join(
            str(block.get("text") or "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        )
        return _as_openai(text, str(data.get("stop_reason") or ""))


@dataclass
class GeminiTransport:
    """Google AI Studio / Gemini generateContent."""

    base_url: str
    api_key: str
    timeout_seconds: float

    def __post_init__(self) -> None:
        self.base_url = (self.base_url or GEMINI_BASE_URL).rstrip("/")

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        system, chat = _split_system(payload)
        config: dict[str, Any] = {
            "temperature": float(payload.get("temperature", 0.2)),
            "maxOutputTokens": int(payload.get("max_tokens", 2048)),
        }
        if payload.get("response_format"):
            config["responseMimeType"] = "application/json"
        body: dict[str, Any] = {
            "contents": [
                {"role": "model" if item["role"] == "assistant" else "user",
                 "parts": [{"text": item["content"]}]}
                for item in chat
            ],
            "generationConfig": config,
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        model = payload.get("model", "gemini-2.0-flash")
        url = f"{self.base_url}/models/{model}:generateContent"
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(
                    url,
                    params={"key": self.api_key or ""},
                    json=body,
                )
        except httpx.HTTPError as exc:
            raise _http_error(exc, self.timeout_seconds) from exc
        data = _parse_json_body(response, "Gemini")
        try:
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(str(part.get("text") or "") for part in parts)
        except (KeyError, IndexError, TypeError):
            text = ""
        return _as_openai(text, str(data.get("promptFeedback", {}).get("finishReason", "")))


def build_transport(
    *,
    provider: str = "auto",
    base_url: str,
    api_key: str,
    timeout_seconds: float,
    allow_remote: bool = False,
) -> LLMClient:
    """Pick the transport for one model configuration."""
    resolved = detect_provider(base_url, provider)
    if resolved == "anthropic":
        return AnthropicTransport(base_url, api_key, timeout_seconds)
    if resolved == "gemini":
        return GeminiTransport(base_url, api_key, timeout_seconds)
    return OpenAICompatTransport(
        base_url,
        api_key,
        timeout_seconds,
        provider=resolved,
        allow_remote=allow_remote,
    )


__all__ = [
    "ANTHROPIC_BASE_URL",
    "GEMINI_BASE_URL",
    "AnthropicTransport",
    "GeminiTransport",
    "LLMClient",
    "LLMError",
    "OpenAICompatTransport",
    "PROVIDERS",
    "build_transport",
    "detect_provider",
    "is_loopback",
]
