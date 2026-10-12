from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


@dataclass(frozen=True)
class BaseArchitecture:
    n_satellites: int
    n_planes: int
    altitude_km: float
    inclination_deg: float
    walker_f: int = 1
    raan0_deg: float = 0.0

    @property
    def base_id(self) -> str:
        return (
            f"T{self.n_satellites:03d}_P{self.n_planes:02d}_"
            f"H{int(round(self.altitude_km)):04d}_"
            f"I{int(round(self.inclination_deg * 10)):04d}_F{self.walker_f}"
        )


class CampaignConfig:
    def __init__(self, path: Path, data: dict[str, Any]):
        self.path = path.resolve()
        self.root = self.path.parent.parent
        self.data = data
        self.validate()

    @classmethod
    def load(cls, path: str | Path) -> "CampaignConfig":
        config_path = Path(path)
        with config_path.open("r", encoding="utf-8") as handle:
            return cls(config_path, json.load(handle))

    def validate(self) -> None:
        grid = self.data["architecture_grid"]
        expected = {
            "n_satellites": [60, 120, 180],
            "n_planes": [3, 5, 10],
            "altitude_km": [500, 800, 1000],
            "inclination_deg": [60, 80, 98.6],
            "cn_fraction_percent": list(range(0, 101, 10)),
        }
        for key, values in expected.items():
            if list(grid[key]) != values:
                raise ValueError(f"The frozen Full891 grid requires {key}={values}; got {grid[key]}")
        bases = list(self.iter_base_architectures())
        if len(bases) != 81 or self.case_count_per_region != 891:
            raise AssertionError("Architecture grid must contain 81 bases and 891 CN cases per region")
        storage = self.data["storage"]
        if float(storage["maximum_output_gb"]) + float(storage["minimum_free_space_gb"]) > 50.0:
            raise ValueError("Configured output budget plus safety reserve exceeds the 50 GB VM limit")
        if int(self.data["robustness"]["random_seeds"]) < 1:
            raise ValueError("At least one robustness seed is required")
        execution = self.data["execution"]
        if int(execution["max_parallel_base_architectures"]) < 1:
            raise ValueError("max_parallel_base_architectures must be at least 1")
        if float(execution["memory_limit_per_worker_gb"]) <= 0:
            raise ValueError("memory_limit_per_worker_gb must be positive")
        if float(execution.get("memory_reserve_gb", 4.0)) < 0:
            raise ValueError("memory_reserve_gb cannot be negative")

    @property
    def tasks_path(self) -> Path:
        path = Path(self.data["tasks_file"])
        return path if path.is_absolute() else self.root / path

    @property
    def output_root(self) -> Path:
        path = Path(self.data["output_root"])
        return path if path.is_absolute() else self.root / path

    @property
    def cn_fractions(self) -> list[float]:
        return [float(x) for x in self.data["architecture_grid"]["cn_fraction_percent"]]

    @property
    def case_count_per_region(self) -> int:
        return len(list(self.iter_base_architectures())) * len(self.cn_fractions)

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(self.data, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(payload).hexdigest()

    def iter_base_architectures(self) -> Iterator[BaseArchitecture]:
        grid = self.data["architecture_grid"]
        for n_sat in grid["n_satellites"]:
            for n_planes in grid["n_planes"]:
                if int(n_sat) % int(n_planes):
                    continue
                for altitude in grid["altitude_km"]:
                    for inclination in grid["inclination_deg"]:
                        yield BaseArchitecture(
                            int(n_sat), int(n_planes), float(altitude), float(inclination),
                            int(grid.get("walker_f", 1)), float(grid.get("raan0_deg", 0.0)),
                        )
