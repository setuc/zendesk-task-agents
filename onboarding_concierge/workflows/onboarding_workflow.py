from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from .activities import OnboardingActivities
    from .data_types import (
        CheckinResponse,
        IntegrationTestResult,
        MilestoneStatus,
        MilestoneType,
        OnboardingPlan,
        OnboardingStage,
        OnboardingState,
    )
    from .integration_test_workflow import IntegrationTestWorkflow


# Maximum event history length before triggering continue-as-new
_MAX_EVENTS_BEFORE_CAN = 10_000


@workflow.defn
class OnboardingWorkflow:
    """Multi-day orchestrator for enterprise customer onboarding.

    Guides a customer through a 2-week onboarding plan with account verification,
    integration testing (via child workflow), training delivery, milestone check-ins,
    and a final review. Uses durable timers between milestones and supports
    continue-as-new to prevent unbounded event history growth.
    """

    def __init__(self) -> None:
        self._state: OnboardingState | None = None
        self._checkin_response: CheckinResponse | None = None
        self._skip_milestone_id: str | None = None
        self._paused: bool = False
        self._resumed: bool = False
        self._integration_results: list[IntegrationTestResult] = []

    # ------------------------------------------------------------------ #
    # Signals                                                              #
    # ------------------------------------------------------------------ #

    @workflow.signal
    async def checkin_response(self, response: CheckinResponse) -> None:
        """Signal that a customer has responded to a check-in."""
        self._checkin_response = response

    @workflow.signal
    async def skip_milestone(self, milestone_id: str) -> None:
        """Signal to skip a specific milestone."""
        self._skip_milestone_id = milestone_id

    @workflow.signal
    async def pause_onboarding(self) -> None:
        """Signal to pause the onboarding process."""
        self._paused = True
        if self._state:
            self._state.paused = True
            self._state.current_stage = OnboardingStage.PAUSED

    @workflow.signal
    async def resume_onboarding(self) -> None:
        """Signal to resume a paused onboarding process."""
        self._paused = False
        self._resumed = True
        if self._state:
            self._state.paused = False

    # ------------------------------------------------------------------ #
    # Queries                                                              #
    # ------------------------------------------------------------------ #

    @workflow.query
    def get_onboarding_status(self) -> OnboardingState | None:
        return self._state

    @workflow.query
    def get_milestone_timeline(self) -> list[dict]:
        if not self._state:
            return []
        return [
            {
                "id": m.id,
                "type": m.type.value,
                "title": m.title,
                "scheduled_day": m.scheduled_day,
                "status": m.status.value,
                "started_at": m.started_at.isoformat() if m.started_at else None,
                "completed_at": m.completed_at.isoformat() if m.completed_at else None,
            }
            for m in self._state.plan.milestones
        ]

    @workflow.query
    def get_integration_results(self) -> list[IntegrationTestResult]:
        return list(self._integration_results)

    # ------------------------------------------------------------------ #
    # Main workflow run                                                    #
    # ------------------------------------------------------------------ #

    @workflow.run
    async def run(self, input_data: OnboardingPlan | OnboardingState) -> OnboardingState:
        """Execute the full onboarding plan.

        Args:
            input_data: Either an OnboardingPlan (fresh start) or an
                OnboardingState (resumed via continue-as-new).

        Returns:
            Final OnboardingState after all milestones are processed.
        """
        # Restore state from continue-as-new, or initialise fresh
        if isinstance(input_data, OnboardingState):
            self._state = input_data
        elif self._state is None:
            self._state = OnboardingState(
                customer_id=input_data.customer_id,
                plan=input_data,
            )
            input_data.started_at = workflow.now()

        activity_timeout = timedelta(seconds=120)
        retry_policy = RetryPolicy(
            maximum_attempts=3,
            initial_interval=timedelta(seconds=2),
            maximum_interval=timedelta(seconds=30),
            backoff_coefficient=2.0,
        )

        milestones = self._state.plan.milestones
        start_index = self._state.current_milestone_index

        try:
            for i in range(start_index, len(milestones)):
                milestone = milestones[i]
                self._state.current_milestone_index = i

                # Check for pause signal
                if self._paused:
                    workflow.logger.info("Onboarding paused, waiting for resume signal")
                    await workflow.wait_condition(lambda: self._resumed)
                    self._resumed = False
                    workflow.logger.info("Onboarding resumed")

                # Check for skip signal
                if self._skip_milestone_id == milestone.id:
                    workflow.logger.info(f"Skipping milestone {milestone.id}")
                    milestone.status = MilestoneStatus.SKIPPED
                    self._skip_milestone_id = None
                    continue

                # Set durable timer for the milestone's scheduled day
                # Calculate wait duration based on the milestone's day offset
                if i > 0:
                    prev_day = milestones[i - 1].scheduled_day
                    day_diff = milestone.scheduled_day - prev_day
                    if day_diff > 0:
                        wait_seconds = day_diff * 86400  # seconds per day
                        workflow.logger.info(
                            f"Waiting {day_diff} day(s) until day {milestone.scheduled_day} "
                            f"for milestone: {milestone.title}"
                        )
                        await workflow.sleep(timedelta(seconds=wait_seconds))

                # Mark milestone as in progress
                milestone.status = MilestoneStatus.IN_PROGRESS
                milestone.started_at = workflow.now()

                # Execute milestone based on type
                try:
                    if milestone.type == MilestoneType.ACCOUNT_VERIFICATION:
                        self._state.current_stage = OnboardingStage.ACCOUNT_VERIFICATION
                        result = await workflow.execute_activity_method(
                            OnboardingActivities.verify_account,
                            self._state.customer_id,
                            start_to_close_timeout=activity_timeout,
                            retry_policy=retry_policy,
                        )
                        milestone.result_data = result
                        if result.get("verified"):
                            milestone.status = MilestoneStatus.COMPLETED
                        else:
                            milestone.status = MilestoneStatus.FAILED
                            self._state.error_message = "Account verification failed"

                    elif milestone.type == MilestoneType.INTEGRATION_SETUP:
                        self._state.current_stage = OnboardingStage.INTEGRATION_SETUP
                        # Start IntegrationTestWorkflow as a child workflow
                        integration_config = milestone.result_data.get(
                            "integration_config",
                            {
                                "integration_id": f"int-{self._state.customer_id}",
                                "endpoint_url": f"https://api.customer.example.com/webhook",
                                "method": "POST",
                            },
                        )
                        test_result: IntegrationTestResult = (
                            await workflow.execute_child_workflow(
                                IntegrationTestWorkflow.run,
                                args=[self._state.customer_id, integration_config],
                                id=f"integration-test-{self._state.customer_id}-{milestone.id}",
                            )
                        )
                        self._integration_results.append(test_result)
                        milestone.result_data = test_result.model_dump(mode="json")
                        milestone.status = (
                            MilestoneStatus.COMPLETED
                            if test_result.test_passed
                            else MilestoneStatus.FAILED
                        )

                    elif milestone.type == MilestoneType.TRAINING_DELIVERY:
                        self._state.current_stage = OnboardingStage.TRAINING_DELIVERY
                        # Generate training materials
                        materials = await workflow.execute_activity_method(
                            OnboardingActivities.generate_training_materials,
                            args=[
                                self._state.customer_id,
                                self._state.plan.customer_name,
                            ],
                            start_to_close_timeout=activity_timeout,
                        )

                        # Send training materials via check-in email
                        await workflow.execute_activity_method(
                            OnboardingActivities.send_checkin,
                            args=[
                                self._state.customer_id,
                                milestone.id,
                                materials,
                            ],
                            start_to_close_timeout=activity_timeout,
                        )

                        # Wait for customer acknowledgement (48h timeout)
                        self._checkin_response = None
                        try:
                            await workflow.wait_condition(
                                lambda: self._checkin_response is not None,
                                timeout=timedelta(hours=48),
                            )
                        except asyncio.TimeoutError:
                            workflow.logger.warning(
                                f"No response to training delivery for "
                                f"{self._state.customer_id} within 48h"
                            )

                        if self._checkin_response:
                            milestone.result_data = {
                                "materials_sent": True,
                                "customer_response": self._checkin_response.response_text,
                                "satisfaction_score": self._checkin_response.satisfaction_score,
                            }
                        else:
                            milestone.result_data = {
                                "materials_sent": True,
                                "customer_response": None,
                                "timed_out": True,
                            }

                        milestone.status = MilestoneStatus.COMPLETED

                    elif milestone.type == MilestoneType.MILESTONE_CHECKIN:
                        self._state.current_stage = OnboardingStage.MILESTONE_CHECKIN
                        # Analyse progress
                        milestone_dicts = [
                            m.model_dump(mode="json")
                            for m in self._state.plan.milestones
                        ]
                        progress_report = await workflow.execute_activity_method(
                            OnboardingActivities.analyze_progress,
                            args=[self._state.customer_id, milestone_dicts],
                            start_to_close_timeout=activity_timeout,
                        )

                        # Send check-in email
                        await workflow.execute_activity_method(
                            OnboardingActivities.send_checkin,
                            args=[
                                self._state.customer_id,
                                milestone.id,
                                progress_report,
                            ],
                            start_to_close_timeout=activity_timeout,
                        )

                        milestone.result_data = {"progress_report": progress_report}
                        milestone.status = MilestoneStatus.COMPLETED

                    elif milestone.type == MilestoneType.FINAL_REVIEW:
                        self._state.current_stage = OnboardingStage.FINAL_REVIEW
                        # Comprehensive review of all milestones
                        milestone_dicts = [
                            m.model_dump(mode="json")
                            for m in self._state.plan.milestones
                        ]
                        final_report = await workflow.execute_activity_method(
                            OnboardingActivities.analyze_progress,
                            args=[self._state.customer_id, milestone_dicts],
                            start_to_close_timeout=activity_timeout,
                        )

                        # Send final review email
                        await workflow.execute_activity_method(
                            OnboardingActivities.send_checkin,
                            args=[
                                self._state.customer_id,
                                milestone.id,
                                final_report,
                            ],
                            start_to_close_timeout=activity_timeout,
                        )

                        milestone.result_data = {"final_report": final_report}
                        milestone.status = MilestoneStatus.COMPLETED

                except Exception as e:
                    workflow.logger.error(
                        f"Milestone {milestone.id} failed: {e}"
                    )
                    milestone.status = MilestoneStatus.FAILED
                    milestone.result_data["error"] = str(e)

                # Mark completed timestamp
                if milestone.status in (
                    MilestoneStatus.COMPLETED,
                    MilestoneStatus.FAILED,
                    MilestoneStatus.SKIPPED,
                ):
                    milestone.completed_at = workflow.now()

                # Continue-as-new if event history is approaching the limit
                if workflow.info().get_current_history_length() > _MAX_EVENTS_BEFORE_CAN:
                    workflow.logger.info(
                        "Event history approaching limit, continuing as new"
                    )
                    self._state.current_milestone_index = i + 1
                    workflow.continue_as_new(args=[self._state])

            # All milestones processed
            self._state.current_stage = OnboardingStage.COMPLETED
            return self._state

        except Exception as e:
            if self._state:
                self._state.error_message = str(e)
            workflow.logger.error(f"Onboarding workflow failed: {e}")
            return self._state  # type: ignore[return-value]
