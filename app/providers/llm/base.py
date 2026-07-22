from typing import Protocol

from app.schemas.assistant import AssistantRequest
from app.schemas.intents import ReminderIntentExtraction


class LLMProvider(Protocol):
    def extract_reminder_intent(
        self,
        *,
        request: AssistantRequest,
    ) -> ReminderIntentExtraction:
        """Extract structured reminder intent data from a normalized assistant request."""
