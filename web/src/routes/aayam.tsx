import { createFileRoute, Link } from "@tanstack/react-router";
import { Shell, ModuleKicker } from "@/components/layout/shell";
import { Badge } from "@/components/ui/badge";
import { getCase } from "@/lib/naadi/cases";
import { fingerprint } from "@/lib/naadi/graph";
import { useNaadi } from "@/lib/naadi/store";
import { useHydrated } from "@/lib/use-hydrated";

export const Route = createFileRoute("/aayam")({ component: AayamPage });

function AayamPage() {
  const hydrated = useHydrated();
  const extra = useNaadi((s) => s.customCases);
  const sessions = useNaadi((s) =>
    Object.values(s.sessions)
      .filter((x) => x.grade)
      .sort((a, b) => b.startedAt - a.startedAt),
  );

  return (
    <Shell>
      <ModuleKicker
        kicker="Aayam — dimension"
        title="Audit pack"
        lede="A hospital or NCVET officer should be able to ask why. The answer is the evidence chain plus a content-addressed id. Blockchain can hash this later. It is not the architecture."
      />
      {!hydrated ? (
        <p className="mt-8 text-muted">Loading…</p>
      ) : sessions.length === 0 ? (
        <p className="mt-8 text-muted">
          No graded session. <Link to="/cases" className="text-accent">Run a case</Link> first.
        </p>
      ) : (
        <div className="mt-8 space-y-4">
          {sessions.map((s) => {
            const bp = getCase(s.caseId, extra);
            return (
              <article key={s.id} className="rounded-xl border border-border bg-surface p-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h2 className="font-display text-2xl">{bp?.persona.name ?? s.caseId}</h2>
                    <p className="font-mono text-xs text-subtle">{fingerprint(s)}</p>
                    <p className="mt-2 text-sm text-muted">
                      {bp?.version} · ended {s.endReason} · {new Date(s.startedAt).toLocaleString()}
                    </p>
                  </div>
                  <Badge variant={s.grade?.failClosed ? "danger" : "ok"}>
                    {s.grade?.letter} · CRS {s.grade?.crs}
                  </Badge>
                </div>
                <dl className="mt-4 grid gap-2 text-sm sm:grid-cols-2">
                  <div>
                    <dt className="text-xs uppercase tracking-[0.14em] text-subtle">Events</dt>
                    <dd className="font-mono tabular-nums">{s.events.length}</dd>
                  </div>
                  <div>
                    <dt className="text-xs uppercase tracking-[0.14em] text-subtle">Guideline</dt>
                    <dd>Case-local · draft-tier · not a filing</dd>
                  </div>
                </dl>
                <Link
                  to="/debrief/$sessionId"
                  params={{ sessionId: s.id }}
                  className="mt-4 inline-block text-sm text-accent"
                >
                  Open full chain
                </Link>
              </article>
            );
          })}
        </div>
      )}
    </Shell>
  );
}
