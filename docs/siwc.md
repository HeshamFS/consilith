# Sign in with ChatGPT sidecar: HTTP contract

The sidecar in `apps/siwc` lets the Python room server use the signed-in person's own ChatGPT
plan as a model. In the default Docker deployment its authenticated API is
`http://siwc:8730` on the private room/provider network; it has no published host API port.
Standalone desktop mode defaults to `http://127.0.0.1:8730`.

Sources:

- Cookbook article: https://developers.openai.com/cookbook/articles/sign-in-with-chatgpt
- DevKit: https://github.com/openai/sign-in-with-chatgpt-devkit

How to run it: [`apps/siwc/README.md`](../apps/siwc/README.md).
For the isolated Docker deployment, use [`docker.md`](docker.md). The bridge, SDK, D-Bus and
Linux keyring run in Docker. Host loopback 8787 forwards only the restricted OAuth callback
to the SDK; the browser must be on the Docker host. Encrypted session/keyring data live in a
dedicated volume, unlocked by a separate secret outside the image. The room retains its
account-owner binding independently. Existing Windows DPAPI credentials cannot be reused in
Linux; authorize the bridge separately when moving between credential stores. Storage/route checks and actual account authorization are separate gates.

Account authorization is per user and per deployment. Credentials and private qualification receipts are not included in this repository.

## Conventions

- Every authenticated API request must send `X-Consilith-Siwc: <secret>` (supplied by
  `CONSILITH_SIWC_SECRET` or its secret file).
  A missing or wrong value gets `403 {"error":{"code":"forbidden","message":"..."}}`, on every API path.
  The separate restricted browser callback uses the SDK's OAuth state and PKCE validation.
- Bodies and responses are JSON. Tokens and prompts are never returned or logged.
- Errors always look like `{"error": {"code": string, "message": string}}`.
- Extra fields may appear in future; ignore fields you do not know.
- Response streams have a180-second bound and are cancelled if the room disconnects. The room
  timeout is190 seconds so the helper can return its own error first.
- Managed desktop helpers expose authenticated `POST /shutdown` for the local lifecycle command.
  It stops the process without revoking the saved connection; it is not routed through the room.

## `GET /status`

Signed out:

```json
{"signedIn": false, "signingIn": false}
```

Signed in:

```json
{"signedIn": true, "signingIn": false, "account": {"email": "person@example.org"}}
```

- `signedIn` is true only when a session exists **and** the person granted ChatGPT plan usage.
- `signingIn` is true while a `/login` flow is waiting for the browser.
- `account.email` appears when the identity token carried it. `account.plan` is never set: the
  DevKit does not expose the plan name.
- `error` (`{code, message}`) appears when the last sign-in or request failed.

## `POST /login`

No body. Starts the DevKit's browser sign-in (OAuth with PKCE, loopback callback) and returns the
URL the person must open in a browser:

```json
{"url": "https://auth.openai.com/oauth/authorize?..."}
```

The sidecar does not open a browser itself. When the person finishes, the encrypted session is
stored in the configured credential store (the dedicated volume in Docker), and `GET /status`
flips to `signedIn: true`. Calling `/login` again while a flow is waiting
returns the same URL. Errors: `409 already_signed_in`; `502`/`504` with the DevKit code
(for example `callback_port_unavailable`, `discovery_failed`, `login_timeout`).

## `POST /logout`

No body. Cancels a pending sign-in, revokes the connection, and removes local credentials:

```json
{"signedIn": false}
```

If the remote revocation could not be confirmed, local credentials are still removed and the
response carries `"warning": {"code": "revocation_failed", "message": "..."}`.

## `GET /models`

```json
{"models": [{"id": "gpt-5", "label": "GPT-5"}], "source": "plan"}
```

`source: "plan"` means the list came from ChatGPT for the signed-in plan (the DevKit's
`listModels`). No built-in fallback list is used. The `id` values are what `/reply` accepts.
Error: `409 not_signed_in`.

## `POST /reply`

Request:

```json
{"system": "You are a careful research assistant.", "text": "Summarise the task requirements.", "model": "gpt-5"}
```

- `system` (string, may be empty) becomes the instructions; `text` (non-empty string) is the user
  message; `model` is optional (default: `CONSILITH_SIWC_MODEL`, else the first listed model).

Response:

```json
{"text": "...", "model": "gpt-5"}
```

The call returns when the response has completed (no streaming to the caller). Errors:

| Status | `error.code` | When |
|---|---|---|
| 409 | `not_signed_in` | No session, or it needs signing in again. Call `/login`. |
| 409 | `plan_usage_not_enabled` | Signed in, but plan usage was not granted. Call `/login` again. |
| 409 | `busy` | A connection change is in progress. |
| 400 | `invalid_request` / `invalid_json` | Bad body or model. |
| 413 | `body_too_large` | Body over 8 MB. |
| 429 | DevKit code | The plan's usage limit was reached. |
| 502 | DevKit code | ChatGPT or the network failed (message is safe to show). |

## Limits and terms

- Plan usage is for open-source tools and personal local projects. Paid or hosted apps need OpenAI's
  waitlist. The DevKit's licence is noncommercial. Usage counts against the person's plan or credits.
- The pinned DevKit is built into the Docker image from source; standalone installation is
  documented in the sidecar README. The example model IDs above are HTTP examples, not a
  guaranteed model catalog; use the signed-in account's actual `/models` response.

## Several ChatGPT accounts (`CONSILITH_SIWC_MULTI`)

Off by default. With it off, everything above is exactly as before: one ChatGPT sign-in per host and
one owner in the room. Turn it on (the same variable on the room server and on the sidecar) only when
two or more people use their own ChatGPT plan.

With it on, the old routes answer `404 legacy_routes_disabled`, and each person has an account on the
sidecar, named by `accountKey`: the first 32 hex characters of `HMAC-SHA256(secret, "siwc-account:" + person)`.
The room computes the same key from the shared secret, so the sidecar never learns who a key belongs to.

### Routes

All need the `X-Consilith-Siwc` header. With the flag off, `/v2/accounts/...` answers `404 multi_disabled`.

| Route | Meaning |
|---|---|
| `GET /v2/capabilities` | `{apiVersion, multi, signIn: {ttlSeconds, attemptsPer10Minutes, authOrigins, pasteCallback}, identity, reply, ...}`. Always available. |
| `GET /v2/accounts/:key/status` | Like `/status` for one account. A refused sign-in shows in `error`. |
| `POST /v2/accounts/:key/login` | Like `/login`. Optional body `{"newProfile": true}` to sign a different ChatGPT account into this slot. |
| `POST /v2/accounts/:key/logout` | Like `/logout`. Cancels the sign-in only if this account holds it. |
| `GET /v2/accounts/:key/models` | Like `/models`. |
| `POST /v2/accounts/:key/reply` | Like `/reply`. |
| `POST /v2/accounts/:key/bind-legacy` | The room tells the sidecar that this key is the person who signed in before several accounts existed: their account keeps the old directory and key label. |
| `POST /v2/accounts/:key/callback` | Paste-the-callback (below). |

### Rules

- **One account per ChatGPT identity.** When a sign-in finishes, its identity (the stable id if the
  DevKit offers one, otherwise the email) is compared with every other account. A match signs the new
  account out and the status carries `identity_in_use`. If the identity has neither id nor email and
  another account is connected, the new one is refused with `identity_unknown`. Checks run one at a time.
- **One sign-in at a time.** The OAuth callback is on a fixed loopback port. A sign-in holds a mutex for
  150 seconds, only the holder can cancel it, and a person gets 3 attempts per 10 minutes
  (`429 sign_in_rate_limited`). A second person gets `409 sign_in_busy` with no URL in the body. A person
  who is already connected gets `409 already_signed_in`.
- **Replies.** At most 4 replies at once across all accounts, and one in flight per account
  (`409 busy` for a second).
- **Storage.** Accounts are listed in `home/accounts.json` (mode 600) with a keyed hash of each identity.
  The legacy account keeps `home` and the legacy key label. A new account uses `home/<accountKey>` and its
  own key entry (`secret-tool ... account <key>` on Linux, a `consilith-siwc:<key>` entry on macOS, its own
  `credential-key.dpapi` on Windows). Nothing is moved and no key changes. The macOS key now goes in on
  standard input of `security -i`, not in a process argument.
- **Restart.** `accounts.json` and each directory are read again; people stay signed in.

### Paste-the-callback

Off unless the sidecar is started with `CONSILITH_SIWC_PASTE=1`. For a person whose browser is not on the
host: after ChatGPT sign-in the browser ends on a `http://127.0.0.1:8787/auth/callback?...` page that does
not load. They paste that address into the page, which sends it to `POST /me/agent/siwc/callback` in the
body. The sidecar accepts it only for the account that holds the sign-in, only with the `state` of that
sign-in, and once; it then makes the same request to the SDK's listener the browser would have made, and the
SDK checks `state` and PKCE itself. The address is never logged or returned. Not yet run against a real
sign-in. The room's journal and log redaction lists do not yet include `code` and `state` (`protocols/journal.py`
belongs to another package).

### On the room

`GET /me/agent/siwc` answers for the signed-in person only. With several accounts on it adds `multi: true`,
masks the email (`a***@example.org`), puts the server's words in `reason` (including a refused identity or a
busy sign-in), and sets `offerDisconnect` when the person has left every room. `POST /me/agent/siwc/login`
checks that the sign-in address is https on an origin the sidecar declared in its capabilities and returns
`authOrigins` so the page checks it too. People sign in with a private window or after signing out of ChatGPT,
otherwise ChatGPT can complete the sign-in with an account that is already open in that browser.
