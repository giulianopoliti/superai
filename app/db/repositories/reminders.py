from typing import Protocol

from app.modules.reminders.schemas import Reminder, ReminderStatus


class ReminderRepository(Protocol):
    def add(self, reminder: Reminder) -> Reminder: ...

    def list_pending(self, business_id: str) -> list[Reminder]: ...

    def get_pending_by_id(self, business_id: str, reminder_id: str) -> Reminder | None: ...

    def find_pending_by_title(self, business_id: str, title_query: str) -> Reminder | None: ...

    def update(self, reminder: Reminder) -> Reminder: ...


class InMemoryReminderRepository:
    def __init__(self) -> None:
        self._reminders: dict[str, Reminder] = {}

    def add(self, reminder: Reminder) -> Reminder:
        self._reminders[reminder.id] = reminder
        return reminder

    def list_pending(self, business_id: str) -> list[Reminder]:
        return [
            reminder
            for reminder in self._reminders.values()
            if reminder.business_id == business_id and reminder.status == ReminderStatus.PENDING
        ]

    def get_pending_by_id(self, business_id: str, reminder_id: str) -> Reminder | None:
        reminder = self._reminders.get(reminder_id)
        if (
            reminder is None
            or reminder.business_id != business_id
            or reminder.status != ReminderStatus.PENDING
        ):
            return None
        return reminder

    def find_pending_by_title(self, business_id: str, title_query: str) -> Reminder | None:
        normalized_query = title_query.lower().strip()
        if not normalized_query:
            return None

        for reminder in self.list_pending(business_id):
            if normalized_query in reminder.title.lower():
                return reminder
        return None

    def update(self, reminder: Reminder) -> Reminder:
        self._reminders[reminder.id] = reminder
        return reminder
