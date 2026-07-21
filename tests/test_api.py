def test_health(client) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_assistant_message_creates_reminder(client) -> None:
    response = client.post(
        "/assistant/message",
        json={
            "channel": "cli",
            "external_user_id": "user-1",
            "business_id": "business-1",
            "message_type": "text",
            "text": "recordame revisar la heladera",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert "guardé el recordatorio" in body["reply"]
    assert body["actions"][0]["type"] == "reminder.created"
