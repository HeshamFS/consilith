# Consilith Sign in with ChatGPT sidecar (`apps/siwc`)

A small Node service for local use, with a loopback desktop mode and a private Docker
network mode. It lets the Python room server use the
person's own ChatGPT plan as a model, through OpenAI's Sign in with ChatGPT DevKit. The HTTP
contract is in [`docs/siwc.md`](../../docs/siwc.md).

## What it does

- Starts the DevKit's browser sign-in (OAuth with PKCE, loopback callback). `POST /login` returns
  the URL; the person opens it, signs in, and allows "ChatGPT plan usage".
- Keeps the session in the DevKit's own token store on this machine only (default
  `~/.consilith/siwc`). The file is encrypted with AES-256-GCM; the key is protected by
  Windows DPAPI, macOS Keychain, or Linux libsecret. Docker uses the Linux provider and
  keeps encrypted session and keyring files in a dedicated volume.
- Runs `POST /reply` against the Responses API with the person's plan, and lists the models the
  plan can use. Task results still come from registered tools; this is only the language model.
- Never logs tokens or prompts. The log line is `METHOD /path status`.

## Install the DevKit (one time)

The DevKit (`@siwc/local`) is **not published to npm**, and its licence is the
"Sign-in with ChatGPT DevKit Noncommercial License v1.0" (personal and local use). It is not
copied into this repository. Build it once next to the project and point the sidecar at it:

```
git clone https://github.com/openai/sign-in-with-chatgpt-devkit.git
cd sign-in-with-chatgpt-devkit
git checkout 0a36fefeb913055c8c7a1a29b63d82b96b2e841a   # the revision this sidecar was written against
npm ci
npm run build --workspace @siwc/local
```

Then set `CONSILITH_SIWC_DEVKIT` to the absolute path of its `packages/local/dist/index.js`.
(Alternatively `npm install --no-save <clone>/packages/local` inside `apps/siwc` lets
`import("@siwc/local")` resolve.) Requires Node 22.12 or newer.

## Portable local Docker mode

`docker/siwc.Dockerfile` builds the same Linux bridge image for Docker on Windows, macOS
and Linux. It has no host PowerShell, DPAPI, Node, or desktop-keychain dependency.
The SDK source is supplied as the named BuildKit context `siwc_sdk`; it is not copied
from an account directory. For a manual build after obtaining the pinned SDK above:

```sh
docker build --build-context siwc_sdk=/path/to/sign-in-with-chatgpt-devkit -f docker/siwc.Dockerfile -t consilith-chatgpt-bridge:local .
```

The platform's Compose setup provides these boundaries:

- The authenticated helper API listens on container port 8730 on the private platform
  network. It is not published to the host.
- The SDK keeps its original `127.0.0.1:8787/auth/callback` listener and all state, PKCE,
  nonce and identity checks. Host loopback port 8787 maps to container port 8788, a proxy
  which accepts only that callback path, GET method and expected Host header. It forwards
  to SDK loopback without forwarding cookies or authorization headers, and logs no URL.
- `/data` stores encrypted SDK sessions and an encrypted GNOME keyring. D-Bus and libsecret
  run inside the non-root container. A separate private unlock secret is mounted at
  `/run/secrets/siwc-keyring-password`; the helper authentication secret is mounted at
  `/run/secrets/siwc-shared-secret`. Neither is baked into the image or committed.
- A short root entrypoint copies these read-only secret mounts into private tmpfs with
  mode 0600 and node ownership, then `gosu` drops to UID 1000 before starting D-Bus,
  keyring or the helper. This handles host-owned mode-0600 files without weakening their
  permissions. Compose supplies only the initialization capabilities and enables
  `no-new-privileges`; the running helper has no effective capabilities.
- The unlock secret is required on restart. Losing it does not erase or reset stored
  connections. Container administrators with both the encrypted volume and unlock secret
  can access the store; this is not hardware-backed protection against the Docker host.

The browser and Docker must run on the same machine so the OAuth loopback callback reaches
this container. A remote Docker daemon is not covered by this local flow. Port 8787 must
be available; changing it requires matching the SDK callback setting and host port mapping.

An existing Windows DPAPI session remains in its original store. Linux cannot decrypt it;
sign in once in the new container after the platform operator switches the bridge endpoint.
This does not log out, revoke, unlink, or overwrite the desktop connection. Synthetic
storage and route tests do not prove live OAuth or account/model access on every host OS.

## Run

```
CONSILITH_SIWC_SECRET=<long random string>  \
CONSILITH_SIWC_DEVKIT=/abs/path/to/packages/local/dist/index.js \
npm --prefix apps/siwc start
```

PowerShell:

```
$env:CONSILITH_SIWC_SECRET = '<long random string>'
$env:CONSILITH_SIWC_DEVKIT = 'C:\path\to\devkit\packages\local\dist\index.js'
npm --prefix apps/siwc start
```

| Variable | Default | Meaning |
|---|---|---|
| `CONSILITH_SIWC_SECRET` | none | Shared secret; required unless supplied by the secret file. |
| `CONSILITH_SIWC_SECRET_FILE` | none | File containing the helper secret. Docker mounts it read-only. |
| `CONSILITH_SIWC_PORT` | `8730` | Authenticated HTTP API port. |
| `CONSILITH_SIWC_HOST` | `127.0.0.1` | Bind address; Docker uses `0.0.0.0` on its private network. |
| `CONSILITH_SIWC_DEVKIT` | none | Absolute path to the built DevKit `dist/index.js`. Otherwise `@siwc/local` is imported. |
| `CONSILITH_SIWC_CALLBACK_PORT` | `8787` | Loopback port for the OAuth callback (the DevKit example's default). `0` picks a free port. |
| `CONSILITH_SIWC_CALLBACK_PROXY_PORT` | `0` (disabled) | Docker callback ingress port; requires a distinct, fixed SDK callback port. |
| `CONSILITH_SIWC_HOME` | `~/.consilith/siwc` | Absolute path of the token store. Keep it out of the repository. |
| `CONSILITH_SIWC_MODEL` | first listed model | Model used by `/reply` when the request has no `model`. |
| `CONSILITH_SIWC_MULTI` | off | `1` turns on one ChatGPT account per person (`/v2/accounts/:key/...`). Off keeps the single-account routes. See `docs/siwc.md`. |
| `CONSILITH_SIWC_PASTE` | off | `1` (with MULTI) accepts a pasted sign-in address for a browser that is not on the host. |
| `CONSILITH_SIWC_AUTH_ORIGINS` | `https://auth.openai.com` | Comma-separated https origins the sign-in address may be on, published in `/v2/capabilities`. |

Every request must carry `X-Consilith-Siwc: <secret>`, or it gets 403.

## The flow

1. `GET /status` shows `signedIn: false`.
2. `POST /login` returns `{url}`. Open it in the browser, sign in, allow plan usage. The DevKit's
   loopback listener receives the callback and stores the session. Poll `GET /status` until
   `signedIn` is true (`signingIn` is true meanwhile; `error` appears if it failed).
3. `GET /models` lists models; `POST /reply` runs a request.
4. `POST /logout` revokes and removes the local credentials.
