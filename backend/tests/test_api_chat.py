from fastapi.testclient import TestClient

from app.main import app


def test_chat_rejects_blank_and_oversized_input():
    client = TestClient(app)

    blank = client.post("/chat", json={"session_id": "session", "message": "   "})
    oversized = client.post("/chat", json={"session_id": "session", "message": "x" * 4001})

    assert blank.status_code == 422
    assert oversized.status_code == 422