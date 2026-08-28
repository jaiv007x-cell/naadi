import { createFileRoute, Link } from "@tanstack/react-router";
import { ArrowRight } from "lucide-react";
import { Shell } from "@/components/layout/shell";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { CASES } from "@/lib/naadi/cases";
import { MODULES } from "@/lib/naadi/catalog";

export const Route = createFileRoute("/")({ component: Home });

function Home() {
  return (
    <Shell>
      <section className="grid gap-10 pb-8 pt-4 md:grid-cols-[1.2fr_0.8fr] md:items-end">
        <div>
          <p className="mb-4 text-xs font-medium uppercase tracking-[0.22em] text-accent">
            Clinical competence OS
          </p>
          <h1 className="font-display text-4xl leading-[1.1] tracking-tight text-fg md:text-5xl">
            Every ed-tech teaches.
            <span className="block text-muted">NAADI certifies competence.</span>
          </h1>
          <p className="mt-5 max-w-xl text-base leading-relaxed text-muted">
            Engine from{" "}
            <a className="text-accent" href="https://github.com/jaiv007x-cell/naadi">
              jaiv007x-cell/naadi
            </a>
            : Nirikshak 0.3.0, physio 0.2.0, seed Ramesh Kale 47M auto-rickshaw
            driver, Nagpur. This preview is the playable ward on that contract —
            not Docker, Redis, or JWT.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Button asChild>
              <Link to="/sim/$caseId" params={{ caseId: "aarav-sepsis" }}>
                Run Baby Aarav
                <ArrowRight />
              </Link>
            </Button>
            <Button variant="secondary" asChild>
              <Link to="/sim/$caseId" params={{ caseId: "ramesh-stemi" }}>
                Run Ramesh Kale
              </Link>
            </Button>
            <Button variant="ghost" asChild>
              <Link to="/os">Open the OS map</Link>
            </Button>
          </div>
        </div>
        <aside className="rounded-xl border border-border bg-surface p-5 shadow-panel">
          <p className="text-xs uppercase tracking-[0.18em] text-subtle">Naadi — pulse</p>
          <p className="mt-3 font-display text-2xl leading-snug">
            Persona, physiology, and grader stay separate. Always.
          </p>
          <p className="mt-3 text-sm leading-relaxed text-muted">
            BEEMA reads. It does not hire. Aayam shows the chain. Blockchain is optional
            later — not the product.
          </p>
        </aside>
      </section>

      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        {MODULES.map((m) => (
          <Link
            key={m.id}
            to={m.to}
            className="rounded-xl border border-border bg-surface p-4 transition-colors duration-150 hover:border-border-strong"
          >
            <p className="text-[10px] uppercase tracking-[0.16em] text-subtle">{m.sanskrit}</p>
            <h2 className="mt-2 font-display text-lg">{m.name}</h2>
            <p className="mt-2 text-xs leading-relaxed text-muted">{m.job}</p>
          </Link>
        ))}
      </section>

      <section className="mt-12">
        <div className="mb-4 flex items-end justify-between gap-4">
          <h2 className="font-display text-2xl">Draft-tier cases</h2>
          <p className="text-sm text-subtle">Engine validation. Not live learners.</p>
        </div>
        <div className="grid gap-4 md:grid-cols-3">
          {CASES.map((c) => (
            <Link
              key={c.id}
              to="/sim/$caseId"
              params={{ caseId: c.id }}
              className="group rounded-xl border border-border bg-surface p-5 transition-colors duration-150 hover:border-border-strong"
            >
              <div className="flex items-center justify-between gap-2">
                <Badge variant="accent">{c.specialty}</Badge>
                <span className="max-w-[9rem] truncate font-mono text-xs text-subtle">
                  {c.version.split(".")[0]}
                </span>
              </div>
              <h3 className="mt-4 font-display text-xl leading-snug group-hover:text-accent">
                {c.persona.name}
              </h3>
              <p className="mt-1 text-sm text-muted">{c.title}</p>
              <p className="mt-3 text-sm leading-relaxed text-muted">{c.persona.summary}</p>
            </Link>
          ))}
        </div>
      </section>
    </Shell>
  );
}
