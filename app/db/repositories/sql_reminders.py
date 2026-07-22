from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ReminderModel
from app.modules.reminders.schemas import Reminder, ReminderStatus


class SqlReminderRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def add(self, reminder: Reminder) -> Reminder:
        with self._session_factory() as session:
            model = ReminderModel(
                id=reminder.id,
                business_id=reminder.business_id,
                created_by_external_user_id=reminder.created_by_external_user_id,
                title=reminder.title,
                description=reminder.description,
                due_at=reminder.due_at,
                recurrence_rule=reminder.recurrence_rule,
                status=reminder.status,
                completed_at=reminder.completed_at,
                created_at=reminder.created_at,
                updated_at=reminder.updated_at,
            )
            session.add(model)
            session.commit()
            session.refresh(model)
            return self._to_domain(model)

    def list_pending(self, business_id: str) -> list[Reminder]:
        with self._session_factory() as session:
            models = session.scalars(
                select(ReminderModel)
                .where(
                    ReminderModel.business_id == business_id,
                    ReminderModel.status == ReminderStatus.PENDING,
                )
                .order_by(ReminderModel.created_at)
            ).all()
            return [self._to_domain(model) for model in models]

    def list_due(self, now: datetime) -> list[Reminder]:
        with self._session_factory() as session:
            models = session.scalars(
                select(ReminderModel)
                .where(
                    ReminderModel.status == ReminderStatus.PENDING,
                    ReminderModel.due_at.is_not(None),
                    ReminderModel.due_at <= now,
                )
                .order_by(ReminderModel.due_at)
            ).all()
            return [self._to_domain(model) for model in models]

    def get_pending_by_id(self, business_id: str, reminder_id: str) -> Reminder | None:
        with self._session_factory() as session:
            model = session.get(ReminderModel, reminder_id)
            if (
                model is None
                or model.business_id != business_id
                or model.status != ReminderStatus.PENDING
            ):
                return None
            return self._to_domain(model)

    def find_pending_by_title(self, business_id: str, title_query: str) -> Reminder | None:
        normalized_query = title_query.lower().strip()
        if not normalized_query:
            return None

        with self._session_factory() as session:
            models = session.scalars(
                select(ReminderModel).where(
                    ReminderModel.business_id == business_id,
                    ReminderModel.status == ReminderStatus.PENDING,
                )
            ).all()
            for model in models:
                if normalized_query in model.title.lower():
                    return self._to_domain(model)
            return None

    def update(self, reminder: Reminder) -> Reminder:
        with self._session_factory() as session:
            model = session.get(ReminderModel, reminder.id)
            if model is None:
                raise ValueError(f"Reminder not found: {reminder.id}")

            model.title = reminder.title
            model.description = reminder.description
            model.due_at = reminder.due_at
            model.recurrence_rule = reminder.recurrence_rule
            model.status = reminder.status
            model.completed_at = reminder.completed_at
            model.updated_at = reminder.updated_at
            session.commit()
            session.refresh(model)
            return self._to_domain(model)

    @staticmethod
    def _to_domain(model: ReminderModel) -> Reminder:
        return Reminder(
            id=model.id,
            business_id=model.business_id,
            created_by_external_user_id=model.created_by_external_user_id,
            title=model.title,
            description=model.description,
            due_at=model.due_at,
            recurrence_rule=model.recurrence_rule,
            status=ReminderStatus(model.status),
            completed_at=model.completed_at,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
