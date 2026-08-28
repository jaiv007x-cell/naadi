import { createFileRoute, Link } from "@tanstack/react-router";
import { Shell } from "@/components/layout/shell";
import { Badge } from "@/components/ui/badge";
import { getCase } from "@/lib/naadi/cases";
import { useNaadi } from "@/lib/naadi/store";
import { useHydrated } from "@/lib/use-hydrated";

export const Route = createFileRoute("/ledger")({ component: LedgerPage });

function LedgerPage() {
  const hydrated = useHydrated();
  const extra = useNaadi((s) => s.customCases);
  const sessions = useNaadi((s) => Object.values(s.sessions).sort((a, b) => b.startedAt - a.startedAt));

  return (
    <Shell>
      <p className="text-xs font-medium uppercase tracking-[0.22em] text-accent">Clinical evidence ledger</p>
      <h1 className="mt-2 font-display text-4xl tracking-tight">Why this score</h1>
      <p className="mt-3 max-w-2xl text-muted">
        Frozen locally on this device. Not a certificate hash. Each row is an event a
        preceptor can replay. BEEMA may read this; it never writes a hiring label into it.
      </p>
      {!hydrated ? (
        <p className="mt-8 text-muted">Loading…</p>
      ) : sessions.length === 0 ? (
        <p className="mt-8 text-muted">No sessions yet. Run a case to write the first events.</p>
      ) : (
        <div className="mt-8 space-y-3">
          {sessions.map((s) => {
            const bp = getCase(s.caseId, extra);
            const inner = (
              <>
                <div>
                  <p className="font-display text-lg">{bp?.persona.name ?? s.caseId}</p>
                  <p className="font-mono text-xs text-subtle">{s.id}</p>
                </div>
                <div className="flex items-center gap-2">
                  <Badge variant={s.status === "ended" ? "default" : "accent"}>{s.status}</Badge>
                  {s.grade ? (
                    <Badge variant={s.grade.failClosed ? "danger" : "ok"}>
                      {s.grade.letter} · CRS {s.grade.crs}
                    </Badge>
                  ) : null}
                </div>
              </>
            );
            const className =
              "flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-surface px-4 py-4 hover:border-border-strong";
            return s.status === "ended" ? (
              <Link key={s.id} to="/debrief/$sessionId" params={{ sessionId: s.id }} className={className}>
                {inner}
              </Link>
            ) : (
              <Link key={s.id} to="/sim/$caseId" params={{ caseId: s.caseId }} className={className}>
                {inner}
              </Link>
            );
          })}
        </div>
      )}
    </Shell>
  );
}
