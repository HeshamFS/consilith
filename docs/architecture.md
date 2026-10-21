# Platform and plugin boundaries

The platform owns identity, permissions, shared-state revisions, ownership, proposals, execution admission, result storage, signed evidence and UI primitives. Plugins own task schemas, methods, workflows, validation policies, skills and knowledge.

Plugin implementations run in isolated worker processes or containers. MCP tool services run independently of the worker. Room and referee images contain contracts and transport, not installed task implementations. A worker receives explicit host ports; it cannot obtain arbitrary room storage, signing keys or model credentials through the contract.

## Results and evidence

The versioned result envelope supports number, text, category, JSON and artifact outputs. Nonnumeric results do not require quantities. Input revisions, implementation identities, typed payloads and artifact hashes are bound to review evidence. The referee operator selects checking capabilities. Replay applies numeric tolerances only to numbers and compares other payloads and files by canonical identity.

The starter uses canonical JSON file bytes so transport normalization of `1` to `1.0` does not change a file. A boolean remains distinct from a number. Task values come from registered tools, not generated prose.

## Workspace composition

The agent selects approved layout and input/result components from the UI contract. Capability navigation has a separate namespace from built-in views. A descriptor preview is an explicit, labelled alternative to model composition. Plugins cannot register arbitrary browser code or silently grant themselves host authority.

Input controls preserve stored compatible units and acknowledge asynchronous saves. Work runs from a recorded server snapshot. Concurrent input changes make late outputs superseded; applications should wait for input-save acknowledgment before initiating dependent work.

## Release lifecycle

Deployment validates service namespaces, health probes, full plugin registration and live MCP tool discovery before publishing the catalog. Candidate services have isolated endpoints while the current release remains available. Failed publication restores the previous catalog and aliases. Cleanup targets only owned unused resources and preserves data volumes.

These are software architecture guarantees. They do not establish scientific validity, arbitrary-plugin safety, live model behavior on every provider, or native macOS/Linux/ARM qualification. Plugin authors must qualify their own methods and supported runtime platforms.
