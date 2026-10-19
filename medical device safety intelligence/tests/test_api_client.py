# File purpose: verifies HTTP request parameters, safe metadata, retries, and API error handling offline.
import pytest
from medical_device_safety.api_client import OpenFDAClient, APIError

# Fake response: supplies predictable HTTP status, headers, and JSON to the client.
class Response:
    status_code = 200
    headers = {}
    def raise_for_status(self): pass
    def json(self): return {"results": [{"id": 1}]}

# Fake session: records outgoing calls so tests can inspect parameters without the network.
class Session:
    def __init__(self): self.calls = []
    def get(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return Response()

# Arrange a fake key, send one request, then check it is sent but removed from returned metadata.
def test_client_sanitizes_key():
    session = Session()
    result = OpenFDAClient("fake-test-key", session=session).get("event", {"limit": 1})
    assert session.calls[0][1]["params"]["api_key"] == "fake-test-key"
    assert "api_key" not in result.params
    assert result.payload["results"][0]["id"] == 1
    assert result.attempts == 1

# Fake malformed response: triggers the JSON parsing error path.
class BadResponse(Response):
    def json(self): raise ValueError("bad")

# Return invalid JSON from a fake session and verify the client raises a safe APIError.
def test_malformed_json():
    class BadSession:
        def get(self, *args, **kwargs): return BadResponse()
    with pytest.raises(APIError, match="malformed JSON"):
        OpenFDAClient(session=BadSession()).get("event")

# Provide a continuation URL and confirm the client follows it without rebuilding its cursor.
def test_api_continuation_url_is_followed_verbatim():
    target = "https://api.fda.gov/device/event.json?search_after=opaque"
    class LinkResponse(Response):
        headers = {"Link": "<next>; rel=next"}
    class LinkSession:
        called = None
        def get(self, url, **kwargs):
            self.called = (url, kwargs)
            return LinkResponse()
    session = LinkSession()
    response = OpenFDAClient(session=session).get_url(target)
    assert session.called[0] == target
    assert response.endpoint == "continuation"


# Follow a continuation URL with a fake key, then verify the key is absent from response metadata.
def test_api_continuation_adds_configured_key_without_exposing_it_in_response():
    class LinkSession:
        called = None
        def get(self, url, **kwargs):
            self.called = (url, kwargs)
            return Response()
    session = LinkSession()
    response = OpenFDAClient("fake-test-key", session=session).get_url(
        "https://api.fda.gov/device/event.json?search_after=opaque"
    )
    assert session.called[1]["params"]["api_key"] == "fake-test-key"
    assert "api_key" not in response.params

# Simulate an HTTP 400 response and check the client reports the status as an APIError.
def test_http_error_is_reported():
    class ErrorResponse(Response):
        status_code = 400
        def raise_for_status(self): raise __import__('requests').HTTPError("bad request")
    class ErrorSession:
        def get(self, *args, **kwargs): return ErrorResponse()
    with pytest.raises(APIError, match="HTTP 400"):
        OpenFDAClient(session=ErrorSession()).get("recall")
