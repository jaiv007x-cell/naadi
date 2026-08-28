import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { Shell, ModuleKicker } from "@/components/layout/shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { CASES } from "@/lib/naadi/cases";
import { useNaadi } from "@/lib/naadi/store";
import { uid } from "@/lib/utils";
import type { CaseBlueprint } from "@/lib/naadi/types";
import { useHydrated } from "@/lib/use-hydrated";

export const Route = createFileRoute("/author")({ component: AuthorPage });

function cloneFrom(src: CaseBlueprint, name: string): CaseBlueprint {
  return {
    ...src,
    id: uid("case"),
    version: `${name.toLowerCase().replace(/\s+/g, "_")}.draft.v1`,
    draft: true,
    title: `${src.title} (draft)`,
    persona: { ...src.persona, name },
    briefing: src.briefing,
    hidden: src.hidden,
    orders: src.orders.map((o) => ({ ...o })),
    rubric: src.rubric.map((r) => ({ ...r })),
    findings: { ...src.findings },
    competencies: [...src.competencies],
    initialVitals: { ...src.initialVitals },
  };
}

function AuthorPage() {
  const hydrated = useHydrated();
  const custom = useNaadi((s) => s.customCases);
  const saveCase = useNaadi((s) => s.saveCase);
  const deleteCase = useNaadi((s) => s.deleteCase);
  const [srcId, setSrcId] = useState(CASES[0].id);
  const [name, setName] = useState("Prakash Patil");
  const [opening, setOpening] = useState("");
  const [briefing, setBriefing] = useState("");
  const [hidden, setHidden] = useState("");
  const [draft, setDraft] = useState<CaseBlueprint | null>(null);

  function seed() {
    const src = CASES.find((c) => c.id === srcId) ?? CASES[0];
    const bp = cloneFrom(src, name.trim() || src.persona.name);
    if (opening.trim()) bp.persona.openingLine = opening.trim();
    if (briefing.trim()) bp.briefing = briefing.trim();
    if (hidden.trim()) bp.hidden = hidden.trim();
    bp.persona.summary = `${bp.persona.age}, ${bp.persona.city}. Draft clone of ${src.persona.name}.`;
    setDraft(bp);
  }

  function save() {
    if (!draft) return;
    saveCase(draft);
  }

  return (
    <Shell>
      <ModuleKicker
        kicker="Authoring harness"
        title="Write a case, dry-run the engine"
        lede="Draft-tier. Faculty can clone a locked seed (do not overwrite Ramesh Kale STEMI). Production still waits on a clinician countersign. The compiler here is a form, not YAML — same blueprint the ward already runs."
      />
      <div className="mt-8 grid gap-4 md:grid-cols-2">
        <label className="text-sm">
          <span className="text-xs uppercase tracking-[0.16em] text-subtle">Clone from</span>
          <select
            className="mt-2 h-11 w-full rounded-md border border-border bg-surface-2 px-3 text-sm"
            value={srcId}
            onChange={(e) => setSrcId(e.target.value)}
          >
            {CASES.map((c) => (
              <option key={c.id} value={c.id}>
                {c.persona.name} — {c.version}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <span className="text-xs uppercase tracking-[0.16em] text-subtle">Persona name</span>
          <Input className="mt-2" value={name} onChange={(e) => setName(e.target.value)} />
        </label>
      </div>
      <label className="mt-4 block text-sm">
        <span className="text-xs uppercase tracking-[0.16em] text-subtle">Opening line</span>
        <Input
          className="mt-2"
          value={opening}
          onChange={(e) => setOpening(e.target.value)}
          placeholder="Doctor sahab…"
        />
      </label>
      <label className="mt-4 block text-sm">
        <span className="text-xs uppercase tracking-[0.16em] text-subtle">Public briefing</span>
        <textarea
          className="mt-2 min-h-24 w-full rounded-md border border-border bg-surface-2 px-3 py-2 text-sm"
          value={briefing}
          onChange={(e) => setBriefing(e.target.value)}
        />
      </label>
      <label className="mt-4 block text-sm">
        <span className="text-xs uppercase tracking-[0.16em] text-subtle">Hidden ground truth</span>
        <textarea
          className="mt-2 min-h-24 w-full rounded-md border border-border bg-surface-2 px-3 py-2 text-sm"
          value={hidden}
          onChange={(e) => setHidden(e.target.value)}
        />
      </label>
      <div className="mt-4 flex flex-wrap gap-3">
        <Button type="button" onClick={seed}>
          Compile draft
        </Button>
        {draft ? (
          <>
            <Button type="button" variant="secondary" onClick={save}>
              Save to library
            </Button>
            <Button type="button" variant="outline" asChild>
              <Link to="/sim/$caseId" params={{ caseId: draft.id }} onClick={save}>
                Dry-run in ward
              </Link>
            </Button>
          </>
        ) : null}
      </div>
      {draft ? (
        <article className="mt-6 rounded-xl border border-border bg-surface p-5 text-sm">
          <p className="font-mono text-xs text-subtle">{draft.version}</p>
          <p className="mt-2">{draft.persona.name}</p>
          <p className="mt-1 text-muted">{draft.orders.length} orders · {draft.rubric.length} rubric hits</p>
          <p className="mt-3 italic text-fg">“{draft.persona.openingLine}”</p>
        </article>
      ) : null}

      <section className="mt-10">
        <h2 className="font-display text-xl">Saved drafts</h2>
        {!hydrated ? (
          <p className="mt-3 text-muted">Loading…</p>
        ) : custom.length === 0 ? (
          <p className="mt-3 text-muted">None yet. Compile, then save. Ramesh Kale STEMI seed stays locked.</p>
        ) : (
          <ul className="mt-4 space-y-3">
            {custom.map((c) => (
              <li key={c.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-surface px-4 py-3">
                <div>
                  <p className="font-display text-lg">{c.persona.name}</p>
                  <p className="font-mono text-xs text-subtle">{c.version}</p>
                </div>
                <div className="flex gap-2">
                  <Button size="sm" asChild>
                    <Link to="/sim/$caseId" params={{ caseId: c.id }}>
                      Dry-run
                    </Link>
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => deleteCase(c.id)}>
                    Delete
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </Shell>
  );
}
