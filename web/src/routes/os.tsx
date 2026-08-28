import { createFileRoute, Link } from "@tanstack/react-router";
import { Shell, ModuleKicker } from "@/components/layout/shell";
import { MODULES } from "@/lib/naadi/catalog";

export const Route = createFileRoute("/os")({ component: OsPage });

function OsPage() {
  return (
    <Shell>
      <ModuleKicker
        kicker="Platform map"
        title="Nine lobes, one ledger"
        lede="Each module stands alone for a pilot. Together they close the loop from ward to credential. Nothing here writes an employment or insurance label."
      />
      <div className="mt-8 grid gap-4 sm:grid-cols-2">
        {MODULES.map((m) => (
          <Link
            key={m.id}
            to={m.to}
            className="rounded-xl border border-border bg-surface p-5 transition-colors duration-150 hover:border-border-strong"
          >
            <p className="text-xs uppercase tracking-[0.16em] text-subtle">{m.sanskrit}</p>
            <h2 className="mt-2 font-display text-2xl">{m.name}</h2>
            <p className="mt-2 text-sm leading-relaxed text-muted">{m.job}</p>
            <p className="mt-3 text-xs text-subtle">{m.standalone}</p>
          </Link>
        ))}
      </div>
    </Shell>
  );
}
