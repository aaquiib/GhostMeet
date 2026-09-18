"""
Tests GET /search's fallback behavior for real: points
opensearch_client._client at a genuinely unreachable address (no Docker
needed to prove a *connection failure* triggers the fallback — that's
different from "not configured at all", which every other test file
already exercises implicitly since OPENSEARCH_HOST is unset in
backend/.env here) and confirms results still come back correctly via
the SQLite LIKE path.

test_search_uses_opensearch_when_available is the real, non-mocked
lifecycle check against docker-compose's OpenSearch — it skips itself
if OPENSEARCH_HOST isn't reachable (this sandbox can't run Docker; see
CLAUDE.md), rather than failing the suite over an environment that
doesn't have it up.
"""

import os
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from opensearchpy import AsyncOpenSearch

import main
import opensearch_client
from decision_detector import DecisionRecord
from decision_store import decision_store

client = TestClient(main.app)

MEETING_ID = "search-test-meeting"


@pytest.mark.asyncio
async def test_search_falls_back_to_sqlite_when_opensearch_unreachable(monkeypatch):
    # A real client pointed at a port nothing listens on — genuinely
    # exercises the connection-failure path in search_decisions(),
    # not just the "OPENSEARCH_HOST unset" path.
    unreachable = AsyncOpenSearch(hosts=["http://localhost:9199"], timeout=1)
    monkeypatch.setattr(opensearch_client, "_client", unreachable)

    record = DecisionRecord(
        meeting_id=MEETING_ID,
        decision_text="Whether to fall back to SQLite when OpenSearch is down",
        speaker="spk_0",
        requires_action_from="Sarah",
        context="fallback test",
        confidence=0.9,
        urgency="medium",
        timestamp=datetime.now(timezone.utc),
    )
    await decision_store.create(record)

    response = client.get("/search", params={"q": "fall back to SQLite"})
    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "sqlite_fallback"
    assert any(r["id"] == str(record.id) for r in data["results"])

    await unreachable.close()


def test_search_uses_opensearch_when_available():
    host = os.environ.get("OPENSEARCH_HOST", "http://localhost:9200")
    import urllib.request

    try:
        urllib.request.urlopen(f"{host}/_cluster/health", timeout=1)
    except Exception:
        pytest.skip(f"OpenSearch not reachable at {host} (docker-compose up? not available in this sandbox)")

    import asyncio

    async def _run():
        real_client = AsyncOpenSearch(hosts=[host], timeout=5)
        monkeypatch_target = opensearch_client._client
        opensearch_client._client = real_client
        try:
            await opensearch_client.ensure_index()
            record = DecisionRecord(
                meeting_id=MEETING_ID,
                decision_text="Whether to verify real OpenSearch search",
                speaker="spk_0",
                requires_action_from="Sarah",
                context="real opensearch test",
                confidence=0.9,
                urgency="medium",
                timestamp=datetime.now(timezone.utc),
            )
            await decision_store.create(record)
            await asyncio.sleep(1)  # OpenSearch indexing isn't instant
            results = await opensearch_client.search_decisions("verify real OpenSearch")
            assert any(r["decision_text"] == record.decision_text for r in results)
        finally:
            opensearch_client._client = monkeypatch_target
            await real_client.close()

    asyncio.run(_run())
