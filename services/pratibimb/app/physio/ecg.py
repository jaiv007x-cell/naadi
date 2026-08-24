from __future__ import annotations

"""Synthetic ECG waveform metadata for STEMI cases."""

import numpy as np


def generate_stemi_inferior_leads(duration_s: float = 10.0, sample_rate: int = 500) -> dict:
    """
    Generate simplified 12-lead ECG metadata + lead II snippet.
    Returns metadata for frontend rendering; not diagnostic-grade.
    """
    n = int(duration_s * sample_rate)
    t = np.linspace(0, duration_s, n)

    # Simplified QRS + ST elevation pattern for inferior STEMI
    hr = 104
    rr_interval = 60.0 / hr
    beat_phase = (t % rr_interval) / rr_interval

    baseline = np.zeros(n)
    qrs_mask = beat_phase < 0.12
    baseline[qrs_mask] = 0.8 * np.sin(beat_phase[qrs_mask] * np.pi / 0.12)

    # ST elevation ~2mm in inferior leads
    st_elevation = 0.2
    baseline += st_elevation

    return {
        "rhythm": "sinus_tachycardia",
        "rate_bpm": hr,
        "st_elevation_leads": ["II", "III", "aVF"],
        "reciprocal_leads": ["I", "aVL"],
        "st_elevation_mm": 2.0,
        "lead_ii_snippet": baseline[::10].tolist()[:200],
        "interpretation": "ST elevation in II, III, aVF consistent with inferior STEMI",
    }
