"""
ERC-8004 A2A (Agent-to-Agent) HTTP server for ProfitPilot.

Endpoints:
  POST /a2a         — Accept a task and return the agent's response
  GET  /health      — Liveness and readiness probe
  GET  /agent_card  — Serve the ERC-8004 agent card JSON

Run with:
    python server.py
or:
    uvicorn server:app --host 0.0.0.0 --port 8000
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from monitoring import check_system_health

logger = logging.getLogger(__name__)

app = FastAPI(title="ProfitPilot A2A Server", version="1.0.0")

_AGENT_CARD_PATH = Path(__file__).parent / "agent_card.json"


# ── Request / response models ─────────────────────────────────────────────────

class A2ATask(BaseModel):
    """Minimal ERC-8004-compatible inbound task message."""

    task_id: str | None = None
    message: str
    context: dict[str, Any] | None = None


class A2AResponse(BaseModel):
    task_id: str | None
    status: str   # "success" | "error"
    response: str
    timestamp: float


# ── Routes ────────────────────────────────────────────────────────────────────

@app.post("/a2a", response_model=A2AResponse)
async def handle_a2a_task(task: A2ATask) -> A2AResponse:
    """Process an incoming A2A task and return the agent's response.

    The agent is invoked synchronously; for long-running tasks consider
    wrapping run_agent in asyncio.to_thread or using a task queue.
    """
    # Import here to avoid circular import at module level
    from agent_core import run_agent  # noqa: PLC0415

    start = time.time()
    logger.info("A2A task received | id=%s", task.task_id or "none")

    try:
        response_text = run_agent(task.message)
    except Exception as exc:
        logger.error("Agent error | id=%s | %s", task.task_id, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    elapsed = time.time() - start
    logger.info("A2A task completed | id=%s | %.2fs", task.task_id, elapsed)

    return A2AResponse(
        task_id=task.task_id,
        status="success",
        response=response_text,
        timestamp=time.time(),
    )


@app.get("/health")
async def health_check() -> JSONResponse:
    """Liveness and readiness probe.

    Returns HTTP 200 when all checks pass, HTTP 503 when any alert is active.
    """
    health = check_system_health()
    status_code = 200 if health["healthy"] else 503
    return JSONResponse(content=health, status_code=status_code)


@app.get("/agent_card")
async def get_agent_card() -> dict:
    """Return this agent's ERC-8004 agent card."""
    try:
        return json.loads(_AGENT_CARD_PATH.read_text())
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="agent_card.json not found") from exc


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("server:app", host=host, port=port, reload=False, log_level="info")
