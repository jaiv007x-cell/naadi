import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Shell, ModuleKicker } from "@/components/layout/shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PROCEDURES } from "@/lib/naadi/catalog";
import { useNaadi } from "@/lib/naadi/store";
import { useHydrated } from "@/lib/use-hydrated";
import type { RotationStep } from "@/lib/naadi/types";

export const Route = createFileRoute("/drishti")({ component: DrishtiPage });

function DrishtiPage() {
  const hydrated = useHydrated();
  const rotations = useNaadi((s) => s.rotations);
  const logRotation = useNaadi((s) => s.logRotation);
  const [procId, setProcId] = useState(PROCEDURES[0].id);
  const proc = PROCEDURES.find((p) => p.id === procId) ?? PROCEDURES[0];
  const [preceptor, setPreceptor] = useState("Duty sister");
  const [steps, setSteps] = useState<RotationStep[]>(
    proc.steps.map((s) => ({ id: s.id, label: s.label, result: "unseen" })),
  );

  function pick(id: string) {
    const next = PROCEDURES.find((p) => p.id === id) ?? PROCEDURES[0];
    setProcId(id);
    setSteps(next.steps.map((s) => ({ id: s.id, label: s.label, result: "unseen" })));
  }

  function mark(id: string, result: RotationStep["result"]) {
    setSteps((prev) => prev.map((s) => (s.id === id ? { ...s, result } : s)));
  }

  function submit() {
    logRotation({
      procedureId: proc.id,
      label: proc.label,
      preceptor,
      steps,
      competencies: [...proc.competencies],
    });
  }

  return (
    <Shell>
      <ModuleKicker
        kicker="Drishti — sight"
        title="Floor observation"
        lede="Not a badge-cam. A preceptor marks steps on a real procedure. Failures write Error-DNA. Passes feed Dhaara. Computer vision is a later lobe — this is the honest v0."
      />
      <div className="mt-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {PROCEDURES.map((p) => (
          <button
            key={p.id}
            type="button"
            onClick={() => pick(p.id)}
            className={
              p.id === procId
                ? "rounded-xl border border-accent bg-accent/10 px-4 py-3 text-left text-sm"
                : "rounded-xl border border-border bg-surface px-4 py-3 text-left text-sm text-muted"
            }
          >
            {p.label}
          </button>
        ))}
      </div>
      <div className="mt-6 max-w-sm">
        <label className="text-xs uppercase tracking-[0.16em] text-subtle" htmlFor="preceptor">
          Preceptor
        </label>
        <Input id="preceptor" className="mt-2" value={preceptor} onChange={(e) => setPreceptor(e.target.value)} />
      </div>
      <ul className="mt-6 space-y-3">
        {steps.map((s) => (
          <li key={s.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-surface px-4 py-3">
            <span className="text-sm">{s.label}</span>
            <div className="flex gap-2">
              {(["pass", "fail", "unseen"] as const).map((r) => (
                <Button key={r} size="sm" variant={s.result === r ? "default" : "outline"} onClick={() => mark(s.id, r)}>
                  {r}
                </Button>
              ))}
            </div>
          </li>
        ))}
      </ul>
      <Button className="mt-6" onClick={submit}>
        Freeze rotation to ledger
      </Button>
      <section className="mt-10">
        <h2 className="font-display text-xl">Logged rotations</h2>
        {!hydrated ? (
          <p className="mt-3 text-muted">Loading…</p>
        ) : rotations.length === 0 ? (
          <p className="mt-3 text-muted">None yet.</p>
        ) : (
          <ul className="mt-4 space-y-3">
            {rotations.map((r) => (
              <li key={r.id} className="rounded-xl border border-border bg-surface px-4 py-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{r.label}</span>
                  <Badge>{r.preceptor}</Badge>
                </div>
                <p className="mt-1 text-xs text-muted">
                  {r.steps.filter((s) => s.result === "pass").length}/{r.steps.length} pass ·{" "}
                  {new Date(r.at).toLocaleString()}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </Shell>
  );
}
