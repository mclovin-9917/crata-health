"""Sending text messages. All SMS goes through `SmsSender`.

The prototype has no SMS provider: `SimulatedSms` keeps the message and the sign-in screen shows
the code, labelled "SMS simulated". A real provider (for example an Indian DLT-registered
gateway) is a new class with the same `send`, chosen by `SMS_PROVIDER`.
"""

from __future__ import annotations

from typing import Protocol


class SmsSender(Protocol):
    simulated: bool

    def send(self, phone: str, text: str) -> None: ...


class SimulatedSms:
    """Nothing leaves the server. The last message per number is kept for the screen and tests."""

    simulated = True

    def __init__(self) -> None:
        self.sent: dict[str, str] = {}

    def send(self, phone: str, text: str) -> None:
        self.sent[phone] = text


def sms_sender(provider: str) -> SmsSender:
    if provider == "simulated":
        return SimulatedSms()
    raise ValueError(f"Unknown SMS provider '{provider}'. Only 'simulated' exists so far.")
