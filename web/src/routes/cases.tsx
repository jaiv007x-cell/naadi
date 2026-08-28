import { createFileRoute, Link } from "@tanstack/react-router";
import { Shell, ModuleKicker } from "@/components/layout/shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CASES } from "@/lib/naadi/cases";
import { useNaadi } from "@/lib/naadi/store";
import { useHydrated } from "@/lib/use-hydrated";

export const Route = createFileRoute("/cases")({ component: CasesPage });

function CasesPage() {
  const hydrated = useHydrated();
  const custom = useNaadi((s) => s.customCases);
  const all = hydrated ? [...CASES, ...custom] : CASES;

  return (
    <Shell>
      <ModuleKicker
        kicker="Pratibimb — ward"
        title="Case library"
        lede="Eight playable seeds: Ramesh + Aarav hash-pinned, Sunita PPH, Dengue + snakebite, legacy PET/asthma/anaphylaxis. Pratibimb :8100 syncs Ramesh when engine is online."
      />
      <div className="mt-4">
        <Button variant="secondary" asChild>
          <Link to="/author">Author a draft</Link>
        </Button>
      </div>
      <div className="mt-8 grid gap-5">
        {all.map((c) => (
          <article
            key={c.id}
            className="grid gap-5 rounded-xl border border-border bg-surface p-5 md:grid-cols-[1fr_auto] md:items-end"
          >
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="accent">{c.specialty}</Badge>
                <Badge>{c.persona.language}</Badge>
                {c.draft ? <Badge variant="warn">draft</Badge> : null}
                <span className="font-mono text-xs text-subtle">{c.version}</span>
              </div>
              <h2 className="mt-3 font-display text-2xl">{c.persona.name}</h2>
              <p className="text-sm text-muted">
                {c.persona.age} · {c.persona.city} · {c.persona.occupation}
              </p>
              <p className="mt-3 max-w-2xl text-sm leading-relaxed text-muted">{c.briefing}</p>
              <p className="mt-3 font-display text-sm italic text-fg">“{c.persona.openingLine}”</p>
            </div>
            <Button asChild>
              <Link to="/sim/$caseId" params={{ caseId: c.id }}>
                Enter casualty
              </Link>
            </Button>
          </article>
        ))}
      </div>
    </Shell>
  );
}
