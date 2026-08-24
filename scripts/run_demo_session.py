#!/usr/bin/env python3
"""Run a full demo session end-to-end against local Pratibimb service."""

from __future__ import annotations

import asyncio
import sys

import httpx

BASE = "http://localhost:8100"
LEARNER = "demo-learner-001"


async def run_demo() -> None:
    async with httpx.AsyncClient(timeout=60.0) as client:
        # Health check
        health = await client.get(f"{BASE}/health")
        health.raise_for_status()
        print(f"[OK] Pratibimb healthy: {health.json()}")

        # Create session
        create = await client.post(
            f"{BASE}/v1/sessions",
            json={
                "learner_id": LEARNER,
                "target_difficulty": 0.55,
                "state": "Maharashtra",
                "language": "mr",
            },
        )
        create.raise_for_status()
        session = create.json()
        sid = session["session_id"]
        print(f"[OK] Session created: {sid}")
        print(f"  Patient: {session['patient_name']}")
        print(f"  Chief complaint: {session['chief_complaint']}")

        # Start
        start = await client.post(f"{BASE}/v1/sessions/{sid}/start")
        start.raise_for_status()
        print("[OK] Session started")

        # Dialogue — greet in Marathi
        dialogue = await client.post(
            f"{BASE}/v1/sessions/{sid}/dialogue",
            json={"utterance": "Namaste, tumhi kasa aahet? Chest madhe kahi problem aahe ka?"},
        )
        dialogue.raise_for_status()
        patient = dialogue.json()["patient"]
        print(f"[OK] Patient: {patient['utterance']}")

        # Clinical actions
        actions = [
            "measure_vitals",
            "order_ecg_within_10min",
            "give_aspirin_325_chewed",
            "order_troponin",
            "arrange_pci_transfer",
        ]
        for action in actions:
            resp = await client.post(
                f"{BASE}/v1/sessions/{sid}/actions",
                json={"action": action},
            )
            resp.raise_for_status()
            result = resp.json()["result"]
            print(f"[OK] Action [{action}]: {result.get('observed', result)}")

        # Complete & score
        complete = await client.post(f"{BASE}/v1/sessions/{sid}/complete")
        complete.raise_for_status()
        report = complete.json()
        rubric = report["rubric"]
        print("\n=== SESSION SCORE ===")
        print(f"  Overall: {rubric['clinical_reasoning'] * 0.3 + rubric['procedural_correctness'] * 0.2 + rubric['communication'] * 0.25 + rubric['ethical_legal'] * 0.1 + rubric['stress_modulated'] * 0.15:.2f}")
        print(f"  Clinical reasoning: {rubric['clinical_reasoning']}")
        print(f"  Procedural: {rubric['procedural_correctness']}")
        print(f"  Communication: {rubric['communication']}")
        print(f"  Critical hit: {report['critical_actions_hit']}")
        print(f"  Critical missed: {report['critical_actions_missed']}")
        print(f"  Competency updates: {len(report['competency_updates'])} tags")


if __name__ == "__main__":
    try:
        asyncio.run(run_demo())
    except httpx.ConnectError:
        print("ERROR: Pratibimb not running. Start with:")
        print("  uvicorn services.pratibimb.app.main:app --port 8100 --reload")
        sys.exit(1)
