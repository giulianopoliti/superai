from datetime import UTC, datetime
from uuid import uuid4

from app.db.repositories.reminders import ReminderRepository
from app.modules.reminders.schemas import Reminder, ReminderStatus


class ReminderService:
    def __init__(self, repository: ReminderRepository) -> None:
        self._repository = repository

    def create_reminder(
        self,
        *,
        business_id: str,
        created_by_external_user_id: str,
        title: str,
        description: str | None = None,
        due_at: datetime | None = None,
    ) -> Reminder:
        reminder = Reminder(
            id=str(uuid4()),
            business_id=business_id,
            created_by_external_user_id=created_by_external_user_id,
            title=title.strip(),
            description=description,
            due_at=due_at,
        )
        return self._repository.add(reminder)

    def list_pending_reminders(self, business_id: str) -> list[Reminder]:
        return self._repository.list_pending(business_id)

    def mark_done(
        self,
        *,
        business_id: str,
        reminder_id: str | None = None,
        title_query: str | None = None,
    ) -> Reminder | None:
        reminder = None
        if reminder_id:
            reminder = self._repository.get_pending_by_id(business_id, reminder_id)
        elif title_query:
            reminder = self._repository.find_pending_by_title(business_id, title_query)

        if reminder is None:
            return None

        now = datetime.now(UTC)
        completed = reminder.model_copy(
            update={
                "status": ReminderStatus.DONE,
                "completed_at": now,
                "updated_at": now,
            }
        )
        return self._repository.update(completed)
