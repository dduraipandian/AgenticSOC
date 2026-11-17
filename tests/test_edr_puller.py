import httpx
import pytest

from pipeline.puller.adapters.adapters import (
    AuthError,
    InvalidCursor,
    RateLimited,
    UnexpectedResponse,
    VendorError,
)
from pipeline.puller.adapters.edr import EDR
from pipeline.puller.main import retriable_errors

CONFIG = {"url": "http://edr.test", "path": "/v2/detections", "api-key": "k", "limit": 2}


def client_with(*responses):
    """A client whose mock client returns these responses in order and records each request it gets."""
    queue, seen = list(responses), []

    def handler(request):
        seen.append(request)
        return queue.pop(0)

    client = httpx.Client(base_url=CONFIG["url"], transport=httpx.MockTransport(handler))
    return client, seen


def page(det_id="det-1", next_cursor="c-2", has_more=True):
    """A 200 response shaped like one page of /v2/detections."""
    return httpx.Response(
        200,
        json={
            "detections": [{"id": det_id, "host": "build-01"}],
            "next_cursor": next_cursor,
            "has_more": has_more,
        },
    )


def test_second_call_sends_the_cursor_from_the_first_page():
    client, seen = client_with(
        page("det-1", next_cursor="c-2"), page("det-2", next_cursor="c-3", has_more=False)
    )
    edr = EDR(CONFIG)

    first = edr.pull(client, None)
    second = edr.pull(client, first.next)

    assert "cursor" not in seen[0].url.params
    assert seen[1].url.params["cursor"] == "c-2"
    assert [e.vendor_event_id for e in first.events] == ["det-1"]
    assert [e.vendor_event_id for e in second.events] == ["det-2"]
    assert second.next == {"cursor": "c-3"}
    assert second.more is False


@pytest.mark.parametrize(
    "status, error, retried",
    [
        (401, AuthError, False),
        (403, AuthError, False),
        (400, InvalidCursor, False),
        (404, UnexpectedResponse, False),
        (500, VendorError, True),
        (503, VendorError, True),
    ],
)
def test_status_maps_to_the_right_error(status, error, retried):
    client, _ = client_with(httpx.Response(status, json={"detail": "nope"}))

    with pytest.raises(error):
        EDR(CONFIG).pull(client, None)

    # The puller loop retries exactly the errors in retriable_errors (plus RateLimited, handled separately).
    assert issubclass(error, retriable_errors) is retried


def test_429_carries_retry_after():
    client, _ = client_with(httpx.Response(429, headers={"Retry-After": "3"}))

    with pytest.raises(RateLimited) as exc:
        EDR(CONFIG).pull(client, None)

    assert exc.value.retry_after == 3
