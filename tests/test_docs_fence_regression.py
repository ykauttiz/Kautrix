"""Synthetic Markdown fences and links, including raw CRLF and odd backticks.

    python -I tests/test_docs_fence_regression.py
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_docs_links import anchors, links, strip_fences  # noqa: E402


class FenceRegressionTests(unittest.TestCase):
    def test_real_heading_and_link_survive_inline_and_nested_fences(self):
        page = ("# First\n"
                "Text with inline ```markers``` does not start a fence.\n"
                "````md\n```\n# Hidden\n[hidden](bad.md)\n```\n````\n"
                "~~~python\n## Hidden tilde\n~~~\n"
                "## Last\n[real](good.md)\n")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "page.md")
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(page)
            self.assertEqual(anchors(path), {"first", "last"})
        self.assertEqual(links(page), ["good.md"])

    def test_raw_crlf_does_not_swallow_heading_after_closing_fence(self):
        page = "```sh\r\necho hi\r\n```\r\n## After\r\n[ok](ok.md)\r\n"
        self.assertIn("## After", strip_fences(page))
        self.assertEqual(links(page), ["ok.md"])

    def test_unclosed_and_indented_fences(self):
        page = "    ```not-a-fence\n## Visible\n~~~\n## Hidden\n"
        self.assertIn("## Visible", strip_fences(page))
        self.assertNotIn("## Hidden", strip_fences(page))

    def test_shorter_closing_fence_does_not_close_longer_one(self):
        page = "````python\n```\n## Hidden\n````\n## Visible\n"
        self.assertIn("## Visible", strip_fences(page))
        self.assertNotIn("## Hidden", strip_fences(page))

    def test_backtick_in_info_string_prevents_opening(self):
        page = "```python`not-a-fence\n## Visible\n"
        self.assertIn("## Visible", strip_fences(page))


    def test_mismatched_fence_characters_do_not_close_block(self):
        page = "~~~python\n```\n# Hidden\n~~~\n# Visible\n"
        self.assertIn("# Visible", strip_fences(page))
        self.assertNotIn("# Hidden", strip_fences(page))

    def test_indented_closing_fence_with_trailing_spaces(self):
        page = "   ```python\n# Hidden\n  ```   \n## Visible\n"
        self.assertEqual(strip_fences(page), "\n\n\n## Visible\n")

    def test_unclosed_fence_hides_remaining_links_and_headings(self):
        page = "## First\n```python\n[hidden](bad.md)\n## Hidden\n"
        self.assertEqual(links(page), [])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test.md")
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(page)
            self.assertEqual(anchors(path), {"first"})

if __name__ == "__main__":
    unittest.main()
