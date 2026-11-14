"""Only the dependency deprecation we expect is ignored in the web tests."""

from __future__ import annotations

import warnings

import pytest


def test_the_anyio_blocking_portal_deprecation_is_ignored():
    warnings.warn(
        "anyio.abc.BlockingPortal is deprecated, use anyio.from_thread instead",
        DeprecationWarning,
        stacklevel=1,
    )


def test_other_deprecations_still_fail():
    with pytest.raises(DeprecationWarning):
        warnings.warn("something else is deprecated", DeprecationWarning, stacklevel=1)
