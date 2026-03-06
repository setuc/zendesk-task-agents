from __future__ import annotations

from typing import Any

from .zendesk_mock import MockZendeskService


def create_services(use_real: bool = False, **kwargs: Any) -> dict[str, Any]:
    """Factory that returns a dict of service instances keyed by name.

    When *use_real* is ``False`` (default), all services are backed by
    in-memory mock implementations suitable for testing and demos.
    """
    if use_real:
        raise NotImplementedError("Real service implementations are not yet available")

    return {
        "zendesk": MockZendeskService(),
    }
