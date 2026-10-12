# GitHub Event Bot

An event-driven GitHub automation bot. Sign in with GitHub, connect a repository, and write simple
rules such as *"when an issue is opened and its title contains `bug`, add the label `bug` and
ping Slack"*. GitHub sends the bot webhooks; the bot checks them, saves them, applies your rules,
and shows everything it did on a dashboard.

Live: https://github-event-bot-jeuz.onrender.com (Render free tier) · Local: http://localhost:8000

## How it works

```
GitHub ──webhook──▶ POST /webhook/github
                      1. verify X-Hub-Signature-256 on the raw body   (bad → 401, nothing saved)
                      2. repo not connected?                           (→ 200 "ignored", nothing saved)
                      3. save the event (unique delivery id)           (repeat → 200 "duplicate")
                      4. reply 200 right away
                              │
                              ▼  background task (+ a retry loop that runs for the life of the app)
                      process_event: claim the event atomically
                         → find the owner's matching rules
                         → run each action once: add label / post comment (GitHub), alert (Slack)
                         → record every action; mark the event done / failed
                         → failed? retry after 30s, 60s, 120s, 240s, 480s, then give up

Browser ──▶ Sign in with GitHub ──▶ /dashboard  (events, actions, errors, Retry now)
                                    /repos      (connect / disconnect, creates the webhook)
                                    /rules      (create, enable/disable, delete)
```

| File | Job |
|---|---|
| `app/config.py` | The only place that reads environment variables |
| `app/main.py` | Wires the app together: session cookie, routers, startup, `/health`, `/health/db` |
| `app/auth.py` | GitHub login: `/auth/login`, `/auth/callback`, `POST /auth/logout` |
| `app/web.py` | Shared page helpers: templates, "who is logged in", one-time messages |
| `app/repos.py` | Connect / disconnect repos (creates and deletes the GitHub webhook) |
| `app/webhook.py` | Receives webhooks: verify, filter, save |
| `app/engine.py` | Rule matching, actions, retries, the retry loop |
| `app/rules.py` | Rules page and the form validation |
| `app/dashboard.py` | Dashboard and "Retry now" |
| `app/github.py`, `app/slack.py` | The outgoing HTTP calls (10-second timeout each) |
| `app/security.py` | Signature check, token encryption |
| `app/db.py` | MongoDB collections and the unique indexes |
| `app/logs.py` | One-line JSON logs |
| `templates/` | Server-rendered pages (Jinja2, no JavaScript) |
| `test_webhook.py` | Send a signed fake webhook to a running app |
| `tests/` | Automated tests (no network, no real database) |

MongoDB database `github_bot`: `users`, `repos`, `rules`, `events`, `actions`. Unique indexes:
`users.github_id`, `repos.full_name`, `events.delivery_id`, and `actions (delivery_id, rule_id, action_index)`.

## Rules

A rule has a repo (or *all my repos*), an event (`issues`, `pull_request`, `push`, `any`),
conditions that must **all** match (`title`, `body`, `author` or `label`, with `contains` or
`equals`, not case sensitive), and actions: `add_label`, `post_comment`, or `slack`.

- Rules run for **newly opened** issues and pull requests, and for every push. Edits, closes and the
  bot's own `labeled` events are ignored; otherwise the bot would trigger itself.
- Push events have no issue to attach to, so only `slack` runs for them.
- The Slack action's value is an optional message prefix. The message also has repo, event, title, link and sender.
- The first time you connect a repo you get one example rule (issue title contains `bug` → label `bug` + Slack).

## Run locally (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env      # then edit .env (see the table below). Never commit it.
uvicorn app.main:app --reload
```

If activation is blocked: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` (once).
Open http://localhost:8000. Health checks: `curl.exe http://localhost:8000/health` and `/health/db`.

The app refuses to start if `SESSION_SECRET` is empty, `APP_URL` is malformed, or MongoDB is unreachable
(it needs the unique indexes). Read the error: it never prints secrets.

## Environment variables

Only `app/config.py` reads these. On Render, add them under *Environment*.

| Name | What | Example |
|---|---|---|
| `MONGODB_URI` | Atlas connection string | `mongodb+srv://user:pass@cluster0.xxxx.mongodb.net/?retryWrites=true&w=majority` |
| `GITHUB_CLIENT_ID` | OAuth App client id | |
| `GITHUB_CLIENT_SECRET` | OAuth App client secret | |
| `GITHUB_WEBHOOK_SECRET` | Random string used to sign webhooks | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `SLACK_WEBHOOK_URL` | Slack incoming webhook | `https://hooks.slack.com/services/...` |
| `SESSION_SECRET` | Random string: signs the cookie, encrypts GitHub tokens | same command as above |
| `APP_URL` | Public base URL, no trailing slash | `http://localhost:8000` or `https://github-event-bot-jeuz.onrender.com` |

## Setup

**MongoDB Atlas (free M0).** Create a cluster → *Database Access*: add a user (use a letters-and-digits
password, or URL-encode it) → *Network Access*: allow `0.0.0.0/0` (Render's free tier has no fixed IP) →
*Connect → Drivers* and copy the string into `MONGODB_URI`. The `github_bot` database is created on first use.

**GitHub OAuth App.** GitHub → Settings → Developer settings → OAuth Apps → New. Homepage URL = your
`APP_URL`. Authorization callback URL = `APP_URL/auth/callback`. A GitHub OAuth App has one callback, so make
one app for `http://localhost:8000` and one for the Render URL. Copy the client id and generate a client secret.
The app asks for the scopes `public_repo` and `admin:repo_hook`.

**Slack.** https://api.slack.com/apps → Create New App → *Incoming Webhooks* → on → *Add New Webhook to
Workspace* → pick a channel → copy the URL into `SLACK_WEBHOOK_URL`.

**Render (free).** New → Web Service → connect this GitHub repo, branch `main` (auto-deploys on push).
Build command `pip install -r requirements.txt`. Start command
`uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Health check path `/health`. Add the seven environment
variables with `APP_URL=https://github-event-bot-jeuz.onrender.com`. The app needs Python 3.10 or newer
(developed on 3.13); set `PYTHON_VERSION` in Render only if you need to pin it.

## Testing

**Automated** (offline: a fake in-memory MongoDB and fake GitHub/Slack):

```powershell
python -m pytest
```

They cover: signature checks, token encryption, login (state, cookie flags), connecting repos, the webhook
route, duplicate deliveries, rule matching, actions, retries and backoff, restart recovery, user isolation,
and HTML escaping.

**A fake webhook against a running app** (`test_webhook.py`). The repo must already be connected on `/repos`.

```powershell
python test_webhook.py owner/name "Login page crashes: bug"                  # issue opened → label + Slack
python test_webhook.py owner/name "Login bug" --delivery-id demo-1           # run it twice: 2nd says "duplicate"
python test_webhook.py owner/name "Broken bug" --number 99999                # issue 99999 does not exist →
                                                                              # GitHub 404 → watch the dashboard retry
python test_webhook.py owner/name "fix typo" --event push                    # Slack only
python test_webhook.py owner/name "A bug" --url https://github-event-bot-jeuz.onrender.com
```

**With a real repo.** GitHub cannot reach `localhost`, so use the Render deployment (or a tunnel such as ngrok with
`APP_URL` set to the tunnel URL): sign in → *Repos* → connect `owner/name` (you need admin rights) → open an issue
whose title contains `bug` → within seconds the issue gets the label, Slack gets a message, and the dashboard shows
the event, its actions and any error. To see a failure, temporarily break `SLACK_WEBHOOK_URL`, or use a rule that
comments on a repo where your token cannot write.

## Security notes

- **Webhooks:** `X-Hub-Signature-256` is checked on the raw body with a constant-time comparison; if the secret is
  missing, every webhook is refused. Unconnected repos are not stored.
- **Idempotency:** unique `delivery_id`; unique `(delivery_id, rule_id, action_index)` so a retry skips actions that
  already succeeded. Verified against a real MongoDB with 8 simultaneous workers.
- **Tokens:** GitHub access tokens are encrypted at rest (Fernet, key = SHA-256 of `SESSION_SECRET`). Secrets, tokens
  and the Slack URL are never logged; stored and displayed error text is sanitized.
- **Login:** random one-time `state`, checked at the callback. Cookie is signed, `HttpOnly`, `SameSite=Lax`, and `Secure`
  when `APP_URL` is https. Every page and every query is limited to the logged-in user. All state changes are POST, and
  `SameSite=Lax` stops other sites from submitting them.
- **Output:** pages escape HTML (issue titles come from strangers); Slack text is escaped so a title cannot `@channel`.
- **Inputs:** repo names are validated before they go into GitHub URLs; the rule form is validated on the server.
- **Network:** every outgoing call has a 10-second timeout.

## Known limitations

- Uses the signed-in user's OAuth token instead of a GitHub App, so actions appear as that user, access is broad, and
  only public repos work (`public_repo` scope). A GitHub App would fix all three.
- If the server dies after an action succeeded but before it was recorded, a retry repeats it. Labels are harmless;
  a comment or Slack message could repeat (at-least-once delivery).
- Render's free tier sleeps after about 15 minutes idle. The first webhook can time out (GitHub allows 10 seconds) and
  shows as failed under the repo's *Settings → Webhooks → Recent Deliveries*; click *Redeliver*. Retries pause while asleep.
- One process: the retry loop runs inside the web app and checks every 15 seconds, 20 events at a time.
- One shared `SLACK_WEBHOOK_URL` for all users. A reused existing webhook keeps its old secret.
- Rules react to *opened* issues/PRs only (see Rules). Whole payloads are stored with no cleanup, and the dashboard
  shows the latest 50 events and reloads the page every 10 seconds (no live push).
- Changing `SESSION_SECRET` signs everyone out and makes stored tokens unreadable; sign in again.
- No rate limiting. Dependencies are not pinned (tested with FastAPI 0.143, pymongo 4.18, Starlette 1.7).
- Uvicorn's own access lines are plain text; the app's lines are JSON.
