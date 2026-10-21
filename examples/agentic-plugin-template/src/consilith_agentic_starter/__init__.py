"""A copyable task plugin with no host or domain-package dependency."""

import hashlib

from consilith_contracts import AgentConfig, Autonomy, CaseDef, Check, FieldDef, LaneDef, PatchOp
from consilith_contracts.base import canonical_bytes, digest
from consilith_contracts.execution import Finding, SolveOutcome

__version__ = "1.1.2"
CONTRACT_VERSION = "1"
TOOLS = ["starter.inspect", "starter.prepare"]
MANIFEST = {
    "id": "starter",
    "distribution": "consilith-agentic-starter",
    "version": __version__,
    "title": "Task workspace",
    "description": "A neutral starter with text, structured data, files and checked evidence.",
    "capabilities": ["tasks"],
    "instructions": (
        "Use stored task inputs and registered tools. Never invent outputs. Ask for missing input. "
        "Respect lane ownership and request review through proposals."
    ),
    "promptKnowledge": ["starter.scope"],
    "roles": [
        {
            "id": "starter.operator",
            "label": "Task operator",
            "description": "Owns the request and runs the task.",
            "autonomy": "run_solvers",
            "goal": "Prepare the supplied task",
            "skills": ["text-inspection"],
        },
        {
            "id": "starter.reviewer",
            "label": "Task reviewer",
            "description": "Owns review notes and accepts proposed changes.",
            "autonomy": "suggest",
            "goal": "Review the supplied task",
            "skills": ["text-inspection"],
        },
    ],
    "tools": TOOLS,
    "skills": [
        {
            "id": "text-inspection",
            "description": "Prepare and review a task through registered tools.",
            "path": "skills/text-inspection/SKILL.md",
        }
    ],
    "knowledge": [
        {
            "id": "starter.scope",
            "title": "Task preparation contract",
            "path": "knowledge/scope.md",
            "mediaType": "text/markdown",
        }
    ],
    "mcpServers": [{"id": "solver-starter", "tools": TOOLS}],
    "schemaRefs": {tool: f"mcp://solver-starter/tools/{tool}" for tool in TOOLS},
    "replayPolicy": {"relativeTolerance": 0, "absoluteTolerance": 0},
    "policyPorts": [
        "snapshot",
        "run_tool",
        "review",
        "result",
        "propose",
        "agent_for",
        "model",
        "owners",
        "proposals",
    ],
    "dependencies": [],
    "packageDependencies": ["consilith-contracts", "mcp==2.3.0", "uvicorn==0.54.0"],
    "artifactMediaTypes": ["text/plain", "application/json"],
}


def role():
    return AgentConfig(
        autonomy=Autonomy.RUN_SOLVERS,
        tools_allowed=TOOLS,
        skills=["text-inspection"],
        goal="Prepare the supplied task",
    )


def reviewer_role():
    return AgentConfig(
        autonomy=Autonomy.SUGGEST,
        tools_allowed=TOOLS,
        skills=["text-inspection"],
        goal="Review the supplied task",
    )


async def workflow(ctx, *, review=True, **options):
    model, snapshot = ctx.snapshot(("request", "review"))
    request = model["request"]
    review_input = model.get("review", {})
    if request.get("format") == "numeric":
        tool, arguments = "starter.inspect", {"text": request["text"]}
    else:
        tool = "starter.prepare"
        arguments = {
            "text": request["text"],
            "format": request.get("format", "all"),
            "note": review_input.get("note", ""),
            "labels": review_input.get("labels", []),
            "metadata": request.get("metadata", {}),
            "duration": request.get("duration"),
        }
    run, _envelope, records, error = await ctx.run_tool(tool, arguments, 1, snapshot=snapshot)
    outcome = SolveOutcome(runs=[run], results={r.quantity: r for r in records}, error=error)
    if records and review:
        outcome = await ctx.review(records, outcome)
    outcome.stamps = {record.quantity: ctx.result(record.id).stamp.value for record in records}
    return outcome


async def strategy(ctx, results):
    """An opt-in deterministic proposal demonstrates the same agent ownership gates."""
    request = ctx.model.get("request", {})
    if not request.get("metadata", {}).get("requestReview") or ctx.model.get("review", {}).get(
        "note"
    ):
        return
    if any(p.get("targetLane") == "review" and p.get("status") == "pending" for p in ctx.proposals):
        return
    owner = ctx.owners.get("request")
    if owner and ctx.owners.get("review") and results:
        await ctx.propose(
            ctx.agent_for(owner),
            "tasks",
            "review",
            [PatchOp(op="add", path="/twin/tasks/review/note", value="Reviewed")],
            "Please review the prepared task before accepting this note.",
            [r.id for r in results.values()],
        )


async def checker(request, item, call):
    args = request.manifest.arguments
    text = args.get("text")
    valid = isinstance(text, str) and len(text) <= 10000
    kind = getattr(item, "kind", "number")
    if item.quantity == "character_count":
        passed = (
            valid and kind == "number" and item.unit == "" and item.value == sum(1 for _ in text)
        )
        method = "independent text recount"
    else:
        # Reconstruct expected content from recorded inputs, never call the producer.
        document = {
            "text": text,
            "note": args.get("note", ""),
            "labels": args.get("labels", []),
            "metadata": args.get("metadata", {}),
            "duration": args.get("duration"),
        }
        expected_bytes = canonical_bytes(document)
        allowed = {
            "all": {"prepared_text", "task_document", "task_file"},
            "text": {"prepared_text"},
            "structured": {"task_document"},
            "artifact": {"task_file"},
        }.get(args.get("format", "all"), set())
        passed = valid and item.quantity in allowed and item.unit == ""
        if item.quantity == "prepared_text":
            passed = passed and kind == "text" and item.value == text and not item.artifacts
        elif item.quantity == "task_document":
            passed = (
                passed
                and kind == "json"
                and digest(item.value) == digest(document)
                and not item.artifacts
            )
        elif item.quantity == "task_file":
            passed = (
                passed and kind == "artifact" and item.value is None and len(item.artifacts) == 1
            )
            if passed:
                ref = item.artifacts[0]
                passed = (
                    ref.sha256 == hashlib.sha256(expected_bytes).hexdigest()
                    and ref.size_bytes == len(expected_bytes)
                    and ref.media_type == "application/json"
                    and ref.role == "output"
                )
        else:
            passed = False
        method = "independent document reconstruction"
    return [
        Finding(
            Check(
                name="task-output",
                outcome="pass" if passed else "fail",
                detail="Compared the typed output with the recorded task inputs.",
            ),
            "block",
        )
    ], method


def extension():
    capability = CaseDef(
        id="tasks",
        label="Task workspace",
        grammar="A shared task request and review",
        inputs=["text-task"],
        lanes=[
            LaneDef(
                id="request",
                label="Task input",
                default_role="starter.operator",
                fields=[
                    FieldDef(name="text", label="Task text", kind="text", required=True),
                    FieldDef(
                        name="format",
                        label="Output format",
                        kind="choice",
                        choices=["text", "structured", "artifact", "all", "numeric"],
                    ),
                    FieldDef(
                        name="duration", label="Duration", kind="number", unit="s", min=0, max=3600
                    ),
                    FieldDef(name="metadata", label="Task metadata", kind="object"),
                ],
            ),
            LaneDef(
                id="review",
                label="Task review",
                default_role="starter.reviewer",
                fields=[
                    FieldDef(name="note", label="Review note", kind="text"),
                    FieldDef(name="labels", label="Review labels", kind="list"),
                ],
            ),
        ],
    )
    return {
        "plugin": {"manifest": MANIFEST},
        "cases": {"tasks": capability},
        "roles": {"starter.operator": role, "starter.reviewer": reviewer_role},
        "workflows": {"tasks": workflow},
        "strategies": {"tasks": strategy},
        "checkers": {"tasks": checker},
        "workflow_required_tools": {"tasks": TOOLS},
        "presentation": {"tasks": {"subtitle": "Prepare a task and review its evidence"}},
        "intake_hints": {"tasks": ["text", "prepare task", "review document"]},
        "intake_kinds": {"text-task": "tasks"},
    }
