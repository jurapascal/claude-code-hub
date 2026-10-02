---
name: jev
description: Levné hromadné rozhodování modelem Jev (TypeSafe AI přes Cloudflare) — ano/ne, kategorie nebo skóre pro desítky až stovky položek (e-maily, objednávky, komentáře, leady). Použij, když je potřeba roztřídit nebo ohodnotit hodně krátkých textů a Jev je napojený.
---
Jev is a cheap "System One" model: it does not write text, it answers typed questions
about a `state` with calibrated probabilities. Use it for bulk triage instead of reading
every item yourself; keep reasoning, writing and final decisions for yourself.

## Check it is connected

Run `{{PYTHON}} {{CLAUDE_DIR}}/tools/jev.py status`. Exit code 1 = not configured:
tell the user to open Claude Code Hub → Nastavení → AI agenti → Jev → Nastavit
(steps and links are there) and stop.

## Call it

Pipe JSON to `{{PYTHON}} {{CLAUDE_DIR}}/tools/jev.py run`. One object, or an array of
objects (max 200) for a batch:

```json
{"state": "text or JSON object of ONE item",
 "questions": {
   "spam":   {"type": "noul",   "instructions": "Is this spam?"},
   "topic":  {"type": "choice", "instructions": "What is it about?",
              "criteria": {"billing": "payments, invoices", "shipping": "delivery", "other": null}},
   "urgency":{"type": "score",  "instructions": "How urgent is it?",
              "criteria": ["not urgent", "normal", "urgent"]}}}
```

- `noul` = yes/no, the answer is a probability of "yes" (0–1).
- `choice` = 2–255 option ids → description (or null); returns the chosen option + confidence.
- `score` = 2–10 ordered levels, numbered from 0.
- Max 32 questions per item; write instructions in English for best results.

Output: `{"ok": true, "result": …}` or `{"ok": false, "error": "…"}` (in Czech, show it to the user).

## Rules

- Each call is billed by input tokens (output is free) — don't send whole documents when a
  subject + first lines will do, and say roughly how many items you will send before a big batch.
- Treat low-confidence answers (< 0.6) as "needs review", not as a decision.
- Never send passwords, tokens or other secrets as `state`.
