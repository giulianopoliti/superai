from app.assistant.intent_router import IntentRouter
from app.db.repositories.conversations import ConversationMessageRepository
from app.modules.reminders.service import ReminderService
from app.schemas.assistant import AssistantAction, AssistantRequest, AssistantResponse
from app.schemas.intents import IntentName


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

        if intent_result.intent == IntentName.CREATE_REMINDER:
            response = self._create_reminder(request, intent_result.entities)
        elif intent_result.intent == IntentName.LIST_REMINDERS:
            response = self._list_reminders(request)
        elif intent_result.intent == IntentName.MARK_REMINDER_DONE:
            response = self._mark_reminder_done(request, intent_result.entities)
        else:
            response = AssistantResponse(
                reply="No estoy seguro de cómo ayudarte con eso todavía. "
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
                reply="¿Qué querés que te recuerde?",
                requires_confirmation=True,
                confirmation_payload={"intent": IntentName.CREATE_REMINDER},
            )

        reminder = self._reminder_service.create_reminder(
            business_id=request.business_id,
            created_by_external_user_id=request.external_user_id,
            title=title,
        )
        return AssistantResponse(
            reply=f"Listo, guardé el recordatorio: {reminder.title}.",
            actions=[
                AssistantAction(
                    type="reminder.created",
                    payload={"reminder_id": reminder.id, "title": reminder.title},
                )
            ],
            metadata={"intent": IntentName.CREATE_REMINDER},
        )

    def _list_reminders(self, request: AssistantRequest) -> AssistantResponse:
        reminders = self._reminder_service.list_pending_reminders(request.business_id)
        if not reminders:
            return AssistantResponse(
                reply="No tenés recordatorios pendientes.",
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
                reply="No encontré un recordatorio pendiente para marcar como hecho.",
                metadata={"intent": IntentName.MARK_REMINDER_DONE},
            )

        return AssistantResponse(
            reply=f"Perfecto, marqué como hecho: {reminder.title}.",
            actions=[
                AssistantAction(
                    type="reminder.completed",
                    payload={"reminder_id": reminder.id, "title": reminder.title},
                )
            ],
            metadata={"intent": IntentName.MARK_REMINDER_DONE},
        )
