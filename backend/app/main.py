from fastapi import FastAPI

from app.api.chat import router as chat_router
from app.core.logging import setup_logging

setup_logging()

app = FastAPI(title="GuidePass AI Trip Planner")
app.include_router(chat_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
