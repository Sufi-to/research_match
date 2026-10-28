#!/usr/bin/env python3
"""Verify completed source batches and package only collection data for Volume upload."""

import argparse
import hashlib
import json
from pathlib import Path
import tarfile

from collect_sources import CollectionError, atomic_json, now, sha


def inventory(paths):
    result = {"created_at": now(), "batches": [], "datasets": {}, "required_sources": {}}
    files = []
    names = set()
    for path in paths:
        if path.name in names:
            raise CollectionError("Batch directory names must be unique")
        names.add(path.name)
        manifest = json.loads((path / "manifest.json").read_text())
        if any(s.get("status") == "running" for s in manifest["sources"].values()):
            raise CollectionError("A batch is still running: " + path.name)
        result["batches"].append({"batch_id": manifest["batch_id"], "sources": manifest["sources"]})
        for name, source in manifest["sources"].items():
            if name in ("faculty", "oai", "openalex"):
                result["required_sources"][name] = source["status"]
        files.append((path / "manifest.json", path.name + "/manifest.json"))
        for meta in manifest["files"].values():
            source = path / meta["path"]
            if not source.resolve().is_relative_to((path / "raw").resolve()):
                raise CollectionError("Raw file path escapes the batch raw directory")
            content = source.read_bytes()
            if sha(content) != meta["sha256"] or len(content) != meta["bytes"]:
                raise CollectionError("Payload integrity failure: " + meta["path"])
            stats = result["datasets"].setdefault(meta["dataset"], {"files": 0, "bytes": 0, "record_occurrences": 0})
            stats["files"] += 1
            stats["bytes"] += len(content)
            stats["record_occurrences"] += meta.get("record_count", 0)
            files.append((source, path.name + "/" + meta["path"]))
        for source in sorted((path / "derived").glob("*.json")):
            if source.is_symlink():
                raise CollectionError("Derived files must not be symlinks")
            json.loads(source.read_text())
            files.append((source, path.name + "/derived/" + source.name))
    # A targeted supplementary batch is loaded alongside the original batches, so it only
    # needs its own OpenAlex source; faculty and OAI come from the earlier batch.
    supplementary = bool(result["batches"]) and all(
        b["sources"].get("openalex", {}).get("targeted") for b in result["batches"])
    required = ("openalex",) if supplementary else ("faculty", "oai", "openalex")
    result["supplementary"] = supplementary
    result["ready_for_bronze_loader"] = all(result["required_sources"].get(s) == "complete" for s in required)
    result["raw_bytes"] = sum(d["bytes"] for d in result["datasets"].values())
    result["raw_files"] = sum(d["files"] for d in result["datasets"].values())
    return result, files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", action="append", required=True)
    parser.add_argument("--output", required=True, help="A new .tar.gz archive path")
    args = parser.parse_args()
    output = Path(args.output)
    report_path = output.with_suffix(".inventory.json")
    if output.exists() or report_path.exists():
        raise CollectionError("Choose a new output name; existing exports are not overwritten")
    result, files = inventory([Path(p) for p in args.batch])
    if not result["ready_for_bronze_loader"]:
        raise CollectionError("Required public sources are incomplete; finish or resume collection first")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with tarfile.open(temporary, "w:gz") as archive:
        for path, archive_name in files:
            archive.add(path, arcname=archive_name, recursive=False)
    temporary.replace(output)
    digest = hashlib.sha256()
    with output.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    result["archive"] = {"name": output.name, "bytes": output.stat().st_size, "sha256": digest.hexdigest(), "members": len(files)}
    atomic_json(report_path, result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
