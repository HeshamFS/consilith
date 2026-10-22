"""Write ``security_gate.json`` from pytest JUnit reports (BUILD_SPEC section 10).

The room refuses to enable ``CONSILITH_AGENT_LOOP`` or ``CONSILITH_PEERS`` unless this file
records every fix its stage requires as passed (``consilith_room.platform.security_gate``).

    uv run --frozen --no-sync --no-default-groups --group platform pytest -q -c pytest.ini \\
        --junitxml=build/junit.xml <the private test paths>
    uv run --frozen --no-sync --no-default-groups --group platform \\
        python tools/security_gate.py --junit build/junit.xml

A fix passes when every test mapped to it ran and passed. A test that was skipped, failed,
errored or is missing from the reports fails it, and a fix with no mapped test (a placeholder
for another workstream) is not passed. Some tests skip on Windows because they check POSIX
file modes, so run the report on Linux (or WSL) for the file the room will read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from consilith_room.platform import security_gate as gate

ROOT = Path(__file__).resolve().parent.parent

# Fix id -> the private tests that prove it, as pytest node ids. A node id without a parameter
# list stands for every parametrized case of that test. Other workstreams add their own here.
RT = "packages/plugin-runtime/tests/ap"
ROOM = "apps/room/tests/ap"
TOOLS = "tools/tests_ap"
RELAY = f"{ROOM}/test_ap_peer_relay.py"
INJECTION = f"{ROOM}/parity/test_ap_peer_injection.py"
STORES = f"{ROOM}/test_ap_stores.py"
TESTS: dict[str, list[str]] = {
    "gateway_open_proxy": [f"{TOOLS}/test_gateway_node_suite.py::test_node_gateway_suite_passes"],
    "operator_catalog_binding": [
        f"{RT}/test_catalog_binding.py::test_worker_that_adds_a_host_port_is_refused",
        f"{RT}/test_catalog_binding.py::test_worker_that_adds_a_tool_is_refused",
        f"{RT}/test_catalog_binding.py::test_worker_that_adds_a_tool_to_a_granted_server_is_refused",
        f"{RT}/test_catalog_binding.py::test_worker_that_claims_another_servers_is_refused",
        f"{RT}/test_catalog_binding.py::test_worker_within_the_grant_loads_and_runs",
        f"{RT}/test_catalog_binding.py::test_load_reads_the_grant_from_the_catalog_the_deployment_publishes",
        f"{TOOLS}/test_deployment_security.py::test_catalog_carries_what_the_operator_declares_for_each_plugin",
    ],
    "replay_denial": [
        f"{ROOM}/test_replay_viewer_denial.py::test_another_viewers_recorded_session_is_denied_for_both_sessions",
        f"{ROOM}/test_replay_viewer_denial.py::test_a_person_still_replays_their_own_session",
    ],
    "artifact_size_stream": [
        f"{ROOM}/test_artifact_upload_limit.py::test_a_declared_oversize_upload_is_refused_before_the_body_is_read",
        f"{ROOM}/test_artifact_upload_limit.py::test_an_undeclared_upload_stops_at_the_limit",
        f"{ROOM}/test_artifact_upload_limit.py::test_a_body_longer_than_its_declared_length_is_cut_off",
    ],
    "journal_viewer": [
        f"{ROOM}/test_brought_chat_journal.py::test_owner_chat_with_a_brought_agent_is_journaled_to_the_owner_only"
    ],
    "catalog_file_mode": [
        f"{TOOLS}/test_deployment_security.py::test_published_catalog_is_owner_only_whatever_the_umask",
        f"{TOOLS}/test_deployment_security.py::test_catalog_is_replaced_atomically_and_leaves_no_temporary_file",
    ],
    "hash_cache_private": [
        f"{RT}/test_hash_cache.py::test_a_planted_file_in_the_shared_temp_dir_is_never_read",
        f"{RT}/test_hash_cache.py::test_host_creates_one_private_unguessable_directory_for_its_workers",
        f"{RT}/test_hash_cache.py::test_a_group_or_world_accessible_directory_is_not_trusted",
        f"{RT}/test_hash_cache.py::test_a_cache_file_others_can_write_is_not_trusted",
    ],
    "capabilities_trim": [
        f"{ROOM}/test_capabilities_access.py::test_an_anonymous_caller_gets_the_studio_catalog_and_nothing_inside_it",
        f"{ROOM}/test_capabilities_access.py::test_a_signed_in_person_gets_the_full_descriptor",
    ],
    "topology_check": [
        f"{TOOLS}/test_deployment_security.py::test_a_rendered_deployment_keeps_every_plugin_network_internal",
        f"{TOOLS}/test_deployment_security.py::test_topology_check_refuses_a_plugin_network_with_a_route_out",
        f"{TOOLS}/test_deployment_security.py::test_production_refuses_plugins_that_run_inside_the_room",
        f"{TOOLS}/test_deployment_security.py::test_production_refuses_a_catalog_that_is_not_fully_bound",
    ],
    "keys_fix": [
        f"{ROOM}/test_ap_key_adapters.py::test_the_key_is_sent_as_a_header_and_never_in_the_body",
        f"{ROOM}/test_ap_key_adapters.py::test_no_client_holding_the_key_outlives_a_successful_call",
        f"{ROOM}/test_ap_key_adapters.py::test_no_client_holding_the_key_outlives_a_failed_call_even_while_the_error_is_held",
        f"{ROOM}/test_ap_key_adapters.py::test_error_messages_carry_neither_the_key_nor_sdk_text",
    ],
    "require_pins_every_step": [
        f"{ROOM}/test_ap_toolhost_limits.py::test_pins_are_checked_before_every_tool",
        f"{ROOM}/test_ap_toolhost_limits.py::test_pins_are_checked_before_and_after_every_model_call",
    ],
    "run_quotas": [
        f"{ROOM}/test_ap_l2_runs.py::test_two_active_jobs_per_person_and_three_per_room",
        f"{ROOM}/test_ap_l2_runs.py::test_the_same_case_mode_and_inputs_are_one_job",
        f"{ROOM}/test_ap_l2_runs.py::test_cancel_fails_the_active_run_as_cancelled_and_only_the_owner_may",
    ],
    # Stage 2 (agent-to-agent), mapped by WS-E2.
    "agent_card_trim": [
        f"{RELAY}::test_the_unauthenticated_card_names_only_name_protocol_and_skills",
        f"{RELAY}::test_the_detailed_profile_is_behind_the_agent_token",
    ],
    "journal_payload_false": [
        f"{RELAY}::test_no_peer_text_appears_in_the_journal_or_the_log",
        f"{RELAY}::test_the_room_document_holds_a_small_thread_index_and_no_bodies",
    ],
    "peer_budgets_optin": [
        f"{RELAY}::test_opt_in_is_off_by_default_and_the_sender_is_told_calmly",
        f"{RELAY}::test_first_contact_needs_the_owners_card_even_for_an_agent_with_a_token",
        f"{RELAY}::test_the_pause_switch_and_a_mute_take_effect_before_delivery",
        f"{RELAY}::test_the_hourly_budgets_cap_sends_per_pair_and_a_capped_notice_posts",
        f"{RELAY}::test_a_thread_holds_twelve_messages_and_eight_threads_are_open_at_most",
        f"{RELAY}::test_with_peers_off_nothing_changes",
    ],
    "restricted_a2a_profile": [
        f"{RELAY}::test_alices_agent_cannot_make_bobs_agent_run_anything",
        f"{RELAY}::test_a_text_part_sent_with_the_conductor_token_gets_only_the_peer_catalog",
        f"{RELAY}::test_a_poisoned_peer_message_gets_no_tool_beyond_reply_and_propose",
        f"{RELAY}::test_a_proposal_into_a_third_persons_lane_is_refused",
        f"{INJECTION}::test_the_peer_catalog_is_reply_and_propose_and_nothing_else",
        f"{INJECTION}::test_a_peer_message_that_gives_orders_gets_no_run_no_edit_and_no_private_read",
    ],
    "peer_messages_retention": [
        f"{STORES}::test_peer_bodies_older_than_the_retention_are_purged",
        f"{ROOM}/test_ap_l2_loop.py::test_the_retention_purges_trim_old_and_excess_chat_turns",
        f"{RELAY}::test_leaving_closes_the_threads_and_purges_the_bodies",
        f"{RELAY}::test_deleting_the_room_purges_peer_traffic",
    ],
}

Outcomes = dict[str, str]


def junit_key(node_id: str) -> str:
    """The ``classname::name`` pytest writes to JUnit for a node id (path dots, class parts)."""
    address, bracket, params = node_id.partition("[")
    parts = address.split("::")
    module = re.sub(r"\.py$", "", parts[0]).replace("/", ".")
    return ".".join([module, *parts[1:-1]]) + "::" + parts[-1] + bracket + params


def read_outcomes(reports: list[Path]) -> Outcomes:
    """``classname::name`` -> passed, failed, error or skipped, across every report.

    The reports are the build's own pytest output, never input from a person or a network.
    """
    outcomes: Outcomes = {}
    for report in reports:
        for case in ET.parse(report).getroot().iter("testcase"):
            key = f"{case.get('classname', '')}::{case.get('name', '')}"
            outcome = "passed"
            for child in case:
                if child.tag in {"failure", "error", "skipped"}:
                    outcome = "failed" if child.tag == "failure" else child.tag
                    break
            # The worst result wins if a test appears twice (for example in two reports).
            rank = ("passed", "skipped", "error", "failed")
            if key not in outcomes or rank.index(outcome) > rank.index(outcomes[key]):
                outcomes[key] = outcome
    return outcomes


def outcome_of(node_id: str, outcomes: Outcomes) -> str:
    key = junit_key(node_id)
    found = [o for k, o in outcomes.items() if k == key or k.startswith(key + "[")]
    if not found:
        return "missing"
    for bad in ("failed", "error", "skipped"):
        if bad in found:
            return bad
    return "passed"


def build(outcomes: Outcomes, tests: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """The document the room reads. Every required fix is listed, passed or not."""
    mapping = TESTS if tests is None else tests
    fixes: dict[str, Any] = {}
    for fix in gate.FIXES:
        names = mapping.get(fix.id, [])
        results = {name: outcome_of(name, outcomes) for name in names}
        entry: dict[str, Any] = {
            "stage": fix.stage,
            "owner": fix.owner,
            "title": fix.title,
            "tests": names,
            "outcomes": results,
            "passed": bool(names) and all(o == "passed" for o in results.values()),
        }
        if not names:
            entry["reason"] = "no test is registered for this fix yet"
        fixes[fix.id] = entry
    return {
        "schemaVersion": gate.SCHEMA_VERSION,
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "platform": sys.platform,
        "fixes": fixes,
    }


def write(document: dict[str, Any], path: Path) -> None:
    """Replace the file atomically, so the room never reads half a gate."""
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".next")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2)
            stream.write("\n")
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--junit", action="append", required=True, type=Path, help="pytest report")
    parser.add_argument("--output", type=Path, default=ROOT / "security_gate.json")
    args = parser.parse_args(argv)
    document = build(read_outcomes(args.junit))
    document["reports"] = [
        {"file": report.name, "sha256": hashlib.sha256(report.read_bytes()).hexdigest()}
        for report in args.junit
    ]
    write(document, args.output)
    for stage in gate.STAGES:
        ids = gate.required(stage)
        open_ids = [i for i in ids if not document["fixes"][i]["passed"]]
        print(f"stage {stage}: {len(ids) - len(open_ids)}/{len(ids)} passed", end="")
        print(f"; open: {', '.join(open_ids)}" if open_ids else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
