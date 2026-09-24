"""Offline tests for I0 (tau2_ihub_overrides): TTS request shaping and LLM routing."""

from types import SimpleNamespace
from unittest import mock

import numpy as np
import pytest
import tau2_ihub_overrides as ihub

import tau2.voice.synthesis.synthesize as tau2_synthesize
from tau2.data_model.audio import AudioEncoding, AudioFormat
from tau2.data_model.voice_personas import ALL_PERSONAS


def _config(voice_id: str, rate: int = 16000):
    return SimpleNamespace(
        voice_id=voice_id,
        output_audio_format=AudioFormat(
            encoding=AudioEncoding.PCM_S16LE, sample_rate=rate
        ),
    )


def _pcm(seconds: float, rate: int = ihub.TTS_RATE) -> bytes:
    t = np.arange(int(seconds * rate)) / rate
    return (np.sin(2 * np.pi * 220 * t) * 8000).astype("<i2").tobytes()


@pytest.mark.parametrize(
    ("text", "tic"),
    [
        (".[cough][cough][cough]", "cough"),
        ("[sneeze]", "sneeze"),
        (" [Sniffle] [sniffle] ", "sniffle"),
    ],
)
def test_vocal_tics_become_sound_words(text, tic):
    assert ihub._request(text, "matt_delaney") == ihub._TIC_SOUNDS[tic]


def test_pause_and_other_tags():
    tts_input, instructions = ihub._request(
        "I was [pause] trying [laughs] to call", "wei_lin"
    )
    assert tts_input == "I was ... trying to call"
    assert instructions.startswith(ALL_PERSONAS["wei_lin"].prompt)
    assert ihub._request("[laughs]", "wei_lin")[0] == "..."


@pytest.mark.parametrize("persona", sorted(ALL_PERSONAS))
def test_each_official_voice_id_maps_to_its_persona(persona):
    response = mock.Mock()
    response.read.return_value = _pcm(0.5)
    with mock.patch.object(
        ihub._client.audio.speech, "create", return_value=response
    ) as create:
        ihub.ihub_tts(
            "Hello there.", _config(ALL_PERSONAS[persona].elevenlabs_voice_id)
        )
    kwargs = create.call_args.kwargs
    assert kwargs["voice"] == ihub.VOICE_MAP[persona]
    assert kwargs["model"] == ihub.MODEL
    assert kwargs["response_format"] == "pcm"
    assert ALL_PERSONAS[persona].prompt in kwargs["instructions"]


def test_resamples_24k_to_config_rate():
    response = mock.Mock()
    response.read.return_value = _pcm(1.0)
    with mock.patch.object(ihub._client.audio.speech, "create", return_value=response):
        audio = ihub.ihub_tts("Hello.", _config("unknown-id", 16000))
    assert audio.format.sample_rate == 16000
    assert audio.format.encoding == AudioEncoding.PCM_S16LE
    assert abs(len(audio.data) // 2 - 16000) <= 16  # one second, 16-bit mono


def test_empty_audio_raises():
    response = mock.Mock()
    response.read.return_value = b""
    with mock.patch.object(ihub._client.audio.speech, "create", return_value=response):
        with pytest.raises(ValueError):
            ihub.ihub_tts("Hello.", _config("unknown-id"))


def test_override_is_installed():
    assert tau2_synthesize.tts_elevenlabs is ihub.ihub_tts


def test_route_rewrites_only_matching_calls():
    original = mock.Mock(return_value="ok")
    module = SimpleNamespace(generate=original)
    ihub._route(module, ihub.DECISION_CALLS, "openai/hub-model", "https://hub/v1")

    module.generate(model="gpt-4.1", messages=[], call_name="backchannel_decision")
    assert original.call_args.kwargs["model"] == "openai/hub-model"
    assert original.call_args.kwargs["api_base"] == "https://hub/v1"

    module.generate(model="gpt-4.1", messages=[], call_name="interruption_decision")
    assert original.call_args.kwargs["model"] == "openai/hub-model"

    module.generate(model="keep-me", messages=[], call_name="user_streaming_response")
    assert original.call_args.kwargs == {
        "model": "keep-me",
        "messages": [],
        "call_name": "user_streaming_response",
    }


def test_route_keeps_an_explicit_api_base_and_matches_prefixes():
    original = mock.Mock()
    module = SimpleNamespace(generate=original)
    ihub._route(module, ihub.REVIEW_CALLS, "openai/judge", "https://hub/v1")
    module.generate(
        model="claude-opus-4-5",
        call_name="llm_judge_hallucination_check",
        api_base="https://other/v1",
    )
    assert original.call_args.kwargs["model"] == "openai/judge"
    assert original.call_args.kwargs["api_base"] == "https://other/v1"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "nine, five, .[sneeze][sneeze][sneeze] seven.",
            "nine, five, (ah... ah-choo!) seven.",
        ),
        (
            "Hello? Are .[sniffle][sniffle][sniffle] you still there?",
            "Hello? Are (sniff, sniff) you still there?",
        ),
        ("My ID is .[Cough][cough] emma.", "My ID is (ahem, hkh-hkh) emma."),
    ],
)
def test_in_turn_vocal_tics_are_rendered_in_place(text, expected):
    tts_input, instructions = ihub._request(text, "arjun_roy")
    assert tts_input == expected
    assert "perform them as real, brief sounds" in instructions


def test_plain_speech_has_no_tic_note():
    assert "perform them" not in ihub._request("Hello there.", "arjun_roy")[1]


def test_tts_client_fails_fast_and_leaves_retries_to_tau2():
    import openai

    from tau2.utils.retry import _is_retryable_tts_error

    assert ihub._client.max_retries == 0
    assert ihub._client.timeout == ihub.TTS_TIMEOUT_S == 15.0
    timeout = openai.APITimeoutError(request=mock.Mock())
    assert _is_retryable_tts_error(timeout)
