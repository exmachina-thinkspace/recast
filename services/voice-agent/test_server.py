import sys
import types
import unittest
from unittest.mock import patch

for dependency in ("crime_search", "permit_search", "business_search"):
    sys.modules.setdefault(dependency, types.ModuleType(dependency))

import server


class VoiceAgentHealthTests(unittest.TestCase):
    def test_audio_suffix_uses_browser_mime_type(self):
        self.assertEqual(server._audio_suffix("audio/webm;codecs=opus"), ".webm")
        self.assertEqual(server._audio_suffix("audio/mp4"), ".m4a")
        self.assertEqual(server._audio_suffix("audio/wav"), ".wav")

    @patch.object(server.os.path, "isdir", return_value=True)
    @patch.object(server, "_endpoint_ready", side_effect=[False, True])
    def test_health_distinguishes_qwen_from_other_capabilities(self, _ready, _isdir):
        health = server.service_health()

        self.assertTrue(health["ok"])
        self.assertFalse(health["agent_ready"])
        self.assertTrue(health["transcription_ready"])
        self.assertTrue(health["vision_ready"])
        self.assertEqual(health["models"]["agent"], server.VLLM_MODEL)
        self.assertEqual(health["models"]["transcription"], "faster-whisper/small")


if __name__ == "__main__":
    unittest.main()
