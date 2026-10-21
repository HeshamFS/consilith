"""Dry-run scoped Docker cleanup; --apply removes retired containers/images, never volumes.

Only this deployment's Compose labels, local release manifests, and saved image receipts
establish ownership. Shared images referenced by another container are preserved.
"""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
from urllib.parse import urlsplit

from docker_stack import ROOT, docker_binary


def is_retired(item: dict, project: str, keep_services: set[str]) -> bool:
    """Require exact deployment ownership; similar container names confer no authority."""
    labels = item["Config"].get("Labels") or {}
    return (
        labels.get("com.docker.compose.project") == project
        and labels.get("com.docker.compose.service") not in keep_services
    ) or (
        labels.get("com.consilith.scope") == "qualification"
        and labels.get("com.consilith.project") == project
        and not item["State"]["Running"]
    )


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    docker = docker_binary()

    def command(*items: str) -> str:
        return subprocess.check_output([docker, *items], text=True)

    def inspect(items: list[str]) -> list[dict]:
        return json.loads(command("inspect", *items)) if items else []

    current = json.loads((ROOT / "deployment.json").read_text())
    project = current["project"]
    platform = json.loads((ROOT / "docker/platform.compose.json").read_text())
    keep_services = set(platform["services"]) | {"siwc"}
    keep_services.update("plugin-" + p["id"] for p in current["plugins"])
    keep_services.update(p["id"] for p in current["mcpServices"])
    from deployment import active_images, active_services

    aliases = active_services()
    keep_services = {aliases.get(name, name) for name in keep_services}
    keep_services.update(aliases.values())
    catalog_path = ROOT / ".docker/catalog/plugins.json"
    if catalog_path.exists():
        catalog = json.loads(catalog_path.read_text())
        references = [item["reference"] for item in catalog["plugins"]]
        references.extend(catalog.get("solverUrls", {}).values())
        # The published catalog is authoritative even after a process is terminated
        # between the separate atomic catalog and lifecycle-state file replacements.
        keep_services.update(urlsplit(reference).hostname for reference in references)
    keep_refs = {
        current["platform"]["image"],
        "consilith-web:local",
        "consilith-chatgpt-bridge:local",
    }
    images = active_images()
    keep_refs.update(images.values())
    original_refs = {p["image"] for p in current["plugins"] + current["mcpServices"]}
    keep_refs.update(p["image"] for p in current["plugins"] if "plugin-" + p["id"] not in images)
    keep_refs.update(p["image"] for p in current["mcpServices"] if p["id"] not in images)
    owned_refs = {
        "consilith-qualification:local",
        "consilith-platform-qualification:local",
        "consilith-plugins:local",
        "consilith-science:local",
    }
    owned_refs.update(original_refs)
    for path in (ROOT / "examples").glob("deployment.*.json"):
        document = json.loads(path.read_text())
        if document.get("project") == project:
            owned_refs.update(p["image"] for p in document["plugins"] + document["mcpServices"])
    owned_refs -= keep_refs
    all_containers = inspect(command("ps", "-aq").split())
    retired = []
    candidates: set[str] = set()
    for item in all_containers:
        name = item["Name"].lstrip("/")
        if is_retired(item, project, keep_services):
            retired.append({"id": item["Id"], "name": name, "image": item["Image"]})
            candidates.add(item["Image"])
    for role in ("runtime", "chatgpt-bridge"):
        labeled = command(
            "image",
            "ls",
            "--quiet",
            "--no-trunc",
            "--filter",
            f"label=com.consilith.project={project}",
            "--filter",
            f"label=com.consilith.role={role}",
        ).split()
        candidates.update(labeled)
    history = ROOT / "var/evidence/bridge-image-history.json"
    if history.exists():
        receipt = json.loads(history.read_text())
        if receipt.get("project") == project and receipt.get("role") == "chatgpt-bridge":
            for image in receipt.get("images", []):
                if image.startswith("sha256:") and len(image) == 71:
                    result = subprocess.run(
                        [docker, "image", "inspect", image], capture_output=True
                    )
                    if result.returncode == 0:
                        candidates.add(image)
    existing = {}
    for line in command("image", "ls", "--no-trunc", "--format", "{{json .}}").splitlines():
        item = json.loads(line)
        ref = item["Repository"] + ":" + item["Tag"]
        if ref in owned_refs or (
            item["ID"] in candidates and ref not in keep_refs and item["Repository"] != "<none>"
        ):
            existing[ref] = item["ID"]
            candidates.add(item["ID"])
    keep_ids = set()
    for ref in keep_refs:
        result = subprocess.run([docker, "image", "inspect", ref], capture_output=True, text=True)
        if result.returncode == 0:
            keep_ids.add(json.loads(result.stdout)[0]["Id"])
    retired_ids = {item["id"] for item in retired}
    protected_ids = {item["Image"] for item in all_containers if item["Id"] not in retired_ids}
    removable = candidates - keep_ids - protected_ids
    references = [ref for ref, image in existing.items() if image in removable]
    report = {
        "applied": args.apply,
        "project": project,
        "containers": retired,
        "imageReferences": references,
        "imageIds": sorted(removable),
        "preservedReferencedImages": sorted(candidates & protected_ids),
        "volumesRemoved": 0,
        "globalPrune": False,
        "physicalBytesReclaimed": None,
        "diskNote": (
            "Shared layers/build cache prevent attributing physical reclaimed bytes; "
            "no global cache was pruned."
        ),
    }
    if args.apply:
        for item in retired:
            fresh = inspect([item["id"]])[0]
            assert is_retired(fresh, project, keep_services)
            subprocess.run(
                [docker, "rm", "--force", item["id"]], check=True, stdout=subprocess.DEVNULL
            )
        for ref in references:
            subprocess.run([docker, "image", "rm", ref], check=True, stdout=subprocess.DEVNULL)
        # Untagged historical images from verified containers may remain after tag removal.
        for image in sorted(removable):
            result = subprocess.run([docker, "image", "inspect", image], capture_output=True)
            if result.returncode == 0:
                remaining = json.loads(result.stdout)[0]
                if not remaining.get("RepoTags"):
                    subprocess.run(
                        [docker, "image", "rm", image], check=True, stdout=subprocess.DEVNULL
                    )
    filename = "docker-cleanup-receipt.json" if args.apply else "docker-cleanup-plan.json"
    destination = ROOT / "var/evidence" / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    if args.apply and destination.exists():
        previous = destination.read_bytes()
        stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%S%fZ")
        destination.with_name(f"docker-cleanup-receipt-{stamp}.json").write_bytes(previous)
    destination.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "applied": args.apply,
                "retiredContainers": len(retired),
                "retiredImageReferences": len(references),
                "volumesRemoved": 0,
                "receipt": str(destination),
            }
        )
    )


def main() -> None:
    from deployment import deployment_guard

    with deployment_guard():
        _main()


if __name__ == "__main__":
    main()
