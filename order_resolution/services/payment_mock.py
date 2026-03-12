from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone


class MockPaymentService:
    """Mock implementation of the PaymentService protocol.

    Critical for saga compensation demos: supports process_refund and
    reverse_refund to demonstrate rollback behaviour when downstream
    steps fail.

    Supports fault injection for testing.
    """

    def __init__(self) -> None:
        self._transactions: dict[str, dict] = {}
        self._failures: set[str] = set()
        self._load_fixtures()

    def _load_fixtures(self) -> None:
        """Pre-load sample transactions matching the overcharge scenario."""
        self._transactions = {
            "TXN-ORD-2847": {
                "transaction_id": "TXN-ORD-2847",
                "order_id": "ORD-2847",
                "customer_id": "CUST-5591",
                "type": "charge",
                "amount": 224.97,
                "currency": "USD",
                "status": "completed",
                "created_at": "2026-02-25T14:22:00Z",
                "description": "Payment for order ORD-2847",
            },
        }

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

    async def process_refund(
        self, transaction_id: str, amount: float, reason: str
    ) -> dict:
        self._check_failure("process_refund")
        original = self._transactions.get(transaction_id)
        if original is None:
            raise KeyError(f"Transaction not found: {transaction_id}")
        if original.get("status") == "reversed":
            raise ValueError(f"Transaction {transaction_id} has been reversed")

        refund_id = f"RFND-{uuid.uuid4().hex[:8].upper()}"
        now = datetime.now(timezone.utc).isoformat()
        refund = {
            "transaction_id": refund_id,
            "original_transaction_id": transaction_id,
            "order_id": original.get("order_id"),
            "customer_id": original.get("customer_id"),
            "type": "refund",
            "amount": amount,
            "currency": original.get("currency", "USD"),
            "status": "completed",
            "reason": reason,
            "created_at": now,
        }
        self._transactions[refund_id] = refund
        return copy.deepcopy(refund)

    async def reverse_refund(self, refund_id: str) -> dict:
        """Reverse a previously issued refund (saga compensation).

        Marks the refund transaction as reversed and creates a reversal record.
        """
        self._check_failure("reverse_refund")
        refund = self._transactions.get(refund_id)
        if refund is None:
            raise KeyError(f"Refund transaction not found: {refund_id}")
        if refund.get("type") != "refund":
            raise ValueError(f"Transaction {refund_id} is not a refund")
        if refund.get("status") == "reversed":
            raise ValueError(f"Refund {refund_id} has already been reversed")

        # Mark the original refund as reversed
        refund["status"] = "reversed"

        reversal_id = f"REV-{uuid.uuid4().hex[:8].upper()}"
        now = datetime.now(timezone.utc).isoformat()
        reversal = {
            "transaction_id": reversal_id,
            "original_transaction_id": refund_id,
            "order_id": refund.get("order_id"),
            "customer_id": refund.get("customer_id"),
            "type": "reversal",
            "amount": refund["amount"],
            "currency": refund.get("currency", "USD"),
            "status": "completed",
            "reason": f"Reversal of refund {refund_id}",
            "created_at": now,
        }
        self._transactions[reversal_id] = reversal
        return copy.deepcopy(reversal)

    async def get_transaction(self, transaction_id: str) -> dict:
        self._check_failure("get_transaction")
        txn = self._transactions.get(transaction_id)
        if txn is None:
            raise KeyError(f"Transaction not found: {transaction_id}")
        return copy.deepcopy(txn)
