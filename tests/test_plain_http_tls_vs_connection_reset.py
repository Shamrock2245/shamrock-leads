"""plain_http: tls_cert_invalid only for a real certificate failure; a dropped handshake is egress_block/connection_reset."""
import ssl

import pytest
import requests

from scrapers import plain_http
from scrapers.plain_http import SourceTLSInvalid, request
from scrapers.scraper_resilience import EgressBlocked, classify_exception


class _Sess:
    def __init__(self, exc):
        self.exc = exc

    def request(self, *a, **k):
        raise self.exc


def _ssl_error(inner):
    try:
        try:
            raise inner
        except BaseException as e:
            raise requests.exceptions.SSLError(e) from e
    except requests.exceptions.SSLError as outer:
        return outer


def test_unexpected_eof_is_connection_reset_not_cert():
    exc = _ssl_error(ssl.SSLEOFError(8, "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol"))
    with pytest.raises(EgressBlocked) as ei:
        request(_Sess(exc), "GET", "https://x", county="indian_river")
    assert not isinstance(ei.value, SourceTLSInvalid)
    msg = str(ei.value)
    assert msg.startswith("egress_block:") and "connection_reset" in msg and "tls_cert_invalid" not in msg
    c = classify_exception(ei.value)
    assert c.egress_block and not c.retryable


def test_reset_during_handshake_is_connection_reset():
    exc = _ssl_error(ConnectionResetError(104, "Connection reset by peer"))
    with pytest.raises(EgressBlocked) as ei:
        request(_Sess(exc), "GET", "https://x", county="indian_river")
    assert not isinstance(ei.value, SourceTLSInvalid)
    assert "connection_reset" in str(ei.value)


def test_cert_verification_error_is_tls_cert_invalid():
    inner = ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate")
    with pytest.raises(SourceTLSInvalid) as ei:
        request(_Sess(_ssl_error(inner)), "GET", "https://x", county="indian_river")
    assert str(ei.value).startswith("tls_cert_invalid:")


def test_cert_failure_text_only_is_tls_cert_invalid():
    exc = requests.exceptions.SSLError("HTTPSConnectionPool: Max retries (Caused by SSLError(SSLCertVerificationError('certificate verify failed: self-signed certificate')))")
    with pytest.raises(SourceTLSInvalid):
        request(_Sess(exc), "GET", "https://x", county="polk")


def test_hostname_mismatch_is_tls_cert_invalid():
    exc = requests.exceptions.SSLError("hostname 'a' doesn't match 'b'")
    with pytest.raises(SourceTLSInvalid):
        request(_Sess(exc), "GET", "https://x", county="collier")


def test_still_verifies_and_honest_ua():
    s = plain_http.make_session()
    assert s.verify is True and "Chrome" not in s.headers["User-Agent"]
