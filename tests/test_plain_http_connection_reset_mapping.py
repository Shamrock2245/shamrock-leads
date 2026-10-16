"""plain_http: aborted/reset/EOF-before-response -> egress_block connection_reset; timeouts and others unchanged."""
import http.client
import socket
import ssl

import pytest
import requests
from urllib3.exceptions import ProtocolError, ReadTimeoutError

from scrapers.plain_http import SourceTLSInvalid, request
from scrapers.scraper_resilience import EgressBlocked, classify_exception


class _Sess:
    def __init__(self, exc):
        self.exc = exc
        self.calls = []

    def request(self, *a, **k):
        self.calls.append(k)
        raise self.exc


def _wrap(outer_cls, inner):
    try:
        try:
            raise inner
        except BaseException as e:
            raise outer_cls(e) from e
    except outer_cls as o:
        return o


def _proto(inner):
    return ProtocolError("Connection aborted.", inner)


RESET_CASES = {
    "remote_disconnected_raw": http.client.RemoteDisconnected("Remote end closed connection without response"),
    "requests_conn_err_remote_disconnected": _wrap(
        requests.exceptions.ConnectionError,
        _proto(http.client.RemoteDisconnected("Remote end closed connection without response"))),
    "requests_conn_err_reset": _wrap(
        requests.exceptions.ConnectionError, _proto(ConnectionResetError(104, "Connection reset by peer"))),
    "requests_conn_err_aborted": _wrap(
        requests.exceptions.ConnectionError, _proto(ConnectionAbortedError(103, "Software caused connection abort"))),
    "urllib3_protocol_error_raw": _proto(ConnectionResetError(104, "Connection reset by peer")),
    "connection_reset_raw": ConnectionResetError(104, "Connection reset by peer"),
    "eof_before_response": _wrap(
        requests.exceptions.ConnectionError,
        _proto(ssl.SSLEOFError(8, "EOF occurred in violation of protocol"))),
    "bad_status_line_eof": _wrap(
        requests.exceptions.ConnectionError, _proto(http.client.RemoteDisconnected(""))),
}


@pytest.mark.parametrize("name", sorted(RESET_CASES))
def test_reset_paths_map_to_egress_block_connection_reset(name):
    sess = _Sess(RESET_CASES[name])
    with pytest.raises(EgressBlocked) as ei:
        request(sess, "GET", "https://x.example", county="Pitt")
    assert not isinstance(ei.value, SourceTLSInvalid)
    msg = str(ei.value)
    assert msg.startswith("egress_block: Pitt connection_reset:")
    assert "tls_cert_invalid" not in msg
    assert len(sess.calls) == 1  # never retried
    assert sess.calls[0]["verify"] is True


@pytest.mark.parametrize("name", sorted(RESET_CASES))
def test_health_labels_reset_as_egress_block(name):
    with pytest.raises(EgressBlocked) as ei:
        request(_Sess(RESET_CASES[name]), "GET", "https://x", county="Pitt")
    c = classify_exception(ei.value)
    assert c.error_class == "anti_bot" and c.egress_block is True and c.retryable is False


TIMEOUTS = [
    requests.exceptions.ReadTimeout("read timed out"),
    requests.exceptions.ConnectTimeout("connect timed out"),
    _wrap(requests.exceptions.ConnectionError, ReadTimeoutError(None, "/", "Read timed out. (Connection aborted.)")),
    _wrap(requests.exceptions.ConnectionError, socket.timeout("timed out")),
]


@pytest.mark.parametrize("exc", TIMEOUTS, ids=lambda e: type(e).__name__)
def test_timeouts_keep_existing_label(exc):
    with pytest.raises(type(exc)) as ei:
        request(_Sess(exc), "GET", "https://x", county="Pitt")
    assert ei.value is exc
    c = classify_exception(ei.value)
    assert c.error_class == "network" and not c.egress_block


@pytest.mark.parametrize("exc", [
    _wrap(requests.exceptions.ConnectionError, socket.gaierror(-2, "Name or service not known")),
    _wrap(requests.exceptions.ConnectionError, ConnectionRefusedError(111, "Connection refused")),
], ids=["dns", "refused"])
def test_other_connection_errors_unchanged(exc):
    with pytest.raises(type(exc)) as ei:
        request(_Sess(exc), "GET", "https://x", county="Pitt")
    assert ei.value is exc
    assert not classify_exception(ei.value).egress_block


def test_presented_and_rejected_cert_still_tls_cert_invalid():
    exc = _wrap(requests.exceptions.SSLError, ssl.SSLCertVerificationError(1, "certificate verify failed: self signed certificate"))
    with pytest.raises(SourceTLSInvalid) as ei:
        request(_Sess(exc), "GET", "https://x", county="Pitt")
    assert str(ei.value).startswith("tls_cert_invalid:")


def test_reset_inside_ssl_error_is_not_cert_invalid():
    exc = _wrap(requests.exceptions.SSLError, ConnectionResetError(104, "Connection reset by peer"))
    with pytest.raises(EgressBlocked) as ei:
        request(_Sess(exc), "GET", "https://x", county="Pitt")
    assert not isinstance(ei.value, SourceTLSInvalid) and "connection_reset" in str(ei.value)
