import { createServerFn } from "@tanstack/react-start";

export const askGuru = createServerFn({ method: "POST" })
  .validator((data: { question: string; gaps: string; crs: number }) => data)
  .handler(async ({ data }) => {
    const apiKey = process.env.XAI_API_KEY;
    if (!apiKey) return { ok: false as const, error: "unavailable" };

    const system = `You are Guru, the tutor lobe of NAADI, for Indian allied-health students (Virohan).
Teach the gap. Short, clinical, vernacular-friendly English with Hindi terms when useful.
Never invent a drug dose. If a dose is needed, say it must be checked against the local protocol and do not number it.
Do not diagnose a real patient. This is a tutor for a simulation OS.
Learner CRS: ${data.crs}. Weak competencies: ${data.gaps || "none recorded yet"}.
Reply in under 160 words.`;

    try {
      const res = await fetch("https://api.x.ai/v1/chat/completions", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${apiKey}`,
        },
        body: JSON.stringify({
          model: "grok-4.5",
          max_tokens: 220,
          temperature: 0.4,
          messages: [
            { role: "system", content: system },
            { role: "user", content: data.question },
          ],
        }),
      });
      if (!res.ok) return { ok: false as const, error: `xAI ${res.status}` };
      const body = (await res.json()) as { choices: { message: { content: string } }[] };
      const text = body.choices[0]?.message.content?.trim() ?? "";
      if (!text) return { ok: false as const, error: "empty" };
      return { ok: true as const, text };
    } catch {
      return { ok: false as const, error: "network" };
    }
  });
