"""Collects Claude usage during a task and writes it to usage_events afterwards."""
import threading
from uuid import UUID

from api.db import as_service


class UsageCollector:
    """Thread-safe sink for pipeline.llm.usage_sink (scoring runs in worker threads)."""

    def __init__(self):
        self._lock = threading.Lock()
        self.records: list[dict] = []

    def add(self, record: dict) -> None:
        with self._lock:
            self.records.append(record)

    @property
    def total_cost(self) -> float:
        return round(sum(r["cost_usd"] for r in self.records), 6)

    def flush(self, conn, user_id: UUID | None, industry_id: str | None = None) -> int:
        with self._lock:
            records, self.records = self.records, []
        if not records:
            return 0
        with as_service(conn), conn.cursor() as cur:
            cur.executemany(
                "insert into public.usage_events (user_id, industry_id, action, model, input_tokens, "
                "output_tokens, cost_usd) values (%s, %s, %s, %s, %s, %s, %s)",
                [(user_id, industry_id, f"{r.get('kind', 'llm')}:{r['purpose']}", r["model"],
                  r["input_tokens"] + r["cache_creation_tokens"] + r["cache_read_tokens"],
                  r["output_tokens"], r["cost_usd"]) for r in records])
        return len(records)
