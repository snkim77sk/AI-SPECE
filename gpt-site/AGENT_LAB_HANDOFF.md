# G2B mock agent UI — local only

Base commit: `3f7d5e9c2cafd233628a3844590b3447012baa08`. Existing Site version observed: 52.
No Git push, saved hosted version, deployment, model call, or production data access was performed by the demo.

## Authentication findings

Sites metadata confirms a custom audience containing the owner only and no external visitors. The application has ChatGPT identity header helpers in `app/chatgpt-auth.ts`; searches found no callers in app/lib. Existing server-sync documents reliance on the owner-private Sites boundary. This is not row-level authorization for a multi-user agent. Do not reuse its bearer updater access for an interactive AI user.

Real integration needs trusted platform identity at the server, per-user evidence authorization, and same-origin/CSRF controls where requests mutate state. Client-provided scopes or IDs are not authorization. The demo permission selector simulates a result only; it is NOT an authentication test or security implementation.

## Implementation

- `/agent-lab`: development-only route (production uses notFound).
- Three task fixtures: collection diagnosis, service notice analysis, market sample analysis.
- Question → mock response → citation link → focused evidence card.
- Normal, stale, denied, and error cases; answer and evidence clear on task/scenario changes.
- React text rendering, 500-character question bound, citation checks, explicit synthetic-data limitations.
- No fetch/connector/LLM/DB access. Questions remain in component memory.
- OriginalShell does not mount the server-sync poller on this route.
- The mock is deterministic and task-based. It does not semantically answer arbitrary questions.
- Prior Python evidence gateway remains a separate candidate; it is not falsely represented as a connected backend.

## Verification

- 10 new Node tests passed.
- Full existing suite including new cases: 712 passed, 0 failed.
- TypeScript check passed.
- Production build passed; production HTTP 404 was not separately exercised.
- Browser: all three tasks, custom question, citation navigation/focus, denied (no evidence), error, stale warning, return to normal.
- Desktop layout inspected. Mobile CSS exists; mobile viewport QA not performed.

## Remaining

Actual model/provider/account, budget reservations and usage settlement, authoritative server-side data retrieval, real authentication/authorization tests, semantic answer validation, API-backed UI tests, production rollout. No live connection or deployment is implied by this demo.

Local preview was verified at http://127.0.0.1:4173/agent-lab#demo-market. This is an ephemeral local address, not a published URL. Restart using the approved Sites preview helper when needed.
