"""Run tau2's voice user simulator entirely on an OpenAI-compatible hub.

Import this module before tau2 runs anything. It installs two overrides:

1. TTS: replaces the ElevenLabs call that tau2.voice.synthesis.synthesize.synthesize_voice()
   makes, so every user utterance and every out-of-turn insert (vocal tics, non-directed
   phrases) is synthesized by the hub.
2. Hardcoded LLM calls: tau2 makes some LLM calls with a fixed model and no api_base, so
   with only a hub key they fail. Each is routed to the hub by its call_name:
   - backchannel_decision / interruption_decision (voice user simulator): model
     VOICE_USER_SIMULATOR_DECISION_MODEL = "gpt-4.1" at api.openai.com. On failure tau2
     answers "no", so the simulated user silently never backchannels or interrupts.
   - llm_judge_hallucination_check (on by default in full-duplex runs,
     --hallucination-retries 3): --review-model, default "claude-opus-4-5". On failure no
     hallucinated conversation is detected or re-run.
   - llm_judge_*review and classify_authentication (only with --auto-review): same model.

No file under src/tau2 is modified.
"""

import os
import re
from copy import deepcopy

import websockets
from openai import OpenAI

import tau2.evaluator.auth_classifier as tau2_auth_classifier
import tau2.evaluator.hallucination_reviewer as tau2_hallucination_reviewer
import tau2.evaluator.review_llm_judge as tau2_review_llm_judge
import tau2.evaluator.review_llm_judge_user_only as tau2_review_user_only
import tau2.user.user_simulator_streaming as tau2_user_streaming
import tau2.voice.audio_native.openai.provider as tau2_realtime_provider
import tau2.voice.synthesis.synthesize as tau2_synthesize
from tau2.data_model.audio import AudioData, AudioEncoding, AudioFormat
from tau2.data_model.voice_personas import (
    ALL_PERSONAS,
    DEFAULT_PERSONA_NAME,
    get_persona_name_by_voice_id,
)
from tau2.voice.utils.audio_preprocessing import resample_audio

BASE_URL = os.getenv("TAU2_USER_TTS_BASE_URL", "https://inference-api.nvidia.com/v1")
MODEL = os.getenv("TAU2_USER_TTS_MODEL", "openai/openai/gpt-4o-mini-tts")
API_KEY = os.getenv("TAU2_USER_TTS_API_KEY") or os.getenv("OPENAI_API_KEY")
DECISION_MODEL = os.getenv("TAU2_USER_DECISION_MODEL", "openai/azure/openai/gpt-4.1")
DECISION_BASE_URL = os.getenv("TAU2_USER_DECISION_BASE_URL", BASE_URL)
DECISION_CALLS = ("backchannel_decision", "interruption_decision")
REVIEW_MODEL = os.getenv("TAU2_REVIEW_MODEL", "openai/azure/openai/gpt-5.2")
REVIEW_BASE_URL = os.getenv("TAU2_REVIEW_BASE_URL", BASE_URL)
REVIEW_CALLS = ("llm_judge_", "classify_authentication")  # call_name prefixes
TTS_RATE = 24000  # response_format="pcm" is 24 kHz, 16-bit, mono

# persona -> gpt-4o-mini-tts voice (gender-matched). The accent/style comes from
# the persona's own voice-design prompt in tau2 (passed as `instructions`).
VOICE_MAP = {
    "matt_delaney": "ash",
    "lisa_brenner": "coral",
    "mildred_kaplan": "sage",
    "arjun_roy": "echo",
    "wei_lin": "shimmer",
    "mamadou_diallo": "onyx",
    "priya_patil": "nova",
}
_TIC = re.compile(r"^\W*(\[(cough|sneeze|sniffle)\]\s*)+$", re.IGNORECASE)
# the same tags inside a sentence (tau2's in-turn vocal tics, e.g. "five, .[sneeze][sneeze] seven")
_INLINE_TIC = re.compile(r"\.?\s*(?:\[(cough|sneeze|sniffle)\]\s*)+", re.IGNORECASE)
_INLINE_TIC_SOUNDS = {
    "cough": " (ahem, hkh-hkh) ",
    "sneeze": " (ah... ah-choo!) ",
    "sniffle": " (sniff, sniff) ",
}
_INLINE_TIC_NOTE = (
    "\nThe parts in parentheses such as (ahem, hkh-hkh), (ah... ah-choo!) or "
    "(sniff, sniff) are a cough, a sneeze or a sniffle in the middle of speaking: "
    "perform them as real, brief sounds, not as words, then carry on speaking."
)
_PAUSE = re.compile(r"\[pause\]", re.IGNORECASE)
_TAG = re.compile(r"\[[a-z_ ]+\]", re.IGNORECASE)
# gpt-4o-mini-tts reads "[cough]" or "*cough*" as the word; sound-words get closest to a sound.
_TIC_SOUNDS = {
    "cough": (
        "Ahem! Hkh-hkh. Hkh-hkh.",
        "Clear your throat and cough. Nonverbal sounds only; no words.",
    ),
    "sneeze": ("Ah... ah... ah-choo!", "A real sneeze, not spoken words."),
    "sniffle": (
        "Sniff... sniff.",
        "Short nasal sniffing sounds, breathy, no voiced words.",
    ),
}
# tau2 synthesizes some user speech inside its tick loop (e.g. when the user interrupts),
# so a hung request freezes the whole simulation: the SDK default (600 s, 2 retries) once
# froze a run for 88 s and cost it its connection. Fail fast instead; tau2's @tts_retry
# retries timeouts (3 attempts with backoff).
TTS_TIMEOUT_S = float(os.getenv("TAU2_USER_TTS_TIMEOUT_S", "15"))
_client = OpenAI(
    api_key=API_KEY, base_url=BASE_URL, timeout=TTS_TIMEOUT_S, max_retries=0
)


def _request(text: str, persona: str) -> tuple[str, str]:
    """Return (input, instructions) for one utterance."""
    style = ALL_PERSONAS[persona].prompt
    tic = _TIC.match(text.strip())
    if tic:  # ElevenLabs v3 audio tags such as ".[cough][cough][cough]": render a non-word sound
        return _TIC_SOUNDS[tic.group(2).lower()]
    note = _INLINE_TIC_NOTE if _INLINE_TIC.search(text) else ""
    text = _INLINE_TIC.sub(lambda m: _INLINE_TIC_SOUNDS[m.group(1).lower()], text)
    text = _TAG.sub("", _PAUSE.sub("...", text))
    text = re.sub(r"\s+", " ", text).strip() or "..."
    return (
        text,
        f"{style}\nRead the text exactly as written, as a caller on a phone line.{note}",
    )


def ihub_tts(text, config):
    """Drop-in for tau2's tts_elevenlabs(text, config) -> AudioData (PCM16 at config's rate)."""
    persona = (
        get_persona_name_by_voice_id(config.voice_id or "") or DEFAULT_PERSONA_NAME
    )
    tts_input, instructions = _request(text, persona)
    response = _client.audio.speech.create(
        model=MODEL,
        voice=VOICE_MAP[persona],
        input=tts_input,
        instructions=instructions,
        response_format="pcm",
    )
    pcm = response.read()
    if not pcm:
        raise ValueError(f"user TTS returned empty audio for {text!r}")
    audio = AudioData(
        data=pcm,
        format=AudioFormat(encoding=AudioEncoding.PCM_S16LE, sample_rate=TTS_RATE),
    )
    target = deepcopy(config.output_audio_format)
    return (
        audio
        if target.sample_rate == TTS_RATE
        else resample_audio(audio, target.sample_rate)
    )


def _route(module, call_names, model, base_url):
    """Send ``module``'s generate() calls whose call_name starts with ``call_names`` to the hub."""
    original = module.generate

    def routed(*args, **kwargs):
        if str(kwargs.get("call_name") or "").startswith(call_names):
            kwargs["model"] = model
            kwargs.setdefault("api_base", base_url)
        return original(*args, **kwargs)

    module.generate = routed


tau2_synthesize.tts_elevenlabs = ihub_tts
_route(tau2_user_streaming, DECISION_CALLS, DECISION_MODEL, DECISION_BASE_URL)
for _module in (
    tau2_hallucination_reviewer,
    tau2_review_llm_judge,
    tau2_review_user_only,
    tau2_auth_classifier,
):
    _route(_module, REVIEW_CALLS, REVIEW_MODEL, REVIEW_BASE_URL)


class _NoPingWebsockets:
    """``websockets`` as tau2's Realtime provider sees it, with the client keepalive off."""

    def __getattr__(self, name):
        return getattr(websockets, name)

    @staticmethod
    def connect(*args, **kwargs):
        kwargs.setdefault("ping_interval", None)
        return websockets.connect(*args, **kwargs)


tau2_realtime_provider.websockets = _NoPingWebsockets()
