from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from .activities import OnboardingActivities
    from .data_types import IntegrationTestResult


@workflow.defn
class IntegrationTestWorkflow:
    """Child workflow that tests a single customer integration.

    Runs the endpoint test, performs diagnostics on failure, and creates
    a support ticket if the integration cannot be resolved automatically.
    """

    def __init__(self) -> None:
        self._result: IntegrationTestResult | None = None

    # ------------------------------------------------------------------ #
    # Queries                                                              #
    # ------------------------------------------------------------------ #

    @workflow.query
    def get_test_result(self) -> IntegrationTestResult | None:
        return self._result

    # ------------------------------------------------------------------ #
    # Main workflow run                                                    #
    # ------------------------------------------------------------------ #

    @workflow.run
    async def run(
        self, customer_id: str, integration_config: dict
    ) -> IntegrationTestResult:
        """Test a customer integration endpoint with retry and diagnostics.

        Args:
            customer_id: The customer whose integration is being tested.
            integration_config: Dict containing integration_id, endpoint_url,
                method, and optional auth details.

        Returns:
            IntegrationTestResult with pass/fail status and diagnostics.
        """
        activity_timeout = timedelta(seconds=120)
        retry_policy = RetryPolicy(
            maximum_attempts=3,
            initial_interval=timedelta(seconds=2),
            maximum_interval=timedelta(seconds=30),
            backoff_coefficient=2.0,
        )

        integration_id = integration_config.get("integration_id", "unknown")
        endpoint_url = integration_config.get("endpoint_url", "")

        workflow.logger.info(
            f"Starting integration test for {customer_id}: {integration_id}"
        )

        # Step 1: Test the endpoint
        self._result = await workflow.execute_activity_method(
            OnboardingActivities.test_integration,
            args=[customer_id, integration_config],
            start_to_close_timeout=activity_timeout,
            retry_policy=retry_policy,
        )

        if self._result.test_passed:
            workflow.logger.info(
                f"Integration {integration_id} passed for {customer_id}"
            )
            return self._result

        # Step 2: Test failed -- the activity already ran diagnostics,
        # but log the failure for visibility
        workflow.logger.warning(
            f"Integration {integration_id} failed for {customer_id}: "
            f"{self._result.error_message}"
        )

        # Step 3: Create a support ticket for persistent failures
        await workflow.execute_activity_method(
            OnboardingActivities.create_support_ticket,
            args=[
                customer_id,
                f"Integration test failed: {integration_id}",
                (
                    f"Automated integration test failed for endpoint {endpoint_url}.\n\n"
                    f"Error: {self._result.error_message}\n\n"
                    f"Diagnostic Report:\n{self._result.diagnostic_report}\n\n"
                    f"Suggestions:\n"
                    + "\n".join(f"- {s}" for s in self._result.suggestions)
                ),
            ],
            start_to_close_timeout=activity_timeout,
        )

        return self._result
