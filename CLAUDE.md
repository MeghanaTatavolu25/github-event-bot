You are my pair-programmer. I am preparing for a 1-hour live technical interview and I need a complete, working, deployed "Event-Driven GitHub Automation Bot". I will read and explain every line you write, so keep the code simple, small and well named, with short comments that explain WHY, not what.

FIRST: read the existing repo (git log and all files). It may already contain a partial skeleton: a FastAPI app in app/ (config.py, db.py, security.py, main.py) with a /webhook/github endpoint that verifies the HMAC signature and rejects duplicate delivery IDs, /health and /health/db, test_webhook.py, requirements.txt, .env.example. Keep whatever is correct, finish whatever is missing, and do not rewrite working code without telling me why. Then create CLAUDE.md in the repo root containing the PROJECT RULES section below, verbatim.

GOAL
A web app plus a bot, live on a public URL, that:
1. Lets a user sign in with GitHub and connect one or more of their repos.
2. Receives GitHub webhooks (issues, pull_request, push) from those repos.
3. Applies the user's rules: writes back to GitHub (adds a label or posts a comment) and sends a Slack notification.
4. Shows a login-protected dashboard with a live log of events, the actions taken, failures and retries, and a UI to manage the rules.
Live URL: https://github-event-bot-jeuz.onrender.com (Render free tier, auto-deploys from main). Local URL: http://localhost:8000.

STACK
Python 3.11+, FastAPI, pymongo (sync), httpx (sync client), Jinja2 server-rendered pages (no React, no build step, minimal inline CSS), Starlette SessionMiddleware (itsdangerous), cryptography (Fernet), pytest. Database: MongoDB Atlas, database name github_bot. Render start command: uvicorn app.main:app --host 0.0.0.0 --port $PORT.
Environment variables (names only, never print values): MONGODB_URI, GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, GITHUB_WEBHOOK_SECRET, SLACK_WEBHOOK_URL, SESSION_SECRET, APP_URL, optional GEMINI_API_KEY. Only app/config.py reads the environment. Do not open or print .env. If you need a new variable, add it to config.py and .env.example and tell me so I add it on Render.

PROJECT RULES (put these in CLAUDE.md)
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

DATA MODEL (MongoDB)
- users: github_id (unique), login, avatar_url, access_token_enc, created_at
- repos: user_id, full_name (unique), webhook_id, connected_at
- rules: user_id, repo (full_name, or "*" for all of the user's repos), name, enabled, event (issues | pull_request | push | any), conditions [{field: title|body|author|label, op: contains|equals, value}], actions [{type: add_label|post_comment|slack, value}], created_at
- events: delivery_id (unique), event, action, repo, sender, title, number, payload, received_at, user_id, status (received|processing|done|failed|ignored), attempts, last_error, next_retry_at
- actions: delivery_id, rule_id, rule_name, action_index, type, status (success|failed), detail (short, no secrets), created_at; unique on (delivery_id, rule_id, action_index)

STAGES
Stage 1 - Review and plan: read the repo, write CLAUDE.md, add the missing indexes, add pytest and a test for verify_signature. Confirm it runs locally and /health/db works.
Stage 2 - GitHub login: /auth/login, /auth/callback, /auth/logout. Scopes: public_repo admin:repo_hook. Authorize URL https://github.com/login/oauth/authorize, token exchange POST https://github.com/login/oauth/access_token with Accept: application/json, user from https://api.github.com/user. Redirect URI is APP_URL + /auth/callback. A landing page with a "Sign in with GitHub" button, and a protected /dashboard placeholder.
Stage 3 - Connect repos: a /repos page. The user enters "owner/name". Verify through GET /repos/{owner}/{name} that they have admin permission, then create the webhook with POST /repos/{owner}/{name}/hooks (config: url = APP_URL + /webhook/github, content_type json, secret = GITHUB_WEBHOOK_SECRET; events: issues, pull_request, push). If the hook already exists (422), find and reuse it. Allow disconnecting (delete the hook). The webhook handler answers 200 "ignored" and stores nothing for repos that are not connected. On the user's first connected repo, create one example rule: issues opened and title contains "bug" -> add_label "bug" + slack.
Stage 4 - Processing engine: after saving an event, a background task process_event(delivery_id) loads the owner's matching rules (enabled, repo match, event match, all conditions true). For each action: add_label -> POST /repos/{repo}/issues/{number}/labels; post_comment -> POST /repos/{repo}/issues/{number}/comments; slack -> POST to SLACK_WEBHOOK_URL with repo, event/action, title, link and sender. Push events support slack only. Use the repo owner's decrypted token. Record every action; skip actions already successful; mark the event done or failed. Implement the retry loop from rule 4.
Stage 5 - Dashboard and rules UI: /dashboard shows the latest 50 events (time, repo, event/action, title, status, attempts), each with its actions and any error, auto-refreshing every 10 seconds, filterable by repo and status, with a "Retry now" POST button on failed events. /rules lists, creates, enables/disables and deletes rules through a simple form.
Stage 6 - Hardening and docs: tests (signature, rule matching, duplicate handling), README.md (what it does, architecture, how to run locally, environment variables, how to set up the GitHub OAuth app, Slack, Atlas and Render, how to test with test_webhook.py and with a real repo, security notes, known limitations such as using the user's OAuth token instead of a GitHub App), a draft AI_NOTES.md, a final .env.example. Run all tests.
Stage 7 - OPTIONAL, only if I say "do stage 7": an AI summary and suggested priority for each issue/PR using the free Gemini API, shown in the Slack message and the dashboard. If the key is missing or the call fails, skip silently and never block the actions.

Begin with Stage 1 only.