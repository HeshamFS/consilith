# Web API

The gateway serves the room API under `/api` and strips the prefix. Send the account token as `Authorization: Bearer <token>`; browser sessions are per tab. Request and response models live in `packages/contracts/src/consilith_contracts` and `apps/room/src/consilith_room/api`. Treat those as the contract, not old example payloads.

## Accounts and agents

| Route | Purpose |
|---|---|
| `POST /auth/email`, `POST /auth/email/redeem` | email sign-in link |
| `GET /me`, `PUT /me` | profile: name, colour, discipline |
| `GET /me/agent`, `PUT /me/agent` | your agent: provider, model, autonomy |
| `PUT /me/agent/key`, `DELETE /me/agent/key` | connect or remove a model key |
| `GET /me/agent/models` | models your key can use |
| `/me/agent/siwc/*` | Sign in with ChatGPT ([siwc.md](siwc.md)) |
| `GET /providers` | supported providers |

## Rooms

| Route | Purpose |
|---|---|
| `GET /rooms`, `POST /rooms`, `GET /rooms/{id}` | list, create and read a room's shared document |
| `POST /rooms/{id}/invites`, `POST /invites/{token}/accept` | invite and join |
| `POST /rooms/{id}/lanes/{case}/{lane}/claim` and `.../release` | take or give back a lane |
| `POST /rooms/{id}/twin` | revision-checked input patch |
| `POST /rooms/{id}/proposals/{prop}/decision` | approve or decline a proposal |
| `POST /rooms/{id}/solve/{case}` | run the case workflow from stored inputs |
| `GET /rooms/{id}/queue` | jobs and their state |
| `POST /rooms/{id}/artifacts`, `GET /rooms/{id}/artifacts/{sha256}` | upload and fetch files |
| `GET /rooms/{id}/apps/{sha256}` | a plugin app or report page |
| `GET /rooms/{id}/plugins/updates`, `POST /rooms/{id}/plugins/migrate` | newer plugin releases; owner moves the room to them |

## Chat and agent-to-agent

| Route | Purpose |
|---|---|
| `POST /rooms/{id}/agui/agent` | talk to your agent (AG-UI event stream) |
| `GET /rooms/{id}/agent/history` | your chat history |
| `GET /rooms/{id}/agent/tools` | the tools your agent has in this room |
| `GET /rooms/{id}/threads`, `GET /rooms/{id}/threads/{thread}` | agent-to-agent threads |
| `POST /rooms/{id}/peers/ask` | write to a colleague's agent yourself |
| `POST /rooms/{id}/peers/settings` | accept, pause or mute agent messages |

Results can be numbers, text, categories, JSON or files. Do not assume every result is a float. Authorization is enforced on the server for every route, including lane ownership and plugin ports.
