from __future__ import annotations

import asyncio
import json
import os
from typing import Any, AsyncIterator

import redis.asyncio as redis

from shared.logging import get_logger
from shared.schemas.freshness import SkillFreshnessSnapshot
from shared.schemas.scoring import SessionScoreReport

log = get_logger(__name__)

STREAM_IN = os.getenv("DHAARA_STREAM_IN", "pratibimb.events.in")
STREAM_OUT = os.getenv("DHAARA_STREAM_OUT", "pratibimb.events.out")
GROUP = os.getenv("DHAARA_GROUP", "pratibimb-workers")


class DhaaraClient:
    """Async client for the shared event bus (Redis Streams)."""

    def __init__(self, url: str | None = None):
        self.url = url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self._r: redis.Redis | None = None

    async def connect(self) -> None:
        self._r = redis.from_url(self.url, decode_responses=True)

    async def publish(self, stream: str, payload: dict[str, Any]) -> str:
        if self._r is None:
            await self.connect()
        assert self._r is not None
        msg_id = await self._r.xadd(stream, {"data": json.dumps(payload)})
        log.debug("dhaara_publish", stream=stream, msg_id=msg_id, type=payload.get("type"))
        return msg_id

    async def consume(
        self, consumer: str, stream: str | None = None, block_ms: int = 5000
    ) -> AsyncIterator[dict]:
        target = stream or STREAM_IN
        if self._r is None:
            await self.connect()
        assert self._r is not None
        group = f"{GROUP}-{target}"
        try:
            await self._r.xgroup_create(target, group, id="0-0", mkstream=True)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise
        while True:
            resp = await self._r.xreadgroup(group, consumer, {target: ">"}, count=8, block=block_ms)
            if not resp:
                await asyncio.sleep(0.01)
                continue
            for _stream, entries in resp:
                for msg_id, fields in entries:
                    try:
                        yield {"id": msg_id, **json.loads(fields["data"])}
                    finally:
                        await self._r.xack(target, group, msg_id)

    async def consume_out(self, consumer: str, block_ms: int = 5000) -> AsyncIterator[dict]:
        async for evt in self.consume(consumer, stream=STREAM_OUT, block_ms=block_ms):
            yield evt

    async def push_competency_updates(self, report: SessionScoreReport) -> dict:
        """Publish score + competency deltas to the out stream; HTTP fallback if configured."""
        payload = {
            "type": "competency.update",
            "learner_id": report.learner_id,
            "session_id": report.session_id,
            "case_id": report.case_id,
            "overall_score": report.rubric.overall,
            "updates": [u.model_dump() for u in report.competency_updates],
            "scorecard": {
                "rubric": report.rubric.model_dump(),
                "critical_actions_hit": report.critical_actions_hit,
                "critical_actions_missed": report.critical_actions_missed,
            },
        }
        try:
            msg_id = await self.publish(STREAM_OUT, payload)
            return {"status": "published", "msg_id": msg_id}
        except Exception as exc:
            log.warning("dhaara_stream_failed", error=str(exc))
            return await self._http_fallback(report)

    async def emit_session_event(self, session_id: str, event_type: str, data: dict | None = None) -> None:
        try:
            await self.publish(STREAM_OUT, {
                "type": event_type,
                "session_id": session_id,
                **(data or {}),
            })
        except Exception as exc:
            log.warning("dhaara_emit_failed", session_id=session_id, error=str(exc))

    async def push_freshness_snapshot(self, snapshot: SkillFreshnessSnapshot) -> dict:
        """Publish a SkillFreshnessSnapshot to Dhaara (stream first, HTTP fallback)."""
        payload = {
            "type": "freshness.snapshot",
            **snapshot.model_dump(mode="json"),
        }
        try:
            msg_id = await self.publish(STREAM_OUT, payload)
            return {"status": "published", "msg_id": msg_id}
        except Exception as exc:
            log.warning("dhaara_freshness_stream_failed", error=str(exc))
            return await self._http_freshness_fallback(snapshot)

    async def push_freshness_snapshots(
        self, snapshots: tuple[SkillFreshnessSnapshot, ...]
    ) -> dict:
        results = []
        for snap in snapshots:
            results.append(await self.push_freshness_snapshot(snap))
        return {"status": "published", "count": len(results), "results": results}

    async def _http_freshness_fallback(self, snapshot: SkillFreshnessSnapshot) -> dict:
        import httpx

        base_url = os.getenv("DHAARA_URL", "http://localhost:8200").rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{base_url}/v1/freshness/ingest",
                    json=snapshot.model_dump(mode="json"),
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            log.warning("dhaara_freshness_http_failed", error=str(exc))
            return {"status": "deferred", "error": str(exc)}

    async def _http_fallback(self, report: SessionScoreReport) -> dict:
        import httpx

        base_url = os.getenv("DHAARA_URL", "http://localhost:8200").rstrip("/")
        payload = {
            "learner_id": report.learner_id,
            "session_id": report.session_id,
            "case_id": report.case_id,
            "overall_score": report.rubric.overall,
            "updates": [u.model_dump() for u in report.competency_updates],
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(f"{base_url}/v1/competency/update", json=payload)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            log.warning("dhaara_http_fallback_failed", error=str(exc))
            return {"status": "deferred", "error": str(exc)}

    async def close(self) -> None:
        if self._r:
            await self._r.aclose()
            self._r = None
