from fastapi import FastAPI

from app.assistant.engine import AssistantEngine
from app.assistant.intent_router import IntentRouter
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

    @api.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.post("/assistant/message", response_model=AssistantResponse)
    def assistant_message(request: AssistantRequest) -> AssistantResponse:
        return engine.handle_message(request)

    return api


app = create_app()
