from typing import Protocol

from app.schemas.assistant import AssistantRequest, AssistantResponse


class ChannelAdapter(Protocol):
    def to_assistant_requests(self, payload: dict[str, object]) -> list[AssistantRequest]: ...

    def send_response(
        self,
        request: AssistantRequest,
        response: AssistantResponse,
    ) -> dict[str, object]: ...
