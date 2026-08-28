import { CASE_COMP_MAP, COMPETENCIES, HOSPITALS, type CompetencyId } from "./catalog";
import type { Rotation, Session } from "./types";

export type NodeScore = {
  id: CompetencyId;
  label: string;
  domain: string;
  mastery: number;
  evidence: number;
  lastAt: number | null;
  decayed: boolean;
};

const TAU_DAYS: Record<string, number> = {
  cardiac: 180,
  obstetric: 180,
  paeds: 180,
  safety: 240,
  communication: 300,
  procedure: 120,
};

export function competencyNodes(sessions: Session[], rotations: Rotation[]): NodeScore[] {
  const now = Date.now();
  const buckets = new Map<CompetencyId, { scores: number[]; at: number[] }>();

  for (const c of COMPETENCIES) buckets.set(c.id, { scores: [], at: [] });

  for (const s of sessions) {
    if (!s.grade) continue;
    const ids = CASE_COMP_MAP[s.caseId] ?? [];
    ids.forEach((id, i) => {
      const mapped = s.grade?.competencies[i];
      const raw = mapped ? mapped.score : s.grade!.failClosed ? 30 : s.grade!.crs;
      const b = buckets.get(id);
      if (!b) return;
      b.scores.push(s.grade!.failClosed ? Math.min(raw, 42) : raw);
      b.at.push(s.startedAt);
    });
    if (s.grade.failClosed) {
      const safety = buckets.get("avoid-harm");
      if (safety) {
        safety.scores.push(20);
        safety.at.push(s.startedAt);
      }
    }
  }

  for (const r of rotations) {
    const passed = r.steps.filter((x) => x.result === "pass").length;
    const failed = r.steps.filter((x) => x.result === "fail").length;
    const pct = r.steps.length === 0 ? 0 : Math.round((passed / r.steps.length) * 100);
    const score = failed > 0 ? Math.min(pct, 55) : pct;
    for (const id of r.competencies as CompetencyId[]) {
      const b = buckets.get(id);
      if (!b) continue;
      b.scores.push(score);
      b.at.push(r.at);
    }
  }

  return COMPETENCIES.map((c) => {
    const b = buckets.get(c.id)!;
    if (b.scores.length === 0) {
      return { id: c.id, label: c.label, domain: c.domain, mastery: 0, evidence: 0, lastAt: null, decayed: false };
    }
    const lastAt = Math.max(...b.at);
    const mean = b.scores.reduce((a, n) => a + n, 0) / b.scores.length;
    const tau = TAU_DAYS[c.domain] ?? 180;
    const days = (now - lastAt) / 86400000;
    const decay = 1 - Math.exp(-days / tau);
    const mastery = Math.round(Math.max(0, mean * (1 - decay * 0.8)));
    return {
      id: c.id,
      label: c.label,
      domain: c.domain,
      mastery,
      evidence: b.scores.length,
      lastAt,
      decayed: decay > 0.05,
    };
  });
}

export function overallCrs(nodes: NodeScore[]) {
  const withEv = nodes.filter((n) => n.evidence > 0);
  if (withEv.length === 0) return 0;
  return Math.round(withEv.reduce((a, n) => a + n.mastery, 0) / withEv.length);
}

export function beemaSignals(sessions: Session[], rotations: Rotation[]) {
  const critical = sessions.flatMap((s) =>
    (s.grade?.critical ?? []).map((id) => ({
      sessionId: s.id,
      caseId: s.caseId,
      id,
      at: s.startedAt,
      kind: "critical" as const,
    })),
  );
  const coded = sessions.filter((s) => s.endReason === "coded").length;
  const floorFails = rotations.flatMap((r) =>
    r.steps.filter((st) => st.result === "fail").map((st) => ({
      sessionId: r.id,
      caseId: r.procedureId,
      id: st.id,
      at: r.at,
      kind: "floor" as const,
    })),
  );
  return {
    critical,
    floorFails,
    coded,
    failClosed: sessions.filter((s) => s.grade?.failClosed).length,
    note: "BEEMA v0.1 composes views over evidence. It does not underwrite, hire, or fire.",
  };
}

export function matchHospitals(nodes: NodeScore[], crs: number) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  return HOSPITALS.map((h) => {
    const parts = h.need.map((id) => byId.get(id)?.mastery ?? 0);
    const needMean = parts.length ? parts.reduce((a, n) => a + n, 0) / parts.length : 0;
    const gate = crs >= h.minCrs ? 1 : crs / h.minCrs;
    const score = Math.round(needMean * 0.75 + gate * 100 * 0.25);
    return { ...h, score: Math.min(100, score), gated: crs < h.minCrs };
  }).sort((a, b) => b.score - a.score);
}

export function nextActions(nodes: NodeScore[]) {
  const weak = [...nodes].filter((n) => n.evidence === 0 || n.mastery < 70).sort((a, b) => a.mastery - b.mastery);
  return weak.slice(0, 4);
}

export function fingerprint(session: Session) {
  const raw = `${session.id}|${session.caseId}|${session.events.map((e) => e.id).join(",")}|${session.grade?.crs ?? 0}`;
  let h = 2166136261;
  for (let i = 0; i < raw.length; i++) {
    h ^= raw.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return `NAADI-${(h >>> 0).toString(16).padStart(8, "0")}`;
}
