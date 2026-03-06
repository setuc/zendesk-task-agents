from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from universal_computer.agents.tools import FunctionTool

from common.services.base import IntegrationTestService


# ---------------------------------------------------------------------------
# RunDiagnosticTool
# ---------------------------------------------------------------------------

class RunDiagnosticArgs(BaseModel):
    integration_id: str = Field(description="The integration ID to run diagnostics on")


class RunDiagnosticTool(FunctionTool[RunDiagnosticArgs, dict]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    tool_name = "run_diagnostic"
    args_model = RunDiagnosticArgs
    description = "Run a full diagnostic check on a customer integration and return a detailed report."
    integration_test: IntegrationTestService = Field(exclude=True)

    async def run(self, args: RunDiagnosticArgs) -> dict:
        return await self.integration_test.run_diagnostics(args.integration_id)


# ---------------------------------------------------------------------------
# CheckEndpointTool
# ---------------------------------------------------------------------------

class CheckEndpointArgs(BaseModel):
    url: str = Field(description="The endpoint URL to test")
    method: str = Field(default="POST", description="HTTP method to use (GET, POST, PUT, etc.)")
    payload: dict | None = Field(default=None, description="Optional JSON payload to send with the request")


class CheckEndpointTool(FunctionTool[CheckEndpointArgs, dict]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    tool_name = "check_endpoint"
    args_model = CheckEndpointArgs
    description = "Test a customer webhook endpoint by sending a request and validating the response."
    integration_test: IntegrationTestService = Field(exclude=True)

    async def run(self, args: CheckEndpointArgs) -> dict:
        return await self.integration_test.test_endpoint(args.url, args.method, args.payload)
