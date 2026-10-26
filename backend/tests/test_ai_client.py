import pytest
from pydantic import BaseModel

from app.ai.client import AIClient, AIError, audio_part, text_part
from app.config import Settings


class _Msg:
    def __init__(self, c): self.content = c
class _Choice:
    def __init__(self, c): self.message = _Msg(c)
class _Resp:
    def __init__(self, c): self.choices = [_Choice(c)]


class FakeOpenAI:
    """Mimics openai.OpenAI().chat.completions.create and records calls."""
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.chat = self
        self.completions = self

    def create(self, **kw):
        self.calls.append(kw)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, _Resp) else _Resp(reply)


class Thing(BaseModel):
    name: str


def _client(replies):
    fake = FakeOpenAI(replies)
    return AIClient(Settings(_env_file=None, reason_provider="nebius"), client=fake), fake


KEYS = {"nvidia_api_key": "nv-key", "nebius_api_key": "nb-key"}


def test_hybrid_split_uses_each_providers_url_key_and_model():
    s = Settings(_env_file=None, perceive_provider="nvidia", reason_provider="nebius", **KEYS)
    assert s.perceive.provider == "nvidia" and "nvidia.com" in s.perceive.base_url
    assert s.perceive.api_key == "nv-key" and "omni" in s.perceive.model
    assert s.reason.provider == "nebius" and "nebius" in s.reason.base_url
    assert s.reason.api_key == "nb-key"


def test_old_ai_provider_variable_still_sets_reason_provider(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "nebius")
    assert Settings(_env_file=None).reason_provider == "nebius"


def test_perceive_on_nebius_fails_loudly_without_a_model():
    with pytest.raises(ValueError, match="Nano Omni"):
        Settings(_env_file=None, perceive_provider="nebius")
    s = Settings(_env_file=None, perceive_provider="nebius", model_perceive="openbmb/MiniCPM-V-4_5")
    assert s.perceive.model == "openbmb/MiniCPM-V-4_5"


def test_overrides_win():
    s = Settings(_env_file=None, model_reason="my/model")
    assert s.model_reason == "my/model"


def test_each_step_gets_its_own_sdk_client():
    c = AIClient(Settings(_env_file=None, reason_provider="nebius", request_max_retries=5,
                          fallback=False, **KEYS))
    assert "nvidia.com" in str(c.perceive_client.base_url)
    assert "nebius" in str(c.reason_client.base_url)
    assert c.reason_client.max_retries == 5
    assert c.perceive_backup is None and c.reason_backup is None


def test_with_a_backup_the_primary_gives_up_quickly():
    c = AIClient(Settings(_env_file=None, request_max_retries=5, **KEYS))
    assert "nebius" in str(c.reason_backup[0].base_url)  # Super on the other provider
    assert c.reason_backup[1].model == "nvidia/nemotron-3-super-120b-a12b"
    assert c.perceive_backup[1].model == "google/gemma-3-27b-it"
    assert c.reason_client.max_retries == 1 and c.reason_client.timeout == 45.0
    assert c.perceive_quick_client.max_retries == 1
    assert c.perceive_client.max_retries == 5  # audio and careful reads keep the long wait


class _Details:
    def __init__(self, cached): self.cached_tokens = cached
class _Usage:
    def __init__(self, p, c, cached=None):
        self.prompt_tokens, self.completion_tokens = p, c
        self.prompt_tokens_details = _Details(cached) if cached is not None else None


def test_token_usage_is_counted_per_provider_and_model():
    """For the eval's cost figures: each answer's tokens, by who answered (a backup apart)."""
    replies = [_Resp('{"name": "a"}'), _Resp("text")]
    replies[0].usage, replies[1].usage = _Usage(100, 20, cached=60), _Usage(50, 5)
    c, _ = _client(replies)
    c.reason_json("sys", "user", Thing)
    c.reason("sys", "user")
    key = "nebius:nvidia/nemotron-3-super-120b-a12b"
    assert c.usage() == {key: {"calls": 2, "prompt_tokens": 150, "completion_tokens": 25,
                               "cached_tokens": 60}}
    assert c.usage(reset=True)[key]["calls"] == 2 and c.usage() == {}
    c2, _ = _client(["no usage reported"])  # a provider that reports none: nothing counted
    c2.reason("sys", "user")
    assert c2.usage() == {}


def test_token_usage_of_a_backup_is_counted_under_the_backup():
    answer = _Resp('{"name": "ok"}')
    answer.usage = _Usage(40, 10)
    c, _, _ = _with_backup([TimeoutError("hung")], [answer])
    c.reason_json("sys", "user", Thing)
    assert c.usage() == {"nebius:nvidia/nemotron-3-super-120b-a12b":
                         {"calls": 1, "prompt_tokens": 40, "completion_tokens": 10, "cached_tokens": 0}}


def _with_backup(primary_replies, backup_replies):
    primary, backup = FakeOpenAI(primary_replies), FakeOpenAI(backup_replies)
    c = AIClient(Settings(_env_file=None, **KEYS), client=primary, backup_client=backup)
    return c, primary, backup


def test_reason_falls_back_to_the_other_provider_and_says_so():
    c, _, backup = _with_backup([TimeoutError("hung")], ['{"name": "ok"}'])
    assert c.reason_json("sys", "user", Thing).name == "ok"
    assert backup.calls[0]["model"] == "nvidia/nemotron-3-super-120b-a12b"
    assert c.used == ["nebius:nvidia/nemotron-3-super-120b-a12b (backup)"]


def test_documents_fall_back_to_the_vision_model_without_the_thinking_switch():
    c, primary, backup = _with_backup([RuntimeError("503 ResourceExhausted")], ["Rx text"])
    assert c.perceive([text_part("photo")], "read") == "Rx text"
    assert primary.calls[0]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert backup.calls[0]["model"] == "google/gemma-3-27b-it"
    assert backup.calls[0]["extra_body"] is None
    assert c.used[-1].endswith("(backup)")


def test_audio_has_no_backup():
    c, _, backup = _with_backup([RuntimeError("hung")], ["never used"])
    with pytest.raises(AIError):
        c.perceive([audio_part(b"abc")], "transcribe")
    assert backup.calls == []


def test_when_both_fail_the_error_names_both():
    c, _, _ = _with_backup([RuntimeError("hung")], [RuntimeError("also down")])
    with pytest.raises(AIError, match="backup on nebius also failed"):
        c.reason("sys", "user")


def test_the_primary_answering_is_recorded_too():
    c, _, backup = _with_backup(["fine"], [])
    assert c.reason("sys", "user") == "fine"
    assert c.used == ["nvidia:nvidia/nemotron-3-super-120b-a12b"] and backup.calls == []


def test_audio_turns_thinking_off_and_temperature_zero():
    c, fake = _client(["hello"])
    c.perceive([audio_part(b"abc")], "transcribe")
    call = fake.calls[0]
    assert call["temperature"] == 0.0
    assert call["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_documents_are_read_with_thinking_off_by_default():
    c, fake = _client(["ok"])
    c.perceive([text_part("x")], "read")
    assert fake.calls[0]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert fake.calls[0]["temperature"] == 0.0


def test_a_careful_read_turns_thinking_on_for_documents_only():
    c, fake = _client(["ok", "ok"])
    c.perceive([text_part("x")], "read", thinking=True)
    assert fake.calls[0]["extra_body"] is None  # the model's default: thinking on
    c.perceive([audio_part(b"abc")], "transcribe", thinking=True)
    assert fake.calls[1]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_reason_json_strips_fences_and_retries_once():
    c, fake = _client(["not json", '```json\n{"name": "ok"}\n```'])
    assert c.reason_json("sys", "user", Thing).name == "ok"
    assert len(fake.calls) == 2


def test_reason_json_gives_up():
    c, _ = _client(["nope", "still nope"])
    with pytest.raises(AIError):
        c.reason_json("sys", "user", Thing)


def test_empty_response_is_an_error():
    c, _ = _client([""])
    with pytest.raises(AIError):
        c.reason("s", "u")
