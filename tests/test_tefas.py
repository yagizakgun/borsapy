"""Tests for TEFAS provider error handling."""

import json
from unittest.mock import Mock

import httpx
import pytest

from borsapy._providers.tefas import TEFASProvider
from borsapy.exceptions import APIError


def _mock_response(body: bytes, content_type: str, status: int = 200) -> Mock:
    """Build a minimal httpx.Response mock with a working .json() method."""
    resp = Mock(spec=httpx.Response)
    resp.status_code = status
    resp.content = body
    resp.headers = {"content-type": content_type}
    resp.json = lambda: json.loads(body)
    return resp


class TestSafeJson:
    """TEFAS _safe_json raises descriptive APIError for non-JSON bodies (issue #14)."""

    def test_empty_body_raises_descriptive_error(self):
        resp = _mock_response(b"", "application/json")
        with pytest.raises(APIError) as exc_info:
            TEFASProvider._safe_json(resp, "GetAllFundAnalyzeData")
        msg = str(exc_info.value)
        assert "GetAllFundAnalyzeData" in msg
        assert "empty response" in msg
        assert "HTTP 200" in msg

    def test_whitespace_only_body_treated_as_empty(self):
        resp = _mock_response(b"   \n\t  ", "application/json")
        with pytest.raises(APIError, match="empty response"):
            TEFASProvider._safe_json(resp, "GetAllFundAnalyzeData")

    def test_html_body_raises_with_preview(self):
        body = b"<html><body>Under maintenance</body></html>"
        resp = _mock_response(body, "text/html; charset=utf-8")
        with pytest.raises(APIError) as exc_info:
            TEFASProvider._safe_json(resp, "GetAllFundAnalyzeData")
        msg = str(exc_info.value)
        assert "non-JSON" in msg
        assert "text/html" in msg
        assert "Under maintenance" in msg

    def test_malformed_json_raises_with_preview(self):
        resp = _mock_response(b"{not valid json", "application/json")
        with pytest.raises(APIError) as exc_info:
            TEFASProvider._safe_json(resp, "BindHistoryInfo")
        msg = str(exc_info.value)
        assert "malformed JSON" in msg
        assert "BindHistoryInfo" in msg
        assert "{not valid json" in msg

    def test_valid_json_returns_parsed_dict(self):
        resp = _mock_response(b'{"fundInfo": [{"FONKODU": "AFV"}]}', "application/json; charset=utf-8")
        result = TEFASProvider._safe_json(resp, "GetAllFundAnalyzeData")
        assert result == {"fundInfo": [{"FONKODU": "AFV"}]}

    def test_valid_json_with_uppercase_content_type(self):
        resp = _mock_response(b'{"ok": true}', "Application/JSON")
        result = TEFASProvider._safe_json(resp, "X")
        assert result == {"ok": True}

    def test_jsondecodeerror_is_chained_as_cause(self):
        resp = _mock_response(b"not json", "application/json")
        with pytest.raises(APIError) as exc_info:
            TEFASProvider._safe_json(resp, "X")
        assert isinstance(exc_info.value.__cause__, json.JSONDecodeError)


class TestPostJsonRetry:
    """TEFAS _post_json retries transient WAF blocks before giving up."""

    def _provider_with_responses(self, responses, monkeypatch):
        """Build a TEFASProvider whose _client.post yields the given responses in order."""
        provider = TEFASProvider.__new__(TEFASProvider)
        provider._client = Mock()
        provider._client.post = Mock(side_effect=responses)
        provider._cache = {}
        # Skip real sleep during tests
        monkeypatch.setattr("borsapy._providers.tefas.time.sleep", lambda _: None)
        return provider

    def test_recovers_after_transient_empty_body(self, monkeypatch):
        empty = _mock_response(b"", "text/html")
        empty.raise_for_status = Mock()
        good = _mock_response(b'{"fundInfo": [{"FONKODU": "AFV"}]}', "application/json")
        good.raise_for_status = Mock()

        provider = self._provider_with_responses([empty, good], monkeypatch)
        result = provider._post_json("http://x", {}, "GetAllFundAnalyzeData")
        assert result == {"fundInfo": [{"FONKODU": "AFV"}]}
        assert provider._client.post.call_count == 2

    def test_raises_after_max_retries(self, monkeypatch):
        empty = _mock_response(b"", "text/html")
        empty.raise_for_status = Mock()

        provider = self._provider_with_responses([empty, empty, empty], monkeypatch)
        with pytest.raises(APIError, match="empty response"):
            provider._post_json("http://x", {}, "GetAllFundAnalyzeData")
        assert provider._client.post.call_count == 3

    def test_does_not_retry_on_success(self, monkeypatch):
        good = _mock_response(b'{"ok": true}', "application/json")
        good.raise_for_status = Mock()

        provider = self._provider_with_responses([good], monkeypatch)
        assert provider._post_json("http://x", {}, "X") == {"ok": True}
        assert provider._client.post.call_count == 1
