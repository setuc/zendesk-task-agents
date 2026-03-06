from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path


class MockOrderDBService:
    """Mock implementation of the OrderDBService protocol.

    Uses an in-memory dict of orders, pre-loaded from the sample fixtures.
    Supports fault injection for testing saga compensation flows.
    """

    def __init__(self) -> None:
        self._orders: dict[str, dict] = {}
        self._failures: set[str] = set()
        self._load_fixtures()

    def _load_fixtures(self) -> None:
        fixtures_path = Path(__file__).parent.parent / "fixtures" / "sample_orders.json"
        if fixtures_path.exists():
            with open(fixtures_path) as f:
                self._orders = json.load(f)

    # ---- Fault injection ----

    def inject_failure(self, method_name: str) -> None:
        """Cause the named method to raise an error on next call."""
        self._failures.add(method_name)

    def clear_failure(self, method_name: str) -> None:
        self._failures.discard(method_name)

    def _check_failure(self, method_name: str) -> None:
        if method_name in self._failures:
            self._failures.discard(method_name)
            raise RuntimeError(f"Injected failure in {method_name}")

    # ---- Protocol methods ----

    async def get_order(self, order_id: str) -> dict:
        self._check_failure("get_order")
        order = self._orders.get(order_id)
        if order is None:
            raise KeyError(f"Order not found: {order_id}")
        return copy.deepcopy(order)

    async def get_orders_by_customer(self, customer_id: str) -> list[dict]:
        self._check_failure("get_orders_by_customer")
        results = [
            copy.deepcopy(o)
            for o in self._orders.values()
            if o.get("customer_id") == customer_id
        ]
        return results

    async def create_replacement_order(
        self, original_order_id: str, items: list[dict]
    ) -> dict:
        self._check_failure("create_replacement_order")
        original = self._orders.get(original_order_id)
        if original is None:
            raise KeyError(f"Original order not found: {original_order_id}")

        new_order_id = f"ORD-REPL-{uuid.uuid4().hex[:6].upper()}"
        now = datetime.now(timezone.utc).isoformat()
        replacement = {
            "order_id": new_order_id,
            "customer_id": original["customer_id"],
            "status": "processing",
            "original_order_id": original_order_id,
            "items": copy.deepcopy(items),
            "subtotal": sum(item.get("price", 0.0) * item.get("quantity", 1) for item in items),
            "charged_total": 0.0,  # Replacement orders are free
            "placed_at": now,
            "delivered_at": None,
        }
        self._orders[new_order_id] = replacement
        return copy.deepcopy(replacement)
