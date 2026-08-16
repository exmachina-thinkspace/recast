import base64
import os
import unittest
from unittest.mock import patch

import imagegen


ARTIFACT = {
    "artifacts": [{
        "base64": base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii"),
        "seed": 7,
        "finishReason": "SUCCESS",
    }]
}


class GenerateRequestFormatTests(unittest.TestCase):
    @patch.dict(os.environ, {"NVIDIA_API_KEY": "test-key"})
    @patch.object(imagegen, "resolve_backend", return_value="hosted")
    @patch.object(imagegen, "_post_json", return_value=(200, ARTIFACT))
    def test_hosted_upload_falls_back_to_base(self, post_json, _resolve_backend):
        result = imagegen.generate(
            "reuse this room",
            mode="depth",
            image="data:image/png;base64,AA==",
            seed=7,
        )

        payload = post_json.call_args.args[1]
        self.assertEqual(payload["mode"], "base")
        self.assertNotIn("image", payload)
        self.assertNotIn("preprocess_image", payload)
        self.assertEqual(payload["cfg_scale"], 3.5)
        self.assertEqual(result.mode, "base")
        self.assertIn("cannot apply arbitrary uploaded room images", result.notice)

    @patch.dict(os.environ, {"NVIDIA_API_KEY": "test-key"})
    @patch.object(imagegen, "resolve_backend", return_value="hosted")
    @patch.object(imagegen, "_post_json", return_value=(200, ARTIFACT))
    def test_hosted_example_id_uses_documented_format(self, post_json, _resolve_backend):
        reference = "data:image/png;example_id,2"
        result = imagegen.generate(
            "reuse the example room",
            mode="depth",
            image=reference,
            seed=7,
        )

        payload = post_json.call_args.args[1]
        self.assertEqual(payload["mode"], "depth")
        self.assertEqual(payload["image"], reference)
        self.assertNotIn("preprocess_image", payload)
        self.assertEqual(result.mode, "depth")
        self.assertIsNone(result.notice)

    @patch.object(imagegen, "resolve_backend", return_value="nim")
    @patch.object(imagegen, "_post_json", return_value=(200, ARTIFACT))
    def test_local_nim_keeps_uploaded_reference(self, post_json, _resolve_backend):
        reference = "data:image/png;base64,AA=="
        result = imagegen.generate(
            "reuse this room",
            mode="depth",
            image=reference,
            seed=7,
        )

        payload = post_json.call_args.args[1]
        self.assertEqual(payload["mode"], "depth")
        self.assertEqual(payload["image"], reference)
        self.assertTrue(payload["preprocess_image"])
        self.assertEqual(result.mode, "depth")
        self.assertIsNone(result.notice)


if __name__ == "__main__":
    unittest.main()
