from __future__ import annotations

import dataclasses

import redis.asyncio as aioredis
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter

from .pii_codec import PIIRedactingCodec
from .pii_config import PIIConfig


async def create_temporal_client(
    address: str = "localhost:7233",
    namespace: str = "default",
    pii_config: PIIConfig | None = None,
) -> Client:
    """Create a Temporal client with optional PII codec.

    If pii_config is provided and enabled, wraps the pydantic data converter
    with a PIIRedactingCodec backed by Redis.
    """
    data_converter = pydantic_data_converter

    if pii_config and pii_config.enabled:
        redis_client = aioredis.from_url(pii_config.redis_url, decode_responses=False)
        codec = PIIRedactingCodec(
            redis_client, ttl_seconds=pii_config.ttl_seconds, fields=pii_config.fields,
        )
        data_converter = dataclasses.replace(
            pydantic_data_converter,
            payload_codec=codec,
        )

    return await Client.connect(
        address,
        namespace=namespace,
        data_converter=data_converter,
    )
