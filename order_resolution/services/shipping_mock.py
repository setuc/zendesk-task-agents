from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path


class MockShippingService:
    """Mock implementation of the ShippingService protocol.

    Uses in-memory tracking data pre-loaded from the sample fixtures.
    Supports fault injection for testing.
    """

    def __init__(self) -> None:
        self._shipments: dict[str, dict] = {}  # keyed by order_id
        self._tracking: dict[str, dict] = {}  # keyed by tracking_id
        self._return_labels: dict[str, dict] = {}
        self._failures: set[str] = set()
        self._load_fixtures()

    def _load_fixtures(self) -> None:
        fixtures_path = Path(__file__).parent.parent / "fixtures" / "sample_shipping.json"
        if fixtures_path.exists():
            with open(fixtures_path) as f:
                data = json.load(f)
            for order_id, shipment in data.items():
                self._shipments[order_id] = shipment
                tracking_id = shipment.get("tracking_id")
                if tracking_id:
                    self._tracking[tracking_id] = shipment

    # ---- Fault injection ----

    def inject_failure(self, method_name: str) -> None:
        self._failures.add(method_name)

    def clear_failure(self, method_name: str) -> None:
        self._failures.discard(method_name)

    def _check_failure(self, method_name: str) -> None:
        if method_name in self._failures:
            self._failures.discard(method_name)
            raise RuntimeError(f"Injected failure in {method_name}")

    # ---- Protocol methods ----

    async def get_tracking(self, tracking_id: str) -> dict:
        self._check_failure("get_tracking")
        shipment = self._tracking.get(tracking_id)
        if shipment is None:
            raise KeyError(f"Tracking not found: {tracking_id}")
        return copy.deepcopy(shipment)

    async def get_shipment_by_order(self, order_id: str) -> dict:
        self._check_failure("get_shipment_by_order")
        shipment = self._shipments.get(order_id)
        if shipment is None:
            raise KeyError(f"Shipment not found for order: {order_id}")
        return copy.deepcopy(shipment)

    async def create_return_label(self, order_id: str, items: list[dict]) -> dict:
        self._check_failure("create_return_label")
        if order_id not in self._shipments:
            raise KeyError(f"No shipment found for order: {order_id}")

        label_id = f"RTN-{uuid.uuid4().hex[:8].upper()}"
        now = datetime.now(timezone.utc).isoformat()
        label = {
            "label_id": label_id,
            "order_id": order_id,
            "carrier": self._shipments[order_id].get("carrier", "FedEx"),
            "tracking_id": f"RTN-TRK-{uuid.uuid4().hex[:6].upper()}",
            "items": copy.deepcopy(items),
            "status": "created",
            "created_at": now,
            "label_url": f"https://shipping.example.com/labels/{label_id}.pdf",
        }
        self._return_labels[label_id] = label
        return copy.deepcopy(label)
