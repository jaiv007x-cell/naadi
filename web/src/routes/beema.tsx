import { createFileRoute, Link } from "@tanstack/react-router";
import { Shell, ModuleKicker } from "@/components/layout/shell";
import { Badge } from "@/components/ui/badge";
import { beemaSignals } from "@/lib/naadi/graph";
import { getCase } from "@/lib/naadi/cases";
import { useNaadi } from "@/lib/naadi/store";
import { useHydrated } from "@/lib/use-hydrated";

export const Route = createFileRoute("/beema")({ component: BeemaPage });

function BeemaPage() {
  const hydrated = useHydrated();
  const extra = useNaadi((s) => s.customCases);
  const sessions = useNaadi((s) => Object.values(s.sessions));
  const rotations = useNaadi((s) => s.rotations);
  const sig = beemaSignals(sessions, rotations);
  const latestDna = sessions
    .filter((s) => s.grade?.errorDna)
    .sort((a, b) => b.startedAt - a.startedAt)[0]?.grade?.errorDna;

  return (
    <Shell>
      <ModuleKicker
        kicker="BEEMA v0.1"
        title="Safety intelligence"
        lede="Reads the ledger. Does not write judgments upstream. No employment decisioning, no monetary pricing, no individual underwriting."
      />
      <div className="mt-8 grid gap-4 sm:grid-cols-3">
        <Stat label="Fail-closed sessions" value={hydrated ? String(sig.failClosed) : "—"} />
        <Stat label="Coded endings" value={hydrated ? String(sig.coded) : "—"} />
        <Stat label="Floor-step fails" value={hydrated ? String(sig.floorFails.length) : "—"} />
      </div>
      <p className="mt-6 max-w-2xl text-sm text-muted">{sig.note}</p>
      <section className="mt-8">
        <h2 className="font-display text-xl">Performance pattern vector</h2>
        <p className="mt-1 text-sm text-muted">
          Six dimensions from the Python Error-DNA module. 1.0 is ideal. BEEMA composes this view; it does not price it.
        </p>
        {hydrated && latestDna ? (
          <ul className="mt-4 grid gap-3 sm:grid-cols-2">
            {Object.entries(latestDna).map(([k, v]) => (
              <li key={k} className="rounded-xl border border-border bg-surface px-4 py-3">
                <p className="text-xs uppercase tracking-[0.14em] text-subtle">{k.replaceAll("_", " ")}</p>
                <p className="mt-1 font-mono text-2xl tabular-nums">{Number(v).toFixed(2)}</p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-3 text-muted">Run a case to freeze a vector.</p>
        )}
      </section>
      <section className="mt-8">
        <h2 className="font-display text-xl">Error-DNA events</h2>
        {!hydrated ? (
          <p className="mt-3 text-muted">Loading…</p>
        ) : sig.critical.length === 0 && sig.floorFails.length === 0 ? (
          <p className="mt-3 text-muted">No critical flags yet. Run a case or a Drishti rotation.</p>
        ) : (
          <ul className="mt-4 space-y-3">
            {sig.critical.map((c) => (
              <li key={`${c.sessionId}-${c.id}`} className="rounded-xl border border-border bg-surface px-4 py-3">
                <Badge variant="danger">critical</Badge>
                <p className="mt-2 text-sm">
                  {getCase(c.caseId, extra)?.persona.name ?? c.caseId} · {c.id}
                </p>
                <Link
                  className="mt-1 inline-block text-xs text-accent"
                  to="/debrief/$sessionId"
                  params={{ sessionId: c.sessionId }}
                >
                  Open evidence
                </Link>
              </li>
            ))}
            {sig.floorFails.map((c) => (
              <li key={`${c.sessionId}-${c.id}`} className="rounded-xl border border-border bg-surface px-4 py-3">
                <Badge variant="warn">floor</Badge>
                <p className="mt-2 text-sm">
                  {c.caseId} · step {c.id}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </Shell>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-border bg-surface p-5">
      <p className="text-xs uppercase tracking-[0.16em] text-subtle">{label}</p>
      <p className="mt-2 font-display text-4xl tabular-nums">{value}</p>
    </div>
  );
}
