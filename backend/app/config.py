"""Central settings. Everything environment-specific lives here and nowhere else.

Every AI endpoint is an OpenAI-compatible base URL, so switching provider is a config change.
The pipeline has two steps, and each step picks its provider:
- perceive (audio, images, documents): Nemotron 3 Nano Omni. Only NVIDIA build serves it; it is
  not in the Token Factory catalogue (tested 2026-10-12). So PERCEIVE_PROVIDER stays nvidia.
- reason (extraction, cross-checks, summaries): Nemotron 3 Super. REASON_PROVIDER is nvidia for
  day-to-day development and nebius (Token Factory) for the final eval and the submitted demo,
  which the hackathon requires.

Backups (2026-10-12): if the primary hangs or fails, extraction moves to the same Super on the
other provider, and document photos to Gemma 3 27B on Token Factory (best of the two vision models
there on the demo documents). Audio has no backup. See `perceive_fallback` and `reason_fallback`.

Keys: NVIDIA_API_KEY and NEBIUS_API_KEY; each step uses its provider's key. MODEL_PERCEIVE and
MODEL_REASON override the profile's model. Model IDs differ between providers (even in case), so
copy them exactly from each provider's model list.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Provider = Literal["nvidia", "nebius"]

# Model IDs verified against each provider's model list on the date shown.
PROFILES: dict[str, dict[str, str | None]] = {
    "nvidia": {  # 2026-10-11
        "base_url": "https://integrate.api.nvidia.com/v1",
        "perceive": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        "reason": "nvidia/nemotron-3-super-120b-a12b",
    },
    "nebius": {  # 2026-10-12
        "base_url": "https://api.tokenfactory.us-central1.nebius.com/v1/",
        "perceive": None,  # no Nano Omni and no audio-capable model on Token Factory
        "reason": "nvidia/nemotron-3-super-120b-a12b",
    },
}

# backend/.env, found the same way whichever folder a command is run from.
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Endpoint(BaseModel):
    """Where one pipeline step sends its model calls."""

    provider: Provider
    base_url: str
    api_key: str
    model: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore", populate_by_name=True)

    perceive_provider: Provider = "nvidia"
    reason_provider: Provider = Field(
        default="nvidia", validation_alias=AliasChoices("REASON_PROVIDER", "AI_PROVIDER")
    )
    nvidia_api_key: str = Field(default="", validation_alias=AliasChoices("NVIDIA_API_KEY"))
    nebius_api_key: str = Field(default="", validation_alias=AliasChoices("NEBIUS_API_KEY"))

    # Optional overrides. Left empty, the provider profile above fills them in.
    model_perceive: str = ""
    model_reason: str = ""

    # NVIDIA's docs for Nano Omni say audio input needs thinking off and temperature 0.
    audio_disable_thinking: bool = True
    # Reading document photos with thinking on took 74 to 90 s for two photos, off under 1 s
    # (tested 2026-10-12). Off by default; "Try a careful read" turns it on for one visit.
    document_thinking: bool = False

    # Per attempt. The SDK retries timeouts, 429 and 5xx with backoff (0.5 s doubling, max 8 s),
    # so the worst case is about (retries + 1) x timeout. The free NVIDIA tier sometimes hangs
    # or answers 503 "ResourceExhausted" (tested 2026-10-11).
    request_timeout_s: float = 90.0
    request_max_retries: int = 3

    # Backup when the primary provider hangs or is busy (decided 2026-10-12). Extraction falls
    # back to the same Nemotron Super on the other provider. Document photos fall back to a
    # vision model on Token Factory, because Nano Omni is only on NVIDIA build. Audio has no
    # backup: no Token Factory model takes audio (model list, 2026-10-12). With a backup ready,
    # the primary gets a short timeout and one retry, so a hang costs about 1.5 minutes, not 6.
    fallback: bool = True
    model_perceive_fallback: str = "google/gemma-3-27b-it"
    fallback_timeout_s: float = 45.0
    fallback_primary_retries: int = 1

    # Medicine summaries (decided 2026-10-12): Tavily searches trusted sites only, Super
    # summarizes, and a summary is kept for 30 days per medicine.
    tavily_api_key: str = Field(default="", validation_alias=AliasChoices("TAVILY_API_KEY"))
    tavily_timeout_s: float = 20.0
    medicine_info_days: int = 30

    # Sign-in (decided 2026-10-12): phone + one-time code. There is no SMS provider yet, so
    # "simulated" shows the code on screen. Demo accounts let hackathon judges in without a phone.
    sms_provider: str = "simulated"
    demo_logins: bool = True

    # Dates the patient sees (consult day, medicine start) are taken in this zone; storage is UTC.
    timezone: str = "Asia/Kolkata"

    # SQLite file. Free hosts wipe the disk, so demo data is re-seeded at startup.
    database_path: str = str(ENV_FILE.parent / "data" / "app.sqlite3")

    @model_validator(mode="after")
    def _fill_from_profile(self) -> "Settings":
        self.model_perceive = self.model_perceive or PROFILES[self.perceive_provider]["perceive"] or ""
        self.model_reason = self.model_reason or PROFILES[self.reason_provider]["reason"] or ""
        if not self.model_perceive:
            raise ValueError(
                f"No perceive model on {self.perceive_provider}: Nano Omni is only on NVIDIA "
                "build. Set PERCEIVE_PROVIDER=nvidia, or MODEL_PERCEIVE to another model."
            )
        return self

    def _endpoint(self, provider: Provider, model: str) -> Endpoint:
        key = {"nvidia": self.nvidia_api_key, "nebius": self.nebius_api_key}[provider]
        base_url = PROFILES[provider]["base_url"] or ""
        return Endpoint(provider=provider, base_url=base_url, api_key=key, model=model)

    @property
    def perceive(self) -> Endpoint:
        return self._endpoint(self.perceive_provider, self.model_perceive)

    @property
    def reason(self) -> Endpoint:
        return self._endpoint(self.reason_provider, self.model_reason)

    @property
    def perceive_fallback(self) -> Endpoint | None:
        """Backup for document photos (never audio), or None if there is none to use."""
        if not (self.fallback and self.perceive_provider == "nvidia" and self.nebius_api_key
                and self.model_perceive_fallback):
            return None
        return self._endpoint("nebius", self.model_perceive_fallback)

    @property
    def reason_fallback(self) -> Endpoint | None:
        """The same reasoning model on the other provider, or None if there is none to use."""
        other: Provider = "nebius" if self.reason_provider == "nvidia" else "nvidia"
        key = {"nvidia": self.nvidia_api_key, "nebius": self.nebius_api_key}[other]
        model = PROFILES[other]["reason"]
        if not (self.fallback and key and model):
            return None
        return self._endpoint(other, model)


@lru_cache
def get_settings() -> Settings:
    return Settings()
