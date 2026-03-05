from __future__ import annotations

from dataclasses import dataclass

from temporalio import activity

from .data_types import (
    EscalationTier,
    SentimentReport,
    UrgencyClassification,
)
from ...common.services.base import SLARulesService, ZendeskService


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
        """Use UC agent to analyze ticket and classify its true urgency.

        Considers ticket content, customer tier, conversation signals,
        and escalation tags to produce an urgency score and assessment.
        """
        activity.logger.info(f"Classifying urgency for ticket {ticket_id}")
        ticket = await self.zendesk.get_ticket(ticket_id)

        # In a real implementation, this would use:
        # agent = create_sla_agent(zendesk=self.zendesk, sla_rules=self.sla_rules)
        # client = UnixLocalSandboxClient()
        # async with agent.start(client=client, client_options=None) as task:
        #     result = await run_agent_to_completion(task, prompt)

        # Mock classification that mirrors what the UC agent would produce
        original_priority = ticket.get("priority", "normal")
        description = ticket.get("description", "").lower()
        customer_tier = ticket.get("requester", {}).get("tier", "standard")
        tags = ticket.get("tags", [])

        # Heuristic urgency scoring
        urgency_score = 0.3

        tier_boost = {"standard": 0.0, "premium": 0.15, "enterprise": 0.3}
        urgency_score += tier_boost.get(customer_tier, 0.0)

        escalation_signals = [
            "urgent", "asap", "immediately", "critical", "deadline",
            "production down", "revenue impact", "legal", "cancel",
        ]
        signal_count = sum(1 for s in escalation_signals if s in description)
        urgency_score += min(signal_count * 0.1, 0.3)

        if "escalation" in tags:
            urgency_score += 0.15

        urgency_score = min(urgency_score, 1.0)

        if urgency_score >= 0.8:
            assessed_priority = "urgent"
        elif urgency_score >= 0.6:
            assessed_priority = "high"
        elif urgency_score >= 0.4:
            assessed_priority = "normal"
        else:
            assessed_priority = "low"

        priority_override = assessed_priority != original_priority

        reasons: list[str] = []
        if customer_tier != "standard":
            reasons.append(f"{customer_tier} tier customer")
        if signal_count > 0:
            reasons.append(f"{signal_count} urgency signal(s) in description")
        if "escalation" in tags:
            reasons.append("escalation tag present")
        if not reasons:
            reasons.append("standard assessment, no special signals detected")

        return UrgencyClassification(
            ticket_id=ticket_id,
            original_priority=original_priority,
            assessed_priority=assessed_priority,
            reasoning="; ".join(reasons),
            urgency_score=round(urgency_score, 2),
            priority_override=priority_override,
        )

    @activity.defn
    async def analyze_sentiment(self, ticket_id: str) -> SentimentReport:
        """Use UC agent to analyze conversation sentiment and frustration trajectory.

        Examines the ticket description and all comments to assess overall
        sentiment, trajectory, and escalation risk.
        """
        activity.logger.info(f"Analyzing sentiment for ticket {ticket_id}")
        ticket = await self.zendesk.get_ticket(ticket_id)

        # In a real implementation, the UC agent would do deep NLP analysis.
        # Mock analysis mirrors what the UC agent would produce.
        description = ticket.get("description", "")
        comments = ticket.get("comments", [])
        comment_texts = [c.get("body", "") for c in comments]
        full_text = (description + " " + " ".join(comment_texts)).lower()

        frustration_keywords = [
            "frustrated", "angry", "unacceptable", "terrible", "worst",
            "ridiculous", "disappointed", "furious", "outraged", "still waiting",
        ]
        positive_keywords = [
            "thank", "appreciate", "great", "helpful", "resolved",
        ]

        frustration_count = sum(1 for kw in frustration_keywords if kw in full_text)
        positive_count = sum(1 for kw in positive_keywords if kw in full_text)

        if frustration_count >= 3:
            sentiment = "angry"
            escalation_risk = 0.9
        elif frustration_count >= 1:
            sentiment = "frustrated"
            escalation_risk = 0.6
        elif positive_count >= 1:
            sentiment = "positive"
            escalation_risk = 0.1
        else:
            sentiment = "neutral"
            escalation_risk = 0.3

        if len(comments) >= 2:
            recent_text = comments[-1].get("body", "").lower()
            has_recent_frustration = any(kw in recent_text for kw in frustration_keywords)
            trajectory = "worsening" if has_recent_frustration else "stable"
        else:
            trajectory = "stable"

        key_phrases = [kw for kw in frustration_keywords + positive_keywords if kw in full_text]

        return SentimentReport(
            ticket_id=ticket_id,
            overall_sentiment=sentiment,
            frustration_trajectory=trajectory,
            key_phrases=key_phrases,
            escalation_risk=round(escalation_risk, 2),
        )

    @activity.defn
    async def draft_escalation(
        self, ticket_id: str, from_tier: str, to_tier: str
    ) -> str:
        """Use UC agent to draft a professional escalation message.

        Composes an internal escalation message including ticket context,
        SLA status, sentiment, and recommended next steps.
        """
        activity.logger.info(
            f"Drafting escalation for ticket {ticket_id}: {from_tier} -> {to_tier}"
        )
        ticket = await self.zendesk.get_ticket(ticket_id)

        # In a real implementation, the UC agent would craft this message.
        # Mock drafting that mirrors what the UC agent would produce.
        subject = ticket.get("subject", "Unknown")
        priority = ticket.get("priority", "normal")
        customer_tier = ticket.get("requester", {}).get("tier", "standard")
        customer_name = ticket.get("requester", {}).get("name", "Unknown Customer")

        message = (
            f"ESCALATION: {from_tier.upper()} -> {to_tier.upper()}\n"
            f"{'=' * 50}\n\n"
            f"Ticket: {ticket_id} - {subject}\n"
            f"Customer: {customer_name} ({customer_tier} tier)\n"
            f"Priority: {priority}\n\n"
            f"This ticket requires escalation to {to_tier.upper()} due to "
            f"approaching SLA deadline. The customer is a {customer_tier} tier "
            f"account and the current priority is {priority}.\n\n"
            f"Recommended Actions:\n"
            f"- Review ticket history and previous resolution attempts\n"
            f"- Contact customer within 1 hour to acknowledge escalation\n"
            f"- Provide status update to management if not resolved within "
            f"the {to_tier.upper()} SLA window\n"
        )

        return message

    @activity.defn
    async def escalate_ticket(
        self, ticket_id: str, to_tier: str, message: str
    ) -> dict:
        """Execute the escalation by updating the ticket and adding an internal note.

        Updates ticket priority/tags and posts the escalation message as an
        internal comment.
        """
        activity.logger.info(f"Escalating ticket {ticket_id} to {to_tier}")

        # Update ticket with escalation metadata
        updates: dict = {
            "tags_add": [f"escalated_{to_tier}", "sla_guardian"],
        }

        # Upgrade priority if escalating to L3 or manager
        if to_tier in ("l3", "manager"):
            updates["priority"] = "urgent"

        await self.zendesk.update_ticket(ticket_id, updates)

        # Add internal escalation note
        await self.zendesk.add_comment(
            ticket_id,
            body=message,
            public=False,
        )

        return {
            "ticket_id": ticket_id,
            "escalated_to": to_tier,
            "status": "escalated",
            "message_posted": True,
        }
