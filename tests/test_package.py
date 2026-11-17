import dataverse_views_mcp


def test_package_exposes_version() -> None:
    assert dataverse_views_mcp.__version__ == "0.1.0"
