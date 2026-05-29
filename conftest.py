import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "live: hits real external APIs; skip in CI")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("-m", default="") == "live":
        skip_live = pytest.mark.skip(reason="live tests disabled by default; use -m live")
        for item in items:
            if item.get_closest_marker("live"):
                item.add_marker(skip_live)
