from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from universal_computer.agents.tools import FunctionTool

from ..services.base import ZendeskService


# ---------------------------------------------------------------------------
# GetTicketTool
# ---------------------------------------------------------------------------

class GetTicketArgs(BaseModel):
    ticket_id: str = Field(description="The Zendesk ticket ID to retrieve")


class GetTicketTool(FunctionTool[GetTicketArgs, dict]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    tool_name = "get_zendesk_ticket"
    args_model = GetTicketArgs
    description = "Retrieve a Zendesk support ticket by ID including all comments and metadata."
    zendesk: ZendeskService = Field(exclude=True)

    async def run(self, args: GetTicketArgs) -> dict:
        return await self.zendesk.get_ticket(args.ticket_id)


# ---------------------------------------------------------------------------
# UpdateTicketTool
# ---------------------------------------------------------------------------

class UpdateTicketArgs(BaseModel):
    ticket_id: str = Field(description="The Zendesk ticket ID to update")
    updates: dict[str, Any] = Field(description="Dictionary of fields to update on the ticket")


class UpdateTicketTool(FunctionTool[UpdateTicketArgs, dict]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    tool_name = "update_zendesk_ticket"
    args_model = UpdateTicketArgs
    description = "Update fields on a Zendesk support ticket."
    zendesk: ZendeskService = Field(exclude=True)

    async def run(self, args: UpdateTicketArgs) -> dict:
        return await self.zendesk.update_ticket(args.ticket_id, args.updates)


# ---------------------------------------------------------------------------
# ListTicketsTool
# ---------------------------------------------------------------------------

class ListTicketsArgs(BaseModel):
    filters: dict[str, Any] | None = Field(
        default=None,
        description="Optional filters to narrow the ticket list (e.g. status, priority, assignee)",
    )


class ListTicketsTool(FunctionTool[ListTicketsArgs, list[dict]]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    tool_name = "list_zendesk_tickets"
    args_model = ListTicketsArgs
    description = "List Zendesk support tickets, optionally filtered by status, priority, or other criteria."
    zendesk: ZendeskService = Field(exclude=True)

    async def run(self, args: ListTicketsArgs) -> list[dict]:
        return await self.zendesk.list_tickets(args.filters)


# ---------------------------------------------------------------------------
# AddCommentTool
# ---------------------------------------------------------------------------

class AddCommentArgs(BaseModel):
    ticket_id: str = Field(description="The Zendesk ticket ID to comment on")
    body: str = Field(description="The comment body text")
    public: bool = Field(default=True, description="Whether the comment is public (visible to requester)")


class AddCommentTool(FunctionTool[AddCommentArgs, dict]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    tool_name = "add_zendesk_comment"
    args_model = AddCommentArgs
    description = "Add a comment to a Zendesk support ticket."
    zendesk: ZendeskService = Field(exclude=True)

    async def run(self, args: AddCommentArgs) -> dict:
        return await self.zendesk.add_comment(args.ticket_id, args.body, args.public)
