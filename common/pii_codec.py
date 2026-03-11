from __future__ import annotations

import json
import hashlib
from collections.abc import Sequence
from typing import Any

import redis.asyncio as aioredis
from temporalio.api.common.v1 import Payload
from temporalio.converter import PayloadCodec

from .pii_config import DEFAULT_PII_FIELDS, PII_REF_PREFIX


class PIIRedactingCodec(PayloadCodec):
    """Temporal PayloadCodec that extracts PII fields and stores them in Redis.

    On encode: walks JSON payload, replaces PII field values with ref tokens,
    stores originals in Redis.
    On decode: finds ref tokens, batch-fetches from Redis, restores values.
    """

    def __init__(
        self,
        redis_client: aioredis.Redis,
        ttl_seconds: int = 2592000,
        fields: frozenset[str] | None = None,
    ) -> None:
        self._redis = redis_client
        self._ttl = ttl_seconds
        self._fields = fields or DEFAULT_PII_FIELDS

    async def encode(self, payloads: Sequence[Payload]) -> list[Payload]:
        encoded = []
        for payload in payloads:
            encoding = payload.metadata.get("encoding", b"").decode()
            if encoding not in ("json/plain", "json/protobuf"):
                encoded.append(payload)
                continue
            try:
                data = json.loads(payload.data)
            except (json.JSONDecodeError, UnicodeDecodeError):
                encoded.append(payload)
                continue

            pii_to_store: dict[str, str] = {}
            self._extract_pii(data, path="root", store=pii_to_store)

            if pii_to_store:
                pipe = self._redis.pipeline()
                for ref_key, value in pii_to_store.items():
                    pipe.set(ref_key, value, ex=self._ttl)
                await pipe.execute()

            new_payload = Payload(
                data=json.dumps(data, default=str).encode(),
                metadata=payload.metadata,
            )
            encoded.append(new_payload)
        return encoded

    async def decode(self, payloads: Sequence[Payload]) -> list[Payload]:
        decoded = []
        for payload in payloads:
            encoding = payload.metadata.get("encoding", b"").decode()
            if encoding not in ("json/plain", "json/protobuf"):
                decoded.append(payload)
                continue
            try:
                data = json.loads(payload.data)
            except (json.JSONDecodeError, UnicodeDecodeError):
                decoded.append(payload)
                continue

            refs: list[str] = []
            self._collect_refs(data, refs)

            if refs:
                values = await self._redis.mget(refs)
                ref_map = {}
                for ref, val in zip(refs, values):
                    if val is not None:
                        try:
                            ref_map[ref] = json.loads(val)
                        except json.JSONDecodeError:
                            ref_map[ref] = val.decode() if isinstance(val, bytes) else val
                self._restore_pii(data, ref_map)

            new_payload = Payload(
                data=json.dumps(data, default=str).encode(),
                metadata=payload.metadata,
            )
            decoded.append(new_payload)
        return decoded

    def _extract_pii(self, obj: Any, path: str, store: dict[str, str]) -> None:
        if isinstance(obj, dict):
            for key in list(obj.keys()):
                child_path = f"{path}.{key}"
                if key in self._fields and obj[key] is not None:
                    ref_key = self._make_ref(child_path)
                    store[ref_key] = json.dumps(obj[key], default=str)
                    obj[key] = f"{PII_REF_PREFIX}{ref_key}"
                else:
                    self._extract_pii(obj[key], child_path, store)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                self._extract_pii(item, f"{path}[{i}]", store)

    def _collect_refs(self, obj: Any, refs: list[str]) -> None:
        if isinstance(obj, str) and obj.startswith(PII_REF_PREFIX):
            refs.append(obj[len(PII_REF_PREFIX):])
        elif isinstance(obj, dict):
            for val in obj.values():
                self._collect_refs(val, refs)
        elif isinstance(obj, list):
            for item in obj:
                self._collect_refs(item, refs)

    def _restore_pii(self, obj: Any, ref_map: dict[str, Any]) -> None:
        if isinstance(obj, dict):
            for key in list(obj.keys()):
                val = obj[key]
                if isinstance(val, str) and val.startswith(PII_REF_PREFIX):
                    ref_key = val[len(PII_REF_PREFIX):]
                    if ref_key in ref_map:
                        obj[key] = ref_map[ref_key]
                else:
                    self._restore_pii(val, ref_map)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                if isinstance(item, str) and item.startswith(PII_REF_PREFIX):
                    ref_key = item[len(PII_REF_PREFIX):]
                    if ref_key in ref_map:
                        obj[i] = ref_map[ref_key]
                else:
                    self._restore_pii(item, ref_map)

    @staticmethod
    def _make_ref(path: str) -> str:
        return hashlib.sha256(path.encode()).hexdigest()[:16]
