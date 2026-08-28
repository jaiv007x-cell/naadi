import { createFileRoute, Link } from "@tanstack/react-router";
import { Shell } from "@/components/layout/shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { getCase } from "@/lib/naadi/cases";
import { useNaadi } from "@/lib/naadi/store";
import { useHydrated } from "@/lib/use-hydrated";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/debrief/$sessionId")({ component: DebriefPage });

function DebriefPage() {
  const { sessionId } = Route.useParams();
  const hydrated = useHydrated();
  const extra = useNaadi((s) => s.customCases);
  const session = useNaadi((s) => s.sessions[sessionId]);
  const bp = session ? getCase(session.caseId, extra) : undefined;
  const grade = session?.grade;

  if (!hydrated) {
    return (
      <Shell>
        <p className="text-muted">Loading ledger…</p>
      </Shell>
    );
  }

  if (!session || !bp || !grade) {
    return (
      <Shell>
        <p className="text-muted">No frozen session with that id.</p>
        <Button className="mt-4" asChild>
          <Link to="/cases">Cases</Link>
        </Button>
      </Shell>
    );
  }

  return (
    <Shell>
      <p className="text-xs font-medium uppercase tracking-[0.22em] text-accent">Nirikshak debrief</p>
      <div className="mt-2 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-4xl tracking-tight">{bp.persona.name}</h1>
          <p className="text-muted">
            {bp.title} · ended {session.endReason} · {session.tMin.toFixed(0)} min
          </p>
        </div>
        <div className="text-right">
          <p className="font-display text-5xl tabular-nums leading-none">{grade.letter}</p>
          <p className="mt-1 font-mono text-sm text-muted">
            CRS {grade.crs} · {grade.letter}
            {grade.passed === false ? " · failed" : ""} · Nirikshak {grade.graderVersion ?? "0.3.0"}
          </p>
          <p className="font-mono text-[10px] text-subtle">manifest {grade.evaluationManifestHash}</p>
        </div>
      </div>

      {grade.failClosed ? (
        <div className="mt-6 rounded-xl border border-danger/40 bg-danger/10 p-4">
          <p className="text-sm font-medium text-danger">Fail-closed</p>
          <p className="mt-1 text-sm text-muted">
            A required safety action was missed, or a forbidden action was taken. Letter
            is F regardless of supporting hits. This is the point of Nirikshak.
          </p>
        </div>
      ) : null}

      <section className="mt-8 grid gap-4 md:grid-cols-2">
        <article className="rounded-xl border border-border bg-surface p-5">
          <h2 className="font-display text-xl">Hidden ground truth</h2>
          <p className="mt-3 text-sm leading-relaxed text-muted">{bp.hidden}</p>
        </article>
        <article className="rounded-xl border border-border bg-surface p-5">
          <h2 className="font-display text-xl">BEEMA — safety view</h2>
          <p className="mt-3 text-sm leading-relaxed text-muted">
            BEEMA reads this ledger. It does not write employment, insurance, or
            underwriting labels. Signals only: {grade.critical.length} critical
            flag{grade.critical.length === 1 ? "" : "s"}; end-state {session.endReason}.
          </p>
          <dl className="mt-4 grid grid-cols-2 gap-2 font-mono text-xs">
            {Object.entries(grade.errorDna ?? {}).map(([k, v]) => (
              <div key={k}>
                <dt className="text-subtle">{k.replaceAll("_", " ")}</dt>
                <dd className="tabular-nums">{v.toFixed(2)}</dd>
              </div>
            ))}
          </dl>
          {bp.contentHash ? (
            <p className="mt-3 font-mono text-[10px] text-subtle">case {bp.contentHash.slice(0, 16)}…</p>
          ) : null}
        </article>
      </section>

      <section className="mt-8">
        <h2 className="font-display text-xl">Rubric</h2>
        <div className="mt-3 overflow-x-auto rounded-xl border border-border">
          <table className="w-full min-w-[32rem] text-left text-sm">
            <thead className="bg-surface-2 text-xs uppercase tracking-[0.14em] text-subtle">
              <tr>
                <th className="px-4 py-3 font-medium">Hit</th>
                <th className="px-4 py-3 font-medium">Axis</th>
                <th className="px-4 py-3 font-medium">Result</th>
                <th className="px-4 py-3 font-medium">Pts</th>
              </tr>
            </thead>
            <tbody>
              {grade.hits.map((h) => (
                <tr key={h.id} className="border-t border-border">
                  <td className="px-4 py-3">{h.label}</td>
                  <td className="px-4 py-3 text-muted">{h.axis}</td>
                  <td className="px-4 py-3">
                    <Badge
                      variant={
                        h.fail ? "danger" : h.forbidden && !h.matched ? "ok" : h.matched ? "ok" : "default"
                      }
                    >
                      {h.fail ? "fail" : h.forbidden && !h.matched ? "withheld" : h.matched ? "matched" : "missed"}
                    </Badge>
                  </td>
                  <td className="px-4 py-3 font-mono tabular-nums">
                    {h.awarded}/{h.points}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="mt-8 grid gap-4 md:grid-cols-2">
        <article className="rounded-xl border border-border bg-surface p-5">
          <h2 className="font-display text-xl">Evidence chain</h2>
          <ol className="mt-4 space-y-3">
            {session.events.map((e) => (
              <li key={e.id} className="flex gap-3 text-sm">
                <span className="font-mono text-xs tabular-nums text-subtle">
                  {e.tMin.toFixed(0)}′
                </span>
                <span>
                  <span className="text-fg">{e.label}</span>
                  {e.detail ? <span className="block text-xs text-muted">{e.detail}</span> : null}
                </span>
              </li>
            ))}
          </ol>
        </article>
        <article className="rounded-xl border border-border bg-surface p-5">
          <h2 className="font-display text-xl">Transcript</h2>
          <div className="mt-4 space-y-3">
            {session.chat.map((m) => (
              <p key={m.id} className="text-sm leading-relaxed">
                <span className={cn("text-xs uppercase tracking-[0.14em] text-subtle")}>
                  {m.role} · {m.tMin.toFixed(0)}′
                </span>
                <span className="mt-1 block text-muted">{m.text}</span>
              </p>
            ))}
          </div>
        </article>
      </section>

      <div className="mt-8 flex flex-wrap gap-3">
        <Button asChild>
          <Link to="/sim/$caseId" params={{ caseId: bp.id }}>
            Run again
          </Link>
        </Button>
        <Button variant="secondary" asChild>
          <Link to="/ledger">Open ledger</Link>
        </Button>
      </div>
    </Shell>
  );
}
