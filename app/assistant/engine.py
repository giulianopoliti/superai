from datetime import datetime
from zoneinfo import ZoneInfo

from app.assistant.intent_router import IntentRouter
from app.db.repositories.conversations import ConversationMessageRepository
from app.modules.reminders.service import ReminderService
from app.schemas.assistant import AssistantAction, AssistantRequest, AssistantResponse
from app.schemas.intents import IntentName

ASSISTANT_TIMEZONE = ZoneInfo("America/Buenos_Aires")


class AssistantEngine:
    def __init__(
        self,
        *,
        intent_router: IntentRouter,
        reminder_service: ReminderService,
        conversation_repository: ConversationMessageRepository,
    ) -> None:
        self._intent_router = intent_router
        self._reminder_service = reminder_service
        self._conversation_repository = conversation_repository

    def handle_message(self, request: AssistantRequest) -> AssistantResponse:
        self._conversation_repository.save_inbound(request)
        intent_result = self._intent_router.route(request)

        if intent_result.requires_clarification:
            response = AssistantResponse(
                reply=intent_result.clarification_question
                or "Me falta un dato para guardar eso. Me lo decis?",
                requires_confirmation=True,
                confirmation_payload={"intent": intent_result.intent},
                metadata={"intent": intent_result.model_dump(mode="json")},
            )
            self._conversation_repository.save_outbound(request, response)
            return response

        if intent_result.intent == IntentName.CREATE_REMINDER:
            response = self._create_reminder(request, intent_result.entities)
        elif intent_result.intent == IntentName.LIST_REMINDERS:
            response = self._list_reminders(request)
        elif intent_result.intent == IntentName.MARK_REMINDER_DONE:
            response = self._mark_reminder_done(request, intent_result.entities)
        else:
            response = AssistantResponse(
                reply="No estoy seguro de como ayudarte con eso todavia. "
                "Por ahora puedo crear, listar y completar recordatorios.",
                metadata={"intent": intent_result.model_dump(mode="json")},
            )

        self._conversation_repository.save_outbound(request, response)
        return response

    def _create_reminder(
        self, request: AssistantRequest, entities: dict[str, object]
    ) -> AssistantResponse:
        title = str(entities.get("title") or "").strip()
        if not title:
            return AssistantResponse(
                reply="Que queres que te recuerde?",
                requires_confirmation=True,
                confirmation_payload={"intent": IntentName.CREATE_REMINDER},
            )

        reminder = self._reminder_service.create_reminder(
            business_id=request.business_id,
            created_by_external_user_id=request.external_user_id,
            title=title,
            due_at=self._parse_due_at(entities.get("due_at")),
        )
        due_text = self._format_due_text(reminder.due_at)
        return AssistantResponse(
            reply=f"Listo, guarde el recordatorio{due_text}: {reminder.title}.",
            actions=[
                AssistantAction(
                    type="reminder.created",
                    payload={
                        "reminder_id": reminder.id,
                        "title": reminder.title,
                        "due_at": reminder.due_at.isoformat() if reminder.due_at else None,
                    },
                )
            ],
            metadata={"intent": IntentName.CREATE_REMINDER},
        )

    def _list_reminders(self, request: AssistantRequest) -> AssistantResponse:
        reminders = self._reminder_service.list_pending_reminders(request.business_id)
        if not reminders:
            return AssistantResponse(
                reply="No tenes recordatorios pendientes.",
                metadata={"intent": IntentName.LIST_REMINDERS, "count": 0},
            )

        reminder_lines = [f"- {reminder.title} ({reminder.id})" for reminder in reminders]
        return AssistantResponse(
            reply="Recordatorios pendientes:\n" + "\n".join(reminder_lines),
            metadata={"intent": IntentName.LIST_REMINDERS, "count": len(reminders)},
        )

    def _mark_reminder_done(
        self, request: AssistantRequest, entities: dict[str, object]
    ) -> AssistantResponse:
        reminder_id = str(entities.get("reminder_id") or "").strip()
        title_query = str(entities.get("title_query") or "").strip()
        reminder = self._reminder_service.mark_done(
            business_id=request.business_id,
            reminder_id=reminder_id or None,
            title_query=title_query or None,
        )
        if reminder is None:
            return AssistantResponse(
                reply="No encontre un recordatorio pendiente para marcar como hecho.",
                metadata={"intent": IntentName.MARK_REMINDER_DONE},
            )

        return AssistantResponse(
            reply=f"Perfecto, marque como hecho: {reminder.title}.",
            actions=[
                AssistantAction(
                    type="reminder.completed",
                    payload={"reminder_id": reminder.id, "title": reminder.title},
                )
            ],
            metadata={"intent": IntentName.MARK_REMINDER_DONE},
        )

    @staticmethod
    def _parse_due_at(value: object) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        return datetime.fromisoformat(value)

    @staticmethod
    def _format_due_text(due_at: datetime | None) -> str:
        if due_at is None:
            return ""
        return f" para {due_at.astimezone(ASSISTANT_TIMEZONE):%d/%m %H:%M}"
