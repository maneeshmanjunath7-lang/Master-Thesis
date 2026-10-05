"""Read-only structural and schema inspection for the full891 ZIP archive."""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import re
import zipfile
from collections import Counter, defaultdict
from pathlib import PurePosixPath


CASE_RE = re.compile(
    r"^(?P<case_id>T(?P<n_sat>\d{3})_P(?P<n_planes>\d{2})_H(?P<altitude>\d{4})_"
    r"I(?P<inclination>\d{4})_CN(?P<cn_percent>\d{3})_F(?P<replicate>\d+))$"
)


def read_text(archive: zipfile.ZipFile, member: str) -> str:
    raw = archive.read(member)
    if member.lower().endswith(".gz"):
        raw = gzip.decompress(raw)
    return raw.decode("utf-8-sig", errors="replace")


def csv_sample(archive: zipfile.ZipFile, member: str, rows: int = 2) -> dict[str, object]:
    raw = archive.open(member)
    stream: io.BufferedIOBase
    if member.lower().endswith(".gz"):
        stream = gzip.GzipFile(fileobj=raw)
    else:
        stream = raw
    with raw, stream, io.TextIOWrapper(stream, encoding="utf-8-sig", errors="replace", newline="") as text:
        reader = csv.DictReader(text)
        sample = []
        for index, row in enumerate(reader):
            if index >= rows:
                break
            sample.append(dict(row))
        return {"columns": reader.fieldnames or [], "sample": sample}


def find_event_roots(names: list[str]) -> list[str]:
    suffix = "/combined_performance_summary.csv"
    return sorted(name[: -len(suffix)] for name in names if name.endswith(suffix))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("zip_path")
    parser.add_argument("--sample-case", default="T060_P03_H0500_I0600_CN000_F1")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--sizes-only", action="store_true")
    args = parser.parse_args()

    with zipfile.ZipFile(args.zip_path) as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        names = [info.filename.replace("\\", "/") for info in infos]
        info_by_name = {info.filename.replace("\\", "/"): info for info in infos}
        event_roots = find_event_roots(names)

        result: dict[str, object] = {
            "archive_file_count": len(infos),
            "archive_member_bytes": sum(info.file_size for info in infos),
            "event_roots": event_roots,
            "events": {},
        }
        for event_root in event_roots:
            prefix = event_root + "/"
            event_members = [name for name in names if name.startswith(prefix)]
            case_members: dict[str, list[str]] = defaultdict(list)
            for name in event_members:
                relative = name[len(prefix) :]
                first = relative.split("/", 1)[0]
                if CASE_RE.fullmatch(first):
                    case_members[first].append(name)

            role_size: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
            for members in case_members.values():
                for name in members:
                    role = PurePosixPath(name).name
                    info = info_by_name[name]
                    role_size[role][0] += 1
                    role_size[role][1] += info.file_size
                    role_size[role][2] += info.compress_size

            event: dict[str, object] = {
                "case_count": len(case_members),
                "case_role_sizes": {
                    role: {"files": values[0], "uncompressed_bytes": values[1], "compressed_bytes": values[2]}
                    for role, values in sorted(role_size.items())
                },
            }

            for basename in [
                "combined_performance_summary.csv",
                "combined_architecture_manifest.csv",
                "combined_central_node_distribution.csv",
                "architecture_sweep_manifest.csv",
                "architecture_sweep_status.csv",
            ]:
                member = f"{event_root}/{basename}"
                if member in info_by_name:
                    event[basename] = csv_sample(archive, member)

            sample_case = args.sample_case if args.sample_case in case_members else sorted(case_members)[0]
            schemas: dict[str, object] = {}
            for name in sorted(case_members[sample_case]):
                basename = PurePosixPath(name).name
                if basename.endswith((".csv", ".csv.gz")):
                    schemas[basename] = csv_sample(archive, name)
                elif basename.endswith(".json"):
                    try:
                        schemas[basename] = json.loads(read_text(archive, name))
                    except json.JSONDecodeError as exc:
                        schemas[basename] = {"json_error": str(exc)}
            event["sample_case"] = sample_case
            event["sample_schemas"] = schemas

            status_member = f"{event_root}/architecture_sweep_status.csv"
            if status_member in info_by_name:
                rows = list(csv.DictReader(io.StringIO(read_text(archive, status_member))))
                status_column = next((col for col in rows[0] if col.lower() == "status"), None) if rows else None
                if status_column:
                    event["status_counts"] = dict(Counter(row[status_column] for row in rows))

                case_column = next((col for col in rows[0] if col.lower() == "case_id"), None) if rows else None
                skipped = {row[case_column] for row in rows if row.get(status_column) == "SKIPPED_COMPLETED"} if case_column else set()
                manifest_roles = {
                    "run_config.json",
                    "architecture_manifest.csv",
                    "architecture_validation_report.csv",
                    "central_node_distribution.csv",
                    "constellation_nodes.csv",
                    "ground_stations.csv",
                }
                missing_manifest_cases = {
                    case_id
                    for case_id, members in case_members.items()
                    if any(
                        f"{event_root}/{case_id}/{role}" not in members
                        for role in manifest_roles
                    )
                }
                event["skipped_completed_cases"] = sorted(skipped)
                event["missing_case_manifest_cases"] = sorted(missing_manifest_cases)
                event["skipped_equals_missing_case_manifests"] = skipped == missing_manifest_cases

            result["events"][PurePosixPath(event_root).name] = event

        if args.sizes_only:
            result = {
                "archive_file_count": result["archive_file_count"],
                "archive_member_bytes": result["archive_member_bytes"],
                "events": {
                    name: {
                        "case_count": event["case_count"],
                        "case_role_sizes": event["case_role_sizes"],
                    }
                    for name, event in result["events"].items()
                },
            }

    if args.compact:
        for event in result["events"].values():
            event.pop("skipped_completed_cases", None)
            event.pop("missing_case_manifest_cases", None)
            for key in list(event):
                if key.endswith(".csv") and isinstance(event[key], dict):
                    event[key].pop("sample", None)
            compact_schemas: dict[str, object] = {}
            for role, schema in event.get("sample_schemas", {}).items():
                if isinstance(schema, dict) and "columns" in schema:
                    compact_schemas[role] = schema["columns"]
                elif isinstance(schema, dict):
                    compact_schemas[role] = {"json_keys": sorted(schema)}
            event["sample_schemas"] = compact_schemas
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
