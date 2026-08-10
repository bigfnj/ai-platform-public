"""resolve_ollama_model — glob -> concrete installed model.

Locks the resolution behavior so it can't silently drift, including the documented
version-over-capability footgun (see test_unscoped_family_glob_...).
"""

import unittest

from app.ollama import resolve_ollama_model

# A representative slice of the RTX-4090 box's installed models (name + real
# parameter_size, as Ollama's /api/tags reports them).
TAGS = [
    {"name": "mistral-small3.2:24b", "details": {"parameter_size": "24.0B"}},
    {"name": "qwen3:30b-a3b", "details": {"parameter_size": "30.5B"}},
    {"name": "qwen3-coder:30b", "details": {"parameter_size": "30.5B"}},
    {"name": "qwen3:4b", "details": {"parameter_size": "4.0B"}},
    {"name": "llama3.1:8b", "details": {"parameter_size": "8.0B"}},
    {"name": "llama3.2:3b", "details": {"parameter_size": "3.2B"}},
    {"name": "gpt-oss:20b", "details": {"parameter_size": "20.9B"}},
    {"name": "nomic-embed-text:latest", "details": {"parameter_size": "334M"}},
]


class ResolveOllamaModel(unittest.TestCase):
    def _tags(self):
        return TAGS

    def test_plain_name_passes_through_without_touching_tags(self):
        # No glob char -> returned as-is; tags_fn must not even be consulted.
        def boom():
            raise AssertionError("tags_fn should not be called for a plain model name")

        self.assertEqual(resolve_ollama_model("qwen3:30b-a3b", boom), "qwen3:30b-a3b")

    def test_size_scoped_glob_resolves_and_floats_version(self):
        # The intended usage: scope with the size tag, version floats up over time.
        self.assertEqual(
            resolve_ollama_model("mistral-small3*:24b", self._tags), "mistral-small3.2:24b"
        )

    def test_size_tag_scope_excludes_other_sizes_and_siblings(self):
        # ':30b-a3b' excludes qwen3-coder:30b (tag ':30b') and qwen3:4b.
        self.assertEqual(resolve_ollama_model("qwen3*:30b-a3b", self._tags), "qwen3:30b-a3b")

    def test_same_version_tie_breaks_on_largest_params(self):
        # qwen3:* matches qwen3:30b-a3b and qwen3:4b (both v3); largest params wins.
        self.assertEqual(resolve_ollama_model("qwen3:*", self._tags), "qwen3:30b-a3b")

    def test_unscoped_family_glob_can_pick_smaller_newer_release(self):
        # Documents the footgun: version, not capability, decides — so a bare family
        # glob prefers llama3.2:3b over the more capable llama3.1:8b. Scope with a size
        # tag (llama3*:8b) to avoid this. If resolution ever changes, this fails loudly.
        self.assertEqual(resolve_ollama_model("llama3*", self._tags), "llama3.2:3b")

    def test_no_match_fails_loudly(self):
        with self.assertRaises(ValueError):
            resolve_ollama_model("does-not-exist*", self._tags)


if __name__ == "__main__":
    unittest.main()
