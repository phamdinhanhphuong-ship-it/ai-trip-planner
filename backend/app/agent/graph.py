from __future__ import annotations

from langchain_groq import ChatGroq
from langgraph.prebuilt import create_react_agent

from app.agent.prompts import SYSTEM_PROMPT
from app.agent.tools import TOOLS
from app.config import settings

DEFAULT_MODEL = "openai/gpt-oss-120b"


def build_agent(model: str = DEFAULT_MODEL):
    if not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY chưa được cấu hình trong .env")
    llm = ChatGroq(api_key=settings.groq_api_key, model=model, temperature=0)
    return create_react_agent(llm, TOOLS, state_modifier=SYSTEM_PROMPT)


async def run_agent(user_message: str, model: str = DEFAULT_MODEL) -> str:
    agent = build_agent(model=model)
    result = await agent.ainvoke({"messages": [("user", user_message)]})
    return result["messages"][-1].content
