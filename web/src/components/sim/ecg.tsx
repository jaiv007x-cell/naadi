import { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";

function beat(x0: number, amp: number) {
  const p = [
    [0, 0],
    [8, 0],
    [10, -amp * 0.12],
    [14, 0],
    [18, 0],
    [20, amp * 0.18],
    [22, -amp],
    [25, amp * 0.55],
    [28, 0],
    [36, 0],
    [42, -amp * 0.22],
    [50, 0],
    [64, 0],
  ];
  return p.map(([x, y]) => [x0 + x, y] as const);
}

export function EcgTrace({
  hr,
  alarmed,
  className,
}: {
  hr: number;
  alarmed?: boolean;
  className?: string;
}) {
  const ref = useRef<SVGPolylineElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let raf = 0;
    const start = performance.now();
    const loop = (now: number) => {
      const t = (now - start) / 1000;
      const w = 640;
      const h = 84;
      const mid = h / 2;
      const spacing = Math.max(42, 7200 / Math.max(hr, 40));
      const offset = (t * (hr / 60) * spacing) % spacing;
      const pts: string[] = [];
      for (let x = -spacing * 2; x < w + spacing; x += spacing) {
        for (const [px, py] of beat(x - offset, 28)) {
          pts.push(`${px.toFixed(1)},${(mid + py).toFixed(1)}`);
        }
      }
      el.setAttribute("points", pts.join(" "));
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [hr]);

  return (
    <svg
      viewBox="0 0 640 84"
      className={cn("h-20 w-full", className)}
      preserveAspectRatio="none"
      aria-hidden
    >
      <polyline
        ref={ref}
        fill="none"
        stroke={alarmed ? "var(--color-danger)" : "var(--color-monitor)"}
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}
