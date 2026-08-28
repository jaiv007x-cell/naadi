import { createFileRoute, Link } from "@tanstack/react-router";
import { Shell, ModuleKicker } from "@/components/layout/shell";
import { Button } from "@/components/ui/button";
import { competencyNodes, overallCrs } from "@/lib/naadi/graph";
import { useNaadi } from "@/lib/naadi/store";
import { useHydrated } from "@/lib/use-hydrated";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/dhaara")({ component: DhaaraPage });

function DhaaraPage() {
  const hydrated = useHydrated();
  const sessions = useNaadi((s) => Object.values(s.sessions));
  const rotations = useNaadi((s) => s.rotations);
  const nodes = competencyNodes(sessions, rotations);
  const crs = overallCrs(nodes);
  const domains = [...new Set(nodes.map((n) => n.domain))];

  return (
    <Shell>
      <ModuleKicker
        kicker="Dhaara — flow"
        title="Competency graph"
        lede="Mastery is evidence plus time. Skills rust. A zero here means no evidence yet — not a failing student."
      />
      <div className="mt-6 flex flex-wrap items-end gap-6">
        <div>
          <p className="text-xs uppercase tracking-[0.16em] text-subtle">CRS</p>
          <p className="font-display text-5xl tabular-nums">{hydrated ? crs : "—"}</p>
        </div>
        <Button variant="secondary" asChild>
          <Link to="/sankalp">Open Sankalp plan</Link>
        </Button>
      </div>
      {!hydrated ? (
        <p className="mt-8 text-muted">Loading graph…</p>
      ) : (
        <div className="mt-8 space-y-8">
          {domains.map((d) => (
            <section key={d}>
              <h2 className="text-xs uppercase tracking-[0.18em] text-muted">{d}</h2>
              <ul className="mt-3 space-y-3">
                {nodes
                  .filter((n) => n.domain === d)
                  .map((n) => (
                    <li key={n.id}>
                      <div className="mb-1 flex items-baseline justify-between gap-3 text-sm">
                        <span>{n.label}</span>
                        <span className="font-mono text-xs tabular-nums text-subtle">
                          {n.mastery} · {n.evidence} ev{n.decayed ? " · decay" : ""}
                        </span>
                      </div>
                      <div className="h-1.5 overflow-hidden rounded-full bg-surface-2">
                        <div
                          className={cn("h-full rounded-full bg-accent", n.mastery < 40 && "bg-danger")}
                          style={{ width: `${n.mastery}%` }}
                        />
                      </div>
                    </li>
                  ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </Shell>
  );
}
