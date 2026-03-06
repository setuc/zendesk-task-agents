from __future__ import annotations

import asyncio
from typing import Any

from openai.types.responses import ResponseTextDeltaEvent, ResponseTextDoneEvent

from universal_computer.agents.events import ToolCalledEvent
from universal_computer.agents.task_runtime import Task


async def run_agent_to_completion(
    task: Task,
    prompt: str,
    *,
    on_text: callable | None = None,
    on_tool_call: callable | None = None,
    timeout: float = 300.0,
) -> str:
    """Drive a UC agent task to completion, collecting all text output.

    Args:
        task: Active Task instance (already opened via TaskContext)
        prompt: User prompt to send
        on_text: Optional callback for streaming text deltas
        on_tool_call: Optional callback when tools are called
        timeout: Maximum seconds to wait

    Returns:
        Complete text response from the agent
    """
    collected_text: list[str] = []
    context: dict[str, Any] = {"role": "user", "content": prompt}

    async def _run() -> str:
        async for event in task.run(context):
            match event:
                case ResponseTextDeltaEvent():
                    collected_text.append(event.delta)
                    if on_text:
                        on_text(event.delta)
                case ResponseTextDoneEvent():
                    pass
                case ToolCalledEvent():
                    if on_tool_call:
                        on_tool_call(event)
                    if not event.requires_approval:
                        await event.tool_call(task)
        return "".join(collected_text)

    return await asyncio.wait_for(_run(), timeout=timeout)
