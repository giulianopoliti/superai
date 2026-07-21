import argparse
from datetime import UTC, datetime

from app.main import build_engine
from app.schemas.assistant import AssistantRequest, Channel, MessageType


def main() -> None:
    parser = argparse.ArgumentParser(description="Send a local message to the Stock AI core.")
    parser.add_argument("text", help="Message text to process.")
    parser.add_argument("--business-id", default="demo-business")
    parser.add_argument("--external-user-id", default="local-user")
    args = parser.parse_args()

    request = AssistantRequest(
        channel=Channel.CLI,
        external_user_id=args.external_user_id,
        business_id=args.business_id,
        message_type=MessageType.TEXT,
        text=args.text,
        timestamp=datetime.now(UTC),
        raw_payload={"source": "cli"},
    )
    response = build_engine().handle_message(request)
    print(response.reply)


if __name__ == "__main__":
    main()
