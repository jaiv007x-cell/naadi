import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { Shell, ModuleKicker } from "@/components/layout/shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { askGuru } from "@/lib/ai/guru";
import { competencyNodes, nextActions, overallCrs } from "@/lib/naadi/graph";
import { useNaadi } from "@/lib/naadi/store";
import { useHydrated } from "@/lib/use-hydrated";

export const Route = createFileRoute("/guru")({ component: GuruPage });

function GuruPage() {
  const hydrated = useHydrated();
  const sessions = useNaadi((s) => Object.values(s.sessions));
  const rotations = useNaadi((s) => s.rotations);
  const log = useNaadi((s) => s.guruLog);
  const pushGuru = useNaadi((s) => s.pushGuru);
  const nodes = competencyNodes(sessions, rotations);
  const weak = nextActions(nodes);
  const crs = overallCrs(nodes);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);

  async function send() {
    const q = draft.trim();
    if (!q) return;
    setDraft("");
    pushGuru({ role: "learner", text: q });
    setBusy(true);
    const gaps = weak.map((w) => `${w.label} (${w.mastery})`).join("; ");
    try {
      const res = await askGuru({ data: { question: q, gaps, crs } });
      pushGuru({
        role: "guru",
        text: res.ok
          ? res.text
          : `Off-line coach: drill ${weak[0]?.label ?? "ECG acquisition"} next. No dose invented. Check the local protocol.`,
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell>
      <ModuleKicker
        kicker="Guru"
        title="Teach the gap"
        lede="Aimed at Dhaara, not at finishing a video. Guru will not invent a milligram. Production doses stay on the protocol card."
      />
      <div className="mt-6 flex flex-wrap gap-2">
        {weak.slice(0, 3).map((w) => (
          <Button
            key={w.id}
            size="sm"
            variant="secondary"
            onClick={() => setDraft(`Drill me on ${w.label}. Keep it ward-practical for an allied-health intern.`)}
          >
            Drill {w.label}
          </Button>
        ))}
        <Button size="sm" variant="ghost" asChild>
          <Link to="/dhaara">See graph</Link>
        </Button>
      </div>
      <div className="mt-8 space-y-3">
        {!hydrated ? (
          <p className="text-muted">Loading…</p>
        ) : log.length === 0 ? (
          <p className="text-sm text-muted">Ask a question or tap a drill chip.</p>
        ) : (
          log.map((t) => (
            <div
              key={t.id}
              className={
                t.role === "guru"
                  ? "max-w-2xl rounded-lg bg-surface-2 px-4 py-3 text-sm leading-relaxed"
                  : "ml-auto max-w-2xl rounded-lg bg-accent/15 px-4 py-3 text-sm"
              }
            >
              <p className="mb-1 text-[10px] uppercase tracking-[0.16em] text-subtle">{t.role}</p>
              {t.text}
            </div>
          ))
        )}
      </div>
      <form
        className="mt-6 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        <Input
          value={draft}
          disabled={busy}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="What is weak on the graph?"
        />
        <Button type="submit" disabled={busy || !draft.trim()}>
          Ask
        </Button>
      </form>
    </Shell>
  );
}
