import { cn } from "@/lib/utils";

export function PulseMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={cn("text-accent", className)} aria-hidden>
      <path
        d="M2 17h6l2.5-7 3.5 14 3-9 2 2H30"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
