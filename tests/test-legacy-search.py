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
        self.assertEqual(results[0]["snippet"], "desc")

    def test_no_results_key(self):
        self.assertEqual(_extract_results({}), [])


class TestSearxngSearch(unittest.TestCase):
    @patch("services.legacy_search_proxy.urllib.request.urlopen")
    def test_returns_parsed_json(self, mock_urlopen):
        fake_resp = MagicMock()
        fake_resp.read.return_value = json.dumps({"results": []}).encode()
        mock_urlopen.return_value.__enter__ = MagicMock(return_value=fake_resp)
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        result = searxng_search("test query")
        self.assertEqual(result, {"results": []})


class TestSearchDispatch(unittest.TestCase):
    @patch("services.legacy_search_proxy.searxng_search")
    def test_default_source_is_searxng(self, mock_searxng):
        mock_searxng.return_value = {"results": []}
        search("query")
        mock_searxng.assert_called_once_with(None)


if __name__ == "__main__":
    unittest.main()
