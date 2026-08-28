import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { Activity, Clock, Send } from "lucide-react";
import { Shell } from "@/components/layout/shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { EcgTrace } from "@/components/sim/ecg";
import { caseSupportsBackend, getCase, isCaseId, dialogueSpeaker } from "@/lib/naadi/cases";
import { useNaadi } from "@/lib/naadi/store";
import { scriptedReply } from "@/lib/naadi/scripted";
import {
  createPratibimbSession,
  pratibimbDialogue,
  startPratibimbSession,
} from "@/lib/naadi/pratibimb";
import { usePratibimbHealth } from "@/lib/naadi/use-pratibimb";
import { speakAsPatient } from "@/lib/ai/patient";
import { useHydrated } from "@/lib/use-hydrated";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/sim/$caseId")({ component: SimPage });

function SimPage() {
  const { caseId } = Route.useParams();
  const extra = useNaadi((s) => s.customCases);
  const bp = getCase(caseId, extra);
  const hydrated = useHydrated();
  const navigate = useNavigate();
  const start = useNaadi((s) => s.start);
  const enterWard = useNaadi((s) => s.enterWard);
  const placeOrder = useNaadi((s) => s.placeOrder);
  const postStudent = useNaadi((s) => s.postStudent);
  const postPatient = useNaadi((s) => s.postPatient);
  const endNow = useNaadi((s) => s.endNow);
  const sessions = useNaadi((s) => s.sessions);
  const attachPratibimb = useNaadi((s) => s.attachPratibimb);
  const syncBackendVitals = useNaadi((s) => s.syncBackendVitals);
  const pratibimbHealth = usePratibimbHealth();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);

  const session = sessionId ? sessions[sessionId] : undefined;

  useEffect(() => {
    if (!hydrated || !bp) return;
    const existing = Object.values(useNaadi.getState().sessions).find(
      (s) => s.caseId === bp.id && s.status !== "ended",
    );
    if (existing) {
      setSessionId(existing.id);
      return;
    }
    setSessionId(start(bp.id));
  }, [hydrated, bp?.id, start]);

  useEffect(() => {
    if (!sessionId) return;
    const handle = window.setInterval(() => {
      const current = useNaadi.getState().sessions[sessionId];
      if (current?.status === "running") useNaadi.getState().tick(sessionId, 0.2);
    }, 2500);
    return () => window.clearInterval(handle);
  }, [sessionId]);

  useEffect(() => {
    if (session?.status === "ended") {
      void navigate({ to: "/debrief/$sessionId", params: { sessionId: session.id } });
    }
  }, [session?.status, session?.id, navigate]);

  const groups = useMemo(() => {
    if (!bp) return [];
    const map = new Map<string, typeof bp.orders>();
    for (const o of bp.orders) {
      const list = map.get(o.group) ?? [];
      list.push(o);
      map.set(o.group, list);
    }
    return [...map.entries()];
  }, [bp]);

  if (!bp || !isCaseId(caseId, extra)) {
    return (
      <Shell>
        <p className="text-muted">Unknown case.</p>
        <Button className="mt-4" asChild>
          <Link to="/cases">Back to cases</Link>
        </Button>
      </Shell>
    );
  }

  if (!hydrated || !session) {
    return (
      <Shell>
        <p className="text-muted">Opening casualty…</p>
      </Shell>
    );
  }

  const shock =
    session.vitals.sbp < 80 ||
    session.vitals.spo2 < 88 ||
    Boolean(session.flags.nitro_rv) ||
    Boolean(session.flags.hypothermia_misread && !session.flags.abx);

  const chatLabel = dialogueSpeaker(bp);

  if (session.status === "briefing") {
    return (
      <Shell>
        <p className="text-xs font-medium uppercase tracking-[0.22em] text-accent">
          Public briefing
        </p>
        <h1 className="mt-2 font-display text-4xl tracking-tight">{bp.persona.name}</h1>
        {bp.persona.informant ? (
          <p className="mt-1 text-sm text-accent">
            Speaking with {bp.persona.informant.name} ({bp.persona.informant.relation}) — proxy history
          </p>
        ) : null}
        <p className="mt-1 text-muted">
          {bp.title} · {bp.persona.city} · {bp.durationMin} min clock
        </p>
        <article className="mt-8 max-w-2xl rounded-xl border border-border bg-surface p-6">
          <p className="text-sm leading-relaxed text-fg">{bp.briefing}</p>
          <p className="mt-4 text-sm leading-relaxed text-muted">
            Hidden diagnosis is withheld. Critical actions are not listed. Talk to the
            patient in their language. Orders on the tray are the only things Nirikshak
            will grade as hard evidence — speech can still count as greeting, history, or
            consent.
          </p>
          <div className="mt-6 flex flex-wrap gap-3">
            <Button
              onClick={() => {
                void enterCasualty(session.id);
              }}
            >
              Enter casualty
            </Button>
            <Button variant="ghost" asChild>
              <Link to="/cases">Cancel</Link>
            </Button>
          </div>
        </article>
      </Shell>
    );
  }

  async function enterCasualty(sid: string) {
    if (!bp) return;
    enterWard(sid);
    if (!pratibimbHealth.ok || !caseSupportsBackend(bp.id)) return;
    try {
      const created = await createPratibimbSession();
      const started = await startPratibimbSession(created.session_id);
      attachPratibimb(sid, started.session_id);
      if (started.vitals) {
        syncBackendVitals(sid, {
          hr: started.vitals.hr,
          sbp: started.vitals.sbp,
          dbp: started.vitals.dbp,
          spo2: started.vitals.spo2,
          rr: started.vitals.rr,
          tempC: started.vitals.temp_c ?? bp.initialVitals.tempC,
          pain: started.vitals.pain ?? bp.initialVitals.pain,
        });
      }
    } catch {
      /* local physio continues */
    }
  }

  async function onSend() {
    if (!session || !bp || !draft.trim()) return;
    setBusy(true);
    const text = draft;
    setDraft("");
    postStudent(session.id, text);
    try {
      if (session.pratibimbSessionId) {
        const dlg = await pratibimbDialogue(session.pratibimbSessionId, text);
        postPatient(session.id, dlg.patient.utterance);
        syncBackendVitals(session.id, {
          hr: dlg.vitals.hr,
          sbp: dlg.vitals.sbp,
          dbp: dlg.vitals.dbp,
          spo2: dlg.vitals.spo2,
          rr: dlg.vitals.rr,
          tempC: dlg.vitals.temp_c ?? session.vitals.tempC,
          pain: dlg.vitals.pain ?? session.vitals.pain,
        });
        return;
      }
      const history = [...session.chat, { role: "student", text }].slice(-8).map(
        (m) => `${m.role}: ${m.text}`,
      );
      const ai = await speakAsPatient({
        data: {
          caseId: bp.id,
          persona: `${bp.persona.name}, ${bp.persona.age}, ${bp.persona.city}. ${bp.persona.voice}`,
          hidden: bp.hidden,
          opening: bp.persona.openingLine,
          vitals: session.vitals,
          tMin: session.tMin,
          history,
          userText: text,
        },
      });
      postPatient(session.id, ai.ok ? ai.text : scriptedReply(bp.id, text));
    } catch {
      postPatient(session.id, scriptedReply(bp.id, text));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell dense>
      <div className="mx-auto grid max-w-[1400px] gap-0 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0 border-b border-border lg:border-b-0 lg:border-r">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
            <div>
              <div className="flex items-center gap-2">
                <h1 className="font-display text-xl">{bp.persona.name}</h1>
                <Badge variant={shock ? "danger" : "accent"}>{bp.persona.city}</Badge>
                {session.pratibimbSessionId ? (
                  <Badge variant="accent">Pratibimb :8100</Badge>
                ) : pratibimbHealth.ok && caseSupportsBackend(bp.id) ? (
                  <Badge>engine online</Badge>
                ) : null}
              </div>
              <p className="text-xs text-muted">
                {bp.persona.age} · {bp.persona.occupation} · {bp.persona.language}
              </p>
            </div>
            <div className="flex items-center gap-4 font-mono text-sm tabular-nums">
              <span className="inline-flex items-center gap-1.5 text-muted">
                <Clock className="size-3.5" />
                {session.tMin.toFixed(0)}′ / {bp.durationMin}′
              </span>
              <Button size="sm" variant="outline" onClick={() => endNow(session.id)}>
                Close & grade
              </Button>
            </div>
          </div>

          <div className={cn("border-b border-border bg-surface px-4 py-3", shock && "bg-danger/10")}>
            <div className="mb-2 flex items-center justify-between text-xs uppercase tracking-[0.16em] text-subtle">
              <span className="inline-flex items-center gap-1.5">
                <Activity className="size-3.5 text-monitor" /> Monitor
              </span>
              <span className={shock ? "text-danger" : "text-monitor"}>
                {shock ? "Unstable" : "Live"}
              </span>
            </div>
            <EcgTrace hr={session.vitals.hr} alarmed={shock} />
            <div className="mt-2 grid grid-cols-3 gap-2 sm:grid-cols-6">
              <Vital label="HR" value={session.vitals.hr} unit="/min" warn={session.vitals.hr > 110} />
              <Vital
                label="BP"
                value={`${session.vitals.sbp}/${session.vitals.dbp}`}
                unit=""
                warn={session.vitals.sbp < 90 || session.vitals.sbp > 160}
              />
              <Vital label="SpO2" value={session.vitals.spo2} unit="%" warn={session.vitals.spo2 < 94} />
              <Vital label="RR" value={session.vitals.rr} unit="/min" warn={session.vitals.rr > 24} />
              <Vital label="Pain" value={session.vitals.pain} unit="/10" warn={session.vitals.pain >= 7} />
              <Vital label="Temp" value={session.vitals.tempC.toFixed(1)} unit="°C" />
            </div>
          </div>

          <div className="flex h-[min(52vh,420px)] flex-col">
            <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
              {session.chat.map((m) => (
                <div
                  key={m.id}
                  className={cn(
                    "max-w-[42rem] rounded-lg px-3 py-2 text-sm leading-relaxed",
                    m.role === "patient" && "bg-surface-2 text-fg",
                    m.role === "student" && "ml-auto bg-accent/15 text-fg",
                    m.role === "system" && "border border-border font-mono text-xs text-monitor",
                  )}
                >
                  <p className="mb-1 text-[10px] uppercase tracking-[0.16em] text-subtle">
                    {m.role === "patient"
                      ? chatLabel
                      : m.role === "student"
                        ? "You"
                        : "Finding"}
                  </p>
                  {m.text}
                </div>
              ))}
            </div>
            <form
              className="flex gap-2 border-t border-border p-3"
              onSubmit={(e) => {
                e.preventDefault();
                void onSend();
              }}
            >
              <Input
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="Speak to the patient — Hindi / Marathi / Telugu / English"
                disabled={busy}
              />
              <Button type="submit" size="icon" disabled={busy || !draft.trim()} aria-label="Send">
                <Send />
              </Button>
            </form>
          </div>
        </div>

        <aside className="max-h-[calc(100dvh-3.5rem)] overflow-y-auto bg-surface px-4 py-4">
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-subtle">Order tray</p>
          <p className="mt-1 text-xs text-muted">Each order advances the clock and writes the ledger.</p>
          <div className="mt-4 space-y-5">
            {groups.map(([group, orders]) => (
              <div key={group}>
                <p className="mb-2 text-xs uppercase tracking-[0.16em] text-muted">{group}</p>
                <div className="flex flex-col gap-2">
                  {orders.map((o) => {
                    const done = session.actions.some((a) => a.orderId === o.id);
                    return (
                      <Button
                        key={o.id}
                        variant={done ? "secondary" : "outline"}
                        size="sm"
                        className="h-11 justify-between"
                        disabled={done}
                        onClick={() => placeOrder(session.id, o.id)}
                      >
                        <span>{o.label}</span>
                        <span className="font-mono text-[10px] text-subtle">+{o.minutes}′</span>
                      </Button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </aside>
      </div>
    </Shell>
  );
}

function Vital({
  label,
  value,
  unit,
  warn,
}: {
  label: string;
  value: string | number;
  unit: string;
  warn?: boolean;
}) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-[0.16em] text-subtle">{label}</p>
      <p className={cn("font-mono text-lg tabular-nums", warn ? "text-danger" : "text-fg")}>
        {value}
        {unit ? <span className="ml-0.5 text-xs text-subtle">{unit}</span> : null}
      </p>
    </div>
  );
}
