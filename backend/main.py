"""
FastAPI app entrypoint. Importing `config` here first means a missing
required credential raises immediately on startup, not on first use.
"""

from fastapi import FastAPI

from config import settings  # noqa: F401  (import triggers validation)

app = FastAPI(title="AI Meeting Ghost")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
