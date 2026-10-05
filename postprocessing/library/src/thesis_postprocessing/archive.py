"""Read-only access and discovery for campaign ZIP archives."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

import pandas as pd


CASE_RE = re.compile(
    r"^T(?P<n_satellites>\d{3})_P(?P<n_planes>\d{2})_H(?P<altitude_km>\d{4})_"
    r"I(?P<inclination_tenths>\d{4})_CN(?P<cn_fraction_percent>\d{3})_F(?P<replicate>\d+)$"
)


def parse_case_id(case_id: str) -> dict[str, int | float | str]:
    match = CASE_RE.fullmatch(case_id)
    if not match:
        raise ValueError(f"Invalid full891 case id: {case_id}")
    values = {key: int(value) for key, value in match.groupdict().items()}
    return {
        "case_id": case_id,
        "n_satellites": values["n_satellites"],
        "n_planes": values["n_planes"],
        "altitude_km": values["altitude_km"],
        "inclination_deg": values["inclination_tenths"] / 10.0,
        "cn_fraction_percent": values["cn_fraction_percent"],
        "replicate": values["replicate"],
    }


def event_label(event_root: str) -> str:
    name = PurePosixPath(event_root).name.lower()
    if "california" in name:
        return "California"
    if "india" in name:
        return "India"
    return PurePosixPath(event_root).name


class CampaignArchive:
    """A read-only facade over the authoritative result ZIP."""

    def __init__(self, zip_path: str | Path):
        self.path = Path(zip_path).expanduser().resolve()
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        self._zip = zipfile.ZipFile(self.path, mode="r")
        self.infos = [info for info in self._zip.infolist() if not info.is_dir()]
        self.names = [info.filename.replace("\\", "/") for info in self.infos]
        self.info_by_name = {info.filename.replace("\\", "/"): info for info in self.infos}
        suffix = "/combined_performance_summary.csv"
        self.event_roots = sorted(name[: -len(suffix)] for name in self.names if name.endswith(suffix))
        if not self.event_roots:
            raise ValueError("No event-level combined_performance_summary.csv files found")

    def close(self) -> None:
        self._zip.close()

    def __enter__(self) -> "CampaignArchive":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def open_binary(self, member: str) -> io.BufferedIOBase:
        raw = self._zip.open(member, mode="r")
        if member.lower().endswith(".gz"):
            return gzip.GzipFile(fileobj=raw)
        return raw

    def read_bytes(self, member: str, decompress_gzip: bool = True) -> bytes:
        raw = self._zip.read(member)
        if decompress_gzip and member.lower().endswith(".gz"):
            raw = gzip.decompress(raw)
        return raw

    def read_json(self, member: str) -> dict[str, object]:
        return json.loads(self.read_bytes(member).decode("utf-8-sig"))

    def read_csv(self, member: str, **kwargs: object) -> pd.DataFrame:
        with self.open_binary(member) as binary:
            return pd.read_csv(binary, **kwargs)

    def sha256(self, member: str, decompress_gzip: bool = True) -> str:
        digest = hashlib.sha256()
        if decompress_gzip:
            stream = self.open_binary(member)
        else:
            stream = self._zip.open(member, mode="r")
        with stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    def event_member(self, event_root: str, basename: str) -> str:
        return f"{event_root}/{basename}"

    def case_member(self, event_root: str, case_id: str, basename: str) -> str:
        return f"{event_root}/{case_id}/{basename}"

    def event_cases(self, event_root: str) -> list[str]:
        prefix = event_root + "/"
        cases = set()
        for name in self.names:
            if not name.startswith(prefix):
                continue
            first = name[len(prefix) :].split("/", 1)[0]
            if CASE_RE.fullmatch(first):
                cases.add(first)
        return sorted(cases)

    def iter_case_members(self, event_root: str, basename: str) -> Iterable[tuple[str, str]]:
        for case_id in self.event_cases(event_root):
            member = self.case_member(event_root, case_id, basename)
            if member in self.info_by_name:
                yield case_id, member

    def archive_sha256(self) -> str:
        digest = hashlib.sha256()
        with self.path.open("rb") as stream:
            while chunk := stream.read(8 * 1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

