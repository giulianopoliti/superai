from uuid import uuid4

from sqlalchemy import literal_column, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ConversationMessageModel
from app.db.repositories.conversations import ConversationMessage, with_conversation_id
from app.schemas.assistant import AssistantRequest, AssistantResponse


class SqlConversationMessageRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save_inbound(self, request: AssistantRequest) -> ConversationMessage:
        with self._session_factory() as session:
            model = ConversationMessageModel(
                id=str(uuid4()),
                business_id=request.business_id,
                external_user_id=request.external_user_id,
                channel=request.channel,
                direction="inbound",
                message_type=request.message_type,
                text=request.text,
                payload=with_conversation_id(request, {"raw_payload": request.raw_payload}),
            )
            session.add(model)
            session.commit()
            session.refresh(model)
            return self._to_domain(model)

    def save_outbound(
        self, request: AssistantRequest, response: AssistantResponse
    ) -> ConversationMessage:
        with self._session_factory() as session:
            model = ConversationMessageModel(
                id=str(uuid4()),
                business_id=request.business_id,
                external_user_id=request.external_user_id,
                channel=request.channel,
                direction="outbound",
                message_type="text",
                text=response.reply,
                payload=with_conversation_id(request, response.model_dump(mode="json")),
            )
            session.add(model)
            session.commit()
            session.refresh(model)
            return self._to_domain(model)

    def list_by_business(self, business_id: str) -> list[ConversationMessage]:
        with self._session_factory() as session:
            models = session.scalars(
                select(ConversationMessageModel)
                .where(ConversationMessageModel.business_id == business_id)
                .order_by(ConversationMessageModel.created_at)
            ).all()
            return [self._to_domain(model) for model in models]

    def recent_messages(self, request: AssistantRequest) -> list[ConversationMessage]:
        message = ConversationMessageModel
        key = message.payload["conversation_id"].as_string()
        session_id = str(request.conversation_id) if request.conversation_id else None
        with self._session_factory() as session:
            # SQLite can assign identical timestamps to successive turns on Windows.
            tie_breaker = (
                literal_column("conversation_messages.rowid")
                if session.get_bind().dialect.name == "sqlite" else message.id
            )
            models = session.scalars(
                select(message).where(
                    message.business_id == request.business_id,
                    message.external_user_id == request.external_user_id,
                    message.channel == request.channel,
                    key == session_id,
                ).order_by(message.created_at.desc(), tie_breaker.desc()).limit(16)
            ).all()
            return [self._to_domain(model) for model in reversed(models)]

    @staticmethod
    def _to_domain(model: ConversationMessageModel) -> ConversationMessage:
        return ConversationMessage(
            id=model.id,
            business_id=model.business_id,
            external_user_id=model.external_user_id,
            channel=model.channel,
            direction=model.direction,
            message_type=model.message_type,
            text=model.text,
            payload=model.payload,
            created_at=model.created_at,
        )
