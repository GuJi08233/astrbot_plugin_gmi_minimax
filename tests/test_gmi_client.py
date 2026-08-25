import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gmi_client", ROOT / "gmi_client.py")
gmi_client = importlib.util.module_from_spec(spec)
sys.modules["gmi_client"] = gmi_client
spec.loader.exec_module(gmi_client)

GMIClient = gmi_client.GMIClient
GMIError = gmi_client.GMIError
build_music_payload = gmi_client.build_music_payload
build_tts_payload = gmi_client.build_tts_payload
build_voice_clone_payload = gmi_client.build_voice_clone_payload
extract_media_urls = gmi_client.extract_media_urls


def _detail(status, outcome=None, request_id="req-1"):
    data = {"request_id": request_id, "status": status}
    if outcome is not None:
        data["outcome"] = outcome
    return data


class PayloadBuilderTest(unittest.TestCase):
    def test_music_payload_includes_prompt_only_when_present(self):
        payload = build_music_payload("[verse]\nhello", "indie folk")
        self.assertEqual(payload["lyrics"], "[verse]\nhello")
        self.assertEqual(payload["prompt"], "indie folk")
        self.assertEqual(payload["sample_rate"], 44100)
        self.assertEqual(payload["format"], "mp3")

        payload = build_music_payload("hello", "")
        self.assertNotIn("prompt", payload)

    def test_music_payload_rejects_empty_and_oversized_lyrics(self):
        with self.assertRaises(GMIError):
            build_music_payload("", "")
        with self.assertRaises(GMIError):
            build_music_payload("x" * 3501, "")

    def test_tts_payload_stringifies_numeric_fields(self):
        payload = build_tts_payload(
            "hello", voice_id="Chinese_voice", speed=1.2, pitch=-3
        )
        self.assertEqual(payload["speed"], "1.2")
        self.assertEqual(payload["pitch"], "-3")
        self.assertEqual(payload["voice_id"], "Chinese_voice")
        self.assertEqual(payload["emotion"], "auto")
        self.assertNotIn("sound_effects", payload)

    def test_tts_payload_omits_empty_voice_id(self):
        payload = build_tts_payload("hello")
        self.assertNotIn("voice_id", payload)

    def test_voice_clone_payload_requires_url_source(self):
        with self.assertRaises(GMIError):
            build_voice_clone_payload("hi", "C:/local/file.mp3")
        payload = build_voice_clone_payload("hi", "https://a.com/v.mp3")
        self.assertEqual(payload["source_audio"], "https://a.com/v.mp3")
        self.assertTrue(payload["need_volumn_normalization"])
        self.assertNotIn("prompt_audio", payload)

    def test_voice_clone_prompt_audio_requires_prompt_text(self):
        with self.assertRaises(GMIError):
            build_voice_clone_payload(
                "hi", "https://a.com/v.mp3", prompt_audio="https://a.com/p.mp3"
            )


class ExtractMediaUrlsTest(unittest.TestCase):
    def test_prefers_media_urls_then_medias_then_audio_url(self):
        detail = _detail(
            "success",
            {
                "media_urls": [{"id": "0", "url": "https://x/media.mp3"}],
                "audio_url": "https://x/audio.mp3",
            },
        )
        self.assertEqual(extract_media_urls(detail), ["https://x/media.mp3"])

        detail = _detail("success", {"audio_url": "https://x/audio.mp3"})
        self.assertEqual(extract_media_urls(detail), ["https://x/audio.mp3"])

        self.assertEqual(extract_media_urls(_detail("success", {})), [])


class GenerateFlowTest(unittest.TestCase):
    def _client(self):
        return GMIClient(
            "test-key", poll_interval=1, poll_timeout=30, request_timeout=30
        )

    def test_sync_success_fetches_outcome_via_get(self):
        client = self._client()
        submit = AsyncMock(return_value=_detail("success"))
        get = AsyncMock(
            return_value=_detail(
                "success", {"media_urls": [{"id": "0", "url": "https://x/a.mp3"}]}
            )
        )
        with patch.object(client, "submit", submit), patch.object(
            client, "get_request", get
        ):
            detail = asyncio.run(client.generate("m", {"text": "hi"}))
        self.assertEqual(extract_media_urls(detail), ["https://x/a.mp3"])
        get.assert_awaited_once_with("req-1")

    def test_queued_then_success_polls_until_done(self):
        client = self._client()
        submit = AsyncMock(return_value=_detail("queued"))
        get = AsyncMock(
            side_effect=[
                _detail("processing"),
                _detail(
                    "success",
                    {"media_urls": [{"id": "0", "url": "https://x/b.mp3"}]},
                ),
            ]
        )
        with patch.object(client, "submit", submit), patch.object(
            client, "get_request", get
        ), patch.object(gmi_client.asyncio, "sleep", AsyncMock()):
            detail = asyncio.run(client.generate("m", {"text": "hi"}))
        self.assertEqual(extract_media_urls(detail), ["https://x/b.mp3"])
        self.assertEqual(get.await_count, 2)

    def test_failed_status_raises_with_reason(self):
        client = self._client()
        submit = AsyncMock(
            return_value=_detail("failed", {"status": "lyrics_invalid"})
        )
        with patch.object(client, "submit", submit):
            with self.assertRaisesRegex(GMIError, "lyrics_invalid"):
                asyncio.run(client.generate("m", {"text": "hi"}))

    def test_missing_request_id_raises(self):
        client = self._client()
        submit = AsyncMock(return_value={"status": "success"})
        with patch.object(client, "submit", submit):
            with self.assertRaisesRegex(GMIError, "request_id"):
                asyncio.run(client.generate("m", {"text": "hi"}))

    def test_missing_api_key_raises(self):
        client = GMIClient("")
        with self.assertRaisesRegex(GMIError, "API Key"):
            asyncio.run(client.list_models())


if __name__ == "__main__":
    unittest.main()
