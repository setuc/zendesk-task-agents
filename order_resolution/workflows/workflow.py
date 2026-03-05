from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from .activities import OrderResolutionActivities
    from .data_types import (
        ApprovalDecision,
        ExtractedIntent,
        InvestigationResult,
        ResolutionPlan,
        StepResult,
        WorkflowProgress,
        WorkflowState,
    )


@workflow.defn
class OrderResolutionWorkflow:
    """Orchestrates the full order resolution lifecycle with saga compensation."""

    def __init__(self) -> None:
        self._state = WorkflowState.CLASSIFYING
        self._ticket_id = ""
        self._intent: ExtractedIntent | None = None
        self._investigation: InvestigationResult | None = None
        self._plan: ResolutionPlan | None = None
        self._completed_steps: list[StepResult] = []
        self._approval_decision: ApprovalDecision | None = None
        self._cancelled = False
        self._error_message: str | None = None
        self._customer_message: str | None = None

    # ------------------------------------------------------------------ #
    # Signals                                                              #
    # ------------------------------------------------------------------ #

    @workflow.signal
    async def approval_decision(self, decision: ApprovalDecision) -> None:
        self._approval_decision = decision

    @workflow.signal
    async def cancel_workflow(self) -> None:
        self._cancelled = True

    # ------------------------------------------------------------------ #
    # Queries                                                              #
    # ------------------------------------------------------------------ #

    @workflow.query
    def get_workflow_state(self) -> str:
        return self._state.value

    @workflow.query
    def get_resolution_plan(self) -> ResolutionPlan | None:
        return self._plan

    @workflow.query
    def get_progress(self) -> WorkflowProgress:
        return WorkflowProgress(
            state=self._state,
            ticket_id=self._ticket_id,
            extracted_intent=self._intent,
            investigation=self._investigation,
            plan=self._plan,
            completed_steps=self._completed_steps,
            error_message=self._error_message,
            customer_message=self._customer_message,
        )

    # ------------------------------------------------------------------ #
    # Main workflow run                                                    #
    # ------------------------------------------------------------------ #

    @workflow.run
    async def run(self, ticket_id: str) -> WorkflowProgress:
        self._ticket_id = ticket_id
        activity_timeout = timedelta(seconds=120)

        try:
            # Step 1: Classify and Extract
            self._state = WorkflowState.CLASSIFYING
            self._intent = await workflow.execute_activity_method(
                OrderResolutionActivities.classify_and_extract,
                ticket_id,
                start_to_close_timeout=activity_timeout,
            )

            if self._cancelled:
                self._state = WorkflowState.CANCELLED
                return self.get_progress()

            # Step 2: Investigate
            self._state = WorkflowState.INVESTIGATING
            self._investigation = await workflow.execute_activity_method(
                OrderResolutionActivities.investigate,
                args=[ticket_id, self._intent],
                start_to_close_timeout=activity_timeout,
            )

            if self._cancelled:
                self._state = WorkflowState.CANCELLED
                return self.get_progress()

            # Step 3: Plan Resolution
            self._state = WorkflowState.PLANNING
            self._plan = await workflow.execute_activity_method(
                OrderResolutionActivities.plan_resolution,
                args=[self._intent, self._investigation],
                start_to_close_timeout=activity_timeout,
            )

            # Step 4: Approval if needed
            if self._plan.requires_human_approval:
                self._state = WorkflowState.AWAITING_APPROVAL

                # Wait for approval signal with 24h timeout
                try:
                    await workflow.wait_condition(
                        lambda: self._approval_decision is not None
                        or self._cancelled,
                        timeout=timedelta(hours=24),
                    )
                except asyncio.TimeoutError:
                    self._state = WorkflowState.FAILED
                    self._error_message = "Approval timed out after 24 hours"
                    return self.get_progress()

                if self._cancelled:
                    self._state = WorkflowState.CANCELLED
                    return self.get_progress()

                if (
                    not self._approval_decision
                    or not self._approval_decision.approved
                ):
                    self._state = WorkflowState.CANCELLED
                    self._error_message = "Resolution plan rejected by reviewer"
                    return self.get_progress()

            # Step 5: Execute steps with saga compensation
            self._state = WorkflowState.EXECUTING
            for step in self._plan.steps:
                if self._cancelled:
                    break

                result = await workflow.execute_activity_method(
                    OrderResolutionActivities.execute_step,
                    step,
                    start_to_close_timeout=activity_timeout,
                    retry_policy=RetryPolicy(
                        maximum_attempts=3,
                        initial_interval=timedelta(seconds=1),
                        maximum_interval=timedelta(seconds=10),
                        backoff_coefficient=2.0,
                    ),
                )

                if result.success:
                    self._completed_steps.append(result)
                else:
                    # Saga compensation: reverse completed steps in reverse order
                    self._state = WorkflowState.COMPENSATING
                    self._error_message = (
                        f"Step {step.step_id} failed: {result.error_message}"
                    )

                    for completed in reversed(self._completed_steps):
                        await workflow.execute_activity_method(
                            OrderResolutionActivities.compensate_step,
                            completed,
                            start_to_close_timeout=activity_timeout,
                        )

                    self._state = WorkflowState.FAILED
                    return self.get_progress()

            # Step 6: Verify and Summarize
            self._state = WorkflowState.VERIFYING
            self._customer_message = await workflow.execute_activity_method(
                OrderResolutionActivities.verify_and_summarize,
                args=[ticket_id, self._completed_steps],
                start_to_close_timeout=activity_timeout,
            )

            self._state = WorkflowState.COMPLETED
            return self.get_progress()

        except Exception as e:
            self._state = WorkflowState.FAILED
            self._error_message = str(e)
            return self.get_progress()
