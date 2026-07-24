import asyncio
import json
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Header, HTTPException, Request

from app.assistant.engine import AssistantEngine
from app.assistant.intent_router import IntentRouter
from app.assistant.llm_intent_router import LLMIntentRouter
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
from app.modules.reminders.dispatcher import ReminderDispatcher
from app.modules.reminders.service import ReminderService
from app.providers.llm.base import LLMProvider
from app.providers.llm.gemini import GeminiLLMProvider
from app.providers.llm.openai import OpenAILLMProvider
from app.providers.notifications.kapso import KapsoNotificationProvider
from app.schemas.assistant import AssistantRequest, AssistantResponse
from app.settings import settings


def build_repositories():
    if SessionLocal is not None:
        return SqlReminderRepository(SessionLocal), SqlConversationMessageRepository(SessionLocal)
    return InMemoryReminderRepository(), InMemoryConversationMessageRepository()


def build_llm_provider() -> LLMProvider | None:
    if not settings.llm_enabled:
        return None

    provider = settings.llm_provider.strip().lower()
    if provider == "gemini" and settings.gemini_api_key:
        return GeminiLLMProvider(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    if provider == "openai" and settings.openai_api_key:
        return OpenAILLMProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    return None


def build_engine(
    reminder_repository=None,
    conversation_repository=None,
) -> AssistantEngine:
    if reminder_repository is None or conversation_repository is None:
        reminder_repository, conversation_repository = build_repositories()

    reminder_service = ReminderService(reminder_repository)
    intent_router = IntentRouter()
    llm_provider = build_llm_provider()
    if llm_provider is not None:
        intent_router = LLMIntentRouter(
            llm_provider=llm_provider,
            fallback_router=intent_router,
        )
    return AssistantEngine(
        intent_router=intent_router,
        reminder_service=reminder_service,
        conversation_repository=conversation_repository,
    )


def create_app() -> FastAPI:
    reminder_repository, conversation_repository = build_repositories()
    reminder_service = ReminderService(reminder_repository)
    engine = build_engine(reminder_repository, conversation_repository)
    reminder_dispatcher = ReminderDispatcher(
        reminder_service=reminder_service,
        notification_provider=KapsoNotificationProvider(
            api_key=settings.kapso_api_key,
            phone_number_id=settings.kapso_sandbox_phone_number_id,
        ),
    )
    kapso_adapter = KapsoWhatsAppAdapter(
        business_id=settings.default_business_id,
        api_key=settings.kapso_api_key,
        phone_number_id=settings.kapso_sandbox_phone_number_id,
    )
    scheduler_task: asyncio.Task[None] | None = None

    async def scheduler_loop() -> None:
        while True:
            reminder_dispatcher.dispatch_due()
            await asyncio.sleep(settings.scheduler_interval_seconds)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        nonlocal scheduler_task
        if settings.scheduler_enabled:
            scheduler_task = asyncio.create_task(scheduler_loop())
        try:
            yield
        finally:
            if scheduler_task is not None:
                scheduler_task.cancel()
                with suppress(asyncio.CancelledError):
                    await scheduler_task

    api = FastAPI(title=settings.app_name, lifespan=lifespan)

    @api.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.post("/assistant/message", response_model=AssistantResponse)
    def assistant_message(request: AssistantRequest) -> AssistantResponse:
        return engine.handle_message(request)

    @api.post("/internal/reminders/dispatch-due")
    def dispatch_due_reminders(
        x_internal_token: str | None = Header(default=None),
    ) -> dict[str, object]:
        if settings.internal_api_token and x_internal_token != settings.internal_api_token:
            raise HTTPException(status_code=401, detail="Invalid internal token.")

        results = reminder_dispatcher.dispatch_due()
        return {
            "processed": len(results),
            "results": [
                {
                    "reminder_id": result.reminder_id,
                    "status": result.status,
                    "notification_status": result.notification_status,
                    "metadata": result.metadata,
                }
                for result in results
            ],
        }

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
