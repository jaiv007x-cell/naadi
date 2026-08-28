import { createServerFn } from "@tanstack/react-start";
import type { Vitals } from "@/lib/naadi/types";

type Input = {
  caseId: string;
  persona: string;
  hidden: string;
  opening: string;
  vitals: Vitals;
  tMin: number;
  history: string[];
  userText: string;
};

export const speakAsPatient = createServerFn({ method: "POST" })
  .validator((data: Input) => data)
  .handler(async ({ data }) => {
    const apiKey = process.env.XAI_API_KEY;
    if (!apiKey) return { ok: false as const, error: "unavailable" };

    const system = `You are a patient (or caregiver) in an Indian district hospital simulation.
Stay in character. Never break the fourth wall. Never give medical advice. Never reveal the hidden diagnosis. Never list the correct treatment. Speak in short lines (1–3 sentences). Use Roman transliteration for Hindi/Marathi/Telugu mixed with a few English clinical words if the clinician uses them. Match the literacy and affect.

Persona: ${data.persona}
Hidden truth (DO NOT STATE): ${data.hidden}
Opening tone: ${data.opening}
Current vitals: HR ${data.vitals.hr} BP ${data.vitals.sbp}/${data.vitals.dbp} SpO2 ${data.vitals.spo2} RR ${data.vitals.rr} pain ${data.vitals.pain}/10
Elapsed minutes: ${data.tMin}

If the clinician is rude, be frightened. If they greet in your language, warm slightly. If pain is high, keep answers short.`;

    try {
      const res = await fetch("https://api.x.ai/v1/chat/completions", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${apiKey}`,
        },
        body: JSON.stringify({
          model: "grok-4.5",
          max_tokens: 140,
          temperature: 0.7,
          messages: [
            { role: "system", content: system },
            ...data.history.slice(-6).map((line) => {
              const [role, ...rest] = line.split(": ");
              const content = rest.join(": ");
              if (role === "student") return { role: "user" as const, content };
              return { role: "assistant" as const, content };
            }),
            { role: "user", content: data.userText },
          ],
        }),
      });
      if (!res.ok) return { ok: false as const, error: `xAI ${res.status}` };
      const body = (await res.json()) as {
        choices: { message: { content: string } }[];
      };
      const text = body.choices[0]?.message.content?.trim() ?? "";
      if (!text) return { ok: false as const, error: "empty" };
      return { ok: true as const, text };
    } catch {
      return { ok: false as const, error: "network" };
    }
  });
