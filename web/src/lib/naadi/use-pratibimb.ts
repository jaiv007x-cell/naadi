import { useEffect, useState } from "react";
import { checkPratibimbHealth, type PratibimbHealth } from "./pratibimb";

export function usePratibimbHealth(): PratibimbHealth & { checking: boolean } {
  const [health, setHealth] = useState<PratibimbHealth>({ ok: false });
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const h = await checkPratibimbHealth();
      if (!cancelled) {
        setHealth(h);
        setChecking(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return { ...health, checking };
}
