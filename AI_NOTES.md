# AI notes (draft: edit the [bracketed] parts so they are true for you)

## How AI was used

- **Tool:** Claude Code (Claude Sonnet 5.5) as a pair programmer inside VS Code.
- **Who decided what:** [I] chose the problem scope, stack, data model, the ten project rules in `CLAUDE.md`, and the
  stage order. Claude wrote the code and tests inside those rules and reported after each stage.
- **How it was kept on a leash:** `CLAUDE.md` rules (small stages, signature check, idempotency, save-first, no secrets in
  logs, free services only, no pushing), a rule never to open `.env`, and a "ponytail" setting that pushes toward the
  smallest working solution.
- [Add: the parts I wrote or rewrote by hand, and what I read line by line.]

## How the work was checked (not just "it looks right")

- **87 automated tests**, all offline (fake in-memory MongoDB, fake GitHub/Slack at the HTTP level).
- **Break-it-on-purpose checks:** after writing the tests, the code was deliberately broken in 15 ways (retry repeats
  actions, backoff ignored, rules of other users apply, unconnected repos stored, OAuth state not checked, cookie not
  `SameSite=Lax`, and so on). Each break made a test fail, which shows the tests guard the behavior and are not just
  passing by accident.
- **Real database run:** the engine was run against the real MongoDB Atlas cluster in a throwaway database (dropped
  afterwards) with 8 simultaneous workers on one event. Result: processed exactly once, one label call, one Slack call.
- **Format checks without reading secrets:** `.env` was never opened. True/false checks (is it set, does it start with
  `http`) found a typo in `APP_URL` and two missing OAuth values.

## Where the first draft was wrong, and what was changed

| Problem | How it was found | Fix |
|---|---|---|
| `hmac.compare_digest` raised `TypeError` (a 500) on a non-ASCII signature header | A test written before the fix failed | Compare bytes; same fix for the OAuth `state` check |
| Webhook was `async` but called blocking pymongo, freezing the server | Review against project rule 8 | Run the insert in a thread |
| App ignored a failed index creation at startup, silently disabling duplicate protection | Review against project rule 3 | Fail loudly at startup |
| The bot's own `labeled` event would re-trigger its rules (loop, repeated Slack alerts) | Thinking through the flow | Rules react to *opened* issues/PRs only |
| An issue title like `<!channel>` would ping a whole Slack channel | Threat thinking about stranger-written text | Escape `& < >` in Slack text |
| httpx error messages can contain the Slack URL (a secret) | Reading how errors are stored and shown | Store only status codes or the exception class name |
| `.env` was committed locally and GitHub rejected the push | Push protection error | Rewrote the unpushed local commit; secrets never left the machine |

## Design decisions to be ready to explain

- **Save first, answer fast, work later:** the event is stored before anything else, so a crash loses nothing.
- **Why two unique indexes:** `delivery_id` stops the same webhook being processed twice;
  `(delivery_id, rule_id, action_index)` stops a retry repeating an action that worked.
- **Atomic claim with a lease:** `find_one_and_update` moves an event to `processing` in one step, so the background task and
  the retry loop never both work on it. If the server dies, the lease runs out (5 minutes) and the loop retries it.
- **Backoff:** `30s * 2^(try-1)` for up to 5 retries, then the event stays `failed` and the dashboard says it gave up.
  "Retry now" makes it due immediately.
- **Why `SameSite=Lax` instead of CSRF tokens:** state changes are POST-only and browsers do not send Lax cookies on
  cross-site POSTs. Simple, and enough for this app.
- **Why OAuth token and not a GitHub App:** quicker to build for the exercise. The costs are listed in the README.
- **Why polling (meta refresh) and not websockets:** no JavaScript, no extra moving parts.

## Not verified by AI

- The real GitHub OAuth round trip and real webhooks from GitHub (they need the OAuth app and a public URL). They are
  covered by tests with GitHub faked, and by `test_webhook.py`. [Add: what I confirmed myself on the live site.]
- The Render deployment. [Add: result of my deploy and the first real issue test.]
