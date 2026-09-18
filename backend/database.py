"""
Owns the SQLite/SQLAlchemy (async) layer: engine/session setup and the
decision history models — what got detected, the drafted answer, the
Cedar verdict, and how the user resolved it. The source of truth for
past-decision context before OpenSearch is added in phase 5.
"""
