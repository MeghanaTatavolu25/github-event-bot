# AI notes

> **How to read this file.** Everything outside the boxes marked **✍️ YOUR TURN** is factual and was checked against
> the code. The marked boxes need *your own words*: an interviewer will ask about them, and only you know the answers.
> Delete each box once you have written your part.

## 1. What was built

An event-driven GitHub automation bot, live at https://github-event-bot-jeuz.onrender.com.

- **Sign in with GitHub** (OAuth), then **connect repos**: the bot creates a webhook on each one.
- **Receives GitHub webhooks** (`issues`, `pull_request`, `push`), checks the signature, and saves every event.
- **Applies the user's rules**: add a label, post a comment, send a Slack message. Rules are data in MongoDB, not code.
- **Login-protected dashboard**: live event log (refreshes every 10 s), the actions taken, failures, automatic retries, a
  "Retry now" button, filters by repo and status, and a page to create, enable/disable and delete rules.
- **Stack:** Python, FastAPI, MongoDB Atlas (pymongo), Jinja2 server-rendered pages (no JavaScript framework),
  httpx, Fernet (cryptography), pytest. Hosted on Render's free tier.
- **Size:** about 2,350 lines across `app/`, `templates/` and `tests/`; **88 automated tests**.
- **Not built:** Stage 7 (AI summaries with Gemini) was optional and was skipped on purpose.

## 2. How AI was used

- **Tool:** Claude Code (Claude Sonnet 5.5) as a pair programmer in VS Code.
- **My part:** the specification: stack, data model, ten project rules (saved in `CLAUDE.md`), and a seven-stage plan
  with small steps. [confirm this is accurate]
- **Claude's part:** wrote the code and tests inside those rules, stage by stage, committing after each stage.
- **Guard rails:** `CLAUDE.md` forbids pushing, printing secrets and opening `.env`; every stage ended with a report of
  what changed, how to test it, and what could go wrong.

> **✍️ YOUR TURN: what I did myself.** Write 3 to 5 honest sentences. Examples of what to cover: which files you read
> line by line, what you ran and tested yourself, what you changed or asked Claude to change, what you set up by hand
> (GitHub OAuth apps, Slack webhook, Atlas, Render). Do not claim more than you did; be ready to explain any file.

## 3. Design decisions and the reason for each

| Decision | Why | Trade-off accepted |
|---|---|---|
| **Save the event first, answer 200 fast, do the work in the background** | GitHub waits only about 10 s; a crash after saving loses nothing | Work finishes a moment after the reply |
| **Unique index on `delivery_id`** | The database itself refuses a second copy of the same delivery, even under a race | One extra index |
| **Unique index on `(delivery_id, rule_id, action_index)` and a record per action** | A retry skips actions that already succeeded, so a label or Slack message is not repeated | At-least-once, not exactly-once (see section 8) |
| **Atomic "claim" with a 5-minute lease** (`find_one_and_update`) | The background task and the retry loop can never process the same event together; a crash is recovered when the lease expires | A crashed event waits up to 5 minutes |
| **Retry with exponential backoff** (30 s, 60 s, 120 s, 240 s, 480 s, then give up) | Gives a failing service time to recover without hammering it | After 5 retries a human must click "Retry now" |
| **Retry loop runs inside the app, state lives in MongoDB** | A restart or redeploy strands nothing: the loop finds unfinished events on startup | One process does web and retry work |
| **Rules stored as data** | Users change behavior from the UI, with no redeploy | Only AND conditions, two operators, three actions |
| **Rules react only to newly *opened* issues and PRs** | The bot's own label creates a `labeled` event; reacting to it would trigger the bot forever | Edits and closes are ignored |
| **Synchronous pymongo/httpx, with `def` routes and a thread pool where needed** | Simple code that is easy to read and explain | Fewer concurrent requests than a fully async design |
| **Server-rendered Jinja2 pages, `<meta refresh>` every 10 s** | No build step, no JavaScript, very little to go wrong | Whole page reloads; not instant like WebSockets |
| **`SameSite=Lax` cookie and POST-only state changes instead of CSRF tokens** | Browsers do not send a Lax cookie on a cross-site POST, so other sites cannot submit our forms | Relies on browser behavior |
| **GitHub tokens encrypted with Fernet** (key from `SESSION_SECRET`) | A leaked database does not leak access to users' repos | Changing `SESSION_SECRET` makes stored tokens unreadable |
| **Fail closed** | Missing webhook secret gives 503; blank `SESSION_SECRET`, bad `APP_URL` or no database stops startup | The app refuses to run half-configured |
| **OAuth user token instead of a GitHub App** | Much faster to build for the exercise | Broad access, actions appear as the user (section 8) |

## 4. How it was checked (not just "it looks right")

- **88 automated tests, all offline** (an in-memory fake MongoDB; GitHub and Slack faked at the HTTP level).
- **Break-it-on-purpose checks:** the code was deliberately broken in 15 ways (retry repeats actions, backoff ignored,
  other users' rules apply, unconnected repos stored, OAuth state not checked, cookie not `SameSite=Lax`,
  anyone can delete anyone's rule, and so on). Each break made a test fail, so the tests really guard the behavior.
- **Real database run:** the engine ran against the real Atlas cluster in a throwaway database (dropped afterwards)
  with 8 workers on one event. Result: processed once, one label call, one Slack call. A Slack failure then retried
  and finished without adding the label twice.
- **Secrets:** `.env` was never opened by Claude. Only true/false checks were used ("is it set", "does it start with
  `http`"). A scan found no real secret value in any committed file.
- **On the live site** (from the dashboard screenshots taken during testing): issues opened in `bot-test-repo` were
  labeled and announced on Slack and shown as `done`; the bot's own `labeled` events were shown as `ignored`; an issue
  without "bug" in the title was `ignored`. [confirm this still matches what you saw]
- **The look of the pages** was checked in headless Chrome screenshots (dashboard and rules, dark mode only).

## 5. Where the first draft was wrong, and what changed

| Problem | How it was found | Fix |
|---|---|---|
| `hmac.compare_digest` raised `TypeError` (a 500) on a non-ASCII signature header | A test written before the fix failed | Compare bytes; same fix for the OAuth `state` check |
| The webhook route was `async` but called blocking pymongo, which could freeze the server | Review against project rule 8 | Run the insert in a thread (`run_in_threadpool`) |
| A failed index creation at startup was ignored, silently turning off duplicate protection | Review against project rule 3 | Startup now fails loudly |
| The bot's own `labeled` event would re-trigger its rules (loop, repeated Slack alerts) | Thinking through the flow | Rules react to *opened* issues/PRs only |
| An issue title like `<!channel>` could ping a whole Slack channel | Thinking about text written by strangers | Escape `& < >` in Slack text |
| httpx error messages can contain the Slack URL (a secret) | Reading how errors are stored and shown | Store only status codes or the exception class name |
| `.env` was committed locally and GitHub rejected the push | Push-protection error | Rewrote the unpushed local commit; the secrets never left the machine |

Real set-up problems met on the way (all configuration, not code):

- Render's `APP_URL` was missing the `-jeuz` part of the address, so GitHub said `redirect_uri is not associated`.
- Render had no GitHub OAuth variables, so `/auth/login` answered "GitHub login is not configured".
- A MongoDB password with special characters made pymongo raise `InvalidURI` until it was URL-encoded or simplified.

> **✍️ YOUR TURN: the hardest bug, in my own words.** Interviewers love this question. Pick the one that cost you the
> most time and write 6 to 10 sentences. Use this shape, and use the real details you remember:
>
> 1. **Symptom:** what did you see (an error message, a screen, a wrong result)? Where?
> 2. **First guess:** what did you think was wrong, and why was that wrong or incomplete?
> 3. **How you narrowed it down:** what did you read, run, log or test?
> 4. **Root cause:** the actual reason, in one plain sentence.
> 5. **Fix and proof:** what changed, and how you know it is fixed (a test, a live run).
> 6. **Lesson:** what you would do differently or check earlier next time.
>
> Candidates from this project, if one is yours: the OAuth `redirect_uri` mismatch between the local and Render apps;
> the "could not talk to GitHub" login failure; the webhook that GitHub would not create for `localhost`;
> the signature check crashing on a non-ASCII header; the bot triggering itself through its own labels.

## 6. What I would do next (known limits)

- **Exactly-once actions.** If the server dies after an action succeeded but before it was recorded, a retry repeats
  it. Labels are harmless; a comment or Slack message could appear twice.
- **Use a GitHub App instead of the user's OAuth token:** the bot would act as itself, with narrow per-repo
  permissions, private-repo support, and one webhook for every installed repo.
- **A real queue and separate workers** (for example Redis or SQS) instead of a loop inside the web process.
- **Missing database indexes:** only the four unique indexes exist. Dashboard and retry queries will slow down as the
  events collection grows; they need indexes on `events (user_id, received_at)` and `events (status, next_retry_at)`.
- **Cleanup:** whole payloads are stored forever; add a TTL index or keep only the fields the app uses.
- **Rate limits:** GitHub's rate limits (and Slack's) are not read or respected yet; there is no limiter on our own routes.
- **Slack:** one shared webhook URL for all users; a per-user Slack connection would be needed for real multi-tenant use.
- **Dashboard:** a full-page refresh every 10 seconds; server-sent events would be smoother. Only the latest 50 events.
- **CI:** tests run by hand; a GitHub Actions run on every push would catch regressions.

> **✍️ YOUR TURN: what I would add first and why.** Choose the 2 improvements you believe matter most and explain your
> reasoning in your own words (for example: "I would add the missing indexes first, because ..."). A clear ranking with
> reasons impresses more than a long list.

## 7. Checked by me, not by the AI

> **✍️ YOUR TURN.** The AI could not do these for you. Write what you actually did and saw, with dates:
>
> - The real GitHub sign-in on the live site: [what happened]
> - A real issue opened with "bug" in the title: did it get the label, did Slack get the message, what did the dashboard show?
> - A failure you caused on purpose (for example a broken Slack URL) and what the retry looked like: [what happened]
> - Anything you tried that did not work, and what you did about it.
