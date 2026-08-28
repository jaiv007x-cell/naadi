import { createFileRoute, Link } from "@tanstack/react-router";
import { Shell, ModuleKicker } from "@/components/layout/shell";
import { Badge } from "@/components/ui/badge";
import { competencyNodes, matchHospitals, overallCrs } from "@/lib/naadi/graph";
import { useNaadi } from "@/lib/naadi/store";
import { useHydrated } from "@/lib/use-hydrated";

export const Route = createFileRoute("/niyukti")({ component: NiyuktiPage });

function NiyuktiPage() {
  const hydrated = useHydrated();
  const sessions = useNaadi((s) => Object.values(s.sessions));
  const rotations = useNaadi((s) => s.rotations);
  const nodes = competencyNodes(sessions, rotations);
  const crs = overallCrs(nodes);
  const matches = matchHospitals(nodes, crs);

  return (
    <Shell>
      <ModuleKicker
        kicker="Niyukti — appointment"
        title="Match, don’t hire"
        lede="Hospitals see a CRS vector and language fit. This page never writes a hiring label into the ledger. BEEMA is not allowed to either."
      />
      <p className="mt-4 font-mono text-sm text-muted">Current CRS {hydrated ? crs : "—"}</p>
      <div className="mt-8 space-y-4">
        {!hydrated
          ? null
          : matches.map((h) => (
              <article key={h.id} className="rounded-xl border border-border bg-surface p-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h2 className="font-display text-2xl">{h.name}</h2>
                    <p className="text-sm text-muted">
                      {h.city} · {h.role} · {h.language}
                      {h.nights ? " · nights" : ""}
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="font-display text-3xl tabular-nums">{h.score}</p>
                    {h.gated ? <Badge variant="warn">below min CRS {h.minCrs}</Badge> : <Badge variant="ok">clears gate</Badge>}
                  </div>
                </div>
                <p className="mt-3 text-xs text-subtle">Needs {h.need.join(" · ")}</p>
              </article>
            ))}
      </div>
      <p className="mt-6 text-sm text-muted">
        Empty graph? <Link to="/cases" className="text-accent">Run a case</Link> or{" "}
        <Link to="/drishti" className="text-accent">log a rotation</Link>.
      </p>
    </Shell>
  );
}
