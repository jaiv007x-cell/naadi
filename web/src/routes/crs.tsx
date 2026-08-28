import { createFileRoute, Link } from "@tanstack/react-router";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Shell, ModuleKicker } from "@/components/layout/shell";
import { Button } from "@/components/ui/button";
import { competencyNodes, overallCrs } from "@/lib/naadi/graph";
import { useNaadi } from "@/lib/naadi/store";
import { useHydrated } from "@/lib/use-hydrated";

export const Route = createFileRoute("/crs")({ component: CrsPage });

function CrsPage() {
  const hydrated = useHydrated();
  const sessions = useNaadi((s) => Object.values(s.sessions).filter((x) => x.grade));
  const rotations = useNaadi((s) => s.rotations);
  const nodes = competencyNodes(
    useNaadi((s) => Object.values(s.sessions)),
    rotations,
  );
  const crs = overallCrs(nodes);
  const latest = sessions.sort((a, b) => b.startedAt - a.startedAt)[0];
  const chart = nodes
    .filter((n) => n.evidence > 0)
    .map((n) => ({
      name: n.label.length > 16 ? `${n.label.slice(0, 14)}…` : n.label,
      score: n.mastery,
    }));

  return (
    <Shell>
      <ModuleKicker
        kicker="Clinical readiness"
        title="CRS"
        lede="A vector with uncertainty. Fail-closed sessions cap the score. Decay is real — unused skills rust. This is the Dhaara projection hospitals can be shown."
      />
      {!hydrated ? (
        <p className="mt-8 text-muted">Loading…</p>
      ) : (
        <>
          <div className="mt-8 grid gap-4 sm:grid-cols-3">
            <Stat label="CRS" value={String(crs)} />
            <Stat label="Graded sessions" value={String(sessions.length)} />
            <Stat label="Latest letter" value={latest?.grade?.letter ?? "—"} />
          </div>
          <div className="mt-4 flex flex-wrap gap-3">
            <Button variant="secondary" asChild>
              <Link to="/dhaara">Open Dhaara</Link>
            </Button>
            <Button variant="outline" asChild>
              <Link to="/niyukti">Match hospitals</Link>
            </Button>
          </div>
          <div className="mt-8 h-72 rounded-xl border border-border bg-surface p-4">
            {chart.length === 0 ? (
              <div className="flex h-full flex-col items-center justify-center gap-3 text-muted">
                <p>No competency vector yet.</p>
                <Button asChild>
                  <Link to="/sim/$caseId" params={{ caseId: "ramesh-stemi" }}>
                    Run the flagship case
                  </Link>
                </Button>
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chart} margin={{ top: 8, right: 8, left: 0, bottom: 28 }}>
                  <CartesianGrid stroke="color-mix(in oklab, var(--color-fg) 10%, transparent)" vertical={false} />
                  <XAxis dataKey="name" tick={{ fill: "var(--color-muted)", fontSize: 11 }} interval={0} angle={-22} textAnchor="end" />
                  <YAxis tick={{ fill: "var(--color-muted)", fontSize: 11 }} domain={[0, 100]} />
                  <Tooltip
                    contentStyle={{
                      background: "var(--color-surface)",
                      border: "1px solid var(--color-border)",
                      color: "var(--color-fg)",
                    }}
                  />
                  <Bar dataKey="score" fill="var(--color-accent)" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </>
      )}
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
