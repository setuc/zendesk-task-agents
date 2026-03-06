from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from universal_computer.agents.tools import FunctionTool

from common.services.base import ZendeskService


# ---------------------------------------------------------------------------
# AnalyzeConversationTool
# ---------------------------------------------------------------------------

class AnalyzeConversationArgs(BaseModel):
    ticket_id: str = Field(description="The Zendesk ticket ID whose conversation to analyze")


class AnalyzeConversationTool(FunctionTool[AnalyzeConversationArgs, dict]):
    """Analyze the conversation history of a Zendesk ticket for sentiment,
    frustration trajectory, and escalation risk."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    tool_name = "analyze_conversation"
    args_model = AnalyzeConversationArgs
    description = (
        "Analyze the full conversation history of a Zendesk ticket to assess "
        "customer sentiment, frustration trajectory, key phrases, and escalation risk."
    )
    zendesk: ZendeskService = Field(exclude=True)

    async def run(self, args: AnalyzeConversationArgs) -> dict:
        ticket = await self.zendesk.get_ticket(args.ticket_id)

        # Gather conversation text from ticket description and comments
        description = ticket.get("description", "")
        comments = ticket.get("comments", [])
        comment_texts = [c.get("body", "") for c in comments]
        full_text = (description + " " + " ".join(comment_texts)).lower()

        # Heuristic sentiment analysis (UC agent would do this properly)
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

        # Determine trajectory based on comment ordering
        if len(comments) >= 2:
            recent_text = comments[-1].get("body", "").lower()
            has_recent_frustration = any(kw in recent_text for kw in frustration_keywords)
            trajectory = "worsening" if has_recent_frustration else "stable"
        else:
            trajectory = "stable"

        key_phrases = [kw for kw in frustration_keywords + positive_keywords if kw in full_text]

        return {
            "ticket_id": args.ticket_id,
            "overall_sentiment": sentiment,
            "frustration_trajectory": trajectory,
            "key_phrases": key_phrases,
            "escalation_risk": escalation_risk,
        }


# ---------------------------------------------------------------------------
# ClassifyUrgencyTool
# ---------------------------------------------------------------------------

class ClassifyUrgencyArgs(BaseModel):
    ticket_id: str = Field(description="The Zendesk ticket ID to classify urgency for")


class ClassifyUrgencyTool(FunctionTool[ClassifyUrgencyArgs, dict]):
    """Classify the true urgency of a Zendesk ticket by analyzing its content,
    priority field, customer tier, and conversation history."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    tool_name = "classify_urgency"
    args_model = ClassifyUrgencyArgs
    description = (
        "Classify the urgency of a Zendesk ticket by analyzing its content, "
        "assigned priority, customer tier, and conversation signals. Returns an "
        "urgency score and whether the priority should be overridden."
    )
    zendesk: ZendeskService = Field(exclude=True)

    async def run(self, args: ClassifyUrgencyArgs) -> dict:
        ticket = await self.zendesk.get_ticket(args.ticket_id)

        original_priority = ticket.get("priority", "normal")
        description = ticket.get("description", "").lower()
        customer_tier = ticket.get("requester", {}).get("tier", "standard")
        tags = ticket.get("tags", [])

        # Heuristic urgency scoring (UC agent would do this properly)
        urgency_score = 0.3  # baseline

        # Boost for high-value customers
        tier_boost = {"standard": 0.0, "premium": 0.15, "enterprise": 0.3}
        urgency_score += tier_boost.get(customer_tier, 0.0)

        # Boost for escalation-related content
        escalation_signals = [
            "urgent", "asap", "immediately", "critical", "deadline",
            "production down", "revenue impact", "legal", "cancel",
        ]
        signal_count = sum(1 for s in escalation_signals if s in description)
        urgency_score += min(signal_count * 0.1, 0.3)

        # Boost for escalation tags
        if "escalation" in tags:
            urgency_score += 0.15

        urgency_score = min(urgency_score, 1.0)

        # Determine assessed priority
        if urgency_score >= 0.8:
            assessed_priority = "urgent"
        elif urgency_score >= 0.6:
            assessed_priority = "high"
        elif urgency_score >= 0.4:
            assessed_priority = "normal"
        else:
            assessed_priority = "low"

        priority_override = assessed_priority != original_priority

        # Build reasoning
        reasons: list[str] = []
        if customer_tier != "standard":
            reasons.append(f"{customer_tier} tier customer")
        if signal_count > 0:
            reasons.append(f"{signal_count} urgency signal(s) in description")
        if "escalation" in tags:
            reasons.append("escalation tag present")
        if not reasons:
            reasons.append("standard assessment, no special signals detected")

        return {
            "ticket_id": args.ticket_id,
            "original_priority": original_priority,
            "assessed_priority": assessed_priority,
            "reasoning": "; ".join(reasons),
            "urgency_score": round(urgency_score, 2),
            "priority_override": priority_override,
        }
