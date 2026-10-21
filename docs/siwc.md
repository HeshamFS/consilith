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
