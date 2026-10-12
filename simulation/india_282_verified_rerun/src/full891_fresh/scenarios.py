from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    family: str
    topology: str = "central_only"
    routing_policy: str = "targeted_central"
    receiver_target: str = "all_useful"
    delivery_cutoff: str = "deadline"
    max_forwarding_hops: int = -1
    max_observer_copies: int = 0
    max_cn_copies: int = 0
    task_size_multiplier: float = 1.0
    failure_type: str = "none"
    failure_level_percent: float = 0.0
    replicate: int = 0
    capacity_factor: float = 1.0
    retain_task_metrics: bool = False
    retain_transfers: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def deterministic_seed(self, campaign_seed: int, case_id: str) -> int:
        text = f"{campaign_seed}|{case_id}|{self.scenario_id}|{self.replicate}".encode()
        return int.from_bytes(hashlib.sha256(text).digest()[:8], "big", signed=False)


def _slug_number(value: float) -> str:
    return str(value).replace(".", "p")


def build_scenarios(config: dict[str, Any]) -> list[Scenario]:
    d = config["dissemination"]
    storage = config["storage"]
    transfer_names = set(storage.get("retain_transfer_scenarios", []))
    scenarios: list[Scenario] = [
        Scenario(
            "nominal_original", "nominal", max_forwarding_hops=int(d["nominal_max_forwarding_hops"]),
            max_observer_copies=int(d["nominal_max_observer_copies"]),
            max_cn_copies=int(d["nominal_max_cn_copies"]), retain_task_metrics=True,
            retain_transfers="nominal_original" in transfer_names,
        ),
        Scenario(
            "unlimited_useful_deadline", "unlimited", retain_task_metrics=True,
            retain_transfers="unlimited_useful_deadline" in transfer_names,
        ),
        Scenario(
            "unlimited_useful_sim_end", "unlimited", delivery_cutoff="simulation_end",
            retain_task_metrics=True,
        ),
        Scenario(
            "unlimited_all_satellites_sim_end", "unlimited", topology="hybrid",
            routing_policy="hybrid", receiver_target="all_satellites",
            delivery_cutoff="simulation_end", retain_task_metrics=True,
        ),
        Scenario(
            "policy_central", "policy_ablation", topology="central_only",
            routing_policy="targeted_central", retain_task_metrics=True,
        ),
        Scenario(
            "policy_peer", "policy_ablation", topology="peer_only",
            routing_policy="peer", retain_task_metrics=True,
        ),
        Scenario(
            "policy_hybrid", "policy_ablation", topology="hybrid",
            routing_policy="hybrid", retain_task_metrics=True,
        ),
    ]

    if config["task_size_sensitivity"].get("enabled", True):
        for multiplier in config["task_size_sensitivity"]["multipliers"]:
            scenarios.append(Scenario(
                f"task_size_x{_slug_number(float(multiplier))}", "task_size",
                task_size_multiplier=float(multiplier), retain_task_metrics=True,
            ))

    robust = config["robustness"]
    if robust.get("enabled", True):
        levels = [float(x) for x in robust["failure_levels_percent"]]
        seeds = int(robust["random_seeds"])
        random_families = {
            "random_satellite_failure", "random_cn_failure",
            "link_window_outage", "ground_station_outage",
        }
        for family in robust["families"]:
            if family in random_families:
                for level in levels:
                    for replicate in range(seeds):
                        scenarios.append(Scenario(
                            f"{family}_p{int(level):02d}_r{replicate:02d}", family,
                            failure_type=family, failure_level_percent=level, replicate=replicate,
                        ))
            elif family == "targeted_cn_failure":
                for level in levels:
                    scenarios.append(Scenario(
                        f"targeted_cn_failure_p{int(level):02d}", family,
                        failure_type=family, failure_level_percent=level,
                    ))
            elif family == "capacity_degradation":
                for level in levels:
                    scenarios.append(Scenario(
                        f"capacity_degradation_p{int(level):02d}", family,
                        failure_type=family, failure_level_percent=level,
                        capacity_factor=max(0.0, 1.0 - level / 100.0),
                    ))
            else:
                raise ValueError(f"Unknown robustness family: {family}")

        combined = robust.get("combined_stress", {})
        if combined.get("enabled", False):
            for multiplier in combined["task_size_multipliers"]:
                for level in combined["satellite_failure_levels_percent"]:
                    for replicate in range(int(combined["random_seeds"])):
                        scenarios.append(Scenario(
                            f"combined_size_x{_slug_number(float(multiplier))}_satfail_p{int(level):02d}_r{replicate:02d}",
                            "combined_stress", task_size_multiplier=float(multiplier),
                            failure_type="random_satellite_failure",
                            failure_level_percent=float(level), replicate=replicate,
                        ))

    ids = [s.scenario_id for s in scenarios]
    if len(ids) != len(set(ids)):
        raise AssertionError("Scenario IDs must be unique")
    return scenarios


def scenario_manifest(scenarios: Iterable[Scenario]) -> list[dict[str, Any]]:
    return [scenario.to_dict() for scenario in scenarios]

