# Web API

The Docker gateway exposes the room service under `/api`; it removes that prefix when forwarding. Authenticate requests using the account bearer token. Browser sessions are tab-scoped. The public schemas are in `packages/contracts/src/consilith_contracts` and route definitions in `apps/room/src/consilith_room/api/app.py`.

## Discovery and rooms

- `GET /capabilities`: capability descriptors used for rooms, roles and workspace controls.
- `GET /plugins`: installed plugin metadata.
- `GET /rooms/{room_id}`: current shared document, including ownership, runs, typed results and ledger references.
- `POST /rooms/{room_id}/plugins/{plugin_id}/activate`: explicitly activate an installed plugin, subject to authority and compatibility checks.
- `GET /rooms/{room_id}/knowledge/{knowledge_id}`: authorized access to active, pinned plugin knowledge.

## Execution and composition

- `POST /rooms/{room_id}/twin`: a revision-aware authorized patch with an operation ID and reason.
- `POST /rooms/{room_id}/solve/{case_id}`: execute the registered capability workflow using stored inputs.
- `GET /rooms/{room_id}/surfaces/{case_id}`: retrieve a saved workspace surface.

Input and result schemas are supplied by the installed capability and public contracts. Results may contain numbers, text, categories, JSON or content-addressed artifacts. Do not assume every result is a float or every capability is scientific. Readiness, input-save acknowledgment and revision changes are separate from tool completion.

The shared document exposes proposal decisions, evidence and replay state. Authorization is enforced server-side, including lane ownership and declared plugin ports. Treat the Python schemas and registered route definitions as the authoritative request/response contract rather than copying an old example payload.

The provider bridge API is documented separately in [siwc.md](siwc.md). A browser must never receive the internal bridge capability secret.
