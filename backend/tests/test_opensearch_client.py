"""
Tests opensearch_client.py's internal logic (index name, document
shape, query shape, never-raises-on-index-failure) against a fake
AsyncOpenSearch-shaped client, since a real OpenSearch instance isn't
available in every environment this runs in (this sandbox can't run
Docker — see test_search_fallback.py for what IS tested against a
real, deliberately-unreachable connection without needing Docker at
all: the actual fallback-to-SQLite path).
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

import opensearch_client
from decision_detector import DecisionRecord


class _FakeIndices:
    def __init__(self):
        self.created_with = None

    async def exists(self, index):
        return False

    async def create(self, index, body):
        self.created_with = (index, body)


class _FakeOpenSearchClient:
    def __init__(self, fail_index=False):
        self.indices = _FakeIndices()
        self.indexed = []
        self.fail_index = fail_index

    async def index(self, index, id, body):
        if self.fail_index:
            raise ConnectionError("simulated OpenSearch outage")
        self.indexed.append((index, id, body))

    async def search(self, index, body):
        return {
            "hits": {
                "hits": [
                    {"_source": {"decision_text": "matched", "speaker": "spk_0"}},
                ]
            }
        }


def _record() -> DecisionRecord:
    return DecisionRecord(
        meeting_id="os-test-meeting",
        decision_text="Whether to test OpenSearch",
        speaker="spk_0",
        requires_action_from="Sarah",
        context="test context",
        confidence=0.9,
        urgency="medium",
        timestamp=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_ensure_index_creates_with_explicit_mapping(monkeypatch):
    fake = _FakeOpenSearchClient()
    monkeypatch.setattr(opensearch_client, "_client", fake)

    await opensearch_client.ensure_index()

    assert fake.indices.created_with is not None
    index_name, body = fake.indices.created_with
    assert index_name == "decisions"
    props = body["mappings"]["properties"]
    assert props["decision_text"] == {"type": "text"}
    assert props["context"] == {"type": "text"}
    assert props["speaker"] == {"type": "keyword"}
    assert props["approved_by"] == {"type": "keyword"}
    assert props["status"] == {"type": "keyword"}
    assert props["timestamp"] == {"type": "date"}
    assert props["confidence"] == {"type": "float"}


@pytest.mark.asyncio
async def test_ensure_index_noop_when_not_configured(monkeypatch):
    monkeypatch.setattr(opensearch_client, "_client", None)
    await opensearch_client.ensure_index()  # must not raise


@pytest.mark.asyncio
async def test_index_decision_sends_correct_document(monkeypatch):
    fake = _FakeOpenSearchClient()
    monkeypatch.setattr(opensearch_client, "_client", fake)

    record = _record()
    await opensearch_client.index_decision(record, approved_by="U_APPROVER")

    assert len(fake.indexed) == 1
    index_name, doc_id, body = fake.indexed[0]
    assert index_name == "decisions"
    assert doc_id == str(record.id)
    assert body["decision_text"] == record.decision_text
    assert body["speaker"] == record.speaker
    assert body["approved_by"] == "U_APPROVER"
    assert body["status"] == record.status


@pytest.mark.asyncio
async def test_index_decision_never_raises_on_failure(monkeypatch):
    fake = _FakeOpenSearchClient(fail_index=True)
    monkeypatch.setattr(opensearch_client, "_client", fake)

    # Must swallow the failure, not propagate it — indexing errors
    # can't be allowed to break decision creation/status updates.
    await opensearch_client.index_decision(_record())


@pytest.mark.asyncio
async def test_search_decisions_queries_the_right_fields(monkeypatch):
    fake = _FakeOpenSearchClient()
    monkeypatch.setattr(opensearch_client, "_client", fake)

    results = await opensearch_client.search_decisions("ship date")

    assert results == [{"decision_text": "matched", "speaker": "spk_0"}]


@pytest.mark.asyncio
async def test_search_decisions_raises_when_not_configured(monkeypatch):
    monkeypatch.setattr(opensearch_client, "_client", None)
    with pytest.raises(RuntimeError):
        await opensearch_client.search_decisions("anything")
