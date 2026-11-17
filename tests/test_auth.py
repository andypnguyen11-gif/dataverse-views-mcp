from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anyio
import pytest

from dataverse_views_mcp.auth import MsalTokenProvider
from dataverse_views_mcp.config import Settings
from dataverse_views_mcp.errors import AuthenticationError, ConfigurationError


class FakeMsalApp:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[list[str]] = []

    def acquire_token_for_client(self, scopes: list[str]) -> Any:
        self.calls.append(scopes)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class Factory:
    def __init__(self, app: FakeMsalApp) -> None:
        self.app = app
        self.kwargs: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> FakeMsalApp:
        self.kwargs.append(kwargs)
        return self.app


def get_token(provider: MsalTokenProvider) -> str:
    return anyio.run(provider.get_token)


def test_returns_token_for_the_environment_scope(settings: Settings) -> None:
    factory = Factory(FakeMsalApp({"access_token": "tok-1"}))

    token = get_token(MsalTokenProvider(settings, app_factory=factory))

    assert token == "tok-1"
    assert factory.app.calls == [["https://org.example.test/.default"]]
    assert factory.kwargs[0]["client_id"] == settings.client_id
    assert factory.kwargs[0]["authority"] == settings.authority
    assert factory.kwargs[0]["client_credential"] == "made-up-secret-value"
    assert factory.kwargs[0]["timeout"] == settings.http_timeout_seconds


def test_reuses_one_msal_client_so_its_token_cache_is_used(settings: Settings) -> None:
    factory = Factory(FakeMsalApp({"access_token": "tok-1"}))
    provider = MsalTokenProvider(settings, app_factory=factory)

    get_token(provider)
    get_token(provider)

    assert len(factory.kwargs) == 1
    assert len(factory.app.calls) == 2


def test_refused_sign_in_raises_plain_error_without_details(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    refused = {
        "error": "invalid_client",
        "error_codes": [7000215],
        "error_description": "AADSTS7000215: secret for tenant 9999 trace 1234 is wrong",
    }
    provider = MsalTokenProvider(settings, app_factory=Factory(FakeMsalApp(refused)))

    with caplog.at_level(logging.ERROR), pytest.raises(AuthenticationError) as excinfo:
        get_token(provider)

    assert "AADSTS" not in excinfo.value.message
    assert "trace 1234" not in caplog.text
    assert "invalid_client" in caplog.text


def test_network_failure_raises_plain_error(settings: Settings) -> None:
    failing = FakeMsalApp(ConnectionError("dns failure for login.example"))
    provider = MsalTokenProvider(settings, app_factory=Factory(failing))

    with pytest.raises(AuthenticationError) as excinfo:
        get_token(provider)

    assert "dns failure" not in excinfo.value.message
    assert excinfo.value.__cause__ is None


def test_bad_authority_raises_plain_error(settings: Settings) -> None:
    def broken_factory(**_: Any) -> Any:
        raise ValueError("authority https://login... not found")

    with pytest.raises(AuthenticationError) as excinfo:
        get_token(MsalTokenProvider(settings, app_factory=broken_factory))

    assert "login" not in excinfo.value.message


def test_certificate_credential_is_passed_to_msal(
    make_settings: Callable[..., Settings], tmp_path: Path
) -> None:
    key_file = tmp_path / "key.pem"
    key_file.write_text("made-up-private-key", encoding="utf-8")
    cert_settings = make_settings(
        client_secret=None,
        client_certificate_path=str(key_file),
        client_certificate_thumbprint="A" * 40,
    )
    factory = Factory(FakeMsalApp({"access_token": "tok-cert"}))

    assert get_token(MsalTokenProvider(cert_settings, app_factory=factory)) == "tok-cert"
    assert factory.kwargs[0]["client_credential"] == {
        "private_key": "made-up-private-key",
        "thumbprint": "A" * 40,
    }


def test_unreadable_certificate_is_a_configuration_error(
    make_settings: Callable[..., Settings], tmp_path: Path
) -> None:
    cert_settings = make_settings(
        client_secret=None,
        client_certificate_path=str(tmp_path / "missing.pem"),
        client_certificate_thumbprint="A" * 40,
    )
    provider = MsalTokenProvider(cert_settings, app_factory=Factory(FakeMsalApp({})))

    with pytest.raises(ConfigurationError) as excinfo:
        get_token(provider)

    assert str(tmp_path) not in excinfo.value.message
