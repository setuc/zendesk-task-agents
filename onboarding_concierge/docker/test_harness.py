"""Simple test harness for validating customer integration endpoints.

Runs inside a Docker container to provide isolation. Takes an endpoint URL
and expected response format as arguments, makes an HTTP request, validates
the response, and outputs JSON results to stdout.

Usage:
    python test_harness.py <endpoint_url> [--method POST] [--expected-status 200]
                           [--expected-fields field1,field2] [--payload '{"key": "value"}']
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import httpx


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Integration Test Harness")
    parser.add_argument("endpoint_url", help="The endpoint URL to test")
    parser.add_argument("--method", default="POST", help="HTTP method (default: POST)")
    parser.add_argument(
        "--expected-status", type=int, default=200, help="Expected HTTP status code"
    )
    parser.add_argument(
        "--expected-fields",
        type=str,
        default="",
        help="Comma-separated list of expected top-level JSON fields",
    )
    parser.add_argument(
        "--payload", type=str, default=None, help="JSON payload to send with the request"
    )
    parser.add_argument(
        "--timeout", type=float, default=30.0, help="Request timeout in seconds"
    )
    return parser.parse_args()


def run_test(args: argparse.Namespace) -> dict:
    """Execute the integration test and return results as a dict."""
    result = {
        "endpoint_url": args.endpoint_url,
        "method": args.method,
        "expected_status": args.expected_status,
        "test_passed": False,
        "response_status": None,
        "response_time_ms": None,
        "error": None,
        "field_validation": {},
    }

    # Parse payload if provided
    payload = None
    if args.payload:
        try:
            payload = json.loads(args.payload)
        except json.JSONDecodeError as e:
            result["error"] = f"Invalid JSON payload: {e}"
            return result

    # Parse expected fields
    expected_fields = [f.strip() for f in args.expected_fields.split(",") if f.strip()]

    # Make the HTTP request
    try:
        start_time = time.monotonic()

        with httpx.Client(timeout=args.timeout) as client:
            response = client.request(
                method=args.method,
                url=args.endpoint_url,
                json=payload,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )

        elapsed_ms = (time.monotonic() - start_time) * 1000
        result["response_status"] = response.status_code
        result["response_time_ms"] = round(elapsed_ms, 2)

        # Check status code
        if response.status_code != args.expected_status:
            result["error"] = (
                f"Expected status {args.expected_status}, got {response.status_code}"
            )
            return result

        # Validate response fields
        if expected_fields:
            try:
                body = response.json()
                for field in expected_fields:
                    present = field in body
                    result["field_validation"][field] = present
                    if not present:
                        result["error"] = f"Missing expected field: {field}"
                        return result
            except (json.JSONDecodeError, ValueError):
                result["error"] = "Response body is not valid JSON"
                return result

        # All checks passed
        result["test_passed"] = True

    except httpx.TimeoutException:
        result["error"] = f"Request timed out after {args.timeout}s"
    except httpx.ConnectError:
        result["error"] = "Connection refused: host unreachable"
    except Exception as e:
        result["error"] = f"Unexpected error: {e}"

    return result


def main() -> None:
    args = parse_args()
    result = run_test(args)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["test_passed"] else 1)


if __name__ == "__main__":
    main()
