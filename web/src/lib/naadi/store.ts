import { create } from "zustand";
import { persist } from "zustand/middleware";
import { uid } from "@/lib/utils";
import { getCase } from "./cases";
import { applyOrder, detectEnd, tickPhysio } from "./physio";
import { gradeSession } from "./grader";
import { inferOrdersFromUtterance } from "./scripted";
import { orderToPratibimbAction } from "./action-map";
import { completePratibimbSession, pratibimbAction } from "./pratibimb";
import type {
  CaseBlueprint,
  CaseId,
  ChatMessage,
  GuruTurn,
  LoggedAction,
  Rotation,
  Session,
  TraceEvent,
  Vitals,
} from "./types";

type NaadiStore = {
  sessions: Record<string, Session>;
  customCases: CaseBlueprint[];
  rotations: Rotation[];
  guruLog: GuruTurn[];
  activeId: string | null;
  start: (caseId: CaseId) => string;
  enterWard: (sessionId: string) => void;
  placeOrder: (sessionId: string, orderId: string) => void;
  postStudent: (sessionId: string, text: string) => void;
  postPatient: (sessionId: string, text: string) => void;
  tick: (sessionId: string, dtMin?: number) => void;
  endNow: (sessionId: string) => void;
  saveCase: (bp: CaseBlueprint) => void;
  deleteCase: (id: string) => void;
  logRotation: (r: Omit<Rotation, "id" | "at">) => void;
  pushGuru: (turn: Omit<GuruTurn, "id" | "at">) => void;
  attachPratibimb: (sessionId: string, pratibimbSessionId: string) => void;
  syncBackendVitals: (sessionId: string, vitals: Partial<Vitals>) => void;
};

function mapBackendVitals(v: {
  hr: number;
  sbp: number;
  dbp: number;
  spo2: number;
  rr: number;
  temp_c?: number;
  pain?: number;
}): Partial<Vitals> {
  return {
    hr: v.hr,
    sbp: v.sbp,
    dbp: v.dbp,
    spo2: v.spo2,
    rr: v.rr,
    tempC: v.temp_c ?? 37,
    pain: v.pain ?? 0,
  };
}

function ev(tMin: number, kind: TraceEvent["kind"], label: string, detail?: string): TraceEvent {
  return { id: uid("ev"), tMin, kind, label, detail };
}

function blueprintOf(get: () => NaadiStore, caseId: string) {
  return getCase(caseId, get().customCases);
}

function finalize(session: Session, bp: CaseBlueprint): Session {
  const reason = session.endReason ?? detectEnd(session, bp) ?? "abandoned";
  const ended: Session = {
    ...session,
    status: "ended",
    endReason: reason,
    events: [...session.events, ev(session.tMin, "end", reason, "Session closed. Evidence frozen.")],
  };
  ended.grade = gradeSession(ended, bp);
  return ended;
}

export const useNaadi = create<NaadiStore>()(
  persist(
    (set, get) => ({
      sessions: {},
      customCases: [],
      rotations: [],
      guruLog: [],
      activeId: null,
      start: (caseId) => {
        const bp = blueprintOf(get, caseId);
        if (!bp) throw new Error("Unknown case");
        const id = uid("sess");
        const session: Session = {
          id,
          caseId,
          startedAt: Date.now(),
          tMin: 0,
          status: "briefing",
          vitals: { ...bp.initialVitals },
          flags: {},
          chat: [],
          actions: [],
          events: [ev(0, "flag", "Briefing issued", bp.version)],
          findingsSeen: [],
          pratibimbSessionId: null,
        };
        set((s) => ({ sessions: { ...s.sessions, [id]: session }, activeId: id }));
        return id;
      },
      enterWard: (sessionId) => {
        const session = get().sessions[sessionId];
        const bp = session && blueprintOf(get, session.caseId);
        if (!session || !bp || session.status !== "briefing") return;
        const opening: ChatMessage = {
          id: uid("m"),
          role: "patient",
          text: bp.persona.openingLine,
          tMin: 0,
          at: Date.now(),
        };
        set((s) => ({
          sessions: {
            ...s.sessions,
            [sessionId]: {
              ...session,
              status: "running",
              chat: [opening],
              events: [...session.events, ev(0, "chat", "Patient speaks")],
            },
          },
        }));
      },
      placeOrder: (sessionId, orderId) => {
        const session = get().sessions[sessionId];
        const bp = session && blueprintOf(get, session.caseId);
        if (!session || !bp || session.status !== "running") return;
        const def = bp.orders.find((o) => o.id === orderId);
        if (!def) return;
        if (session.actions.some((a) => a.orderId === orderId)) return;

        const action: LoggedAction = {
          id: uid("act"),
          orderId,
          label: def.label,
          tMin: session.tMin,
          at: Date.now(),
        };
        let next: Session = {
          ...session,
          tMin: session.tMin + def.minutes,
          actions: [...session.actions, action],
          events: [...session.events, ev(session.tMin, "order", def.label, `t=${session.tMin.toFixed(0)} min`)],
        };
        next = applyOrder(next, bp, orderId);

        const finding = bp.findings[orderId];
        if (finding) {
          next.chat = [
            ...next.chat,
            { id: uid("m"), role: "system", text: finding, tMin: next.tMin, at: Date.now() },
          ];
        }

        const end = detectEnd(next, bp);
        if (end) next = finalize({ ...next, endReason: end }, bp);
        set((s) => ({ sessions: { ...s.sessions, [sessionId]: next } }));

        const pbId = session.pratibimbSessionId;
        if (pbId) {
          const mapped = orderToPratibimbAction(bp.id, orderId);
          if (mapped) {
            void pratibimbAction(pbId, mapped)
              .then((r) => {
                if (r.vitals) get().syncBackendVitals(sessionId, mapBackendVitals(r.vitals));
              })
              .catch(() => undefined);
          }
        }
      },
      postStudent: (sessionId, text) => {
        const session = get().sessions[sessionId];
        const bp = session && blueprintOf(get, session.caseId);
        if (!session || !bp || session.status !== "running") return;
        const trimmed = text.trim();
        if (!trimmed) return;

        const student: ChatMessage = {
          id: uid("m"),
          role: "student",
          text: trimmed,
          tMin: session.tMin,
          at: Date.now(),
        };
        let next: Session = {
          ...session,
          tMin: session.tMin + 1,
          chat: [...session.chat, student],
        };

        for (const oid of inferOrdersFromUtterance(bp.id, trimmed)) {
          if (!next.actions.some((a) => a.orderId === oid)) {
            const def = bp.orders.find((o) => o.id === oid);
            if (def) {
              next.actions = [
                ...next.actions,
                { id: uid("act"), orderId: oid, label: def.label, tMin: next.tMin, at: Date.now() },
              ];
              next.events = [...next.events, ev(next.tMin, "order", `${def.label} (from speech)`)];
              next = applyOrder(next, bp, oid);
            }
          }
        }

        const end = detectEnd(next, bp);
        if (end) next = finalize({ ...next, endReason: end }, bp);
        set((s) => ({ sessions: { ...s.sessions, [sessionId]: next } }));
      },
      postPatient: (sessionId, text) => {
        const session = get().sessions[sessionId];
        const bp = session && blueprintOf(get, session.caseId);
        if (!session || !bp || session.status !== "running") return;
        const reply: ChatMessage = {
          id: uid("m"),
          role: "patient",
          text,
          tMin: session.tMin,
          at: Date.now(),
        };
        let after: Session = {
          ...session,
          chat: [...session.chat, reply],
          events: [...session.events, ev(session.tMin, "chat", "Patient replies")],
        };
        const end = detectEnd(after, bp);
        if (end) after = finalize({ ...after, endReason: end }, bp);
        set((s) => ({ sessions: { ...s.sessions, [sessionId]: after } }));
      },
      tick: (sessionId, dtMin = 0.25) => {
        const session = get().sessions[sessionId];
        const bp = session && blueprintOf(get, session.caseId);
        if (!session || !bp || session.status !== "running") return;
        let next = tickPhysio(session, bp, dtMin);
        const end = detectEnd(next, bp);
        if (end) next = finalize({ ...next, endReason: end }, bp);
        set((s) => ({ sessions: { ...s.sessions, [sessionId]: next } }));
      },
      endNow: (sessionId) => {
        const session = get().sessions[sessionId];
        const bp = session && blueprintOf(get, session.caseId);
        if (!session || !bp || session.status === "ended") return;
        if (session.pratibimbSessionId) {
          void completePratibimbSession(session.pratibimbSessionId).catch(() => undefined);
        }
        const next = finalize({ ...session, endReason: session.endReason ?? "abandoned" }, bp);
        set((s) => ({ sessions: { ...s.sessions, [sessionId]: next } }));
      },
      saveCase: (bp) => {
        set((s) => ({
          customCases: [...s.customCases.filter((c) => c.id !== bp.id), bp],
        }));
      },
      deleteCase: (id) => {
        set((s) => ({ customCases: s.customCases.filter((c) => c.id !== id) }));
      },
      logRotation: (r) => {
        const row: Rotation = { ...r, id: uid("rot"), at: Date.now() };
        set((s) => ({ rotations: [row, ...s.rotations] }));
      },
      pushGuru: (turn) => {
        const row: GuruTurn = { ...turn, id: uid("g"), at: Date.now() };
        set((s) => ({ guruLog: [...s.guruLog, row].slice(-40) }));
      },
      attachPratibimb: (sessionId, pratibimbSessionId) => {
        const session = get().sessions[sessionId];
        if (!session) return;
        set((s) => ({
          sessions: {
            ...s.sessions,
            [sessionId]: { ...session, pratibimbSessionId },
          },
        }));
      },
      syncBackendVitals: (sessionId, vitals) => {
        const session = get().sessions[sessionId];
        if (!session || session.status !== "running") return;
        set((s) => ({
          sessions: {
            ...s.sessions,
            [sessionId]: {
              ...session,
              vitals: { ...session.vitals, ...vitals },
            },
          },
        }));
      },
    }),
    {
      name: "naadi-ledger-v1",
      merge: (persisted, current) => {
        const p = (persisted ?? {}) as Partial<NaadiStore>;
        return {
          ...current,
          ...p,
          sessions: p.sessions ?? current.sessions,
          customCases: p.customCases ?? [],
          rotations: p.rotations ?? [],
          guruLog: p.guruLog ?? [],
        };
      },
    },
  ),
);
