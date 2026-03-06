from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Name pools
# ---------------------------------------------------------------------------

CUSTOMER_NAMES = [
    "Sarah Chen", "Marcus Williams", "Diana Park", "Tom Baker", "Lisa Nguyen",
    "James Rodriguez", "Emma Thompson", "Raj Patel", "Maria Garcia", "David Kim",
    "Jennifer Lee", "Michael Brown", "Sophia Martinez", "Robert Wilson", "Amanda Taylor",
    "Christopher Davis", "Jessica Anderson", "Daniel Thomas", "Ashley Jackson", "Matthew White",
    "Olivia Harris", "Andrew Martin", "Emily Clark", "Joshua Lewis", "Megan Robinson",
    "Kevin Walker", "Nicole Hall", "Ryan Allen", "Samantha Young", "Tyler King",
    "Hannah Wright", "Brandon Lopez", "Kayla Scott", "Justin Green", "Rachel Adams",
    "Aisha Okafor", "Wei Zhang", "Priya Sharma", "Carlos Mendoza", "Yuki Tanaka",
    "Fatima Al-Rashidi", "Ivan Petrov", "Chloe Dubois", "Lars Johansson", "Nadia Kowalski",
    "Tariq Hassan", "Ingrid Muller", "Joaquin Reyes", "Suki Yamamoto", "Dmitri Volkov",
]

COMPANY_DOMAINS = [
    "acme-corp.com", "globex.io", "initech.co", "hooli.com", "piedpiper.net",
    "contoso.com", "northwind-traders.com", "widgetco.io", "megacorp.com", "startupx.co",
    "enterprise-solutions.com", "cloudnine.io", "rapidgrowth.co", "techforward.com",
    "bluesky-industries.com", "greenmountain.io", "silverline.co", "ironclad-systems.com",
    "quantum-leap.io", "fusionworks.com", "apex-digital.co", "crestview.io",
    "summit-tech.com", "vanguard-software.io", "pinnacle-services.com",
]

AGENT_NAMES = [
    "agent_alex", "agent_jane", "agent_mike", "agent_sarah", "agent_tom",
    "agent_rachel", "agent_david", "agent_emma", "agent_carlos", "agent_priya",
]

CUSTOMER_TIERS = ["standard", "premium", "enterprise"]
TIER_WEIGHTS = [0.50, 0.30, 0.20]

PRIORITIES = ["low", "normal", "high", "urgent"]
PRIORITY_WEIGHTS = [0.15, 0.40, 0.30, 0.15]

STATUSES = ["new", "open"]
STATUS_WEIGHTS = [0.30, 0.70]

# ---------------------------------------------------------------------------
# Item / product pools
# ---------------------------------------------------------------------------

ITEM_NAMES = [
    "wireless headphones", "laptop stand", "USB-C hub", "ergonomic keyboard",
    "27-inch monitor", "webcam", "desk lamp", "standing desk converter",
    "mechanical keyboard", "noise-cancelling earbuds", "portable charger",
    "smart thermostat", "wireless mouse", "docking station", "cable management kit",
    "router", "NAS drive", "SSD upgrade kit", "graphics tablet", "microphone",
]

WRONG_ATTRS = ["color", "size", "model", "version", "configuration"]

DAMAGE_DESCRIPTIONS = [
    "The screen has a large crack running diagonally across it",
    "The packaging was crushed and the item inside is bent",
    "There are visible scratches all over the surface",
    "One of the legs is broken off completely",
    "The power button is stuck and won't click",
    "Water damage is evident - there's condensation inside the screen",
    "The box was open when it arrived and parts are missing",
    "The casing is cracked in multiple places",
    "It arrived with a dent on the top panel",
    "The hinge is broken and the lid won't stay up",
]

# ---------------------------------------------------------------------------
# Technical pools
# ---------------------------------------------------------------------------

ERROR_CODES = ["500", "502", "503", "429", "408", "504", "401", "403"]

API_ENDPOINTS = [
    "/api/v2/sync", "/api/v2/users", "/api/v2/tickets", "/api/v2/webhooks",
    "/api/v3/data-export", "/api/v2/organizations", "/api/v2/search",
    "/api/v2/automations", "/graphql", "/api/v2/triggers",
]

WEBHOOK_ENDPOINTS = [
    "https://hooks.example.com/zendesk", "https://api.internal.io/webhooks/support",
    "https://integrations.myapp.com/zendesk-hook", "https://n8n.company.io/webhook/zd",
    "https://zapier.com/hooks/catch/12345/", "https://automate.internal.net/ingest",
]

LOG_DETAILS = [
    "connection reset by peer", "TLS handshake timeout", "HTTP 502 from upstream",
    "ECONNREFUSED on port 443", "DNS resolution failure for api.yourservice.com",
    "request body too large (413)", "invalid JSON response body",
    "certificate verification failed", "socket hang up after 30s",
]

TECHNICAL_DETAILS = [
    "We're sending standard JSON payloads, typically 2-5KB each.",
    "The error started after your last maintenance window on Saturday.",
    "Our request rate is well within the documented limits (50 req/s).",
    "We've confirmed our API key and OAuth tokens are valid and not expired.",
    "The issue is intermittent - about 30% of requests fail.",
    "This only affects our production environment, staging works fine.",
    "We've tried rotating our API credentials but the issue persists.",
    "Our monitoring shows latency spikes correlating with the errors.",
    "The same requests work perfectly when tested via curl from a local machine.",
    "We've verified our firewall rules haven't changed recently.",
]

LOAD_TIMES = ["8", "12", "15", "22", "35", "45", "60"]

# ---------------------------------------------------------------------------
# Billing pools
# ---------------------------------------------------------------------------

BILLING_CONTEXTS = [
    "I've double-checked my contract and the agreed rate is clearly stated.",
    "This is the second time this quarter we've been overcharged.",
    "Our finance team flagged this during their monthly reconciliation.",
    "I have the original quote email from your sales rep confirming the price.",
    "We signed up for the annual plan specifically to lock in this rate.",
    "The pricing page on your website still shows the lower amount.",
]

# ---------------------------------------------------------------------------
# Feature request pools
# ---------------------------------------------------------------------------

FEATURE_NAMES = [
    "bulk export to CSV", "dark mode for dashboard", "SSO with Okta",
    "custom webhook filters", "API rate limit dashboard", "team-level permissions",
    "automated report scheduling", "Slack integration for alerts",
    "multi-language support", "audit log export", "custom SLA policies",
    "sandbox environment", "IP allowlisting", "two-factor enforcement",
    "custom email templates", "real-time collaboration", "version history",
]

FEATURE_DESCRIPTIONS = [
    "export all our ticket data in bulk to CSV for our quarterly reports",
    "switch the dashboard to a dark theme - our team works late nights",
    "integrate with our Okta SSO provider for seamless authentication",
    "filter which events trigger our webhooks more granularly",
    "see a dashboard of our API usage and rate limit status in real time",
    "set permissions at the team level instead of per-user",
    "schedule reports to be generated and emailed automatically each week",
    "get alert notifications pushed directly to our Slack channels",
    "use the platform in Spanish and Portuguese for our LATAM team",
    "export our audit logs for compliance reporting",
    "define custom SLA policies per customer tier",
    "have a sandbox environment to test integrations safely",
    "restrict API access to specific IP ranges for security",
    "enforce two-factor authentication for all team members",
    "customize the email notification templates with our branding",
    "have multiple team members collaborate on a ticket in real time",
    "see the full version history of any configuration change",
]

BUSINESS_VALUES = [
    "save our ops team about 4 hours per week",
    "reduce eye strain for our support agents who work 12-hour shifts",
    "meet our enterprise security requirements for SOC 2 compliance",
    "reduce noise in our event pipeline by about 80%",
    "proactively manage our API usage instead of hitting limits unexpectedly",
    "onboard new departments much faster with template permissions",
    "eliminate the manual work of creating reports every Monday morning",
    "keep our engineering team informed about critical tickets instantly",
    "expand our support coverage to Latin American customers",
    "pass our upcoming security audit without manual data gathering",
]

# ---------------------------------------------------------------------------
# Crisis pools
# ---------------------------------------------------------------------------

CRISIS_TYPES = [
    "complete system outage", "data synchronization failure",
    "payment processing down", "authentication system broken",
    "critical data export corrupted", "production API completely unresponsive",
    "customer data integrity issue", "service degradation across all endpoints",
]

CRISIS_DESCRIPTIONS = [
    "Our entire integration with your platform has been down since this morning. "
    "No data is flowing and our operations team is manually processing everything.",
    "We discovered that the data sync between our systems has been silently failing "
    "for the past 48 hours. We have a significant data gap that needs to be addressed.",
    "Our customers' payments are failing because your payment webhook is returning errors. "
    "We are hemorrhaging revenue with every minute this continues.",
    "None of our 500+ team members can log in. Your authentication endpoint is returning "
    "invalid token errors for every request. Our entire company is at a standstill.",
    "The data export we ran last night contains corrupted records. We used this data "
    "to update our CRM and now have incorrect information for thousands of customers.",
    "Your API has been completely unresponsive for the past 2 hours. All of our automated "
    "workflows depend on this and our SLA commitments to our own customers are at risk.",
    "We've discovered discrepancies in customer records that suggest a data integrity issue "
    "on your end. Affected records span the last 30 days.",
    "Response times for all API endpoints have degraded to 10+ seconds, making our "
    "application essentially unusable for end users.",
]

IMPACT_STATEMENTS = [
    "This directly impacts our revenue - we estimate $50K/hour in losses.",
    "Our SLA commitments to our own enterprise customers are now at risk.",
    "We have a board meeting tomorrow and need this data for the presentation.",
    "Our compliance team has flagged this as a potential reportable incident.",
    "This is affecting all 2,000+ of our end users.",
    "We're in the middle of a product launch and this is threatening the timeline.",
    "Our investors are asking questions about system reliability.",
    "Multiple departments across our organization are impacted.",
]

THREAT_PHRASES = [
    "If this isn't resolved within the hour, we will need to invoke our contract's SLA penalty clause.",
    "Our legal team is on standby and we may need to involve them if this continues.",
    "We are prepared to escalate this to your CEO if necessary.",
    "I've already informed our board about this situation.",
    "We have a call scheduled with your competitor this afternoon.",
    "We'll be posting about this on social media if we don't get a resolution soon.",
    "Our CTO is asking for a formal incident report from your team.",
    "",
    "",
]

# ---------------------------------------------------------------------------
# Sentiment & urgency phrase pools
# ---------------------------------------------------------------------------

URGENCY_PHRASES = [
    "I need this resolved ASAP.",
    "This is extremely urgent.",
    "Please prioritize this - it's critical for our business.",
    "We're losing money every hour this isn't fixed.",
    "This needs immediate attention.",
    "Can someone look at this urgently?",
    "Time-sensitive issue - our deadline is tomorrow.",
    "Production is blocked until this is fixed.",
    "This cannot wait - we need a resolution today.",
    "",  # no urgency phrase
    "",
    "",
    "",  # weight toward no urgency
]

SENTIMENT_PHRASES_ANGRY = [
    "This is completely unacceptable.",
    "I'm furious about this situation.",
    "This is the worst service I've ever experienced.",
    "I'm seriously considering switching to a competitor.",
    "If this isn't resolved today, I'm cancelling my account.",
    "This is outrageous and unprofessional.",
    "I've never been so disappointed in a company.",
    "Your service has gone downhill dramatically.",
    "I'm appalled by the lack of response.",
    "This is inexcusable for the price we're paying.",
    "I'm fed up with these constant issues.",
    "I'm losing patience with your team.",
]

SENTIMENT_PHRASES_FRUSTRATED = [
    "This is really frustrating.",
    "I've been trying to get this resolved for days.",
    "I'm disappointed with how this has been handled.",
    "This keeps happening and it's getting old.",
    "I expected better from your company.",
    "Please help - I'm at my wit's end.",
    "Every time I contact support, nothing gets done.",
    "This is the third time I've reported this issue.",
    "I'm losing confidence in your platform.",
    "Still waiting for a meaningful response.",
]

SENTIMENT_PHRASES_NEUTRAL = [
    "Looking forward to your response.",
    "Please let me know how to proceed.",
    "Thanks for looking into this.",
    "Appreciate any help you can provide.",
    "Let me know if you need more information.",
    "Happy to provide additional details if needed.",
    "",
    "",
]

SENTIMENT_PHRASES_POSITIVE = [
    "I love your product otherwise, just need help with this.",
    "Your support team has been great in the past - hoping for the same here.",
    "Thanks in advance for your help!",
    "Appreciate the quick responses I usually get.",
    "Big fan of the platform, just hit a small snag.",
    "Your product has been instrumental for our growth - just need this one fix.",
]

ESCALATION_PHRASES = [
    "I'd like to speak with a manager.",
    "Please escalate this to your supervisor.",
    "I want this escalated to senior management.",
    "If this isn't resolved, I'll need to involve our legal team.",
    "I need to speak to someone with authority to fix this.",
    "Can I get transferred to a senior engineer?",
    "",
    "",
    "",
    "",  # often no escalation
]

ACTION_REQUESTS = [
    "Please send a replacement immediately.",
    "I'd like a full refund processed today.",
    "Can you overnight a correct replacement?",
    "Please arrange a pickup for the wrong item.",
    "I need a credit applied to my account.",
    "Please provide a tracking number for the replacement.",
    "I'd like store credit as compensation for the inconvenience.",
]

POLITE_PHRASES = [
    "Thanks for considering this!",
    "No rush on this - whenever you get a chance.",
    "Appreciate you taking the time to read this.",
    "Looking forward to hearing your thoughts.",
    "Cheers, and keep up the great work!",
    "Not critical, just wanted to plant the seed.",
    "Would love to discuss this further if there's interest.",
]

# ---------------------------------------------------------------------------
# Comment templates -- (role, template_text)
# ---------------------------------------------------------------------------

COMMENT_TEMPLATES_CUSTOMER_FOLLOWUP = [
    "It's been {hours} hours and no response. Is anyone working on this?",
    "Hello? I submitted this ticket {hours} hours ago. Still waiting.",
    "Just checking in on this. Any update?",
    "Can I get a status update please? This is still unresolved.",
    "Bumping this. We really need a resolution.",
    "Following up again. This is becoming a pattern.",
    "Is this ticket even assigned to someone? It feels like it's been forgotten.",
    "Still experiencing the issue. Any timeline for a fix?",
    "Our team is asking me for updates. What should I tell them?",
    "This is day {days} of waiting. Please advise.",
]

COMMENT_TEMPLATES_CUSTOMER_ESCALATING = [
    "I've been a customer for {years} years and this is the worst experience I've had.",
    "Still waiting. This is ridiculous.",
    "I'm going to have to escalate this if I don't hear back soon.",
    "Your competitor responded to my inquiry within 30 minutes. Just saying.",
    "I'm documenting all of this for our quarterly vendor review.",
    "At this point I'm considering switching providers entirely.",
    "The issue is getting worse. We're now seeing {additional_issue} as well.",
    "Is there a direct number I can call? This ticket system isn't working for me.",
    "I've looped in my manager because this is affecting our entire department.",
    "I need to speak with a supervisor. This level of support is unacceptable.",
]

COMMENT_TEMPLATES_CUSTOMER_NEUTRAL = [
    "Thanks for the update. Please keep me posted.",
    "Understood, I'll wait for the fix. Let me know if you need anything from my end.",
    "Okay, I appreciate you looking into it.",
    "Got it. I'll test on my end and report back.",
    "That makes sense. I'll try the workaround you suggested.",
    "Any update on this? No rush, just planning my week.",
]

COMMENT_TEMPLATES_AGENT = [
    "Thank you for your patience. We're looking into this and will update you shortly.",
    "I've escalated this to our technical team. Expected resolution within 24 hours.",
    "Hi {name}, I understand your frustration. Let me personally look into this.",
    "We've identified the root cause and our engineering team is working on a fix.",
    "I've applied a temporary workaround. Please let me know if the issue persists.",
    "Thank you for the additional details. This is very helpful for our investigation.",
    "I'm sorry for the delay. We've been experiencing higher than normal volume.",
    "Good news - we've deployed a fix. Could you please verify on your end?",
    "I've credited your account for the inconvenience. The refund should appear in 3-5 days.",
    "I've raised the priority on this ticket and assigned it to our senior team.",
]

ADDITIONAL_ISSUES = [
    "timeout errors on the dashboard",
    "data discrepancies in our reports",
    "failed email notifications",
    "broken file uploads",
    "incorrect calculations in the billing summary",
    "permission errors when accessing the admin panel",
    "missing data in the API responses",
    "duplicate entries showing up in search results",
]

# ---------------------------------------------------------------------------
# Ticket templates
# ---------------------------------------------------------------------------

# Each template is: (subject_template, description_template, tags, issue_category)
TICKET_TEMPLATES: list[tuple[str, str, list[str], str]] = [
    # --- Shipping issues ---
    (
        "Order {order_id} not delivered",
        "I placed order {order_id} {days_ago} days ago and it still hasn't arrived. "
        "Tracking shows no updates since {tracking_status}. {urgency_phrase} {sentiment_phrase}",
        ["shipping", "delivery"],
        "shipping",
    ),
    (
        "Where is my order {order_id}?",
        "Hi, I ordered {item_name} on {order_date} (order {order_id}) and was told it would "
        "arrive within 5 business days. It's now been {days_ago} days with no delivery and "
        "no tracking updates. {sentiment_phrase}",
        ["shipping", "delivery"],
        "shipping",
    ),
    (
        "Wrong item received in order {order_id}",
        "I received order {order_id} but the {item_name} is the wrong {wrong_attr}. "
        "I ordered {expected} but got {received}. {sentiment_phrase} {action_request}",
        ["shipping", "wrong_item"],
        "fulfillment",
    ),
    (
        "Damaged package - order {order_id}",
        "My order {order_id} arrived today but the {item_name} is damaged. "
        "{damage_description}. {sentiment_phrase} {action_request}",
        ["shipping", "damaged"],
        "fulfillment",
    ),
    (
        "Missing items from order {order_id}",
        "I received my order {order_id} but it's missing the {item_name}. The packing slip "
        "shows it should have been included. The box didn't appear to be tampered with. "
        "{sentiment_phrase} {action_request}",
        ["shipping", "missing_item"],
        "fulfillment",
    ),
    # --- Billing issues ---
    (
        "Billing discrepancy on invoice {invoice_id}",
        "I was charged ${charged_amount} on invoice {invoice_id} but the correct amount "
        "should be ${correct_amount}. That's a ${overcharge} overcharge. "
        "{billing_context} {sentiment_phrase}",
        ["billing", "overcharge"],
        "billing",
    ),
    (
        "Duplicate charge on my account",
        "I see two charges of ${amount} on {charge_date} for the same service. "
        "Transaction IDs: {txn_1} and {txn_2}. {sentiment_phrase} Please refund the duplicate.",
        ["billing", "duplicate_charge"],
        "billing",
    ),
    (
        "Subscription renewal price increased without notice",
        "My subscription renewed at ${new_price}/month but I was paying ${old_price}/month. "
        "I never received any notification about this price increase. {sentiment_phrase} "
        "{escalation_phrase}",
        ["billing", "subscription", "pricing"],
        "billing",
    ),
    (
        "Refund not received for cancelled service",
        "I cancelled my subscription on {cancel_date} and was promised a prorated refund of "
        "${refund_amount}. It's been {days_ago} days and I still haven't received it. "
        "Reference number: {ref_number}. {sentiment_phrase}",
        ["billing", "refund"],
        "billing",
    ),
    (
        "Unexpected charge of ${amount} on my credit card",
        "I just noticed an unexpected charge of ${amount} from your company on {charge_date}. "
        "I don't recall authorizing this. My account is under {customer_email}. "
        "{sentiment_phrase} Please investigate immediately.",
        ["billing", "unauthorized_charge"],
        "billing",
    ),
    # --- Technical / API issues ---
    (
        "API returning {error_code} errors since {since_when}",
        "Our integration with your API endpoint {endpoint} has been returning {error_code} errors "
        "since {since_when}. This is affecting {impact}. "
        "{technical_detail} {urgency_phrase} {sentiment_phrase}",
        ["api", "integration", "technical"],
        "technical",
    ),
    (
        "Webhook deliveries failing",
        "We've noticed that webhook deliveries to our endpoint {endpoint} have been "
        "failing since {since_when}. Our server logs show {log_detail}. "
        "This is {impact}. {urgency_phrase}",
        ["api", "webhook", "integration"],
        "technical",
    ),
    (
        "Dashboard loading extremely slowly",
        "The admin dashboard has been loading very slowly for the past {duration}. "
        "Page load times are averaging {load_time} seconds. {team_impact} {sentiment_phrase}",
        ["performance", "dashboard"],
        "technical",
    ),
    (
        "Data sync between systems is broken",
        "The data synchronization between your platform and our {integration_name} "
        "integration stopped working {since_when}. Records created after that point "
        "are not appearing in our system. {urgency_phrase} {sentiment_phrase}",
        ["integration", "data_sync", "technical"],
        "technical",
    ),
    (
        "Search functionality returning incorrect results",
        "When searching for {search_term} in the admin panel, the results include "
        "completely unrelated records. This started {since_when} and is making it "
        "impossible to find what we need. {sentiment_phrase}",
        ["bug", "search", "technical"],
        "technical",
    ),
    (
        "File upload failing with timeout error",
        "We're unable to upload files larger than {file_size}MB. The upload starts "
        "but times out after about 60 seconds. We've tested with multiple file types "
        "and browsers. {technical_detail} {sentiment_phrase}",
        ["bug", "upload", "technical"],
        "technical",
    ),
    # --- Account issues ---
    (
        "Cannot login after password reset",
        "I reset my password {when} but I still can't log in. "
        "I've tried {attempts} times and cleared my browser cache. "
        "{error_message} {sentiment_phrase}",
        ["authentication", "login"],
        "account",
    ),
    (
        "Need to add team members urgently",
        "We need to add {num_users} new team members to our {plan_name} plan {urgency_reason}. "
        "{context} {action_request}",
        ["account", "users", "licensing"],
        "account",
    ),
    (
        "Account locked out - no explanation given",
        "My account ({customer_email}) has been locked with no warning or explanation. "
        "I've been a paying customer for {years} years. I have no idea what triggered this. "
        "{urgency_phrase} {sentiment_phrase}",
        ["account", "locked", "authentication"],
        "account",
    ),
    (
        "Need to transfer account ownership",
        "Our previous account admin {old_admin} has left the company. We need to transfer "
        "ownership to {new_admin} ({new_admin_email}). We've already verified this with "
        "your security team via ticket {ref_ticket}. {sentiment_phrase}",
        ["account", "ownership", "admin"],
        "account",
    ),
    # --- Feature requests ---
    (
        "Feature request: {feature_name}",
        "It would be great if we could {feature_description}. "
        "This would help us {business_value}. {polite_phrase}",
        ["feature_request"],
        "feature",
    ),
    (
        "Suggestion for improving {feature_name}",
        "We've been using {feature_name} extensively and have some feedback. "
        "It would be much more useful if we could {feature_description}. "
        "Our team of {num_users} people would really benefit from this. {polite_phrase}",
        ["feature_request", "feedback"],
        "feature",
    ),
    # --- Crisis / Escalation ---
    (
        "URGENT: {crisis_type} affecting production",
        "{crisis_description} This has been ongoing for {duration} and is affecting "
        "{affected_count} {affected_unit}. {impact_statement} "
        "I need this escalated immediately. {threat_phrase}",
        ["urgent", "escalation", "production"],
        "crisis",
    ),
    (
        "CRITICAL: Complete {crisis_type} - immediate help needed",
        "{crisis_description}\n\nImpact:\n- Duration: {duration}\n- Affected: "
        "{affected_count} {affected_unit}\n- Business impact: {impact_statement}\n\n"
        "This is a P0 incident for us. {threat_phrase} {urgency_phrase}",
        ["urgent", "critical", "escalation", "production"],
        "crisis",
    ),
    (
        "Dissatisfied with support response - ticket {ref_ticket}",
        "I opened ticket {ref_ticket} {days_ago} days ago about {original_issue} and "
        "the response was {response_quality}. {complaint_detail} "
        "{escalation_phrase} {sentiment_phrase}",
        ["escalation", "complaint"],
        "escalation",
    ),
    (
        "Repeated issue - 3rd time reporting {original_issue}",
        "This is the THIRD time I'm reporting {original_issue}. Previous tickets: "
        "{ref_ticket} and {ref_ticket_2}. Each time I was told it was fixed, but the "
        "problem keeps coming back. {sentiment_phrase} {escalation_phrase}",
        ["escalation", "recurring"],
        "escalation",
    ),
    # --- General / How-to ---
    (
        "How to configure {feature_name}?",
        "Hi, I'm trying to set up {feature_name} for our team but I'm not sure about "
        "the correct configuration. The documentation mentions {doc_reference} but I'm "
        "confused about {confusion_point}. Any guidance would be appreciated. {polite_phrase}",
        ["how_to", "configuration"],
        "general",
    ),
    (
        "Question about {plan_name} plan limits",
        "We're on the {plan_name} plan and I have a question about the limits. "
        "Specifically, does {limit_question}? We're planning our usage for next quarter "
        "and need to know if we should upgrade. {polite_phrase}",
        ["question", "account", "pricing"],
        "general",
    ),
]

# ---------------------------------------------------------------------------
# Helper data for template filling
# ---------------------------------------------------------------------------

TRACKING_STATUSES = [
    "last Monday", "'in transit' three days ago", "the regional hub on Tuesday",
    "picked up by carrier", "'label created' five days ago",
    "a warehouse in Ohio", "'out for delivery' but never arrived",
]

INTEGRATION_NAMES = [
    "Salesforce", "HubSpot", "Jira", "Slack", "Microsoft Teams",
    "Shopify", "Stripe", "QuickBooks", "ServiceNow", "Datadog",
]

SEARCH_TERMS = [
    "active subscriptions", "pending invoices", "open tickets",
    "user accounts", "recent transactions", "error logs",
]

PLAN_NAMES = ["Starter", "Professional", "Business", "Enterprise", "Growth"]

ERROR_MESSAGES = [
    "The error says 'Invalid credentials' even though I just set the password.",
    "I get redirected back to the login page without any error message.",
    "It shows 'Account not found' which makes no sense.",
    "The page just spins forever and eventually times out.",
    "I get a '403 Forbidden' error when trying to access the dashboard.",
]

URGENCY_REASONS = [
    "because we're onboarding a new client next week",
    "for a project that starts Monday",
    "due to a sudden team expansion",
    "because our current licenses are maxed out",
    "for our new office that opens this Friday",
]

CONTEXTS = [
    "We've already pre-purchased additional seats in our contract renewal.",
    "Our account manager said this should be a simple change.",
    "We need admin access for at least 3 of the new users.",
    "This was supposed to be handled during our contract renewal last month.",
]

RESPONSE_QUALITIES = [
    "a generic copy-paste that didn't address my specific situation at all",
    "completely unhelpful - the agent clearly didn't read my message",
    "technically incorrect - the suggested solution doesn't even apply to my version",
    "dismissive and lacking any empathy for the business impact",
    "slow and incomplete - I had to explain the same thing three times",
]

COMPLAINT_DETAILS = [
    "I spent 45 minutes on chat only to be told to 'try clearing my cache.'",
    "The agent closed the ticket without resolving anything.",
    "I was promised a callback within 2 hours. That was 3 days ago.",
    "Each agent I talk to asks me to repeat the entire issue from scratch.",
    "The resolution provided actually made the problem worse.",
]

ORIGINAL_ISSUES = [
    "a billing discrepancy", "an API integration failure", "a data sync problem",
    "a login issue", "incorrect search results", "webhook delivery failures",
    "dashboard performance", "missing order items", "subscription pricing",
]

DOC_REFERENCES = [
    "custom webhook configurations", "SSO integration settings",
    "API rate limit policies", "team permission hierarchies",
    "data retention policies", "export scheduling options",
]

CONFUSION_POINTS = [
    "the difference between the v1 and v2 settings",
    "which authentication method to use for our setup",
    "how the inheritance model works for nested teams",
    "whether we need a separate API key for each environment",
    "the interaction between global and team-level settings",
]

LIMIT_QUESTIONS = [
    "the 10,000 API calls/month include internal dashboard requests",
    "archived tickets count toward our storage limit",
    "there's a way to get temporary burst capacity for high-traffic events",
    "the user seat count is per-team or per-organization",
    "we can roll over unused API calls to the next month",
]

DURATIONS = [
    "2 hours", "4 hours", "6 hours", "12 hours", "since this morning",
    "since yesterday", "the past 3 hours", "over 8 hours",
]

TEAM_IMPACTS = [
    "Our entire 20-person support team is affected and falling behind on their queues.",
    "This is slowing down our analysts who rely on the dashboard for real-time data.",
    "We've had to switch to manual processes which is costing us hours of productivity.",
    "Three departments are unable to access the reports they need for their daily standups.",
    "Our operations team can't do their job effectively until this is resolved.",
]

AFFECTED_UNITS = [
    "customers", "users", "team members", "end users",
    "transactions", "API calls", "client accounts",
]


# ---------------------------------------------------------------------------
# Generator logic
# ---------------------------------------------------------------------------


def _pick(rng: random.Random, pool: list) -> Any:
    """Pick a random item from a pool."""
    return rng.choice(pool)


def _pick_weighted(rng: random.Random, items: list, weights: list[float]) -> Any:
    """Pick from items with given weights."""
    return rng.choices(items, weights=weights, k=1)[0]


def _generate_order_id(rng: random.Random) -> str:
    return f"ORD-{rng.randint(1000, 99999)}"


def _generate_invoice_id(rng: random.Random) -> str:
    return f"INV-{rng.randint(1000, 9999)}"


def _generate_txn_id(rng: random.Random) -> str:
    return f"TXN-{rng.randint(10000, 99999)}"


def _generate_ref_ticket(rng: random.Random) -> str:
    return f"TKT-{rng.randint(10000, 19999)}"


def _generate_email(rng: random.Random, name: str) -> str:
    """Generate a plausible email from a customer name."""
    first = name.split()[0].lower()
    domain = _pick(rng, COMPANY_DOMAINS)
    return f"{first}@{domain}"


def _pick_sentiment_phrase(rng: random.Random, sentiment_bias: str) -> str:
    """Pick a sentiment phrase based on a bias category."""
    if sentiment_bias == "angry":
        return _pick(rng, SENTIMENT_PHRASES_ANGRY)
    elif sentiment_bias == "frustrated":
        return _pick(rng, SENTIMENT_PHRASES_FRUSTRATED)
    elif sentiment_bias == "positive":
        return _pick(rng, SENTIMENT_PHRASES_POSITIVE)
    else:
        return _pick(rng, SENTIMENT_PHRASES_NEUTRAL)


def _fill_template_vars(
    rng: random.Random,
    template: str,
    customer_name: str,
    customer_email: str,
    sentiment_bias: str,
) -> str:
    """Fill template placeholders with realistic random values."""
    # Build a substitution dict with every possible placeholder
    order_id = _generate_order_id(rng)
    invoice_id = _generate_invoice_id(rng)
    charged_amount = rng.randint(100, 2000)
    overcharge = rng.randint(20, 500)
    correct_amount = charged_amount - overcharge
    amount = rng.choice([29, 49, 79, 99, 149, 199, 299, 499])
    old_price = amount
    new_price = int(amount * rng.uniform(1.15, 1.60))
    refund_amount = rng.randint(30, 500)
    days_ago = rng.randint(2, 14)
    hours = rng.randint(4, 72)
    years = rng.randint(1, 7)
    num_users = rng.randint(3, 25)
    affected_count = rng.choice([50, 100, 200, 500, 1000, 2000, 5000])
    file_size = rng.choice([10, 25, 50, 100])

    feature_idx = rng.randint(0, min(len(FEATURE_NAMES), len(FEATURE_DESCRIPTIONS), len(BUSINESS_VALUES)) - 1)

    now = datetime.now(timezone.utc)
    order_date = (now - timedelta(days=days_ago)).strftime("%B %d")
    charge_date = (now - timedelta(days=rng.randint(1, 10))).strftime("%B %d")
    cancel_date = (now - timedelta(days=days_ago)).strftime("%B %d")
    since_when_dt = now - timedelta(hours=rng.randint(2, 48))
    since_when = since_when_dt.strftime("%A at %I:%M %p")

    subs: dict[str, str] = {
        "order_id": order_id,
        "invoice_id": invoice_id,
        "item_name": _pick(rng, ITEM_NAMES),
        "wrong_attr": _pick(rng, WRONG_ATTRS),
        "expected": f"the {_pick(rng, ['black', 'white', 'silver', 'blue', 'large', 'medium', 'v2', 'pro'])} one",
        "received": f"a {_pick(rng, ['red', 'green', 'small', 'v1', 'basic', 'refurbished'])} one",
        "damage_description": _pick(rng, DAMAGE_DESCRIPTIONS),
        "days_ago": str(days_ago),
        "hours": str(hours),
        "years": str(years),
        "tracking_status": _pick(rng, TRACKING_STATUSES),
        "order_date": order_date,
        "charged_amount": str(charged_amount),
        "correct_amount": str(correct_amount),
        "overcharge": str(overcharge),
        "amount": str(amount),
        "new_price": str(new_price),
        "old_price": str(old_price),
        "refund_amount": str(refund_amount),
        "charge_date": charge_date,
        "cancel_date": cancel_date,
        "txn_1": _generate_txn_id(rng),
        "txn_2": _generate_txn_id(rng),
        "ref_number": f"REF-{rng.randint(100000, 999999)}",
        "billing_context": _pick(rng, BILLING_CONTEXTS),
        "error_code": _pick(rng, ERROR_CODES),
        "endpoint": _pick(rng, API_ENDPOINTS),
        "since_when": since_when,
        "impact": _pick(rng, [
            "our entire data pipeline",
            f"approximately {affected_count} customer transactions per hour",
            "all automated workflows in our production environment",
            "our ability to process new orders",
            "our real-time dashboards and alerting systems",
            "our customer-facing application",
        ]),
        "technical_detail": _pick(rng, TECHNICAL_DETAILS),
        "log_detail": _pick(rng, LOG_DETAILS),
        "load_time": _pick(rng, LOAD_TIMES),
        "duration": _pick(rng, DURATIONS),
        "team_impact": _pick(rng, TEAM_IMPACTS),
        "integration_name": _pick(rng, INTEGRATION_NAMES),
        "search_term": _pick(rng, SEARCH_TERMS),
        "file_size": str(file_size),
        "when": _pick(rng, ["yesterday", "two days ago", "this morning", "30 minutes ago", "an hour ago"]),
        "attempts": str(rng.randint(3, 15)),
        "error_message": _pick(rng, ERROR_MESSAGES),
        "num_users": str(num_users),
        "plan_name": _pick(rng, PLAN_NAMES),
        "urgency_reason": _pick(rng, URGENCY_REASONS),
        "context": _pick(rng, CONTEXTS),
        "customer_email": customer_email,
        "old_admin": _pick(rng, CUSTOMER_NAMES),
        "new_admin": _pick(rng, CUSTOMER_NAMES),
        "new_admin_email": _generate_email(rng, _pick(rng, CUSTOMER_NAMES)),
        "ref_ticket": _generate_ref_ticket(rng),
        "ref_ticket_2": _generate_ref_ticket(rng),
        "feature_name": FEATURE_NAMES[feature_idx],
        "feature_description": FEATURE_DESCRIPTIONS[feature_idx],
        "business_value": BUSINESS_VALUES[min(feature_idx, len(BUSINESS_VALUES) - 1)],
        "urgency_phrase": _pick(rng, URGENCY_PHRASES),
        "sentiment_phrase": _pick_sentiment_phrase(rng, sentiment_bias),
        "escalation_phrase": _pick(rng, ESCALATION_PHRASES),
        "action_request": _pick(rng, ACTION_REQUESTS),
        "polite_phrase": _pick(rng, POLITE_PHRASES),
        "crisis_type": _pick(rng, CRISIS_TYPES),
        "crisis_description": _pick(rng, CRISIS_DESCRIPTIONS),
        "affected_count": str(affected_count),
        "affected_unit": _pick(rng, AFFECTED_UNITS),
        "impact_statement": _pick(rng, IMPACT_STATEMENTS),
        "threat_phrase": _pick(rng, THREAT_PHRASES),
        "original_issue": _pick(rng, ORIGINAL_ISSUES),
        "response_quality": _pick(rng, RESPONSE_QUALITIES),
        "complaint_detail": _pick(rng, COMPLAINT_DETAILS),
        "doc_reference": _pick(rng, DOC_REFERENCES),
        "confusion_point": _pick(rng, CONFUSION_POINTS),
        "limit_question": _pick(rng, LIMIT_QUESTIONS),
        "additional_issue": _pick(rng, ADDITIONAL_ISSUES),
        "name": customer_name.split()[0],
    }

    result = template
    for key, value in subs.items():
        result = result.replace("{" + key + "}", value)

    return result


def _generate_comments(
    rng: random.Random,
    customer_name: str,
    customer_id: str,
    sentiment_bias: str,
    num_comments: int,
    created_at: datetime,
) -> list[dict]:
    """Generate a thread of comments with a realistic sentiment trajectory."""
    comments: list[dict] = []
    current_time = created_at + timedelta(hours=rng.randint(1, 6))

    # Determine sentiment trajectory
    if sentiment_bias == "angry":
        trajectory = ["frustrated", "frustrated", "angry", "angry", "angry", "angry"]
    elif sentiment_bias == "frustrated":
        trajectory = ["neutral", "frustrated", "frustrated", "angry", "frustrated", "angry"]
    elif sentiment_bias == "positive":
        trajectory = ["positive", "neutral", "positive", "neutral", "positive", "positive"]
    else:
        trajectory = ["neutral", "neutral", "frustrated", "neutral", "frustrated", "neutral"]

    for i in range(num_comments):
        phase_sentiment = trajectory[min(i, len(trajectory) - 1)]

        # Alternate between agent and customer, starting with agent response
        if i % 2 == 0:
            # Agent response
            author = _pick(rng, AGENT_NAMES)
            template = _pick(rng, COMMENT_TEMPLATES_AGENT)
            body = template.replace("{name}", customer_name.split()[0])
        else:
            # Customer response
            author = customer_id
            if phase_sentiment in ("angry", "frustrated"):
                if rng.random() < 0.6:
                    template = _pick(rng, COMMENT_TEMPLATES_CUSTOMER_ESCALATING)
                else:
                    template = _pick(rng, COMMENT_TEMPLATES_CUSTOMER_FOLLOWUP)
            elif phase_sentiment == "positive":
                template = _pick(rng, COMMENT_TEMPLATES_CUSTOMER_NEUTRAL)
            else:
                if rng.random() < 0.4:
                    template = _pick(rng, COMMENT_TEMPLATES_CUSTOMER_FOLLOWUP)
                else:
                    template = _pick(rng, COMMENT_TEMPLATES_CUSTOMER_NEUTRAL)

            # Fill mini-template variables
            body = template.replace("{hours}", str(rng.randint(4, 72)))
            body = body.replace("{days}", str(rng.randint(2, 7)))
            body = body.replace("{years}", str(rng.randint(1, 7)))
            body = body.replace("{additional_issue}", _pick(rng, ADDITIONAL_ISSUES))

        comments.append({
            "id": f"comment_{uuid.uuid4().hex[:8]}",
            "author": author,
            "body": body,
            "created_at": current_time.isoformat(),
            "public": True,
        })

        # Advance time - agents respond in hours, customers follow up after longer
        if i % 2 == 0:
            current_time += timedelta(hours=rng.randint(1, 4))
        else:
            current_time += timedelta(hours=rng.randint(2, 24))

    return comments


def generate_ticket(
    ticket_num: int,
    rng: random.Random,
    *,
    sla_offset_minutes: int = 120,
    force_tier: str | None = None,
    force_priority: str | None = None,
    force_category: str | None = None,
) -> dict:
    """Generate a single realistic ticket.

    Args:
        ticket_num: Sequence number for deterministic ticket ID.
        rng: Random instance for reproducibility.
        sla_offset_minutes: Minutes from now to set the SLA deadline.
        force_tier: Override the customer tier.
        force_priority: Override the ticket priority.
        force_category: If set, only pick templates matching this category.
    """
    now = datetime.now(timezone.utc)
    ticket_id = f"TKT-{30000 + ticket_num}"

    # Pick customer details
    customer_name = _pick(rng, CUSTOMER_NAMES)
    customer_email = _generate_email(rng, customer_name)
    customer_id = f"cust_{300 + ticket_num}"
    customer_tier = force_tier or _pick_weighted(rng, CUSTOMER_TIERS, TIER_WEIGHTS)

    # Pick priority
    priority = force_priority or _pick_weighted(rng, PRIORITIES, PRIORITY_WEIGHTS)

    # Pick status
    status = _pick_weighted(rng, STATUSES, STATUS_WEIGHTS)

    # Pick template (filter by category if forced)
    available = TICKET_TEMPLATES
    if force_category:
        filtered = [t for t in available if t[3] == force_category]
        if filtered:
            available = filtered
    template = _pick(rng, available)

    subject_template, description_template, tags, category = template

    # Determine sentiment bias based on priority and category
    if category in ("crisis", "escalation"):
        sentiment_bias = rng.choice(["angry", "angry", "frustrated"])
    elif priority == "urgent":
        sentiment_bias = rng.choice(["angry", "frustrated", "frustrated"])
    elif priority == "high":
        sentiment_bias = rng.choice(["frustrated", "frustrated", "neutral"])
    elif category == "feature":
        sentiment_bias = rng.choice(["positive", "neutral", "neutral"])
    elif category == "general":
        sentiment_bias = rng.choice(["neutral", "neutral", "positive"])
    else:
        sentiment_bias = rng.choice(["neutral", "frustrated", "neutral", "positive"])

    # Fill templates
    subject = _fill_template_vars(rng, subject_template, customer_name, customer_email, sentiment_bias)
    description = _fill_template_vars(rng, description_template, customer_name, customer_email, sentiment_bias)

    # For crisis/urgent, boost with extra urgency keywords
    if category == "crisis" and rng.random() < 0.7:
        extra = rng.choice([
            " This is a production down situation with revenue impact.",
            " Our entire pipeline is blocked and we're losing money.",
            " This is a critical outage affecting our production systems.",
            " Immediate attention required - compliance deadline approaching.",
        ])
        description += extra

    # Generate created_at (a few days ago)
    created_ago_hours = rng.randint(2, 120)
    created_at = now - timedelta(hours=created_ago_hours)
    updated_at = created_at + timedelta(hours=rng.randint(1, min(created_ago_hours, 48)))

    # SLA deadline
    sla_jitter = rng.randint(-30, 30)
    sla_deadline = now + timedelta(minutes=sla_offset_minutes + sla_jitter)

    # Assignee (some tickets unassigned)
    assignee = _pick(rng, AGENT_NAMES) if rng.random() < 0.75 else None

    # Account created date (1-5 years ago)
    account_created = now - timedelta(days=rng.randint(180, 1800))

    # Generate comments (0 to 6)
    if category in ("crisis", "escalation"):
        num_comments = rng.randint(2, 6)
    elif priority in ("urgent", "high"):
        num_comments = rng.randint(1, 5)
    elif category == "feature":
        num_comments = rng.randint(0, 1)
    else:
        num_comments = rng.randint(0, 4)

    comments = _generate_comments(
        rng, customer_name, customer_id, sentiment_bias, num_comments, created_at
    )

    # Custom fields based on category
    custom_fields: dict[str, Any] = {}
    if category in ("shipping", "fulfillment"):
        custom_fields["order_id"] = _generate_order_id(rng)
    elif category == "billing":
        custom_fields["invoice_id"] = _generate_invoice_id(rng)
    elif category == "technical":
        custom_fields["integration_id"] = f"int_{rng.randint(100, 999)}"

    # Add extra tags for enterprise/premium
    extra_tags = list(tags)
    if customer_tier == "enterprise":
        extra_tags.append("enterprise")
    if customer_tier == "premium":
        extra_tags.append("premium")
    if category in ("crisis", "escalation"):
        extra_tags.append("escalation")
    if priority == "urgent":
        if "urgent" not in extra_tags:
            extra_tags.append("urgent")

    ticket: dict[str, Any] = {
        "id": ticket_id,
        "subject": subject,
        "description": description,
        "status": status,
        "priority": priority,
        "requester": {
            "id": customer_id,
            "name": customer_name,
            "email": customer_email,
            "tier": customer_tier,
            "account_created": account_created.isoformat(),
        },
        "tags": extra_tags,
        "created_at": created_at.isoformat(),
        "updated_at": updated_at.isoformat(),
        "sla_deadline": sla_deadline.isoformat(),
        "custom_fields": custom_fields,
        "comments": comments,
    }

    if assignee:
        ticket["assignee"] = assignee

    return ticket


def generate_ticket_batch(
    count: int = 100,
    *,
    seed: int = 42,
    sla_offset_minutes: int = 120,
    tier_distribution: dict[str, float] | None = None,
    crisis_percentage: float = 0.05,
) -> list[dict]:
    """Generate a batch of realistic tickets with deterministic randomness.

    Args:
        count: Number of tickets to generate.
        seed: Random seed for reproducibility across processes.
        sla_offset_minutes: Base minutes until SLA deadlines.
        tier_distribution: Override default tier weights (not yet used, reserved).
        crisis_percentage: Fraction of tickets forced to crisis category.

    Returns:
        List of ticket dicts matching the sample_open_tickets.json format.
    """
    rng = random.Random(seed)
    tickets: list[dict] = []

    for i in range(count):
        # Force some crisis tickets
        force_cat: str | None = None
        if rng.random() < crisis_percentage:
            force_cat = "crisis"

        ticket = generate_ticket(
            i + 1,
            rng,
            sla_offset_minutes=sla_offset_minutes,
            force_category=force_cat,
        )
        tickets.append(ticket)

    return tickets


def get_ticket_ids(count: int = 100, seed: int = 42) -> list[str]:
    """Return deterministic ticket IDs without generating full tickets.

    Useful for the demo controller to know ticket IDs without
    generating the full ticket data.
    """
    return [f"TKT-{30000 + i + 1}" for i in range(count)]
