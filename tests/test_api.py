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
    assert "recordatorio" in body["reply"]
    assert body["actions"][0]["type"] == "reminder.created"


def test_assistant_message_creates_reminder_with_due_at(client) -> None:
    response = client.post(
        "/assistant/message",
        json={
            "channel": "cli",
            "external_user_id": "user-1",
            "business_id": "business-1",
            "message_type": "text",
            "text": "recordame revisar caja en 1 minutos",
            "timestamp": "2020-01-01T12:59:00Z",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["actions"][0]["payload"]["due_at"] == "2020-01-01T10:00:00-03:00"


def test_dispatch_due_endpoint(client) -> None:
    client.post(
        "/assistant/message",
        json={
            "channel": "cli",
            "external_user_id": "user-1",
            "business_id": "business-1",
            "message_type": "text",
            "text": "recordame revisar caja en 1 minutos",
            "timestamp": "2020-01-01T12:59:00Z",
        },
    )

    response = client.post("/internal/reminders/dispatch-due")

    assert response.status_code == 200
    assert response.json()["processed"] == 1
