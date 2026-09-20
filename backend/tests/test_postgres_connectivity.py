"""
Postgres equivalent of Phase 0's original AWS/Slack sanity checks:
connects using DATABASE_URL and runs a trivial query, proving the
server is reachable and credentials are valid before trusting anything
else in the suite. If this fails, every other test in this directory
that touches the database (test_database.py, test_notification_pipeline.py,
test_slack_webhook.py) will fail too, for the same underlying reason —
run this one first when diagnosing a red suite.
"""

import pytest
from sqlalchemy import text

import database


@pytest.mark.asyncio
async def test_can_connect_and_run_trivial_query():
    async with database.engine.begin() as conn:
        result = await conn.execute(text("SELECT 1"))
        assert result.scalar() == 1
