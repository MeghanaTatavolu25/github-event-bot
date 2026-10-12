# Project: Event-Driven GitHub Automation Bot

Never open, read or print the .env file or any secret value.

## PROJECT RULES

1. Work in small stages. After each stage tell me: what changed in plain English, how to test it (exact commands and what I should see), and what could go wrong. Then STOP and wait for me to say "continue".
2. Verify X-Hub-Signature-256 on the raw request body for every webhook, with a constant-time comparison. Fail closed if the secret is missing.
3. Idempotency: delivery ID has a unique index. Each action is also unique per (delivery_id, rule_id, action_index), so a retry never repeats an action that already succeeded.
4. Save the event BEFORE any work. The webhook answers fast and processing runs in a background task. A failure marks the event failed with attempts and last_error, and is retried automatically up to 5 times with increasing delay (30s * 2^attempts). A retry loop started at app startup also picks up events left unprocessed after a restart.
5. Secrets only via environment variables. Never log or print tokens, secrets, the Slack URL or full payloads. Encrypt GitHub access tokens at rest with Fernet (key derived from SESSION_SECRET via SHA-256). .env is gitignored and .env.example has fake values only.
6. OAuth: random "state" per login, verified at the callback. Session cookie signed, HttpOnly, SameSite=Lax, Secure when APP_URL is https. Every dashboard route requires login, and every query is filtered by the logged-in user so nobody can see another user's data. State-changing actions are POST only.
7. Structured JSON logs, one line each, with event, delivery_id, repo and outcome.
8. pymongo and httpx are synchronous: use plain def for routes that touch them (or a threadpool), and never block the async event loop. Every outbound HTTP call has a 10-second timeout.
9. Simple, clean structure, no dead code, no over-engineering. Commit after each stage with a clear message. Do NOT push; I will push.
10. Free services only. No paid APIs, no credit cards.
