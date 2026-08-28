import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { PulseMark } from "@/components/pulse-mark";
import { cn } from "@/lib/utils";

const NAV = [
  { to: "/", label: "Pulse" },
  { to: "/cases", label: "Ward" },
  { to: "/os", label: "OS" },
  { to: "/ledger", label: "Ledger" },
] as const;

export function Shell({
  children,
  dense,
}: {
  children: ReactNode;
  dense?: boolean;
}) {
  return (
    <div className="min-h-dvh bg-bg text-fg">
      <header className="sticky top-0 z-30 border-b border-border bg-bg/90 backdrop-blur-sm">
        <div className="mx-auto flex h-14 max-w-6xl min-w-0 items-center justify-between gap-2 px-3 sm:px-4">
          <Link to="/" className="flex min-w-0 items-center gap-2 text-fg">
            <PulseMark className="size-6 shrink-0 sm:size-7" />
            <span className="font-display text-base tracking-tight sm:text-lg">NAADI</span>
          </Link>
          <nav className="flex min-w-0 items-center gap-0 text-xs sm:gap-1 sm:text-sm">
            {NAV.map((item) => (
              <Link
                key={item.to}
                to={item.to}
                className={cn(
                  "rounded-md px-2 py-2 text-muted transition-colors duration-150 hover:bg-surface-2 hover:text-fg sm:px-3",
                  "[&.active]:text-fg",
                )}
                activeOptions={{ exact: item.to === "/" }}
              >
                {item.label}
              </Link>
            ))}
          </nav>
        </div>
      </header>
      <div className={cn(dense ? "" : "mx-auto max-w-6xl px-4 py-8")}>{children}</div>
    </div>
  );
}

export function ModuleKicker({ kicker, title, lede }: { kicker: string; title: string; lede: string }) {
  return (
    <>
      <p className="text-xs font-medium uppercase tracking-[0.22em] text-accent">{kicker}</p>
      <h1 className="mt-2 font-display text-4xl tracking-tight">{title}</h1>
      <p className="mt-3 max-w-2xl text-muted">{lede}</p>
    </>
  );
}
