import json

from fastapi import FastAPI, Header, HTTPException, Request

from app.assistant.engine import AssistantEngine
from app.assistant.intent_router import IntentRouter
from app.channels.whatsapp_kapso import (
    KapsoWebhookError,
    KapsoWhatsAppAdapter,
    verify_kapso_signature,
)
from app.db.repositories.conversations import InMemoryConversationMessageRepository
from app.db.repositories.reminders import InMemoryReminderRepository
from app.db.repositories.sql_conversations import SqlConversationMessageRepository
from app.db.repositories.sql_reminders import SqlReminderRepository
from app.db.session import SessionLocal
from app.modules.reminders.service import ReminderService
from app.schemas.assistant import AssistantRequest, AssistantResponse
from app.settings import settings


def build_engine() -> AssistantEngine:
    if SessionLocal is not None:
        reminder_repository = SqlReminderRepository(SessionLocal)
        conversation_repository = SqlConversationMessageRepository(SessionLocal)
    else:
        reminder_repository = InMemoryReminderRepository()
        conversation_repository = InMemoryConversationMessageRepository()

    reminder_service = ReminderService(reminder_repository)
    intent_router = IntentRouter()
    return AssistantEngine(
        intent_router=intent_router,
        reminder_service=reminder_service,
        conversation_repository=conversation_repository,
    )


def create_app() -> FastAPI:
    api = FastAPI(title=settings.app_name)
    engine = build_engine()
    kapso_adapter = KapsoWhatsAppAdapter(
        business_id=settings.default_business_id,
        api_key=settings.kapso_api_key,
        phone_number_id=settings.kapso_sandbox_phone_number_id,
    )

    @api.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.post("/assistant/message", response_model=AssistantResponse)
    def assistant_message(request: AssistantRequest) -> AssistantResponse:
        return engine.handle_message(request)

    @api.post("/webhooks/kapso")
    async def kapso_webhook(
        request: Request,
        x_webhook_signature: str | None = Header(default=None),
        x_webhook_event: str | None = Header(default=None),
        x_idempotency_key: str | None = Header(default=None),
    ) -> dict[str, object]:
        raw_body = await request.body()
        if not verify_kapso_signature(
            raw_body,
            x_webhook_signature,
            settings.kapso_webhook_secret,
        ):
            raise HTTPException(status_code=401, detail="Invalid Kapso webhook signature.")

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON payload.") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Kapso webhook payload must be an object.")

        if x_webhook_event and x_webhook_event != "whatsapp.message.received":
            return {"processed": 0, "event": x_webhook_event, "status": "ignored"}

        try:
            assistant_requests = kapso_adapter.to_assistant_requests(payload)
        except KapsoWebhookError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        replies: list[str] = []
        outbound_deliveries: list[dict[str, object]] = []
        for assistant_request in assistant_requests:
            assistant_request.raw_payload["headers"] = {
                "x_webhook_event": x_webhook_event,
                "x_idempotency_key": x_idempotency_key,
            }
            assistant_response = engine.handle_message(assistant_request)
            outbound_deliveries.append(
                kapso_adapter.send_response(assistant_request, assistant_response)
            )
            replies.append(assistant_response.reply)

        return {
            "processed": len(assistant_requests),
            "event": x_webhook_event,
            "replies": replies,
            "outbound_deliveries": outbound_deliveries,
        }

    return api


app = create_app()
