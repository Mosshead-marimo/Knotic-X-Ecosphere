"""Closed version-1 domain vocabularies."""

from enum import StrEnum


class SessionStatus(StrEnum):
    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    ENDING = "ENDING"
    ENDED = "ENDED"
    FAILED = "FAILED"


class BuyingStage(StrEnum):
    NURTURE = "NURTURE"
    DISCOVERY = "DISCOVERY"
    QUALIFIED = "QUALIFIED"
    FOLLOWUP = "FOLLOWUP"
    HANDOFF = "HANDOFF"


class NextBestAction(StrEnum):
    ASK_DISCOVERY = "ASK_DISCOVERY"
    ANSWER_QUESTION = "ANSWER_QUESTION"
    HANDLE_OBJECTION = "HANDLE_OBJECTION"
    OFFER_DEMO = "OFFER_DEMO"
    SCHEDULE_FOLLOWUP = "SCHEDULE_FOLLOWUP"
    ESCALATE_HUMAN = "ESCALATE_HUMAN"
    END_SESSION = "END_SESSION"


class RequirementField(StrEnum):
    USERS = "users"
    USE_CASES = "use_cases"
    INTEGRATIONS = "integrations"
    BUDGET = "budget"
    TIMELINE = "timeline"
    DEPLOYMENT = "deployment"
    SECURITY = "security"


class MemoryField(StrEnum):
    CUSTOMER_NAME = "customer_name"
    COMPANY = "company"
    ROLE = "role"
    USERS = "users"
    USE_CASES = "use_cases"
    INTEGRATIONS = "integrations"
    BUDGET = "budget"
    TIMELINE = "timeline"
    COMPETITORS = "competitors"
    CURRENT_TOPIC = "current_topic"
    NEXT_ACTION = "next_action"


class RequirementUpdateSource(StrEnum):
    CUSTOMER_CONFIRMATION = "CUSTOMER_CONFIRMATION"
    HUMAN_CORRECTION = "HUMAN_CORRECTION"
    WORKFLOW_CONFIRMATION = "WORKFLOW_CONFIRMATION"


class ObjectionCategory(StrEnum):
    PRICE = "PRICE"
    TIMING = "TIMING"
    SECURITY = "SECURITY"
    INTEGRATION = "INTEGRATION"
    COMPETITOR = "COMPETITOR"
    AUTHORITY = "AUTHORITY"
    OTHER = "OTHER"


class ObjectionStatus(StrEnum):
    OPEN = "OPEN"
    ADDRESSED = "ADDRESSED"
    RESOLVED = "RESOLVED"


class Speaker(StrEnum):
    CUSTOMER = "CUSTOMER"
    ASSISTANT = "ASSISTANT"
    HUMAN_AGENT = "HUMAN_AGENT"
    SYSTEM = "SYSTEM"


class MessageSource(StrEnum):
    TEXT = "TEXT"
    VOICE = "VOICE"
    TOOL = "TOOL"
    SYSTEM = "SYSTEM"


class ToolCallStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_PERMANENT = "FAILED_PERMANENT"
    CANCELLED = "CANCELLED"


class OutcomeType(StrEnum):
    ENTERPRISE_DEMO_BOOKED = "ENTERPRISE_DEMO_BOOKED"
    LEAD_QUALIFIED = "LEAD_QUALIFIED"
    FOLLOWUP_CREATED = "FOLLOWUP_CREATED"
    HUMAN_ESCALATED = "HUMAN_ESCALATED"
    NURTURE = "NURTURE"
    CLOSED_NO_ACTION = "CLOSED_NO_ACTION"


class EventType(StrEnum):
    SESSION_CREATED = "session.created"
    TURN_ACCEPTED = "turn.accepted"
    TURN_COMPLETED = "turn.completed"
    RESPONSE_INTERRUPTED = "response.interrupted"
    REQUIREMENT_UPDATED = "requirement.updated"
    QUALIFICATION_UPDATED = "qualification.updated"
    OPERATION_UPDATED = "operation.updated"
    SESSION_ENDED = "session.ended"
