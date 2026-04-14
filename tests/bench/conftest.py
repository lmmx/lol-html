"""Pytest fixtures for benchmark scenarios."""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(config, items):  # type: ignore[no-untyped-def]
    for item in items:
        if "bench" in str(item.fspath):
            item.add_marker(pytest.mark.benchmark)


@pytest.fixture(scope="session")
def small_payload() -> bytes:
    return b"<html><body>" + b"<p>hi</p>" * 100 + b"</body></html>"


@pytest.fixture(scope="session")
def medium_payload() -> bytes:
    return b"<html><body>" + b"<p>item</p>" * 10_000 + b"</body></html>"


@pytest.fixture(scope="session")
def large_payload() -> bytes:
    return (
        b"<html><body>"
        + b"<div><p>nested " + b"<span>stuff</span>" * 5 + b"</p></div>" * 20_000
        + b"</body></html>"
    )