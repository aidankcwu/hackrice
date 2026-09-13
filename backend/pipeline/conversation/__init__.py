"""The voice agent (docs/CONVERSATION_DESIGN.md).

Three agents: T0 tags frames, T1 -- the clerk -- reasons in silence, and this
one owns the mouth. It holds one conversation at a time; the clerk can no
longer speak or ask directly, only hand off a topic and a reason.
"""

from __future__ import annotations

from .agent import ConversationAgent
from .client import (
    FakeVoiceClient,
    OpenAIVoiceClient,
    VoiceClient,
    make_voice_client,
)
from .prompts import VOICE_OBJECTIVE, build_voice_system_prompt
from .schema import VOICE_TEXT_FORMAT, VoiceReply, VoiceSettled

__all__ = [
    "ConversationAgent",
    "VoiceClient",
    "OpenAIVoiceClient",
    "FakeVoiceClient",
    "make_voice_client",
    "VOICE_OBJECTIVE",
    "build_voice_system_prompt",
    "VoiceReply",
    "VoiceSettled",
    "VOICE_TEXT_FORMAT",
]
