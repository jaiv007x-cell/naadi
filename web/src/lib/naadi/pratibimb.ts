/**
 * Pratibimb HTTP client — proxies to :8100 in dev via Vite.
 * Falls back silently when the engine is offline (local physio still runs).
 */

const BASE =
  (typeof import.meta !== "undefined" &&
    import.meta.env?.VITE_PRATIBIMB_BASE) ||
  "/api/pratibimb";

export type PratibimbHealth = { ok: boolean; service?: string };

export type PratibimbSession = {
  session_id: string;
  status: string;
  case_id: string;
  patient_name: string;
  chief_complaint: string;
  vitals?: {
    hr: number;
    sbp: number;
    dbp: number;
    spo2: number;
    rr: number;
    temp_c?: number;
    pain?: number;
  };
  elapsed_seconds?: number;
};

export type PratibimbDialogue = {
  patient: { utterance: string };
  vitals: NonNullable<PratibimbSession["vitals"]>;
  elapsed_seconds: number;
};

const DEV_SUBJECT =
  (typeof import.meta !== "undefined" && import.meta.env?.VITE_PRATIBIMB_DEV_SUBJECT) ||
  "web-preview-learner";
const DEV_TENANT =
  (typeof import.meta !== "undefined" && import.meta.env?.VITE_PRATIBIMB_DEV_TENANT) ||
  "ncvet-default";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Dev-Subject": DEV_SUBJECT,
      "X-Dev-Tenant": DEV_TENANT,
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Pratibimb ${res.status}: ${body.slice(0, 200)}`);
  }
  return res.json() as Promise<T>;
}

export async function checkPratibimbHealth(): Promise<PratibimbHealth> {
  try {
    const data = await request<{ status: string; service?: string }>("/health");
    return { ok: data.status === "ok", service: data.service };
  } catch {
    return { ok: false };
  }
}

export async function createPratibimbSession(opts?: {
  learnerId?: string;
  tenantId?: string;
}): Promise<PratibimbSession> {
  return request<PratibimbSession>("/v1/sessions", {
    method: "POST",
    body: JSON.stringify({
      learner_id: opts?.learnerId ?? "web-preview-learner",
      tenant_id: opts?.tenantId ?? "ncvet-default",
      assessment_mode: "practice",
      target_difficulty: 0.55,
    }),
  });
}

export async function startPratibimbSession(sessionId: string): Promise<PratibimbSession> {
  return request<PratibimbSession>(`/v1/sessions/${sessionId}/start`, { method: "POST" });
}

export async function pratibimbDialogue(
  sessionId: string,
  utterance: string,
): Promise<PratibimbDialogue> {
  return request<PratibimbDialogue>(`/v1/sessions/${sessionId}/dialogue`, {
    method: "POST",
    body: JSON.stringify({ utterance }),
  });
}

export async function pratibimbAction(
  sessionId: string,
  action: string,
  params: Record<string, unknown> = {},
): Promise<{ vitals?: PratibimbSession["vitals"] }> {
  return request(`/v1/sessions/${sessionId}/actions`, {
    method: "POST",
    body: JSON.stringify({ action, params }),
  });
}

export async function completePratibimbSession(sessionId: string): Promise<unknown> {
  return request(`/v1/sessions/${sessionId}/complete`, { method: "POST" });
}
