import { createFileRoute, Link } from "@tanstack/react-router";
import { Shell, ModuleKicker } from "@/components/layout/shell";
import { Button } from "@/components/ui/button";
import { CASE_COMP_MAP } from "@/lib/naadi/catalog";
import { CASES } from "@/lib/naadi/cases";
import { competencyNodes, nextActions, overallCrs } from "@/lib/naadi/graph";
import { PROCEDURES } from "@/lib/naadi/catalog";
import { useNaadi } from "@/lib/naadi/store";
import { useHydrated } from "@/lib/use-hydrated";

export const Route = createFileRoute("/sankalp")({ component: SankalpPage });

function SankalpPage() {
  const hydrated = useHydrated();
  const sessions = useNaadi((s) => Object.values(s.sessions));
  const rotations = useNaadi((s) => s.rotations);
  const nodes = competencyNodes(sessions, rotations);
  const weak = nextActions(nodes);
  const crs = overallCrs(nodes);

  const nextCase = CASES.find((c) => {
    const ids = CASE_COMP_MAP[c.id] ?? [];
    return ids.some((id) => weak.some((w) => w.id === id));
  });
  const nextProc = PROCEDURES.find((p) => p.competencies.some((id) => weak.some((w) => w.id === id)));

  return (
    <Shell>
      <ModuleKicker
        kicker="Sankalp — resolution"
        title="What to do next"
        lede="A plan, not a syllabus. Weak nodes first. Execution still happens in the ward or on the floor."
      />
      <p className="mt-4 font-mono text-sm text-muted">CRS {hydrated ? crs : "—"}</p>
      <ol className="mt-8 space-y-4">
        {hydrated &&
          weak.map((w, i) => (
            <li key={w.id} className="rounded-xl border border-border bg-surface p-5">
              <p className="text-xs uppercase tracking-[0.16em] text-subtle">Priority {i + 1}</p>
              <h2 className="mt-1 font-display text-2xl">{w.label}</h2>
              <p className="mt-1 text-sm text-muted">
                Mastery {w.mastery}
                {w.evidence === 0 ? " · no evidence yet" : ` · ${w.evidence} events`}
                {w.decayed ? " · decaying" : ""}
              </p>
            </li>
          ))}
      </ol>
      <div className="mt-8 flex flex-wrap gap-3">
        {nextCase ? (
          <Button asChild>
            <Link to="/sim/$caseId" params={{ caseId: nextCase.id }}>
              Run {nextCase.persona.name}
            </Link>
          </Button>
        ) : null}
        {nextProc ? (
          <Button variant="secondary" asChild>
            <Link to="/drishti">Log {nextProc.label}</Link>
          </Button>
        ) : null}
        <Button variant="ghost" asChild>
          <Link to="/guru">Ask Guru</Link>
        </Button>
      </div>
    </Shell>
  );
}
