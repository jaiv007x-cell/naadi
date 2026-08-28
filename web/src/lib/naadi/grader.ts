import type { CaseBlueprint, GradeResult, Session } from "./types";

export const NIRIKSHAK_VERSION = "0.3.0";
export const PHYSIO_VERSION = "0.2.0";

const AXIS_WEIGHT: Record<string, number> = {
  diagnosis: 0.3,
  procedure: 0.25,
  timing: 0.15,
  safety: 0.2,
  communication: 0.1,
};

function fnv1a(text: string) {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0).toString(16).padStart(8, "0");
}

function letterFor(overall: number): GradeResult["letter"] {
  if (overall >= 0.9) return "A";
  if (overall >= 0.8) return "B";
  if (overall >= 0.7) return "C";
  if (overall >= 0.6) return "D";
  return "F";
}

function errorDna(session: Session, requiredOrder: string[]): GradeResult["errorDna"] {
  const ids = session.actions.map((a) => a.orderId);
  const seqHits = requiredOrder.filter((id, i) => {
    const at = ids.indexOf(id);
    if (at < 0) return false;
    const prev = requiredOrder.slice(0, i).every((p) => {
      const pi = ids.indexOf(p);
      return pi >= 0 && pi <= at;
    });
    return prev;
  });
  const firstT = session.actions[0]?.tMin ?? session.tMin;
  return {
    recognition_delay: session.actions.length === 0 ? 0 : Math.max(0, Math.min(1, 1 - firstT / 15)),
    action_sequencing: requiredOrder.length ? seqHits.length / requiredOrder.length : 1,
    dose_accuracy: ids.includes("aspirin") || ids.includes("magnesium") || ids.includes("nebulized_salbutamol") ? 1 : 0.5,
    escalation_timing: ids.includes("pci_transfer") || ids.includes("referral_higher") ? 1 : 0.35,
    recovery_velocity: session.endReason === "coded" ? 0.15 : session.endReason === "abandoned" ? 0.3 : 0.85,
    communication_score: ids.includes("greet_native") || ids.includes("consent") ? 0.85 : 0.5,
  };
}

export function gradeSession(session: Session, bp: CaseBlueprint): GradeResult {
  const critical: string[] = [];
  let score = 0;
  let max = 0;

  const hits = bp.rubric.map((r) => {
    const placed = r.orderId ? session.actions.some((a) => a.orderId === r.orderId) : Boolean(r.flag && session.flags[r.flag]);
    const inWindow =
      !r.windowMin ||
      session.actions.some(
        (a) => a.orderId === r.orderId && a.tMin >= r.windowMin![0] && a.tMin <= r.windowMin![1],
      );

    // Nirikshak: failCaseOnHit = drug_not_given matcher (withholding is the hit).
    const matched = r.failCaseOnHit ? !placed : placed && (!r.windowMin || inWindow);
    const violation = Boolean(r.failCaseOnMiss && !matched) || Boolean(r.failCaseOnHit && placed);
    if (violation) critical.push(r.id);

    max += r.points;
    const awarded = matched ? r.points : 0;
    score += awarded;

    return {
      id: r.id,
      label: r.label,
      axis: r.axis,
      points: r.points,
      awarded,
      matched,
      required: r.required,
      fail: violation,
      forbidden: Boolean(r.failCaseOnHit),
    };
  });

  const axes = ["diagnosis", "procedure", "timing", "safety", "communication"] as const;
  let overall = 0;
  for (const axis of axes) {
    const group = hits.filter((h) => h.axis === axis);
    const earned = group.reduce((a, h) => a + h.awarded, 0);
    const axMax = group.reduce((a, h) => a + h.points, 0);
    const normalized = axMax === 0 ? 1 : earned / axMax;
    overall += normalized * (AXIS_WEIGHT[axis] ?? 0);
  }

  const failClosed = critical.length > 0;
  const failed = failClosed || overall < 0.7;
  const pct = Math.round(overall * 100);
  const letter = letterFor(overall);
  const dna = errorDna(
    session,
    bp.rubric.filter((r) => r.required && r.orderId && !r.failCaseOnHit).map((r) => r.orderId!),
  );

  const byComp = bp.competencies.map((id, i) => {
    const related = hits.filter((_, idx) => idx % bp.competencies.length === i);
    const use = related.length ? related : hits;
    const s = use.reduce((a, h) => a + (h.matched && !h.fail ? 1 : 0), 0);
    return { id, score: Math.round((s / Math.max(use.length, 1)) * 100) };
  });

  const manifest = fnv1a(
    JSON.stringify({
      nirikshak: NIRIKSHAK_VERSION,
      physio: PHYSIO_VERSION,
      case: bp.version,
      content: bp.contentHash ?? "",
      hits: hits.map((h) => [h.id, h.matched, h.awarded]),
      tMin: session.tMin,
      end: session.endReason,
    }),
  );

  return {
    score,
    max,
    pct,
    letter,
    failClosed,
    passed: !failed,
    overall,
    graderVersion: NIRIKSHAK_VERSION,
    physioVersion: PHYSIO_VERSION,
    evaluationManifestHash: manifest,
    errorDna: dna,
    critical,
    hits,
    crs: failClosed ? Math.min(42, pct) : Math.round(Math.max(0, Math.min(100, overall * 100))),
    competencies: byComp,
  };
}
