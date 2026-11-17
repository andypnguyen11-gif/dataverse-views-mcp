from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from dataverse_views_mcp.config import Settings, load_settings
from dataverse_views_mcp.errors import ConfigurationError
from tests.conftest import BASE_SETTINGS, OTHER_VIEW_ID, VIEW_ID


def test_loads_valid_settings_and_derives_addresses(settings: Settings) -> None:
    assert settings.url == "https://org.example.test"
    assert settings.authority == (
        "https://login.microsoftonline.com/99999999-9999-9999-9999-999999999999"
    )
    assert settings.scope == "https://org.example.test/.default"
    assert settings.api_base_url == "https://org.example.test/api/data/v9.2/"


def test_reads_from_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in BASE_SETTINGS.items():
        monkeypatch.setenv(f"DATAVERSE_{key.upper()}", value)
    monkeypatch.setenv("DATAVERSE_APPROVED_VIEW_IDS", f"{VIEW_ID}, {{{OTHER_VIEW_ID.upper()}}}")
    monkeypatch.setenv("DATAVERSE_SEARCHABLE_TABLES", "Office, new_region")
    monkeypatch.setenv("DATAVERSE_EXTRA_FILTER_COLUMNS", "case.officeid")

    loaded = load_settings()

    assert loaded.approved_view_ids == {VIEW_ID, OTHER_VIEW_ID}
    assert loaded.searchable_tables == {"office", "new_region"}
    assert loaded.extra_filter_columns == {"case.officeid"}


def test_settings_file_is_read_only_when_named_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines = [f"DATAVERSE_{key.upper()}={value}" for key, value in BASE_SETTINGS.items()]
    (tmp_path / ".env").write_text("\n".join(lines), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="DATAVERSE_URL is required"):
        load_settings()

    monkeypatch.setenv("DATAVERSE_ENV_FILE", str(tmp_path / ".env"))
    assert load_settings().client_id == BASE_SETTINGS["client_id"]

    monkeypatch.setenv("DATAVERSE_ENV_FILE", str(tmp_path / "missing.env"))
    with pytest.raises(ConfigurationError, match="DATAVERSE_ENV_FILE does not point"):
        load_settings()


def test_missing_required_settings_are_named() -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()

    message = excinfo.value.message
    assert "DATAVERSE_URL is required" in message
    assert "DATAVERSE_TENANT_ID is required" in message
    assert "DATAVERSE_CLIENT_ID is required" in message


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"url": "http://org.example.test"}, "DATAVERSE_URL must start with https://"),
        ({"url": "https://org.example.test/api/data"}, "host name only"),
        ({"url": "https://user:pw@org.example.test"}, "host name only"),
        ({"url": "https://org.example.test?x=1"}, "host name only"),
        ({"url": "https://org.example.test#frag"}, "host name only"),
        ({"url": "https://org.example.test:abc"}, "host name only"),
        ({"url": "https://org.example.test:99999"}, "valid host name"),
        ({"url": "https://org exa.mple.test"}, "host name only"),
        ({"url": "https://evil.test\\.org.example.test"}, "host name only"),
        ({"url": "https://org.example.test\n.evil.test"}, "host name only"),
        ({"url": "https://org.example.test\t"}, "host name only"),
        ({"url": "https://-bad-.example.test"}, "valid host name"),
        ({"url": "https://localhost"}, "valid host name"),
        ({"url": 5}, "DATAVERSE_URL"),
        ({"authority_host": "http://login.example.test"}, "DATAVERSE_AUTHORITY_HOST"),
        ({"tenant_id": "not a tenant"}, "DATAVERSE_TENANT_ID"),
        ({"client_id": "abc"}, "DATAVERSE_CLIENT_ID must be a GUID"),
        ({"api_version": "9.2/../x"}, "DATAVERSE_API_VERSION"),
        ({"approved_view_ids": "not-a-guid"}, "DATAVERSE_APPROVED_VIEW_IDS"),
        ({"searchable_tables": "office;drop"}, "DATAVERSE_SEARCHABLE_TABLES"),
        ({"extra_filter_columns": "officeid"}, "DATAVERSE_EXTRA_FILTER_COLUMNS"),
        ({"max_page_size": 100000}, "DATAVERSE_MAX_PAGE_SIZE"),
        ({"log_level": "LOUD"}, "DATAVERSE_LOG_LEVEL"),
    ],
)
def test_malformed_settings_are_rejected(
    make_settings: Callable[..., Settings], override: dict[str, Any], expected: str
) -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        make_settings(**override)

    assert expected in excinfo.value.message


def test_error_message_never_echoes_a_value(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        make_settings(client_id="hunter2-typed-into-the-wrong-field")

    assert "hunter2" not in excinfo.value.message


def test_secret_is_not_shown_in_repr(settings: Settings) -> None:
    assert "made-up-secret-value" not in repr(settings)
    assert "made-up-secret-value" not in str(settings.model_dump())


def test_exactly_one_credential_is_required(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ConfigurationError, match="exactly one"):
        make_settings(client_secret=None)
    with pytest.raises(ConfigurationError, match="exactly one"):
        make_settings(client_certificate_path="/tmp/key.pem")  # noqa: S108
    with pytest.raises(ConfigurationError, match="THUMBPRINT is required"):
        make_settings(client_secret=None, client_certificate_path="/tmp/key.pem")  # noqa: S108


def test_certificate_thumbprint_is_normalized(make_settings: Callable[..., Settings]) -> None:
    loaded = make_settings(
        client_secret=None,
        client_certificate_path="/tmp/key.pem",  # noqa: S108
        client_certificate_thumbprint="ab:cd:" + "0" * 36,
    )
    assert loaded.client_certificate_thumbprint == "ABCD" + "0" * 36

    with pytest.raises(ConfigurationError, match="40 hexadecimal"):
        make_settings(
            client_secret=None,
            client_certificate_path="/tmp/key.pem",  # noqa: S108
            client_certificate_thumbprint="xyz",
        )


def test_sovereign_cloud_needs_only_settings(make_settings: Callable[..., Settings]) -> None:
    loaded = make_settings(url="https://org.example.us", authority_host="https://login.example.us/")
    assert loaded.authority.startswith("https://login.example.us/")
    assert loaded.scope == "https://org.example.us/.default"


def test_view_approval(make_settings: Callable[..., Settings]) -> None:
    assert make_settings().is_view_approved(VIEW_ID)
    assert not make_settings().is_view_approved(OTHER_VIEW_ID)
    assert make_settings(approved_view_ids="").approved_view_ids == frozenset()
    assert make_settings(allow_all_system_views=True).is_view_approved(OTHER_VIEW_ID)


def test_address_is_normalized(make_settings: Callable[..., Settings]) -> None:
    assert make_settings(url="https://ORG.Example.Test/").url == "https://org.example.test"
    assert make_settings(url="https://org.example.test:8443").url == "https://org.example.test:8443"
