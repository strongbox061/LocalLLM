#!/usr/bin/env python3
"""Smoke and unit tests for services/legacy_search_proxy.py."""

import json
import unittest
from unittest.mock import MagicMock, patch

from services.legacy_search_proxy import (
    _extract_results,
    brave_search,
    search,
    searxng_search,
)


class TestExtractResults(unittest.TestCase):
    def test_basic_extraction(self):
        data = {"results": [{"title": "A", "url": "http://a.com", "content": "desc"}]}
        results = _extract_results(data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "A")
        self.assertEqual(results[0]["url"], "http://a.com")
        self.assertEqual(results[0]["snippet"], "desc")

    def test_max_results_limit(self):
        data = {"results": [{"title": f"R{i}", "url": f"http://i", "content": ""} for i in range(20)]}
        results = _extract_results(data, max_results=5)
        self.assertEqual(len(results), 5)

    def test_no_results_key(self):
        self.assertEqual(_extract_results({}), [])

    def test_invalid_results_type(self):
        self.assertEqual(_extract_results({"results": "not a list"}), [])


class TestSearxngSearch(unittest.TestCase):
    @patch("services.legacy_search_proxy.urllib.request.urlopen")
    def test_returns_parsed_json(self, mock_urlopen):
        fake_resp = MagicMock()
        fake_resp.read.return_value = json.dumps({"results": []}).encode()
        mock_urlopen.return_value.__enter__ = MagicMock(return_value=fake_resp)
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        result = searxng_search("test query")
        self.assertEqual(result, {"results": []})

    @patch("services.legacy_search_proxy.urllib.request.urlopen")
    def test_http_error_raises_runtime_error(self, mock_urlopen):
        import urllib.error

        exc = urllib.error.HTTPError("http://x", 500, "Internal Error", {}, None)
        mock_urlopen.side_effect = exc
        with self.assertRaises(RuntimeError) as ctx:
            searxng_search("query")
        self.assertIn("500", str(ctx.exception))


class TestBraveSearch(unittest.TestCase):
    @patch.dict("os.environ", {"BRAVE_API_KEY": ""})
    def test_missing_api_key_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            brave_search("query")
        self.assertIn("BRAVE_API_KEY", str(ctx.exception))

    @patch("services.legacy_search_proxy.urllib.request.urlopen")
    @patch.dict("os.environ", {"BRAVE_API_KEY": "fake-key"})
    def test_sets_authorization_header(self, mock_urlopen):
        fake_resp = MagicMock()
        fake_resp.read.return_value = json.dumps({"web": {"results": []}}).encode()
        mock_urlopen.return_value.__enter__ = MagicMock(return_value=fake_resp)
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        brave_search("test")
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.get_header("Authorization"), "Bearer fake-key")


class TestSearchDispatch(unittest.TestCase):
    @patch("services.legacy_search_proxy.searxng_search")
    def test_default_source_is_searxng(self, mock_searxng):
        mock_searxng.return_value = {"results": []}
        search("query")
        mock_searxng.assert_called_once_with(None)

    @patch("services.legacy_search_proxy.brave_search")
    def test_brave_source_dispatches(self, mock_brave):
        mock_brave.return_value = {"web": {"results": []}}
        search("query", source="brave")
        mock_brave.assert_called_once_with("query")


if __name__ == "__main__":
    unittest.main()
