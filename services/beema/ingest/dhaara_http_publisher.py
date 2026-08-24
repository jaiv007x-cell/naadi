from __future__ import annotations

"""
HTTP publisher for SkillFreshnessSnapshot → Dhaara ingest API.
"""
import json
import logging
import os
import urllib.error
import urllib.request

from shared.schemas.freshness import SkillFreshnessSnapshot

log = logging.getLogger(__name__)


class DhaaraHttpPublisher:
    """POST snapshots to Dhaara /v1/freshness/ingest."""

    def __init__(self, base_url: str | None = None, *, timeout_s: float = 10.0) -> None:
        self.base_url = (base_url or os.getenv("DHAARA_URL", "http://localhost:8200")).rstrip("/")
        self.timeout_s = timeout_s

    def publish(self, snapshot: SkillFreshnessSnapshot) -> None:
        payload = json.dumps(snapshot.model_dump(mode="json")).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/v1/freshness/ingest",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                if resp.status >= 400:
                    raise RuntimeError(f"dhaara ingest failed: HTTP {resp.status}")
        except urllib.error.URLError as exc:
            log.warning("dhaara_freshness_publish_failed snapshot=%s error=%s", snapshot.competency_id, exc)
            raise
