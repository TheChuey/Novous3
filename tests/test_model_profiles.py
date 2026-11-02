import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from modelProfiles.logic_modelProfiles import (
    model_profiles,
    size_from_name,
    size_from_parameter_size,
    suggested_profile,
)
from server import app


def facts(model, *, size_b=None, native=None, capabilities=("completion", "tools"), available=True):
    return {
        "model": model, "available": available, "parameter_size": None, "family": None,
        "native_context": native, "capabilities": list(capabilities), "size_b": size_b,
    }


class TestModelSizes(unittest.TestCase):
    def test_size_comes_from_the_tag_and_handles_the_e_prefix(self):
        self.assertEqual(size_from_name("qwen2.5-coder:7b"), 7)
        self.assertEqual(size_from_name("gemma4:e2b"), 2)
        self.assertEqual(size_from_name("qwen3:14b"), 14)
        self.assertEqual(size_from_name("llama3.1:8b-instruct"), 8)
        self.assertIsNone(size_from_name("nomic-embed-text:latest"))
        self.assertIsNone(size_from_name("mystery"))

    def test_parameter_size_text_is_read_in_billions(self):
        self.assertEqual(size_from_parameter_size("7.6B"), 7.6)
        self.assertEqual(size_from_parameter_size("500M"), 0.5)
        self.assertIsNone(size_from_parameter_size(None))

    def test_suggested_tiers_and_native_clamp(self):
        self.assertEqual(suggested_profile(2, 131072),
                         {"num_ctx": 8192, "reply_reserve": 2048, "max_tools": 4})
        self.assertEqual(suggested_profile(7, None),
                         {"num_ctx": 16384, "reply_reserve": 2048, "max_tools": 8})
        self.assertEqual(suggested_profile(14, None)["max_tools"], 12)
        self.assertEqual(suggested_profile(70, None), {"num_ctx": 32768, "reply_reserve": 2048, "max_tools": None})
        self.assertEqual(suggested_profile(None, None)["max_tools"], None)
        self.assertEqual(suggested_profile(2, 4096)["num_ctx"], 4096)


class TestModelProfileService(unittest.TestCase):
    def setUp(self):
        self.patch = patch.object(model_profiles, "_fetch_facts",
                                  side_effect=lambda model: facts(model, size_b=7, native=32768))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        model_profiles._facts.clear()

    def test_suggested_values_apply_until_a_profile_is_saved(self):
        effective = model_profiles.effective("qwen2.5-coder:7b")
        self.assertEqual(effective["source"], "suggested")
        self.assertEqual((effective["num_ctx"], effective["max_tools"]), (16384, 8))

        model_profiles.save("qwen2.5-coder:7b", 8192, 1024, 3)
        saved = model_profiles.effective("qwen2.5-coder:7b")
        self.assertEqual(saved["source"], "saved")
        self.assertEqual((saved["num_ctx"], saved["reply_reserve"], saved["max_tools"]), (8192, 1024, 3))

    def test_save_rejects_values_outside_the_model_limits(self):
        with self.assertRaisesRegex(ValueError, "num_ctx must be between"):
            model_profiles.save("qwen2.5-coder:7b", 1024, 256, None)
        with self.assertRaisesRegex(ValueError, "num_ctx must be between"):
            model_profiles.save("qwen2.5-coder:7b", 65536, 256, None)
        with self.assertRaisesRegex(ValueError, "reply_reserve must be between"):
            model_profiles.save("qwen2.5-coder:7b", 8192, 5000, None)
        with self.assertRaisesRegex(ValueError, "max_tools must be"):
            model_profiles.save("qwen2.5-coder:7b", 8192, 1024, 99)

    def test_reset_returns_to_suggested_values(self):
        model_profiles.save("qwen2.5-coder:7b", 8192, 1024, 2)
        reset = model_profiles.reset("qwen2.5-coder:7b")
        self.assertEqual(reset["source"], "suggested")
        self.assertEqual(reset["num_ctx"], 16384)

    def test_saved_context_is_clamped_when_the_model_is_smaller_than_saved(self):
        model_profiles.save("qwen2.5-coder:7b", 32768, 2048, None)
        with patch.object(model_profiles, "_fetch_facts",
                          side_effect=lambda model: facts(model, size_b=7, native=8192)):
            model_profiles._facts.clear()
            self.assertEqual(model_profiles.effective("qwen2.5-coder:7b")["num_ctx"], 8192)

    def test_list_models_reports_each_installed_model_with_its_profile(self):
        listed = {"models": [{"model": "qwen2.5-coder:7b"}, {"name": "gemma4:e2b"}]}
        with patch("modelProfiles.logic_modelProfiles.ollama.list", return_value=listed):
            result = model_profiles.list_models()
        self.assertIsNone(result["error"])
        self.assertEqual([m["model"] for m in result["models"]], ["gemma4:e2b", "qwen2.5-coder:7b"])

    def test_embedding_models_are_left_out_of_the_model_list(self):
        def fetch(model):
            capabilities = ["embedding"] if "embed" in model else ["completion", "tools"]
            return facts(model, size_b=7, native=32768, capabilities=capabilities)

        listed = {"models": [{"model": "nomic-embed-text:latest"}, {"model": "qwen2.5-coder:7b"}]}
        with patch.object(model_profiles, "_fetch_facts", side_effect=fetch), \
                patch("modelProfiles.logic_modelProfiles.ollama.list", return_value=listed):
            model_profiles._facts.clear()
            names = [m["model"] for m in model_profiles.list_models()["models"]]
        self.assertEqual(names, ["qwen2.5-coder:7b"])

    def test_list_models_reports_an_error_when_ollama_is_down(self):
        with patch("modelProfiles.logic_modelProfiles.ollama.list", side_effect=ConnectionError("down")):
            result = model_profiles.list_models()
        self.assertEqual(result["models"], [])
        self.assertIn("Could not list models", result["error"])


class TestModelProfileRoutes(unittest.TestCase):
    def setUp(self):
        self.patch = patch.object(model_profiles, "_fetch_facts",
                                  side_effect=lambda model: facts(model, size_b=7, native=32768))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        model_profiles._facts.clear()
        self.client = TestClient(app)

    def test_put_get_and_delete_handle_model_names_with_colons_and_slashes(self):
        model = "library/qwen2.5-coder:7b"
        saved = self.client.put(f"/api/model-profiles/{model}",
                                json={"num_ctx": 8192, "reply_reserve": 1024, "max_tools": 3})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["source"], "saved")

        invalid = self.client.put(f"/api/model-profiles/{model}",
                                  json={"num_ctx": 100, "reply_reserve": 1024, "max_tools": None})
        self.assertEqual(invalid.status_code, 400)

        reset = self.client.delete(f"/api/model-profiles/{model}")
        self.assertEqual(reset.json()["source"], "suggested")

    def test_get_lists_installed_models(self):
        with patch("modelProfiles.logic_modelProfiles.ollama.list",
                   return_value={"models": [{"model": "qwen2.5-coder:7b"}]}):
            response = self.client.get("/api/model-profiles")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["models"][0]["model"], "qwen2.5-coder:7b")


if __name__ == "__main__":
    unittest.main()
