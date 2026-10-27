"""Thin wrapper around an OpenAI-compatible chat API.

Design rules (they come from the project's principles):
- Media is sent from memory and never written to disk here (process, then purge).
- The rest of the app talks to this class, never to a vendor SDK directly, so the
  provider can change without touching the pipeline.
- When the primary provider hangs or fails, a backup answers if one is configured (see
  `Settings.perceive_fallback` and `reason_fallback`); every call records which model answered.
"""

from __future__ import annotations

import base64
import json
import re
import threading
from typing import Any, TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from app.config import Endpoint, Settings, get_settings

T = TypeVar("T", bound=BaseModel)

ContentPart = dict[str, Any]


def text_part(text: str) -> ContentPart:
    return {"type": "text", "text": text}


def image_part(data: bytes, mime: str = "image/png") -> ContentPart:
    b64 = base64.b64encode(data).decode()
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def audio_part(data: bytes, mime: str = "audio/wav") -> ContentPart:
    b64 = base64.b64encode(data).decode()
    return {"type": "audio_url", "audio_url": {"url": f"data:{mime};base64,{b64}"}}


class AIError(RuntimeError):
    """Raised when the model call fails or returns something we cannot use."""


Backup = tuple[Any, Endpoint]


class AIClient:
    """Perceive and reason can live on different providers (see app/config.py)."""

    def __init__(
        self,
        settings: Settings | None = None,
        client: Any | None = None,
        backup_client: Any | None = None,
    ):
        self.settings = s = settings or get_settings()
        self._local = threading.local()  # one visit per worker thread: its own `used` list
        # Tokens per "provider:model" over this client's life, for the eval's cost figures.
        self._usage: dict[str, dict[str, int]] = {}
        self._usage_lock = threading.Lock()
        # `client` replaces the SDK clients (tests pass a fake); a backup then exists only if
        # the test passes `backup_client` too.
        self.perceive_backup = self._backup(s.perceive_fallback, client, backup_client)
        self.reason_backup = self._backup(s.reason_fallback, client, backup_client)
        self.perceive_client = client or self._sdk(s.perceive)
        # Document photos with a backup ready: don't wait minutes on a hung primary.
        self.perceive_quick_client = client or (
            self._sdk(s.perceive, quick=True) if self.perceive_backup else self.perceive_client
        )
        self.reason_client = client or self._sdk(s.reason, quick=self.reason_backup is not None)

    def _sdk(self, endpoint: Endpoint, *, quick: bool = False) -> OpenAI:
        s = self.settings
        return OpenAI(
            base_url=endpoint.base_url,
            api_key=endpoint.api_key or "missing-key",
            timeout=s.fallback_timeout_s if quick else s.request_timeout_s,
            max_retries=s.fallback_primary_retries if quick else s.request_max_retries,
        )

    def _backup(self, endpoint: Endpoint | None, client: Any, backup_client: Any) -> Backup | None:
        if endpoint is None:
            return None
        if client is not None:
            return (backup_client, endpoint) if backup_client is not None else None
        return (self._sdk(endpoint), endpoint)

    @property
    def used(self) -> list[str]:
        """Models that answered on this thread, e.g. 'nvidia:nvidia/...' or '... (backup)'."""
        if not hasattr(self._local, "used"):
            self._local.used = []
        return self._local.used

    def _with_backup(
        self,
        client: Any,
        endpoint: Endpoint,
        backup: Backup | None,
        messages: list[dict[str, Any]],
        **kw: Any,
    ) -> str:
        try:
            out = self._chat(client, endpoint.model, messages, usage_key=f"{endpoint.provider}:{endpoint.model}",
                             **kw)
        except AIError as first:
            if backup is None:
                raise
            b_client, b_endpoint = backup
            try:  # the backup may have no thinking switch (Gemma), so it is left out
                out = self._chat(b_client, b_endpoint.model, messages,
                                 usage_key=f"{b_endpoint.provider}:{b_endpoint.model}",
                                 **{**kw, "extra_body": None})
            except AIError as second:
                raise AIError(f"{first}. The backup on {b_endpoint.provider} also failed: "
                              f"{second}") from second
            self.used.append(f"{b_endpoint.provider}:{b_endpoint.model} (backup)")
            return out
        self.used.append(f"{endpoint.provider}:{endpoint.model}")
        return out

    def _count(self, key: str, usage: Any) -> None:
        """Add one response's token counts (when the provider reports them) to the totals."""
        if usage is None:
            return
        prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion = int(getattr(usage, "completion_tokens", 0) or 0)
        # Part of the prompt the provider served from its cache (billed lower on Token Factory).
        cached = int(getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0)
        with self._usage_lock:
            t = self._usage.setdefault(key, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                                             "cached_tokens": 0})
            t["calls"] += 1
            t["prompt_tokens"] += prompt
            t["completion_tokens"] += completion
            t["cached_tokens"] += cached

    def usage(self, reset: bool = False) -> dict[str, dict[str, int]]:
        """Tokens per "provider:model" so far (a copy); `reset` starts the count again."""
        with self._usage_lock:
            out = {k: dict(v) for k, v in self._usage.items()}
            if reset:
                self._usage.clear()
        return out

    # ---- low level -------------------------------------------------------
    def _chat(
        self,
        client: Any,
        model: str,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        extra_body: dict[str, Any] | None = None,
        usage_key: str | None = None,
        allow_empty: bool = False,
    ) -> str:
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body=extra_body or None,
            )
        except Exception as exc:  # network, auth, bad model id, unsupported input...
            raise AIError(f"Model call failed ({model}): {exc}") from exc
        self._count(usage_key or model, getattr(resp, "usage", None))
        content = resp.choices[0].message.content
        if not content:
            if allow_empty:
                return ""
            raise AIError(f"Model {model} returned an empty response")
        return content

    # ---- perceive: audio / images / documents ---------------------------
    def perceive(
        self,
        parts: list[ContentPart],
        instruction: str,
        *,
        max_tokens: int = 8192,
        thinking: bool | None = None,
    ) -> str:
        """Send media plus an instruction to the perceive model and return its text.

        `thinking` None means the setting for the input: audio always runs with thinking off
        (NVIDIA's docs require it), documents follow `document_thinking`. True asks for a slower,
        more careful read (the "Try a careful read" button); it never applies to audio.
        """
        has_audio = any(p.get("type") == "audio_url" for p in parts)
        if has_audio and self.settings.audio_disable_thinking:
            think = False
        elif thinking is not None:
            think = thinking
        else:
            think = self.settings.document_thinking
        extra: dict[str, Any] | None = None
        temperature = 0.2
        if not think:
            extra = {"chat_template_kwargs": {"enable_thinking": False}}
            temperature = 0.0
        messages = [{"role": "user", "content": [*parts, text_part(instruction)]}]
        # Audio has no backup. A careful read (thinking on) takes minutes by design, so it keeps
        # the long timeout; a quick document read gives up early when a backup is ready.
        backup = None if has_audio else self.perceive_backup
        client = self.perceive_quick_client if backup and not think else self.perceive_client
        return self._with_backup(
            client,
            self.settings.perceive,
            backup,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            extra_body=extra,
        )

    def transcribe(self, wav: bytes, instruction: str, *, max_tokens: int = 2048) -> str:
        """Write down what one short WAV says (voice input), on Nano Omni only: no backup, thinking
        off as for all audio. Silence may give an empty string."""
        messages = [{"role": "user", "content": [audio_part(wav), text_part(instruction)]}]
        return self._with_backup(
            self.perceive_client, self.settings.perceive, None, messages, temperature=0.0,
            max_tokens=max_tokens, allow_empty=True,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}})

    # ---- reason: text in, text or validated JSON out --------------------
    def reason(
        self, system: str, user: str, *, max_tokens: int = 4096, temperature: float = 0.2,
        thinking: bool = True,
    ) -> str:
        """`thinking` False skips the model's reasoning: much faster, for simple tasks."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        return self._with_backup(
            self.reason_client,
            self.settings.reason,
            self.reason_backup,
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            extra_body=None if thinking else {"chat_template_kwargs": {"enable_thinking": False}},
        )

    def reason_json(
        self,
        system: str,
        user: str,
        schema: type[T],
        *,
        retries: int = 1,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        thinking: bool = True,
    ) -> T:
        """Ask for JSON matching `schema`; validate it; retry once with the error fed back.

        Temperature 0 by default: structured extraction should give the same answer every time.
        """
        instruction = (
            f"{system}\n\nReturn ONLY a JSON object matching this JSON Schema. "
            f"No prose, no code fences.\n{json.dumps(schema.model_json_schema())}"
        )
        prompt = user
        last_err: Exception | None = None
        for _ in range(retries + 1):
            raw = self.reason(instruction, prompt, max_tokens=max_tokens, temperature=temperature,
                              thinking=thinking)
            try:
                return schema.model_validate_json(_strip_fences(raw))
            except (ValidationError, ValueError) as exc:
                last_err = exc
                prompt = (
                    f"{user}\n\nYour previous answer was invalid ({exc}). "
                    "Return corrected JSON only."
                )
        raise AIError(f"Could not get valid JSON for {schema.__name__}: {last_err}")


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _strip_fences(raw: str) -> str:
    text = raw.strip()
    text = _FENCE.sub("", text).strip()
    # Some reasoning models prepend thinking; keep from the first '{' to the last '}'.
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end != -1 else text
