from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from temporalio import activity

from .data_types import (
    EscalationResult,
    EscalationTier,
    SentimentReport,
    UrgencyClassification,
)
from ...common.services.base import SLARulesService, ZendeskService


# ---------------------------------------------------------------------------
# Keyword / signal dictionaries
# ---------------------------------------------------------------------------

_URGENCY_SIGNALS: dict[str, float] = {
    "urgent": 0.12,
    "asap": 0.10,
    "immediately": 0.12,
    "critical": 0.14,
    "deadline": 0.08,
    "production down": 0.18,
    "revenue impact": 0.15,
    "legal": 0.10,
    "cancel": 0.08,
    "data loss": 0.16,
    "security breach": 0.18,
    "compliance": 0.10,
    "outage": 0.14,
    "blocked": 0.08,
    "cannot access": 0.06,
    "down": 0.06,
    "broken": 0.06,
    "failing": 0.06,
    "500 error": 0.10,
}

_FRUSTRATION_KEYWORDS: list[str] = [
    "frustrated", "angry", "unacceptable", "terrible", "worst",
    "ridiculous", "disappointed", "furious", "outraged", "still waiting",
    "fed up", "disgusted", "appalled", "infuriating", "inexcusable",
    "waste of time", "incompetent", "useless", "pathetic", "shocking",
    "losing patience", "last straw", "cancel my account",
    "switching providers", "considering switching",
]

_POSITIVE_KEYWORDS: list[str] = [
    "thank", "thanks", "appreciate", "great", "helpful", "resolved",
    "excellent", "wonderful", "amazing", "good job", "well done",
    "quick response", "impressed", "satisfied", "happy",
]

_TIER_BOOST: dict[str, float] = {
    "standard": 0.0,
    "premium": 0.15,
    "enterprise": 0.30,
}

_TEAM_ASSIGNMENTS: dict[str, str] = {
    "l2": "Senior Support Engineers",
    "l3": "Engineering Escalation Team",
    "manager": "VP of Customer Success + Engineering Lead",
}

_RESPONSE_TIMES: dict[str, str] = {
    "l2": "within 2 hours",
    "l3": "within 30 minutes",
    "manager": "within 15 minutes",
}


def _score_text_for_sentiment(text: str) -> tuple[int, int, list[str]]:
    """Return (frustration_count, positive_count, matched_phrases)."""
    lower = text.lower()
    frust = 0
    pos = 0
    phrases: list[str] = []
    for kw in _FRUSTRATION_KEYWORDS:
        if kw in lower:
            frust += 1
            phrases.append(kw)
    for kw in _POSITIVE_KEYWORDS:
        if kw in lower:
            pos += 1
            phrases.append(kw)
    return frust, pos, phrases


def _classify_single_text_sentiment(text: str) -> str:
    """Return a sentiment label for a single piece of text."""
    frust, pos, _ = _score_text_for_sentiment(text)
    if frust >= 3:
        return "angry"
    if frust >= 1:
        return "frustrated"
    if pos >= 1:
        return "positive"
    return "neutral"


def _format_timedelta_human(total_seconds: float) -> str:
    """Format seconds as a human-readable duration like '2h 15m'."""
    if total_seconds < 0:
        return "OVERDUE by " + _format_timedelta_human(abs(total_seconds))
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


@dataclass
class SLAGuardianActivities:
    """Activity implementations for SLA Guardian workflows.

    Uses the UC agent (via service references) for intelligent ticket analysis,
    urgency classification, sentiment analysis, and escalation drafting.
    """

    zendesk: ZendeskService
    sla_rules: SLARulesService

    @activity.defn
    async def scan_open_tickets(self, filters: dict | None) -> list[dict]:
        """Scan Zendesk for open tickets matching the given filters.

        Returns a list of ticket dicts for monitoring.
        """
        activity.logger.info("Scanning open tickets")

        # Default to open/new tickets if no filters provided
        scan_filters = filters or {"status": "open"}
        tickets = await self.zendesk.list_tickets(scan_filters)

        # Also include new tickets
        if scan_filters.get("status") == "open":
            new_tickets = await self.zendesk.list_tickets({"status": "new"})
            seen_ids = {t.get("id") for t in tickets}
            for t in new_tickets:
                if t.get("id") not in seen_ids:
                    tickets.append(t)

        activity.logger.info(f"Found {len(tickets)} open/new tickets")
        return tickets

    @activity.defn
    async def classify_urgency(self, ticket_id: str) -> UrgencyClassification:
        """Analyze ticket deeply and classify its true urgency.

        Considers: customer tier priority overrides, time pressure relative to
        SLA deadline, number of customer comments (frustration proxy),
        escalation signals in the description and comments, and tag metadata.
        """
        activity.logger.info(f"Classifying urgency for ticket {ticket_id}")
        ticket = await self.zendesk.get_ticket(ticket_id)

        original_priority = ticket.get("priority", "normal")
        description = ticket.get("description", "")
        customer_tier = ticket.get("requester", {}).get("tier", "standard")
        customer_name = ticket.get("requester", {}).get("name", "Unknown")
        tags = ticket.get("tags", [])
        comments = ticket.get("comments", [])
        created_at_raw = ticket.get("created_at", "")
        sla_deadline_raw = ticket.get("sla_deadline", "")

        # Collect all text for signal scanning
        all_text = description.lower()
        for c in comments:
            all_text += " " + c.get("body", "").lower()

        # --- Score calculation ---
        urgency_score = 0.3  # baseline

        # 1. Customer tier boost
        tier_boost = _TIER_BOOST.get(customer_tier, 0.0)
        urgency_score += tier_boost

        # 2. Urgency signals in text
        signals_detected: list[str] = []
        signal_score = 0.0
        for signal, weight in _URGENCY_SIGNALS.items():
            if signal in all_text:
                signals_detected.append(signal)
                signal_score += weight
        urgency_score += min(signal_score, 0.35)

        # 3. Escalation tag
        if "escalation" in tags:
            urgency_score += 0.10
            signals_detected.append("escalation tag")

        # 4. Comment count -- more comments = likely more frustrated customer
        customer_comments = [
            c for c in comments
            if not c.get("author", "").startswith("agent")
        ]
        comment_boost = min(len(customer_comments) * 0.05, 0.15)
        urgency_score += comment_boost

        # 5. Time pressure relative to SLA deadline
        time_pressure_str = ""
        now = datetime.now(timezone.utc)
        if sla_deadline_raw:
            try:
                if isinstance(sla_deadline_raw, str):
                    deadline = datetime.fromisoformat(sla_deadline_raw)
                else:
                    deadline = sla_deadline_raw
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=timezone.utc)
                remaining = (deadline - now).total_seconds()
                time_pressure_str = _format_timedelta_human(remaining) + " remaining"
                # Closer to deadline = higher urgency
                if remaining <= 0:
                    urgency_score += 0.25
                    time_pressure_str = "OVERDUE by " + _format_timedelta_human(
                        abs(remaining)
                    )
                    signals_detected.append("SLA breached")
                elif remaining < 3600:  # < 1 hour
                    urgency_score += 0.20
                    signals_detected.append("< 1h to SLA deadline")
                elif remaining < 7200:  # < 2 hours
                    urgency_score += 0.12
                    signals_detected.append("< 2h to SLA deadline")
                elif remaining < 14400:  # < 4 hours
                    urgency_score += 0.06
            except (ValueError, TypeError):
                pass

        # Look up SLA policy for context
        policy = await self.sla_rules.get_policy(original_priority, customer_tier)
        sla_resolution_hours = policy.get("resolution_hours", 72)

        # Clamp
        urgency_score = round(min(urgency_score, 1.0), 2)

        # --- Priority mapping ---
        if urgency_score >= 0.80:
            assessed_priority = "urgent"
        elif urgency_score >= 0.60:
            assessed_priority = "high"
        elif urgency_score >= 0.40:
            assessed_priority = "normal"
        else:
            assessed_priority = "low"

        priority_override = assessed_priority != original_priority

        # --- Detailed reasoning ---
        reasons: list[str] = []
        if customer_tier != "standard":
            reasons.append(
                f"{customer_tier.upper()} tier customer ({customer_name}) "
                f"-- SLA target: {sla_resolution_hours}h resolution"
            )
        if time_pressure_str:
            reasons.append(f"Time pressure: {time_pressure_str}")
        if signals_detected:
            reasons.append(
                f"Detected {len(signals_detected)} urgency signal(s): "
                + ", ".join(signals_detected)
            )
        if len(customer_comments) > 0:
            reasons.append(
                f"Customer has posted {len(customer_comments)} follow-up comment(s) "
                f"(+{comment_boost:.2f} urgency boost)"
            )
        if priority_override:
            reasons.append(
                f"PRIORITY OVERRIDE: {original_priority} -> {assessed_priority} "
                f"(score {urgency_score})"
            )
        if not reasons:
            reasons.append("Standard assessment -- no special signals detected")

        reasoning = "\n  ".join(reasons)

        return UrgencyClassification(
            ticket_id=ticket_id,
            original_priority=original_priority,
            assessed_priority=assessed_priority,
            reasoning=reasoning,
            urgency_score=urgency_score,
            priority_override=priority_override,
            customer_tier=customer_tier,
            time_pressure=time_pressure_str,
            comment_count=len(comments),
            signals_detected=signals_detected,
        )

    @activity.defn
    async def analyze_sentiment(self, ticket_id: str) -> SentimentReport:
        """Rich sentiment analysis across all comments.

        Tracks frustration/positive keyword counts, sentiment trajectory
        (first comment vs latest), frustration score (0-10), and produces
        actionable insights for the support team.
        """
        activity.logger.info(f"Analyzing sentiment for ticket {ticket_id}")
        ticket = await self.zendesk.get_ticket(ticket_id)

        description = ticket.get("description", "")
        comments = ticket.get("comments", [])
        comment_texts = [c.get("body", "") for c in comments]
        full_text = description + " " + " ".join(comment_texts)

        # --- Overall scoring ---
        total_frust, total_pos, all_phrases = _score_text_for_sentiment(full_text)

        # Frustration score on 0-10 scale
        frustration_score = round(min(total_frust * 2.0, 10.0), 1)
        positive_score = round(min(total_pos * 2.5, 10.0), 1)

        # Overall sentiment label
        if frustration_score >= 7.0:
            sentiment = "angry"
            escalation_risk = 0.95
        elif frustration_score >= 4.0:
            sentiment = "frustrated"
            escalation_risk = 0.70
        elif frustration_score >= 2.0:
            sentiment = "concerned"
            escalation_risk = 0.45
        elif positive_score >= 4.0:
            sentiment = "positive"
            escalation_risk = 0.10
        elif positive_score >= 2.0:
            sentiment = "satisfied"
            escalation_risk = 0.15
        else:
            sentiment = "neutral"
            escalation_risk = 0.30

        # --- Trajectory analysis ---
        first_comment_sentiment = ""
        latest_comment_sentiment = ""
        trajectory = "stable"

        if len(comments) >= 1:
            first_comment_sentiment = _classify_single_text_sentiment(
                comments[0].get("body", "")
            )
        if len(comments) >= 2:
            latest_comment_sentiment = _classify_single_text_sentiment(
                comments[-1].get("body", "")
            )
            # Determine trajectory
            sentiment_rank = {
                "positive": 1, "satisfied": 2, "neutral": 3,
                "concerned": 4, "frustrated": 5, "angry": 6,
            }
            first_rank = sentiment_rank.get(first_comment_sentiment, 3)
            latest_rank = sentiment_rank.get(latest_comment_sentiment, 3)
            if latest_rank > first_rank:
                trajectory = "worsening"
            elif latest_rank < first_rank:
                trajectory = "improving"
            else:
                trajectory = "stable"
        elif len(comments) == 1:
            latest_comment_sentiment = first_comment_sentiment

        # If description alone is very negative and no comments, note it
        if not comments:
            desc_frust, desc_pos, _ = _score_text_for_sentiment(description)
            if desc_frust >= 2:
                first_comment_sentiment = "frustrated (initial description)"
                latest_comment_sentiment = first_comment_sentiment

        # --- Actionable insights ---
        insights: list[str] = []
        customer_name = ticket.get("requester", {}).get("name", "the customer")
        customer_tier = ticket.get("requester", {}).get("tier", "standard")

        if sentiment in ("angry", "frustrated"):
            insights.append(
                f"Customer sentiment is {sentiment.upper()} -- prioritize empathetic "
                f"response and acknowledge frustration directly"
            )
        if trajectory == "worsening":
            insights.append(
                "Sentiment is WORSENING across comments -- immediate attention needed "
                "to prevent churn"
            )
        if "cancel" in full_text.lower() or "switching providers" in full_text.lower():
            insights.append(
                f"CHURN RISK: {customer_name} has mentioned cancellation or switching "
                f"providers"
            )
        if customer_tier == "enterprise" and frustration_score >= 4.0:
            insights.append(
                f"HIGH-VALUE ACCOUNT at risk: {customer_name} is an enterprise "
                f"customer with elevated frustration"
            )
        if len(comments) >= 3 and trajectory != "improving":
            insights.append(
                f"Extended conversation ({len(comments)} comments) without "
                f"improvement -- consider escalation"
            )
        if positive_score >= 4.0:
            insights.append(
                f"Customer is expressing satisfaction -- good opportunity to "
                f"strengthen relationship"
            )
        if not insights:
            insights.append("No immediate red flags -- continue standard monitoring")

        return SentimentReport(
            ticket_id=ticket_id,
            overall_sentiment=sentiment,
            frustration_trajectory=trajectory,
            key_phrases=all_phrases,
            escalation_risk=round(escalation_risk, 2),
            frustration_score=frustration_score,
            positive_score=positive_score,
            comment_count=len(comments),
            first_comment_sentiment=first_comment_sentiment,
            latest_comment_sentiment=latest_comment_sentiment,
            actionable_insights=insights,
        )

    @activity.defn
    async def draft_escalation(
        self, ticket_id: str, from_tier: str, to_tier: str
    ) -> str:
        """Draft a personalized escalation message with tone/content based on tier.

        L1->L2: Informational -- context handoff with ticket summary.
        L2->L3: Urgent -- technical details, timeline, customer impact.
        L3->Manager: Critical -- business impact, revenue risk, executive summary.
        """
        activity.logger.info(
            f"Drafting escalation for ticket {ticket_id}: {from_tier} -> {to_tier}"
        )
        ticket = await self.zendesk.get_ticket(ticket_id)

        subject = ticket.get("subject", "Unknown")
        description = ticket.get("description", "")
        priority = ticket.get("priority", "normal")
        status = ticket.get("status", "open")
        customer_tier = ticket.get("requester", {}).get("tier", "standard")
        customer_name = ticket.get("requester", {}).get("name", "Unknown Customer")
        customer_email = ticket.get("requester", {}).get("email", "")
        account_created = ticket.get("requester", {}).get("account_created", "")
        tags = ticket.get("tags", [])
        comments = ticket.get("comments", [])
        created_at = ticket.get("created_at", "")
        sla_deadline = ticket.get("sla_deadline", "")
        custom_fields = ticket.get("custom_fields", {})

        # Look up SLA policy
        policy = await self.sla_rules.get_policy(priority, customer_tier)
        resolution_hours = policy.get("resolution_hours", 72)

        assigned_team = _TEAM_ASSIGNMENTS.get(to_tier, "Support Team")
        expected_response = _RESPONSE_TIMES.get(to_tier, "within 4 hours")

        # Comment summary
        comment_count = len(comments)
        last_customer_comment = ""
        for c in reversed(comments):
            if not c.get("author", "").startswith("agent"):
                last_customer_comment = c.get("body", "")[:200]
                break

        # Custom fields summary
        cf_lines = ""
        if custom_fields:
            cf_parts = [f"    {k}: {v}" for k, v in custom_fields.items()]
            cf_lines = "\n".join(cf_parts)

        # --- Tier-specific message composition ---

        if to_tier == "l2":
            # Informational handoff
            message = (
                f"ESCALATION: {from_tier.upper()} -> L2 (Senior Support)\n"
                f"{'=' * 60}\n"
                f"\n"
                f"Ticket #{ticket_id}: {subject}\n"
                f"Status: {status}  |  Priority: {priority.upper()}\n"
                f"Created: {created_at}\n"
                f"SLA Deadline: {sla_deadline}  |  Resolution Target: {resolution_hours}h\n"
                f"\n"
                f"CUSTOMER PROFILE\n"
                f"{'-' * 40}\n"
                f"  Name: {customer_name}\n"
                f"  Email: {customer_email}\n"
                f"  Tier: {customer_tier.upper()}\n"
                f"  Account Since: {account_created}\n"
                f"\n"
                f"ISSUE SUMMARY\n"
                f"{'-' * 40}\n"
                f"  {description[:300]}\n"
                f"\n"
                f"CONVERSATION ({comment_count} comments)\n"
                f"{'-' * 40}\n"
            )
            if last_customer_comment:
                message += f"  Last customer message: \"{last_customer_comment}\"\n"
            if cf_lines:
                message += f"\nRELATED DATA\n{'-' * 40}\n{cf_lines}\n"
            message += (
                f"\n"
                f"RECOMMENDED ACTIONS\n"
                f"{'-' * 40}\n"
                f"  1. Review full conversation history and prior resolution attempts\n"
                f"  2. Respond to customer {expected_response} acknowledging escalation\n"
                f"  3. If blocked on technical issue, loop in L3 engineering\n"
                f"  4. Update ticket with progress notes within 1 hour\n"
            )

        elif to_tier == "l3":
            # Urgent -- technical focus
            message = (
                f"!! URGENT ESCALATION: L2 -> L3 (Engineering) !!\n"
                f"{'=' * 60}\n"
                f"\n"
                f"Ticket #{ticket_id}: {subject}\n"
                f"Priority: {priority.upper()}  |  SLA Deadline: {sla_deadline}\n"
                f"Customer: {customer_name} ({customer_tier.upper()} tier)\n"
                f"\n"
                f"THIS TICKET REQUIRES IMMEDIATE ENGINEERING ATTENTION.\n"
                f"\n"
                f"TECHNICAL CONTEXT\n"
                f"{'-' * 40}\n"
                f"  {description[:500]}\n"
                f"\n"
                f"Tags: {', '.join(tags)}\n"
            )
            if cf_lines:
                message += f"\nTechnical References\n{cf_lines}\n"
            message += (
                f"\n"
                f"TIMELINE\n"
                f"{'-' * 40}\n"
                f"  Ticket created: {created_at}\n"
                f"  Comments exchanged: {comment_count}\n"
                f"  SLA resolution target: {resolution_hours}h\n"
                f"  Assigned team: {assigned_team}\n"
                f"\n"
            )
            if last_customer_comment:
                message += (
                    f"LATEST CUSTOMER MESSAGE\n"
                    f"{'-' * 40}\n"
                    f"  \"{last_customer_comment}\"\n"
                    f"\n"
                )
            message += (
                f"REQUIRED ACTIONS (Response expected {expected_response})\n"
                f"{'-' * 40}\n"
                f"  1. Investigate root cause of the reported issue\n"
                f"  2. Provide initial technical assessment within 30 minutes\n"
                f"  3. Contact customer directly if reproduction steps are needed\n"
                f"  4. If fix requires deployment, coordinate with release team\n"
                f"  5. Post status update to ticket every 30 minutes until resolved\n"
            )

        elif to_tier == "manager":
            # Critical -- business impact, executive summary
            message = (
                f"*** CRITICAL ESCALATION: L3 -> MANAGEMENT ***\n"
                f"{'=' * 60}\n"
                f"\n"
                f"EXECUTIVE SUMMARY\n"
                f"{'-' * 40}\n"
                f"  Ticket #{ticket_id} ({subject}) has reached the highest\n"
                f"  escalation tier. The {customer_tier.upper()} tier customer\n"
                f"  {customer_name} ({customer_email}) has an unresolved issue\n"
                f"  that has breached or is about to breach SLA commitments.\n"
                f"\n"
                f"BUSINESS IMPACT\n"
                f"{'-' * 40}\n"
                f"  Customer Tier: {customer_tier.upper()}\n"
                f"  Account Since: {account_created}\n"
                f"  SLA Target: {resolution_hours}h resolution\n"
                f"  SLA Deadline: {sla_deadline}\n"
                f"  Ticket Priority: {priority.upper()}\n"
                f"  Conversation Length: {comment_count} comments\n"
            )
            # Flag churn risk signals
            full_text = (description + " " + " ".join(
                c.get("body", "") for c in comments
            )).lower()
            churn_signals = []
            for signal in ["cancel", "switching providers", "considering switching",
                           "legal", "lawsuit", "BBB", "social media"]:
                if signal in full_text:
                    churn_signals.append(signal)
            if churn_signals:
                message += (
                    f"  CHURN RISK SIGNALS: {', '.join(churn_signals)}\n"
                )
            if customer_tier == "enterprise":
                message += (
                    f"  ** HIGH-VALUE ACCOUNT -- revenue impact likely **\n"
                )
            message += (
                f"\n"
                f"ISSUE DETAILS\n"
                f"{'-' * 40}\n"
                f"  {description[:400]}\n"
                f"\n"
            )
            if last_customer_comment:
                message += (
                    f"LATEST CUSTOMER MESSAGE\n"
                    f"{'-' * 40}\n"
                    f"  \"{last_customer_comment}\"\n"
                    f"\n"
                )
            message += (
                f"REQUIRED MANAGEMENT ACTIONS (Response expected {expected_response})\n"
                f"{'-' * 40}\n"
                f"  1. Personally review ticket status and engineering progress\n"
                f"  2. Consider direct outreach to customer as executive sponsor\n"
                f"  3. Authorize emergency resources if needed (weekend/overtime)\n"
                f"  4. Prepare incident report for leadership review\n"
                f"  5. Schedule post-incident review once resolved\n"
                f"  6. Evaluate if SLA credits or goodwill gestures are appropriate\n"
            )

        else:
            # Generic fallback
            message = (
                f"ESCALATION: {from_tier.upper()} -> {to_tier.upper()}\n"
                f"{'=' * 60}\n"
                f"\n"
                f"Ticket #{ticket_id}: {subject}\n"
                f"Customer: {customer_name} ({customer_tier} tier)\n"
                f"Priority: {priority.upper()}\n"
                f"SLA Deadline: {sla_deadline}\n"
                f"\n"
                f"Please review and take appropriate action.\n"
            )

        return message

    @activity.defn
    async def escalate_ticket(
        self, ticket_id: str, to_tier: str, message: str
    ) -> EscalationResult:
        """Execute escalation: update ticket, add internal note, return rich result."""
        activity.logger.info(f"Escalating ticket {ticket_id} to {to_tier}")

        now = datetime.now(timezone.utc)

        # Update ticket with escalation metadata
        updates: dict = {
            "tags_add": [f"escalated_{to_tier}", "sla_guardian"],
        }

        # Upgrade priority if escalating to L3 or manager
        if to_tier in ("l3", "manager"):
            updates["priority"] = "urgent"

        await self.zendesk.update_ticket(ticket_id, updates)

        # Add internal escalation note
        comment_result = await self.zendesk.add_comment(
            ticket_id,
            body=message,
            public=False,
        )

        assigned_team = _TEAM_ASSIGNMENTS.get(to_tier, "Support Team")
        expected_response = _RESPONSE_TIMES.get(to_tier, "within 4 hours")

        return EscalationResult(
            ticket_id=ticket_id,
            escalated_to=to_tier,
            status="escalated",
            message_posted=True,
            timestamp=now.isoformat(),
            assigned_team=assigned_team,
            expected_response_time=expected_response,
            internal_note_id=comment_result.get("id", ""),
        )
