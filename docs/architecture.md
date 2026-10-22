# Architecture

Consilith separates the **platform**, which is the same for every science, from **plugins**, which carry a domain.

| The platform owns | A plugin owns |
|---|---|
| accounts, rooms, seats and lane ownership | capabilities (cases), lanes and input fields |
| proposals, approvals and permissions | workflows and the tools they call (over MCP) |
| personal agents and the tool loop | skills, knowledge and agent instructions |
| runs, the job queue and stored results | checks, replay tolerances and reports |
| the referee and signed evidence | apps and reports shown in the workspace |
| the generative UI catalog | presentation hints for its fields and results |

## Services

| Service | Role |
|---|---|
| `room` | API, shared room document, agents, runs and storage |
| `referee` | independently configured checks; signs evidence |
| `web` | gateway and the React workspace |
| `siwc` | Sign in with ChatGPT bridge |
| plugin worker | runs a plugin's workflow code through narrow host ports |
| plugin MCP servers | run the plugin's tools |

Plugin code never runs inside the room, referee or web images. A worker gets explicit host ports; it cannot reach room storage, signing keys or anyone's model key.

## How a result is made

1. A person or their agent edits an input in a lane they own, or proposes a change to someone else's lane.
2. The owner approves. Every change is a recorded, revision-checked patch.
3. A run executes the plugin workflow from a snapshot of the inputs.
4. Results are stored as typed records (number, text, category, JSON or artifact) with their provenance.
5. The referee checks them with a method that did not produce them and signs the evidence.
6. Later input changes mark old results as stale instead of overwriting them.

## Workspace

The agent composes each person's view from the platform's component catalog: a control desk, a model space, results, evidence, charts, tables and plugin apps (self-contained HTML pages in a sandboxed frame). Plugins cannot ship browser code into the shell or grant themselves permissions. One shared layout per room and case keeps everyone on the same picture.

## Releases

A deployment validates every plugin (namespaces, health, registration and live MCP discovery) before publishing its catalog. Rooms pin the plugin releases they were created with; moving to a newer release is an explicit owner action. A failed publication restores the previous catalog.

These are software guarantees. They do not make a plugin's science valid; each plugin documents and qualifies its own methods.
