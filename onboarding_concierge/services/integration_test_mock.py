from __future__ import annotations

import random
import uuid
from datetime import datetime, timezone


class MockIntegrationTestService:
    """Mock implementation of the IntegrationTestService protocol.

    Simulates hitting customer webhook endpoints and running diagnostics.
    Supports configurable failure scenarios for demo purposes.
    """

    def __init__(self) -> None:
        self._failure_scenarios: dict[str, str] = {}
        self._test_history: list[dict] = []

    # ---- Failure injection ----

    def inject_failure(self, url: str, error_type: str) -> None:
        """Register a failure scenario for a specific URL.

        Args:
            url: The endpoint URL that should fail.
            error_type: One of "timeout", "500", "connection_refused", "invalid_response".
        """
        self._failure_scenarios[url] = error_type

    def clear_failure(self, url: str) -> None:
        self._failure_scenarios.pop(url, None)

    # ---- Protocol methods ----

    async def test_endpoint(
        self, url: str, method: str = "POST", payload: dict | None = None
    ) -> dict:
        """Simulate hitting a webhook URL, returns success/failure with response time."""
        test_id = f"test-{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()

        # Check for injected failures
        failure = self._failure_scenarios.get(url)
        if failure == "timeout":
            result = {
                "test_id": test_id,
                "url": url,
                "method": method,
                "success": False,
                "response_status": None,
                "response_time_ms": 30000.0,
                "error": "Connection timed out after 30000ms",
                "tested_at": now,
            }
        elif failure == "500":
            result = {
                "test_id": test_id,
                "url": url,
                "method": method,
                "success": False,
                "response_status": 500,
                "response_time_ms": round(random.uniform(50, 200), 2),
                "error": "Internal Server Error: upstream service returned 500",
                "tested_at": now,
            }
        elif failure == "connection_refused":
            result = {
                "test_id": test_id,
                "url": url,
                "method": method,
                "success": False,
                "response_status": None,
                "response_time_ms": None,
                "error": "Connection refused: host unreachable",
                "tested_at": now,
            }
        elif failure == "invalid_response":
            result = {
                "test_id": test_id,
                "url": url,
                "method": method,
                "success": False,
                "response_status": 200,
                "response_time_ms": round(random.uniform(50, 300), 2),
                "error": "Response body did not match expected JSON schema",
                "tested_at": now,
            }
        else:
            # Successful response
            result = {
                "test_id": test_id,
                "url": url,
                "method": method,
                "success": True,
                "response_status": 200,
                "response_time_ms": round(random.uniform(15, 150), 2),
                "error": None,
                "tested_at": now,
            }

        self._test_history.append(result)
        return result

    async def run_diagnostics(self, integration_id: str) -> dict:
        """Return a diagnostic report for an integration."""
        now = datetime.now(timezone.utc).isoformat()

        # Simulate diagnostic checks
        checks = [
            {
                "check": "dns_resolution",
                "status": "passed",
                "detail": "DNS resolves correctly",
            },
            {
                "check": "tls_certificate",
                "status": "passed",
                "detail": "TLS certificate valid, expires in 245 days",
            },
            {
                "check": "authentication",
                "status": "passed",
                "detail": "API key accepted, scopes verified",
            },
            {
                "check": "response_format",
                "status": "passed",
                "detail": "Response matches expected JSON schema",
            },
            {
                "check": "latency",
                "status": "passed",
                "detail": f"Average response time: {round(random.uniform(20, 80), 1)}ms",
            },
        ]

        return {
            "integration_id": integration_id,
            "diagnostic_id": f"diag-{uuid.uuid4().hex[:8]}",
            "checks": checks,
            "overall_status": "healthy",
            "recommendations": [],
            "run_at": now,
        }
