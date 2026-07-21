import re

from app.schemas.assistant import AssistantRequest
from app.schemas.intents import IntentName, IntentResult


class IntentRouter:
    """Rule-based router used until an LLMProvider is introduced behind an interface."""

    def route(self, request: AssistantRequest) -> IntentResult:
        text = (request.text or "").strip()
        normalized = text.lower()

        if self._looks_like_list_reminders(normalized):
            return IntentResult(intent=IntentName.LIST_REMINDERS, confidence=0.9)

        if self._looks_like_mark_done(normalized):
            return IntentResult(
                intent=IntentName.MARK_REMINDER_DONE,
                entities=self._extract_mark_done_entities(text),
                confidence=0.78,
            )

        if self._looks_like_create_reminder(normalized):
            return IntentResult(
                intent=IntentName.CREATE_REMINDER,
                entities={"title": self._extract_reminder_title(text)},
                confidence=0.82,
            )

        return IntentResult(intent=IntentName.UNKNOWN, confidence=0.2)

    @staticmethod
    def _looks_like_create_reminder(normalized: str) -> bool:
        triggers = ("recordame", "recuérdame", "crear recordatorio", "agendame", "reminder")
        return any(trigger in normalized for trigger in triggers)

    @staticmethod
    def _looks_like_list_reminders(normalized: str) -> bool:
        list_words = ("listar", "lista", "ver", "mostrar", "qué tengo", "que tengo")
        reminder_words = ("recordatorio", "recordatorios", "pendiente", "pendientes")
        return any(word in normalized for word in list_words) and any(
            word in normalized for word in reminder_words
        )

    @staticmethod
    def _looks_like_mark_done(normalized: str) -> bool:
        done_words = ("hecho", "listo", "completado", "terminado", "marcar")
        reminder_words = ("recordatorio", "recordame", "tarea", "pendiente")
        return any(word in normalized for word in done_words) and any(
            word in normalized for word in reminder_words
        )

    @staticmethod
    def _extract_reminder_title(text: str) -> str:
        title = text.strip()
        patterns = [
            r"(?i)^recordame\s+(que\s+)?",
            r"(?i)^recuérdame\s+(que\s+)?",
            r"(?i)^crear\s+recordatorio\s+(para\s+)?",
            r"(?i)^agendame\s+(que\s+)?",
            r"(?i)^reminder\s+",
        ]
        for pattern in patterns:
            title = re.sub(pattern, "", title).strip()
        return title

    @staticmethod
    def _extract_mark_done_entities(text: str) -> dict[str, str]:
        id_match = re.search(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            text,
            flags=re.IGNORECASE,
        )
        if id_match:
            return {"reminder_id": id_match.group(0)}

        title_query = re.sub(
            r"(?i)(marcar|como|hecho|listo|completado|terminado|recordatorio|tarea|pendiente)",
            "",
            text,
        ).strip()
        return {"title_query": title_query}
