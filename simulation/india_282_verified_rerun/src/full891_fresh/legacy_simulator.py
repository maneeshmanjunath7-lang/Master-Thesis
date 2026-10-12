#!/usr/bin/env python3
"""
walker_standard_arch_hpc_v19_cn_sweep_0_100_actual_epoch_final.py

Walker architecture sweep pipeline for the DSS wildfire thesis using real Sentinel-3 tasks only.

Purpose
-------
This script is a local, standalone validation pipeline for:

1. Walker Delta constellation generation.
2. Central-node placement at the explicitly requested CN fractions.
3. Communication-window generation.
4. Observation-opportunity generation.
5. Capacity-aware, targeted central-node routing from a physical ground source,
   followed by priority-deadline observation success evaluation.

This corrected version keeps the Walker architecture sweep while enforcing:

- save communication windows with time and duration,
- save observation opportunities,
- 30/60/180/360-minute deadlines reconstructed from each task row,
- targeted routing with hop and copy limits,
- one priority-based task-packet size model,
- priority-specific deadline success and percentile latency,
- ensure Walker constellations and central nodes are distributed properly.

Default sweep
-------------
Walker architecture screening varies:
T   = 60, 120, 180 satellites
P   = 3, 5, 10 planes
h   = 500, 800, 1000 km
inc = 60, 80, 98.6 deg
CN  = supplied explicitly; the Standard81 runner fixes it at 30%
Walker F = 1

This is the Tier-2 architecture screening stage. The companion actual-epoch
runner passes `--sweep-cn-fractions-percent 30`, producing 81 cases.

Central-node placement strategy
-------------------------------
1. Distribute total CN count across planes as evenly as possible.
2. Within each plane:
   - if CN count is even: place CNs as opposite pairs.
   - if CN count is odd: place CNs with equal angular spacing.

Example: T=60, P=5, CN=30%
- S = 12 satellites per plane
- n_CN depends on the selected CN fraction
- plane CN distribution = [4, 3, 4, 3, 4]
- planes with 4 CN: opposite-pair spacing such as [0, 3, 6, 9]
- planes with 3 CN: equal spacing such as [0, 4, 8]

Dependencies
------------
Python 3.9+
numpy
pandas

Run examples
------------
# Inspect the corrected two-event Standard81 workflow
python run_81_cn30_by_region_actual_epoch.py --tasks-file final_wildfire_tasks.csv --dry-run

# Generate only architecture files
python walker_standard_arch_hpc_v19_cn_sweep_0_100_actual_epoch_final.py --mode architectures --sweep-cn-fractions-percent 30

# Use your final wildfire task CSV
python walker_standard_arch_hpc_v19_cn_sweep_0_100_actual_epoch_final.py --mode all --tasks-file final_wildfire_tasks.csv --max-tasks 20 --sweep-cn-fractions-percent 30

# Use all tasks. This may take longer.
python walker_standard_arch_hpc_v19_cn_sweep_0_100_actual_epoch_final.py --mode all --tasks-file final_wildfire_tasks.csv --max-tasks 0 --sweep-cn-fractions-percent 30
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


# =============================================================================
# Constants
# =============================================================================

EARTH_RADIUS_KM = 6378.137
EARTH_MU_KM3_S2 = 398600.4418
EARTH_ROT_RATE_RAD_S = 7.2921159e-5
SECONDS_PER_DAY = 86400.0

DEFAULT_EPOCH_UTC = "2026-01-01T00:00:00Z"

PRIORITY_DEADLINE_MINUTES = {
    "Critical": 30.0,
    "High": 60.0,
    "Medium": 180.0,
    "Low": 360.0,
}

EXPECTED_DEADLINE_MINUTES = frozenset(PRIORITY_DEADLINE_MINUTES.values())

PRIORITY_RANK = {
    "Critical": 0,
    "High": 1,
    "Medium": 2,
    "Low": 3,
}

# =============================================================================
# V19 communication / capacity defaults
# =============================================================================

PRIORITY_PACKAGE_TWO_LEVEL = {
    "Critical": "urgent",
    "High": "urgent",
    "Medium": "routine",
    "Low": "routine",
}
PRIORITY_PACKAGE_RANK_TWO_LEVEL = {"urgent": 0, "routine": 1, "unknown": 9}
PRIORITY_PACKAGE_FOUR_LEVEL = {
    "Critical": "critical",
    "High": "high",
    "Medium": "medium",
    "Low": "low",
}
PRIORITY_PACKAGE_RANK_FOUR_LEVEL = {"critical": 0, "high": 1, "medium": 2, "low": 3, "unknown": 9}

DEFAULT_TASK_SIZE_MODE = "priority"  # One authoritative choice for thesis campaigns.
DEFAULT_TASK_OVERHEAD_FACTOR = 100.0
DEFAULT_FIXED_TASK_SIZE_MBITS = 0.50
DEFAULT_CAPACITY_UTILIZATION_LIMIT = 1.0

PRIORITY_TASK_SIZE_MBITS = {
    "Critical": 2.0,
    "High": 1.5,
    "Medium": 1.0,
    "Low": 0.5,
}

NOMINAL_LINK_DATA_RATE_BPS = {
    "UHF": 19_200.0,
    "S-band": 2_000_000.0,
    "X-band": 20_000_000.0,
}

# Final Sentinel-3 wildfire task file used by default.
# The code reads this CSV directly; it does not perform any extra deduplication.
DEFAULT_TASKS_FILE = Path(
    Path(__file__).resolve().parent
    / "input_tasks"
    / "all_sentinel3_tasks_with_priority_score_deduplicated.csv"
)


# =============================================================================
# Data containers
# =============================================================================

@dataclass(frozen=True)
class ArchitectureConfig:
    """Input design variables for one Walker architecture."""

    n_satellites: int = 60
    n_planes: int = 5
    altitude_km: float = 500.0
    inclination_deg: float = 98.6
    cn_fraction_percent: float = 30.0
    walker_f: int = 1
    raan0_deg: float = 0.0
    eccentricity: float = 0.0
    arg_perigee_deg: float = 0.0

    @property
    def satellites_per_plane(self) -> int:
        if self.n_satellites % self.n_planes != 0:
            raise ValueError(
                f"n_satellites={self.n_satellites} must be divisible by "
                f"n_planes={self.n_planes}."
            )
        return self.n_satellites // self.n_planes

    @property
    def n_central_nodes(self) -> int:
        return int(round(self.n_satellites * self.cn_fraction_percent / 100.0))

    @property
    def semi_major_axis_km(self) -> float:
        return EARTH_RADIUS_KM + self.altitude_km

    @property
    def architecture_id(self) -> str:
        # Example: ARCH_T060_P05_H500_I986_CN030_F1
        inc_code = int(round(self.inclination_deg * 10))
        return (
            f"ARCH_T{self.n_satellites:03d}_"
            f"P{self.n_planes:02d}_"
            f"H{int(round(self.altitude_km)):04d}_"
            f"I{inc_code:04d}_"
            f"CN{int(round(self.cn_fraction_percent)):03d}_"
            f"F{self.walker_f}"
        )


@dataclass(frozen=True)
class GroundStation:
    """Simple ground-station metadata."""

    node_id: str
    name: str
    latitude_deg: float
    longitude_deg: float
    altitude_km: float = 0.0
    elevation_mask_deg: float = 10.0


# =============================================================================
# Small utilities
# =============================================================================

def parse_utc(timestamp: str) -> datetime:
    """Parse an ISO timestamp and force timezone-aware UTC."""
    if timestamp.endswith("Z"):
        timestamp = timestamp.replace("Z", "+00:00")
    dt = datetime.fromisoformat(timestamp)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_utc(dt: datetime) -> str:
    """Write compact ISO string in UTC with Z suffix."""
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def deg2rad(x: float | np.ndarray) -> float | np.ndarray:
    return np.deg2rad(x)


def rad2deg(x: float | np.ndarray) -> float | np.ndarray:
    return np.rad2deg(x)


def wrap_360(deg: float | np.ndarray) -> float | np.ndarray:
    return np.mod(deg, 360.0)


# =============================================================================
# Central-node placement
# =============================================================================

def evenly_spaced_indices_discrete(total_slots: int, n_selected: int) -> List[int]:
    """
    Select approximately equally spaced integer slots on a circular sequence.

    Examples
    --------
    total_slots=12, n_selected=3 -> [0, 4, 8]
    total_slots=12, n_selected=4 -> [0, 3, 6, 9]
    total_slots=18, n_selected=5 -> [0, 4, 7, 11, 14]

    The first index is fixed at 0 for reproducibility.
    """
    if n_selected <= 0:
        return []
    if n_selected >= total_slots:
        return list(range(total_slots))

    # Ideal continuous positions around the circle. Rounding gives good spacing
    # for most cases. Duplicates are unlikely for n_selected <= total_slots, but
    # we still guard against them below.
    indices = [int(round(k * total_slots / n_selected)) % total_slots for k in range(n_selected)]

    if len(set(indices)) == n_selected:
        return sorted(indices)

    # Fallback: use floor spacing, then fill missing indices greedily.
    indices = [int(math.floor(k * total_slots / n_selected)) % total_slots for k in range(n_selected)]
    selected = set(indices)
    candidate = 0
    while len(selected) < n_selected:
        selected.add(candidate)
        candidate += 1
    return sorted(selected)


def circular_gaps(indices: Sequence[int], total_slots: int) -> List[int]:
    """Return circular integer gaps between selected indices."""
    if not indices:
        return []
    sorted_idx = sorted(indices)
    gaps = []
    for i, idx in enumerate(sorted_idx):
        nxt = sorted_idx[(i + 1) % len(sorted_idx)]
        if i == len(sorted_idx) - 1:
            gaps.append((nxt + total_slots) - idx)
        else:
            gaps.append(nxt - idx)
    return gaps


def evenly_spaced_plane_indices(n_planes: int, n_extra_planes: int) -> List[int]:
    """
    Select planes to receive the +1 extra CN, as evenly as possible.

    This is used only for distributing the remainder after integer division.
    Since n_planes is small, use exhaustive search for best circular spacing.
    """
    if n_extra_planes <= 0:
        return []
    if n_extra_planes >= n_planes:
        return list(range(n_planes))

    best_combo: Optional[Tuple[int, ...]] = None
    best_score: Optional[Tuple[float, int, Tuple[int, ...]]] = None

    # Fix plane 0 as included for deterministic symmetry and to avoid equivalent rotations.
    for combo_rest in combinations(range(1, n_planes), n_extra_planes - 1):
        combo = (0,) + combo_rest
        gaps = circular_gaps(combo, n_planes)
        # Minimize unevenness first, then max gap, then lexicographic order.
        score = (float(np.std(gaps)), max(gaps), combo)
        if best_score is None or score < best_score:
            best_score = score
            best_combo = combo

    return list(best_combo if best_combo is not None else range(n_extra_planes))


def distribute_central_nodes_across_planes(n_cn: int, n_planes: int) -> List[int]:
    """
    Distribute total central nodes across planes with imbalance <= 1.

    Remainder central nodes are assigned to planes that are approximately evenly
    spaced around the RAAN distribution.
    """
    base = n_cn // n_planes
    rem = n_cn % n_planes
    counts = [base for _ in range(n_planes)]
    for p in evenly_spaced_plane_indices(n_planes, rem):
        counts[p] += 1
    return counts


def select_cn_indices_within_plane(sats_per_plane: int, n_cn_in_plane: int) -> List[int]:
    """
    Select central-node satellite indices within one orbital plane.

    Rule requested by user:
    - even CN count: place CNs in opposite pairs.
    - odd CN count: place CNs at equal angular distance.

    Because all current design cases have an even number of satellites per plane,
    exact opposite pairing is possible for even n_cn_in_plane.
    """
    S = sats_per_plane
    m = n_cn_in_plane

    if m < 0 or m > S:
        raise ValueError(f"Invalid n_cn_in_plane={m}; must be in [0, {S}].")
    if m == 0:
        return []
    if m == S:
        return list(range(S))
    if m == 1:
        return [0]

    if m % 2 == 0:
        if S % 2 == 0:
            # Exact opposite-pair placement.
            n_pairs = m // 2
            half_orbit = S // 2
            base_indices = evenly_spaced_indices_discrete(half_orbit, n_pairs)
            opposite_indices = [(idx + half_orbit) % S for idx in base_indices]
            return sorted(set(base_indices + opposite_indices))

        # Exact opposite pairing is impossible when the number of satellites per
        # plane is odd. Do not crash the architecture sweep; fall back to equal
        # angular spacing and let the validation report mark the placement type.
        return evenly_spaced_indices_discrete(S, m)

    # Odd number of CNs: equal angular spacing around the full plane.
    return evenly_spaced_indices_discrete(S, m)


# =============================================================================
# Walker architecture generation
# =============================================================================

def generate_walker_constellation(config: ArchitectureConfig) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Generate Walker constellation and central-node placement.

    Returns
    -------
    architecture_manifest, constellation_nodes, central_node_distribution, validation_report
    """
    T = config.n_satellites
    P = config.n_planes
    S = config.satellites_per_plane
    n_cn = config.n_central_nodes
    a_km = config.semi_major_axis_km

    cn_counts_per_plane = distribute_central_nodes_across_planes(n_cn, P)

    satellite_rows: List[Dict] = []
    distribution_rows: List[Dict] = []

    cn_global_indices = set()

    for plane_id in range(P):
        raan_deg = wrap_360(config.raan0_deg + plane_id * 360.0 / P)
        cn_indices_in_plane = select_cn_indices_within_plane(S, cn_counts_per_plane[plane_id])

        distribution_rows.append(
            {
                "architecture_id": config.architecture_id,
                "plane_id": plane_id,
                "n_satellites_in_plane": S,
                "n_central_nodes_in_plane": len(cn_indices_in_plane),
                "cn_satellite_indices": ";".join(str(i) for i in cn_indices_in_plane),
                "plane_cn_fraction_percent": 100.0 * len(cn_indices_in_plane) / S,
            }
        )

        for sat_idx in range(S):
            global_idx = plane_id * S + sat_idx
            sat_id = f"SAT_P{plane_id:02d}_S{sat_idx:02d}"

            # Walker Delta phasing.
            mean_anomaly_deg = wrap_360(
                sat_idx * 360.0 / S + plane_id * config.walker_f * 360.0 / T
            )

            is_cn = sat_idx in cn_indices_in_plane
            if is_cn:
                cn_global_indices.add(global_idx)

            satellite_rows.append(
                {
                    "architecture_id": config.architecture_id,
                    "satellite_id": sat_id,
                    "node_id": sat_id,
                    "global_satellite_index": global_idx,
                    "plane_id": plane_id,
                    "satellite_index_in_plane": sat_idx,
                    "altitude_km": config.altitude_km,
                    "semi_major_axis_km": a_km,
                    "inclination_deg": config.inclination_deg,
                    "raan_deg": raan_deg,
                    "mean_anomaly_deg": mean_anomaly_deg,
                    "eccentricity": config.eccentricity,
                    "arg_perigee_deg": config.arg_perigee_deg,
                    "is_central_node": bool(is_cn),
                    "node_type": "central_node" if is_cn else "observer",
                }
            )

    nodes_df = pd.DataFrame(satellite_rows)
    distribution_df = pd.DataFrame(distribution_rows)

    manifest_df = pd.DataFrame(
        [
            {
                "architecture_id": config.architecture_id,
                "n_satellites": T,
                "n_planes": P,
                "satellites_per_plane": S,
                "altitude_km": config.altitude_km,
                "inclination_deg": config.inclination_deg,
                "walker_f": config.walker_f,
                "cn_fraction_percent": config.cn_fraction_percent,
                "n_central_nodes": n_cn,
                "actual_cn_fraction_percent": 100.0 * int(nodes_df["is_central_node"].sum()) / T,
                "central_node_strategy": "balanced_planes_even_opposite_odd_equal_spacing",
                "raan0_deg": config.raan0_deg,
                "eccentricity": config.eccentricity,
                "arg_perigee_deg": config.arg_perigee_deg,
            }
        ]
    )

    validation_df = validate_architecture(config, nodes_df, distribution_df)
    return manifest_df, nodes_df, distribution_df, validation_df


def validate_architecture(
    config: ArchitectureConfig,
    nodes_df: pd.DataFrame,
    distribution_df: pd.DataFrame,
) -> pd.DataFrame:
    """Run validation checks on Walker geometry and CN placement."""
    checks: List[Dict] = []

    def add_check(name: str, passed: bool, message: str) -> None:
        checks.append(
            {
                "architecture_id": config.architecture_id,
                "check": name,
                "passed": bool(passed),
                "message": message,
            }
        )

    T = config.n_satellites
    P = config.n_planes
    S = config.satellites_per_plane
    n_cn_expected = config.n_central_nodes
    n_cn_actual = int(nodes_df["is_central_node"].sum())

    add_check("T_divisible_by_P", T % P == 0, f"T={T}, P={P}, S={S}")
    add_check("correct_satellite_count", len(nodes_df) == T, f"rows={len(nodes_df)}, expected={T}")
    add_check("correct_cn_count", n_cn_actual == n_cn_expected, f"actual={n_cn_actual}, expected={n_cn_expected}")
    add_check(
        "unique_satellite_ids",
        nodes_df["satellite_id"].is_unique,
        f"unique={nodes_df['satellite_id'].nunique()}, rows={len(nodes_df)}",
    )

    # Plane imbalance check.
    cn_counts = distribution_df["n_central_nodes_in_plane"].to_numpy(dtype=int)
    if len(cn_counts) > 0:
        imbalance = int(cn_counts.max() - cn_counts.min())
    else:
        imbalance = 0
    add_check("cn_plane_imbalance_le_1", imbalance <= 1, f"counts={cn_counts.tolist()}, imbalance={imbalance}")

    # In-plane spacing checks.
    for _, row in distribution_df.iterrows():
        plane_id = int(row["plane_id"])
        m = int(row["n_central_nodes_in_plane"])
        idxs = [] if row["cn_satellite_indices"] == "" else [int(x) for x in str(row["cn_satellite_indices"]).split(";")]

        add_check(
            f"plane_{plane_id}_cn_count_matches_indices",
            len(idxs) == m,
            f"plane={plane_id}, m={m}, indices={idxs}",
        )
        add_check(
            f"plane_{plane_id}_no_duplicate_cn_indices",
            len(idxs) == len(set(idxs)),
            f"plane={plane_id}, indices={idxs}",
        )

        if m > 1 and m % 2 == 0 and m < S:
            if S % 2 == 0:
                half = S // 2
                idx_set = set(idxs)
                opposite_ok = all(((idx + half) % S) in idx_set for idx in idxs)
                add_check(
                    f"plane_{plane_id}_even_cn_has_opposite_pairs",
                    opposite_ok,
                    f"plane={plane_id}, S={S}, indices={idxs}",
                )
            else:
                # Exact opposite pairs do not exist for odd satellites-per-plane.
                # In that case, the intended fallback is equal angular spacing.
                gaps = circular_gaps(idxs, S)
                gap_ok = (max(gaps) - min(gaps)) <= 1 if gaps else True
                add_check(
                    f"plane_{plane_id}_even_cn_equal_spacing_because_S_odd",
                    gap_ok,
                    f"plane={plane_id}, S={S}, indices={idxs}, gaps={gaps}",
                )
        elif m > 1 and m % 2 == 1:
            gaps = circular_gaps(idxs, S)
            gap_ok = (max(gaps) - min(gaps)) <= 1 if gaps else True
            add_check(
                f"plane_{plane_id}_odd_cn_equal_spacing",
                gap_ok,
                f"plane={plane_id}, S={S}, indices={idxs}, gaps={gaps}",
            )

    return pd.DataFrame(checks)


# =============================================================================
# Orbit and geometry functions
# =============================================================================

def rotation_matrix_1(angle_rad: float) -> np.ndarray:
    c, s = np.cos(angle_rad), np.sin(angle_rad)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])


def rotation_matrix_3(angle_rad: float) -> np.ndarray:
    c, s = np.cos(angle_rad), np.sin(angle_rad)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def satellite_eci_position(row: pd.Series, t_sec: float) -> np.ndarray:
    """
    Circular-orbit ECI position for one satellite.

    This is a lightweight propagator for architecture/contact screening.
    It does not include perturbations, drag, J2, or precise ephemerides.
    """
    a = float(row["semi_major_axis_km"])
    inc = deg2rad(float(row["inclination_deg"]))
    raan = deg2rad(float(row["raan_deg"]))
    mean_anomaly0 = deg2rad(float(row["mean_anomaly_deg"]))
    argp = deg2rad(float(row.get("arg_perigee_deg", 0.0)))

    n_rad_s = math.sqrt(EARTH_MU_KM3_S2 / (a**3))
    u = mean_anomaly0 + n_rad_s * t_sec + argp

    r_pf = np.array([a * math.cos(u), a * math.sin(u), 0.0])
    return rotation_matrix_3(raan) @ rotation_matrix_1(inc) @ r_pf


def ecef_to_eci(r_ecef_km: np.ndarray, t_sec: float) -> np.ndarray:
    theta = EARTH_ROT_RATE_RAD_S * t_sec
    return rotation_matrix_3(theta) @ r_ecef_km


def eci_to_ecef(r_eci_km: np.ndarray, t_sec: float) -> np.ndarray:
    theta = EARTH_ROT_RATE_RAD_S * t_sec
    return rotation_matrix_3(-theta) @ r_eci_km


def geodetic_to_ecef_spherical(lat_deg: float, lon_deg: float, alt_km: float = 0.0) -> np.ndarray:
    """Spherical-Earth geodetic to ECEF conversion."""
    lat = deg2rad(lat_deg)
    lon = deg2rad(lon_deg)
    r = EARTH_RADIUS_KM + alt_km
    return np.array(
        [
            r * math.cos(lat) * math.cos(lon),
            r * math.cos(lat) * math.sin(lon),
            r * math.sin(lat),
        ]
    )


def ecef_to_latlon_spherical(r_ecef_km: np.ndarray) -> Tuple[float, float]:
    x, y, z = r_ecef_km
    lon = math.atan2(y, x)
    hyp = math.sqrt(x * x + y * y)
    lat = math.atan2(z, hyp)
    return float(rad2deg(lat)), float(((rad2deg(lon) + 540.0) % 360.0) - 180.0)


def elevation_deg_from_gs(sat_eci_km: np.ndarray, gs: GroundStation, t_sec: float) -> float:
    """Approximate elevation angle from spherical Earth ground station."""
    sat_ecef = eci_to_ecef(sat_eci_km, t_sec)
    gs_ecef = geodetic_to_ecef_spherical(gs.latitude_deg, gs.longitude_deg, gs.altitude_km)
    rho = sat_ecef - gs_ecef
    rho_norm = np.linalg.norm(rho)
    if rho_norm <= 0:
        return -90.0
    zenith = gs_ecef / np.linalg.norm(gs_ecef)
    sin_el = float(np.dot(rho / rho_norm, zenith))
    return float(rad2deg(math.asin(max(-1.0, min(1.0, sin_el)))))


def sat_sat_los_clear(r1_km: np.ndarray, r2_km: np.ndarray, earth_radius_km: float = EARTH_RADIUS_KM) -> bool:
    """
    True if line segment between satellites is not blocked by Earth.

    Uses minimum distance from Earth center to the segment.
    """
    d = r2_km - r1_km
    denom = float(np.dot(d, d))
    if denom <= 0:
        return False
    u = -float(np.dot(r1_km, d)) / denom
    u_clamped = max(0.0, min(1.0, u))
    closest = r1_km + u_clamped * d
    clearance = np.linalg.norm(closest)
    return bool(clearance > earth_radius_km)


def haversine_km(lat1_deg: np.ndarray, lon1_deg: np.ndarray, lat2_deg: float, lon2_deg: float) -> np.ndarray:
    """Vectorized haversine distance in km."""
    lat1 = deg2rad(lat1_deg)
    lon1 = deg2rad(lon1_deg)
    lat2 = deg2rad(lat2_deg)
    lon2 = deg2rad(lon2_deg)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2.0) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2.0) ** 2
    c = 2.0 * np.arctan2(np.sqrt(a), np.sqrt(1.0 - a))
    return EARTH_RADIUS_KM * c


# =============================================================================
# Communication windows
# =============================================================================

def default_ground_stations(set_name: str = "munich_only") -> List[GroundStation]:
    """Return selectable ground-station sets for communication-constrained runs.

    munich_only is intentionally the default for the final CN-fraction study.
    It prevents the task dissemination problem from becoming unrealistically easy.
    """
    stations = {
        "munich_only": [
            GroundStation("GS_Munich", "GS_Munich", 48.1351, 11.5820, 0.0, 10.0),
        ],
        "tempe_only": [
            GroundStation("GS_Tempe", "GS_Tempe", 33.4484, -112.0740, 0.0, 10.0),
        ],
        "munich_tempe": [
            GroundStation("GS_Munich", "GS_Munich", 48.1351, 11.5820, 0.0, 10.0),
            GroundStation("GS_Tempe", "GS_Tempe", 33.4484, -112.0740, 0.0, 10.0),
        ],
        "global_4": [
            GroundStation("GS_Munich", "GS_Munich", 48.1351, 11.5820, 0.0, 10.0),
            GroundStation("GS_Tempe", "GS_Tempe", 33.4484, -112.0740, 0.0, 10.0),
            GroundStation("GS_Singapore", "GS_Singapore", 1.3521, 103.8198, 0.0, 10.0),
            GroundStation("GS_Sydney", "GS_Sydney", -33.8688, 151.2093, 0.0, 10.0),
        ],
    }
    key = str(set_name).strip().lower()
    if key not in stations:
        raise ValueError(f"Unknown ground_station_set={set_name}. Use one of {sorted(stations)}")
    return stations[key]


def allowed_sat_sat_link(row_i: pd.Series, row_j: pd.Series, sat_sat_mode: str) -> bool:
    """
    Link-topology filter.

    central_only:
        only links where at least one endpoint is a central node.
    all:
        all satellite-satellite links are allowed.
    none:
        no satellite-satellite links are allowed.
    """
    if sat_sat_mode == "none":
        return False
    if sat_sat_mode == "all":
        return True
    if sat_sat_mode == "central_only":
        return bool(row_i["is_central_node"] or row_j["is_central_node"])
    raise ValueError(f"Unknown sat_sat_mode={sat_sat_mode}")


def link_type_for_sat_pair(row_i: pd.Series, row_j: pd.Series) -> str:
    i_cn = bool(row_i["is_central_node"])
    j_cn = bool(row_j["is_central_node"])
    if i_cn and j_cn:
        return "cn_to_cn"
    if i_cn and not j_cn:
        return "cn_to_obs"
    if not i_cn and j_cn:
        return "obs_to_cn"
    return "obs_to_obs"


def group_boolean_windows(valid: Sequence[bool]) -> List[Tuple[int, int]]:
    """
    Convert a boolean time series into index windows [start_idx, end_idx].

    The returned end_idx is inclusive for the last valid sample.
    Actual end time is usually time[end_idx] + step.
    """
    windows = []
    in_window = False
    start = 0
    for idx, flag in enumerate(valid):
        if flag and not in_window:
            in_window = True
            start = idx
        elif not flag and in_window:
            windows.append((start, idx - 1))
            in_window = False
    if in_window:
        windows.append((start, len(valid) - 1))
    return windows


def precompute_satellite_positions(
    nodes_df: pd.DataFrame,
    time_seconds: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Precompute ECI positions and subpoint lat/lon for all satellites/times.

    Returns
    -------
    positions_eci: shape (n_times, n_sats, 3)
    subpoint_lat_deg: shape (n_times, n_sats)
    subpoint_lon_deg: shape (n_times, n_sats)
    """
    sat_rows = [row for _, row in nodes_df.iterrows()]
    n_t = len(time_seconds)
    n_s = len(sat_rows)
    positions = np.zeros((n_t, n_s, 3), dtype=float)
    sub_lat = np.zeros((n_t, n_s), dtype=float)
    sub_lon = np.zeros((n_t, n_s), dtype=float)

    for ti, t in enumerate(time_seconds):
        for si, row in enumerate(sat_rows):
            r_eci = satellite_eci_position(row, float(t))
            positions[ti, si, :] = r_eci
            r_ecef = eci_to_ecef(r_eci, float(t))
            lat, lon = ecef_to_latlon_spherical(r_ecef)
            sub_lat[ti, si] = lat
            sub_lon[ti, si] = lon

    return positions, sub_lat, sub_lon


def generate_communication_windows(
    config: ArchitectureConfig,
    nodes_df: pd.DataFrame,
    ground_stations: List[GroundStation],
    epoch: datetime,
    duration_hours: float,
    time_step_sec: int,
    sat_sat_max_range_km: float,
    sat_sat_data_rate_bps: float,
    sat_gs_data_rate_bps: float,
    sat_sat_mode: str,
    positions_eci: Optional[np.ndarray] = None,
    time_seconds: Optional[np.ndarray] = None,
) -> pd.DataFrame:
    """Generate sat-sat and sat-GS communication windows."""
    if time_seconds is None:
        time_seconds = np.arange(0.0, duration_hours * 3600.0 + 0.1, time_step_sec)
    if positions_eci is None:
        positions_eci, _, _ = precompute_satellite_positions(nodes_df, time_seconds)

    sat_rows = [row for _, row in nodes_df.reset_index(drop=True).iterrows()]
    window_rows: List[Dict] = []
    window_id = 0
    sim_end_sec = float(duration_hours * 3600.0)

    # Satellite-satellite windows.
    for i in range(len(sat_rows)):
        for j in range(i + 1, len(sat_rows)):
            row_i = sat_rows[i]
            row_j = sat_rows[j]
            if not allowed_sat_sat_link(row_i, row_j, sat_sat_mode):
                continue

            valid = []
            ranges = []
            for ti, _t in enumerate(time_seconds):
                r1 = positions_eci[ti, i, :]
                r2 = positions_eci[ti, j, :]
                rng = float(np.linalg.norm(r2 - r1))
                ranges.append(rng)
                valid.append(rng <= sat_sat_max_range_km and sat_sat_los_clear(r1, r2))

            for start_idx, end_idx in group_boolean_windows(valid):
                start_t = float(time_seconds[start_idx])
                # Add one time step because end_idx is the last valid sample.
                end_t = min(float(time_seconds[end_idx] + time_step_sec), sim_end_sec)
                duration_sec = max(0.0, end_t - start_t)
                if duration_sec <= 0:
                    continue

                rng_segment = np.array(ranges[start_idx : end_idx + 1], dtype=float)
                cap_mbits = sat_sat_data_rate_bps * duration_sec / 1e6
                link_type = link_type_for_sat_pair(row_i, row_j)

                window_rows.append(
                    {
                        "architecture_id": config.architecture_id,
                        "window_id": f"W{window_id:08d}",
                        "node_i": row_i["node_id"],
                        "node_j": row_j["node_id"],
                        "from_node": row_i["node_id"],
                        "to_node": row_j["node_id"],
                        "node_i_type": row_i["node_type"],
                        "node_j_type": row_j["node_type"],
                        "plane_i": int(row_i["plane_id"]),
                        "plane_j": int(row_j["plane_id"]),
                        "link_type": link_type,
                        "is_sat_sat": True,
                        "is_sat_gs": False,
                        "start_time_utc": iso_utc(epoch + timedelta(seconds=start_t)),
                        "end_time_utc": iso_utc(epoch + timedelta(seconds=end_t)),
                        "start_time_sec": start_t,
                        "end_time_sec": end_t,
                        "duration_sec": duration_sec,
                        "data_rate_bps": sat_sat_data_rate_bps,
                        "capacity_mbits": cap_mbits,
                        "min_range_km": float(np.min(rng_segment)),
                        "max_range_km": float(np.max(rng_segment)),
                        "mean_range_km": float(np.mean(rng_segment)),
                    }
                )
                window_id += 1

    # Satellite-ground-station windows.
    for si, sat_row in enumerate(sat_rows):
        for gs in ground_stations:
            valid = []
            elevations = []
            ranges = []
            gs_ecef = geodetic_to_ecef_spherical(gs.latitude_deg, gs.longitude_deg, gs.altitude_km)
            for ti, t in enumerate(time_seconds):
                sat_eci = positions_eci[ti, si, :]
                sat_ecef = eci_to_ecef(sat_eci, float(t))
                elev = elevation_deg_from_gs(sat_eci, gs, float(t))
                rng = float(np.linalg.norm(sat_ecef - gs_ecef))
                elevations.append(elev)
                ranges.append(rng)
                valid.append(elev >= gs.elevation_mask_deg)

            for start_idx, end_idx in group_boolean_windows(valid):
                start_t = float(time_seconds[start_idx])
                end_t = min(float(time_seconds[end_idx] + time_step_sec), sim_end_sec)
                duration_sec = max(0.0, end_t - start_t)
                if duration_sec <= 0:
                    continue
                cap_mbits = sat_gs_data_rate_bps * duration_sec / 1e6
                elev_segment = np.array(elevations[start_idx : end_idx + 1], dtype=float)
                rng_segment = np.array(ranges[start_idx : end_idx + 1], dtype=float)

                window_rows.append(
                    {
                        "architecture_id": config.architecture_id,
                        "window_id": f"W{window_id:08d}",
                        "node_i": sat_row["node_id"],
                        "node_j": gs.node_id,
                        "from_node": sat_row["node_id"],
                        "to_node": gs.node_id,
                        "node_i_type": sat_row["node_type"],
                        "node_j_type": "ground_station",
                        "plane_i": int(sat_row["plane_id"]),
                        "plane_j": -1,
                        "link_type": "sat_gs",
                        "is_sat_sat": False,
                        "is_sat_gs": True,
                        "start_time_utc": iso_utc(epoch + timedelta(seconds=start_t)),
                        "end_time_utc": iso_utc(epoch + timedelta(seconds=end_t)),
                        "start_time_sec": start_t,
                        "end_time_sec": end_t,
                        "duration_sec": duration_sec,
                        "data_rate_bps": sat_gs_data_rate_bps,
                        "capacity_mbits": cap_mbits,
                        "min_range_km": float(np.min(rng_segment)),
                        "max_range_km": float(np.max(rng_segment)),
                        "mean_range_km": float(np.mean(rng_segment)),
                        "max_elevation_deg": float(np.max(elev_segment)),
                    }
                )
                window_id += 1

    windows_df = pd.DataFrame(window_rows)
    if not windows_df.empty:
        windows_df = windows_df.sort_values(["start_time_sec", "end_time_sec", "window_id"]).reset_index(drop=True)
    return windows_df


# =============================================================================
# Wildfire tasks and observation opportunities
# =============================================================================

def resolve_task_file_path(tasks_file: Optional[Path]) -> Path:
    """
    Resolve the wildfire task CSV path before starting heavy computation.

    Important:
    - No silent fallback to demo tasks is allowed.
    - If the path has no suffix and does not exist, also try the same path with .csv.
    - The returned path is absolute so parallel worker processes can all access it.
    """
    candidate = Path(tasks_file) if tasks_file is not None else DEFAULT_TASKS_FILE

    checked: List[Path] = []
    candidate_expanded = candidate.expanduser()
    checked.append(candidate_expanded)

    if candidate_expanded.exists() and candidate_expanded.is_file():
        return candidate_expanded.resolve()

    # User sometimes gives the task filename without the .csv suffix.
    if candidate_expanded.suffix == "":
        for ext in [".csv", ".CSV"]:
            csv_candidate = Path(str(candidate_expanded) + ext)
            checked.append(csv_candidate)
            if csv_candidate.exists() and csv_candidate.is_file():
                return csv_candidate.resolve()

    checked_text = "\n".join(f"  - {p}" for p in checked)
    raise FileNotFoundError(
        "Wildfire task CSV not found. The code will not fall back to demo tasks.\n"
        "Checked paths:\n"
        f"{checked_text}\n\n"
        "Fix the path with --tasks-file or place the CSV at DEFAULT_TASKS_FILE."
    )


def load_tasks(
    tasks_file: Path,
    epoch: datetime,
    max_tasks: int,
    task_time_mode: str = "remap_to_sim_start",
    task_spacing_minutes: float = 1.0,
) -> pd.DataFrame:
    """
    Load wildfire tasks from CSV and normalize the schema.

    This function intentionally reads the task CSV as-is. It does not apply any
    additional deduplication or filtering beyond the optional max_tasks limit.

    Expected flexible columns:
    - task_id / id / fire_id
    - latitude / lat / centroid_lat / task_lat
    - longitude / lon / centroid_lon / task_lon
    - timestamp_utc / created_time_utc / acq_datetime / datetime_utc
    - priority_class optional
    - deadline_time_utc optional
    - task_size_mbits optional
    """
    resolved_path = resolve_task_file_path(tasks_file)
    raw_df = pd.read_csv(resolved_path)
    if raw_df.empty:
        raise ValueError(f"Task CSV is empty: {resolved_path}")

    print(f"Loading wildfire tasks from: {resolved_path}")
    print(f"Raw task rows found: {len(raw_df)}")
    print(f"Raw task columns: {list(raw_df.columns)}")

    df = normalize_tasks(
        raw_df,
        epoch=epoch,
        task_time_mode=task_time_mode,
        task_spacing_minutes=task_spacing_minutes,
    )
    if max_tasks and max_tasks > 0:
        df = df.head(max_tasks).copy()
        print(f"Using first {len(df)} tasks because --max-tasks={max_tasks}")
    else:
        print(f"Using all {len(df)} tasks because --max-tasks=0")
    return df.reset_index(drop=True)


def validate_task_horizon(
    tasks_df: pd.DataFrame,
    duration_hours: float,
    task_time_mode: str,
    allow_truncated_task_horizon: bool = False,
) -> None:
    """
    Protect thesis runs from accidentally truncating a real multi-day task set.

    For the final actual-epoch workflow, late tasks and deadlines must fit inside
    the simulated horizon. If they do not, the resulting performance tables can
    look artificially poor and should not be used for architecture optimization.
    """
    if tasks_df.empty:
        return
    if str(task_time_mode).strip().lower() != "original":
        return

    sim_end_sec = float(duration_hours) * 3600.0
    latest_created = float(tasks_df["created_time_sec"].max())
    latest_deadline = float(tasks_df["deadline_time_sec"].max())
    if latest_deadline <= sim_end_sec and latest_created <= sim_end_sec:
        return

    message = (
        "The loaded wildfire task set extends beyond the simulation duration. "
        f"Latest creation time is {latest_created / 3600.0:.2f} h and latest deadline is "
        f"{latest_deadline / 3600.0:.2f} h, but --duration-hours is only {float(duration_hours):.2f} h. "
        "This would make the architecture ranking artificially pessimistic for late tasks. "
        "For real multi-event thesis studies, do not run the raw Walker sweep directly on the combined "
        "task CSV. Use run_891_by_region_actual_epoch_laptop_v3.py or run_final_thesis_pipeline.py so "
        "each event gets its own duration. If you intentionally want a truncated debugging run, pass "
        "--allow-truncated-task-horizon."
    )
    if allow_truncated_task_horizon:
        print(f"WARNING: {message}")
        return
    raise RuntimeError(message)

def first_existing_column(df: pd.DataFrame, candidates: Sequence[str]) -> Optional[str]:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def infer_epoch_from_task_file(tasks_file: Path, epoch_floor: str = "none") -> datetime:
    """Infer the simulation epoch from the earliest real task timestamp in the CSV.

    This is used for the final thesis runs where the simulation should start at
    the actual wildfire-task epoch instead of a synthetic epoch such as
    2026-01-01. The function only reads the task CSV and finds the earliest
    valid timestamp column supported by the normal task loader.
    """
    df = pd.read_csv(tasks_file)
    time_col = first_existing_column(
        df,
        [
            "timestamp_utc",
            "timestamp",
            "created_time_utc",
            "acq_datetime",
            "datetime_utc",
            "acquired_datetime_utc",
            "acquisition_time_utc",
            "time_utc",
        ],
    )
    if time_col is None:
        raise ValueError(
            "Cannot infer actual task epoch because no timestamp column was found. "
            f"Available columns: {list(df.columns)}"
        )

    dt = pd.to_datetime(df[time_col], utc=True, errors="coerce").dropna()
    if dt.empty:
        raise ValueError(f"Cannot infer actual task epoch because column {time_col!r} contains no valid datetimes.")

    epoch = dt.min().to_pydatetime()
    floor = str(epoch_floor).strip().lower()
    if floor == "minute":
        epoch = epoch.replace(second=0, microsecond=0)
    elif floor == "hour":
        epoch = epoch.replace(minute=0, second=0, microsecond=0)
    elif floor == "day":
        epoch = epoch.replace(hour=0, minute=0, second=0, microsecond=0)
    elif floor == "none":
        pass
    else:
        raise ValueError("--epoch-floor must be one of: none, minute, hour, day")

    if epoch.tzinfo is None:
        epoch = epoch.replace(tzinfo=timezone.utc)
    else:
        epoch = epoch.astimezone(timezone.utc)
    return epoch


def format_datetime_series_as_utc(series: pd.Series) -> pd.Series:
    """Convert a pandas datetime series to ISO UTC strings with Z suffix."""
    dt = pd.to_datetime(series, utc=True, errors="coerce")
    return dt.map(lambda x: iso_utc(x.to_pydatetime()) if pd.notna(x) else "")


def normalize_tasks(
    df: pd.DataFrame,
    epoch: datetime,
    task_time_mode: str = "remap_to_sim_start",
    task_spacing_minutes: float = 1.0,
) -> pd.DataFrame:
    """Normalize task CSV columns and optionally remap task times into the simulation window.

    Why remapping is needed
    -----------------------
    The Sentinel-3 task file contains real acquisition timestamps, e.g. July 2025.
    The architecture validation simulation usually starts at DEFAULT_EPOCH_UTC
    (2026-01-01 by default). If the original timestamps are used directly, every
    task deadline may occur before the simulation starts. That produces negative
    deadlines and invalid performance metrics.

    Therefore, the default mode is ``remap_to_sim_start``:
    - keep the original acquisition time in ``timestamp_utc_original``;
    - sort tasks by original timestamp;
    - create simulation timestamps from ``epoch + i * task_spacing_minutes``;
    - recompute deadlines from the remapped timestamp and priority class.

    No deduplication or spatial filtering is applied here. The input CSV is used
    as the task set.
    """
    df = df.copy()

    task_col = first_existing_column(df, ["task_id", "id", "fire_id", "source_task_id", "event_id"])
    lat_col = first_existing_column(df, ["latitude", "lat", "centroid_lat", "task_lat", "fire_lat", "y", "Latitude", "LATITUDE"])
    lon_col = first_existing_column(df, ["longitude", "lon", "lng", "centroid_lon", "task_lon", "fire_lon", "x", "Longitude", "LONGITUDE"])
    time_col = first_existing_column(df, ["timestamp_utc", "timestamp", "created_time_utc", "acq_datetime", "datetime_utc", "acquired_datetime_utc", "acquisition_time_utc", "time_utc"])
    priority_col = first_existing_column(df, ["priority_class", "priority", "class", "task_priority_class"])
    size_col = first_existing_column(df, ["task_size_mbits", "size_mbits", "data_size_mbits"])
    deadline_col = first_existing_column(df, ["deadline_time_utc", "deadline_utc"])
    response_minutes_col = first_existing_column(
        df,
        [
            "required_response_time_min",
            "required_response_min",
            "response_time_min",
            "required_response_time_minutes",
            "deadline_minutes",
        ],
    )

    if lat_col is None or lon_col is None:
        raise ValueError(
            "Task file must contain latitude/longitude columns. "
            f"Available columns: {list(df.columns)}"
        )

    out = pd.DataFrame()
    out["source_row_index"] = np.arange(len(df), dtype=int)
    out["task_id"] = df[task_col].astype(str) if task_col else [f"TASK_{i:06d}" for i in range(len(df))]
    out["latitude"] = pd.to_numeric(df[lat_col], errors="coerce")
    out["longitude"] = pd.to_numeric(df[lon_col], errors="coerce")
    out["priority_class"] = df[priority_col].astype(str) if priority_col else "Medium"
    out["task_size_mbits"] = pd.to_numeric(df[size_col], errors="coerce") if size_col else 0.5
    out["region"] = df["region"].astype(str) if "region" in df.columns else "Unknown"
    out["required_response_time_min"] = (
        pd.to_numeric(df[response_minutes_col], errors="coerce")
        if response_minutes_col
        else np.nan
    )

    # Normalize priority strings.
    out["priority_class"] = out["priority_class"].map(lambda x: str(x).strip().capitalize())
    out.loc[~out["priority_class"].isin(PRIORITY_DEADLINE_MINUTES.keys()), "priority_class"] = "Medium"
    out["task_size_mbits"] = out["task_size_mbits"].fillna(0.5).astype(float)

    # Drop tasks with invalid coordinates. The drop is explicit and reported by count.
    before = len(out)
    out = out.dropna(subset=["latitude", "longitude"]).reset_index(drop=True)
    dropped = before - len(out)
    if dropped:
        print(f"Dropped {dropped} tasks with invalid latitude/longitude.")

    # Keep original timestamps for traceability.
    if time_col:
        original_dt = pd.to_datetime(df.loc[out["source_row_index"], time_col].reset_index(drop=True), utc=True, errors="coerce")
        out["timestamp_utc_original"] = original_dt.map(lambda x: iso_utc(x.to_pydatetime()) if pd.notna(x) else "")
    else:
        original_dt = pd.Series([pd.NaT] * len(out))
        out["timestamp_utc_original"] = ""

    if deadline_col:
        original_deadline_dt = pd.to_datetime(df.loc[out["source_row_index"], deadline_col].reset_index(drop=True), utc=True, errors="coerce")
        out["deadline_time_utc_original"] = original_deadline_dt.map(lambda x: iso_utc(x.to_pydatetime()) if pd.notna(x) else "")
    else:
        out["deadline_time_utc_original"] = ""

    mode = str(task_time_mode).strip().lower()
    if mode not in {"remap_to_sim_start", "original"}:
        raise ValueError("--task-time-mode must be either 'remap_to_sim_start' or 'original'.")

    if mode == "remap_to_sim_start":
        # Sort chronologically when possible; NaT values go last.
        out["_sort_time"] = pd.to_datetime(out["timestamp_utc_original"], utc=True, errors="coerce")
        out = out.sort_values(["_sort_time", "source_row_index"], na_position="last").drop(columns=["_sort_time"]).reset_index(drop=True)

        spacing = timedelta(minutes=float(task_spacing_minutes))
        sim_times = [epoch + i * spacing for i in range(len(out))]
        deadlines = []
        applied_response_minutes = []
        for sim_time, priority, response_minutes in zip(
            sim_times,
            out["priority_class"],
            out["required_response_time_min"],
        ):
            minutes = (
                float(response_minutes)
                if pd.notna(response_minutes)
                else PRIORITY_DEADLINE_MINUTES[str(priority)]
            )
            deadlines.append(sim_time + timedelta(minutes=minutes))
            applied_response_minutes.append(minutes)

        out["timestamp_utc"] = [iso_utc(t) for t in sim_times]
        out["deadline_time_utc"] = [iso_utc(t) for t in deadlines]
        out["required_response_time_min"] = applied_response_minutes
        out["task_time_mode"] = "remap_to_sim_start"
        out["task_spacing_minutes"] = float(task_spacing_minutes)
    else:
        # Use original real acquisition times. This is usually invalid for local
        # architecture tests unless --epoch-utc is aligned with the task date.
        if time_col is None:
            raise ValueError("Cannot use --task-time-mode original because no timestamp column was found.")
        out["timestamp_utc"] = out["timestamp_utc_original"]
        deadlines = []
        applied_response_minutes = []
        for _, row in out.iterrows():
            start = parse_utc(str(row["timestamp_utc"]))
            response_minutes = row.get("required_response_time_min", np.nan)
            if pd.notna(response_minutes):
                # required_response_time_min is authoritative when present.
                minutes = float(response_minutes)
                deadlines.append(iso_utc(start + timedelta(minutes=minutes)))
                applied_response_minutes.append(minutes)
            elif row["deadline_time_utc_original"]:
                deadline = parse_utc(str(row["deadline_time_utc_original"]))
                minutes = (deadline - start).total_seconds() / 60.0
                deadlines.append(iso_utc(deadline))
                applied_response_minutes.append(minutes)
            else:
                minutes = PRIORITY_DEADLINE_MINUTES[str(row["priority_class"])]
                deadlines.append(iso_utc(start + timedelta(minutes=minutes)))
                applied_response_minutes.append(minutes)
        out["deadline_time_utc"] = deadlines
        out["required_response_time_min"] = applied_response_minutes
        out["task_time_mode"] = "original"
        out["task_spacing_minutes"] = np.nan

    # Add seconds from simulation epoch for easy validation/debugging.
    out["created_time_sec"] = out["timestamp_utc"].map(lambda x: (parse_utc(str(x)) - epoch).total_seconds())
    out["deadline_time_sec"] = out["deadline_time_utc"].map(lambda x: (parse_utc(str(x)) - epoch).total_seconds())

    applied_minutes = (out["deadline_time_sec"] - out["created_time_sec"]) / 60.0
    expected_minutes = out["priority_class"].map(PRIORITY_DEADLINE_MINUTES).astype(float)
    source_minutes = pd.to_numeric(out["required_response_time_min"], errors="coerce")
    expected_from_source = source_minutes.where(source_minutes.notna(), expected_minutes)
    if not np.allclose(applied_minutes.to_numpy(), expected_from_source.to_numpy(), atol=1e-6):
        bad = out.loc[
            ~np.isclose(applied_minutes, expected_from_source, atol=1e-6),
            ["task_id", "priority_class", "timestamp_utc", "deadline_time_utc", "required_response_time_min"],
        ]
        raise AssertionError(
            "Applied task deadlines do not match required_response_time_min. "
            f"First mismatches:\n{bad.head(10).to_string(index=False)}"
        )

    allowed = np.array(sorted(EXPECTED_DEADLINE_MINUTES), dtype=float)
    unexpected = sorted(
        float(v)
        for v in pd.unique(np.round(applied_minutes, 6))
        if not np.isclose(float(v), allowed, atol=1e-6).any()
    )
    if unexpected:
        raise AssertionError(
            "Deadline distribution contains values outside the approved "
            f"30/60/180/360-minute set: {unexpected}"
        )
    if not np.allclose(applied_minutes.to_numpy(), expected_minutes.to_numpy(), atol=1e-6):
        raise AssertionError(
            "Applied response times conflict with the approved priority mapping "
            "Critical=30, High=60, Medium=180, Low=360 minutes."
        )
    out["applied_response_time_min"] = applied_minutes.astype(float)

    return out


def generate_observation_opportunities(
    config: ArchitectureConfig,
    nodes_df: pd.DataFrame,
    tasks_df: pd.DataFrame,
    epoch: datetime,
    duration_hours: float,
    time_step_sec: int,
    observation_radius_km: float,
    subpoint_lat: Optional[np.ndarray] = None,
    subpoint_lon: Optional[np.ndarray] = None,
    time_seconds: Optional[np.ndarray] = None,
) -> pd.DataFrame:
    """Generate task-satellite observation opportunities using subpoint-radius access."""
    if time_seconds is None:
        time_seconds = np.arange(0.0, duration_hours * 3600.0 + 0.1, time_step_sec)
    if subpoint_lat is None or subpoint_lon is None:
        _, subpoint_lat, subpoint_lon = precompute_satellite_positions(nodes_df, time_seconds)

    sat_rows = nodes_df.reset_index(drop=True)
    opportunity_rows: List[Dict] = []
    opp_id = 0
    sim_end_sec = float(duration_hours * 3600.0)

    for _, task in tasks_df.iterrows():
        task_id = str(task["task_id"])
        task_lat = float(task["latitude"])
        task_lon = float(task["longitude"])
        created_dt = parse_utc(str(task["timestamp_utc"]))
        deadline_dt = parse_utc(str(task["deadline_time_utc"]))
        created_sec = max(0.0, (created_dt - epoch).total_seconds())
        deadline_sec = min(duration_hours * 3600.0, (deadline_dt - epoch).total_seconds())

        # Distance array shape: (n_times, n_sats)
        dist_km = haversine_km(subpoint_lat, subpoint_lon, task_lat, task_lon)
        valid = (dist_km <= observation_radius_km) & (time_seconds[:, None] >= created_sec)

        for si, sat_row in sat_rows.iterrows():
            valid_series = valid[:, si].tolist()
            for start_idx, end_idx in group_boolean_windows(valid_series):
                start_t = float(time_seconds[start_idx])
                end_t = min(float(time_seconds[end_idx] + time_step_sec), sim_end_sec)
                duration_sec = max(0.0, end_t - start_t)
                if duration_sec <= 0:
                    continue
                segment_dist = dist_km[start_idx : end_idx + 1, si]
                earliest_obs_t = start_t

                opportunity_rows.append(
                    {
                        "architecture_id": config.architecture_id,
                        "opportunity_id": f"O{opp_id:08d}",
                        "task_id": task_id,
                        "satellite_id": sat_row["satellite_id"],
                        "node_id": sat_row["node_id"],
                        "plane_id": int(sat_row["plane_id"]),
                        "satellite_index_in_plane": int(sat_row["satellite_index_in_plane"]),
                        "is_central_node": bool(sat_row["is_central_node"]),
                        "task_latitude": task_lat,
                        "task_longitude": task_lon,
                        "access_start_time_utc": iso_utc(epoch + timedelta(seconds=start_t)),
                        "access_end_time_utc": iso_utc(epoch + timedelta(seconds=end_t)),
                        "access_start_time_sec": start_t,
                        "access_end_time_sec": end_t,
                        "access_duration_sec": duration_sec,
                        "earliest_observation_time_utc": iso_utc(epoch + timedelta(seconds=earliest_obs_t)),
                        "earliest_observation_time_sec": earliest_obs_t,
                        "min_distance_km": float(np.min(segment_dist)),
                        "observation_radius_km": observation_radius_km,
                        "priority_class": task["priority_class"],
                        "created_time_utc": task["timestamp_utc"],
                        "deadline_time_utc": task["deadline_time_utc"],
                        "deadline_time_sec": deadline_sec,
                        "possible_before_deadline": bool(earliest_obs_t <= deadline_sec),
                    }
                )
                opp_id += 1

    opp_df = pd.DataFrame(opportunity_rows)
    if not opp_df.empty:
        opp_df = opp_df.sort_values(["task_id", "earliest_observation_time_sec", "satellite_id"]).reset_index(drop=True)
    return opp_df


# =============================================================================
# Adjacency matrices
# =============================================================================

def make_node_index(nodes_df: pd.DataFrame, ground_stations: List[GroundStation]) -> Tuple[List[str], Dict[str, int]]:
    nodes = list(nodes_df["node_id"].astype(str)) + [gs.node_id for gs in ground_stations]
    return nodes, {node_id: idx for idx, node_id in enumerate(nodes)}


def adjacency_from_windows(
    windows_df: pd.DataFrame,
    node_ids: List[str],
    node_index: Dict[str, int],
) -> Dict[str, pd.DataFrame]:
    """Build opportunity adjacency matrices from communication windows."""
    n = len(node_ids)
    count = np.zeros((n, n), dtype=float)
    duration = np.zeros((n, n), dtype=float)
    capacity = np.zeros((n, n), dtype=float)

    for _, row in windows_df.iterrows():
        i = node_index[str(row["node_i"])]
        j = node_index[str(row["node_j"])]
        count[i, j] += 1
        count[j, i] += 1
        duration[i, j] += float(row["duration_sec"])
        duration[j, i] += float(row["duration_sec"])
        capacity[i, j] += float(row["capacity_mbits"])
        capacity[j, i] += float(row["capacity_mbits"])

    return {
        "adjacency_window_count.csv": pd.DataFrame(count, index=node_ids, columns=node_ids),
        "adjacency_window_duration_sec.csv": pd.DataFrame(duration, index=node_ids, columns=node_ids),
        "adjacency_window_capacity_mbits.csv": pd.DataFrame(capacity, index=node_ids, columns=node_ids),
    }


def adjacency_from_transfers(
    transfers_df: pd.DataFrame,
    node_ids: List[str],
    node_index: Dict[str, int],
) -> Dict[str, pd.DataFrame]:
    """Build actual-use adjacency matrices from post-processed transfer events."""
    n = len(node_ids)
    count = np.zeros((n, n), dtype=float)
    data = np.zeros((n, n), dtype=float)

    if transfers_df.empty:
        return {
            "adjacency_transfer_count.csv": pd.DataFrame(count, index=node_ids, columns=node_ids),
            "adjacency_transfer_data_mbits.csv": pd.DataFrame(data, index=node_ids, columns=node_ids),
        }

    for _, row in transfers_df.iterrows():
        i = node_index[str(row["from_node"])]
        j = node_index[str(row["to_node"])]
        count[i, j] += 1
        data[i, j] += float(row["transferred_data_mbits"])

    return {
        "adjacency_transfer_count.csv": pd.DataFrame(count, index=node_ids, columns=node_ids),
        "adjacency_transfer_data_mbits.csv": pd.DataFrame(data, index=node_ids, columns=node_ids),
    }


# =============================================================================
# Lightweight post-processing replay
# =============================================================================

def postprocess_targeted_dissemination_and_observation(
    config: ArchitectureConfig,
    nodes_df: pd.DataFrame,
    ground_stations: List[GroundStation],
    tasks_df: pd.DataFrame,
    windows_df: pd.DataFrame,
    opportunities_df: pd.DataFrame,
    epoch: datetime,
    routing_policy: str = "targeted_central",
    max_forwarding_hops: int = 3,
    max_observer_copies_per_task: int = 3,
    max_cn_copies_per_task: int = 5,
    task_size_mode: str = "priority",
    default_task_size_mbits: float = 10.0,
    critical_task_size_mbits: float = 25.0,
    high_task_size_mbits: float = 15.0,
    medium_task_size_mbits: float = 10.0,
    low_task_size_mbits: float = 5.0,
    initial_ground_station_id: Optional[str] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Capacity- and route-constrained store-and-forward communication replay.

    This replaces the earlier easy dissemination model that effectively flooded
    every task to every satellite. The new model is closer to a real DSS command
    dissemination problem:

    - Tasks originate at ground stations at their creation time.
    - A task can be injected into the constellation only during a sat-GS window.
    - Satellite-satellite forwarding is only possible during generated contact windows.
    - Each packet consumes time and capacity: transfer_time = task_size / data_rate.
    - Transfers are sequential inside each window.
    - Routing has a finite hop limit.
    - By default, observers only receive a task if they are useful for observing it.
    - Central nodes are used as relay/coordinator nodes, not as magic global memory.

    routing_policy
    --------------
    targeted_central:
        GS can uplink to satellites. CNs relay to CNs and to useful observers.
        Observers may forward only to CNs. Observers are useful if they have a
        future observation opportunity before the task deadline. Full flooding
        is deliberately not part of the corrected mission workflow.

    max_observer_copies_per_task and max_cn_copies_per_task
    -------------------------------------------------------
    Use 0 or a negative value for unlimited copies. Keeping these finite prevents
    every task from being copied to the whole constellation.
    """
    routing_policy = str(routing_policy).strip().lower()
    if routing_policy != "targeted_central":
        raise ValueError("The corrected mission workflow requires routing_policy='targeted_central'.")

    task_size_mode = str(task_size_mode).strip().lower().replace("-", "_")
    if task_size_mode != "priority":
        raise ValueError("The corrected mission workflow requires task_size_mode='priority'.")

    gs_ids = [gs.node_id for gs in ground_stations]
    if not gs_ids:
        raise ValueError("At least one ground station is required for physically grounded task injection.")
    source_ground_station = initial_ground_station_id or gs_ids[0]
    if source_ground_station not in gs_ids:
        raise ValueError(
            f"Initial ground station {source_ground_station!r} is not in the configured set {gs_ids}."
        )
    sat_ids = list(nodes_df["node_id"].astype(str))
    node_type: Dict[str, str] = {str(row["node_id"]): str(row["node_type"]) for _, row in nodes_df.iterrows()}
    for gs_id in gs_ids:
        node_type[gs_id] = "ground_station"

    central_nodes = {node_id for node_id, typ in node_type.items() if typ == "central_node"}
    observer_nodes = {node_id for node_id, typ in node_type.items() if typ == "observer"}

    # ------------------------------------------------------------------
    # Task info and realistic packet sizes.
    # ------------------------------------------------------------------
    priority_size = {
        "Critical": float(critical_task_size_mbits),
        "High": float(high_task_size_mbits),
        "Medium": float(medium_task_size_mbits),
        "Low": float(low_task_size_mbits),
    }

    def effective_task_size_mbits(task_row: pd.Series) -> float:
        priority = str(task_row.get("priority_class", "Medium"))
        csv_size = float(task_row.get("task_size_mbits", np.nan)) if "task_size_mbits" in task_row.index else np.nan
        if task_size_mode == "fixed":
            return float(default_task_size_mbits)
        if task_size_mode == "priority":
            return float(priority_size.get(priority, float(default_task_size_mbits)))
        # csv_or_default
        if np.isfinite(csv_size) and csv_size > 0:
            return float(csv_size)
        return float(default_task_size_mbits)

    task_info: Dict[str, Dict] = {}
    for _, task in tasks_df.iterrows():
        task_id = str(task["task_id"])
        created_sec = (parse_utc(str(task["timestamp_utc"])) - epoch).total_seconds()
        deadline_sec = (parse_utc(str(task["deadline_time_utc"])) - epoch).total_seconds()
        priority = str(task["priority_class"])
        task_info[task_id] = {
            "created_sec": created_sec,
            "deadline_sec": deadline_sec,
            "priority_class": priority,
            "priority_rank": PRIORITY_RANK.get(priority, 2),
            "task_size_mbits": effective_task_size_mbits(task),
            "region": str(task.get("region", "Unknown")),
        }

    # ------------------------------------------------------------------
    # Useful observer selection. This is what prevents unrealistic flood-to-all.
    # A useful observer is a satellite with a valid future access before deadline.
    # ------------------------------------------------------------------
    useful_observers_by_task: Dict[str, List[str]] = {tid: [] for tid in task_info}
    useful_satellites_by_task: Dict[str, List[str]] = {tid: [] for tid in task_info}
    best_access_by_task_observer: Dict[Tuple[str, str], float] = {}
    possible_before_deadline_by_task: Dict[str, bool] = {tid: False for tid in task_info}

    if not opportunities_df.empty:
        for task_id, group in opportunities_df.groupby("task_id"):
            tid = str(task_id)
            if tid not in task_info:
                continue
            deadline = task_info[tid]["deadline_sec"]
            valid = group[group["earliest_observation_time_sec"] <= deadline].copy()
            if valid.empty:
                continue
            possible_before_deadline_by_task[tid] = True
            best = valid.sort_values("earliest_observation_time_sec").groupby("node_id", as_index=False).first()
            best = best.sort_values("earliest_observation_time_sec")
            observers = []
            useful_satellites = []
            for _, row in best.iterrows():
                node = str(row["node_id"])
                if node in sat_ids:
                    useful_satellites.append(node)
                    best_access_by_task_observer[(tid, node)] = float(row["earliest_observation_time_sec"])
                # A CN can observe in the geometry model, but for task allocation we
                # count targeted observer copies separately from relay CN copies.
                if node in observer_nodes:
                    observers.append(node)
            if max_observer_copies_per_task and max_observer_copies_per_task > 0:
                observers = observers[: int(max_observer_copies_per_task)]
            useful_observers_by_task[tid] = observers
            useful_satellites_by_task[tid] = useful_satellites

    # ------------------------------------------------------------------
    # Reception state.
    # Each state entry stores the earliest complete reception of the task at node.
    # The designated source ground station knows each task at creation time;
    # satellites and other ground stations do not. This models a ground-generated
    # task entering the constellation through a real uplink contact.
    # ------------------------------------------------------------------
    received: Dict[Tuple[str, str], Dict] = {}
    for task_id, info in task_info.items():
        received[(task_id, source_ground_station)] = {
            "time_sec": float(info["created_sec"]),
            "hop_count": 0,
            "from_node": "GROUND_TASK_SERVICE",
            "via_window_id": "",
        }

    observer_copy_count: Dict[str, int] = {tid: 0 for tid in task_info}
    cn_copy_count: Dict[str, int] = {tid: 0 for tid in task_info}

    def has_received(task_id: str, node: str) -> bool:
        return (task_id, node) in received

    def recv_time(task_id: str, node: str) -> float:
        return float(received[(task_id, node)]["time_sec"])

    def recv_hops(task_id: str, node: str) -> int:
        return int(received[(task_id, node)]["hop_count"])

    def receiver_allowed(task_id: str, receiver: str) -> bool:
        typ = node_type.get(receiver, "unknown")
        if typ == "ground_station":
            # This replay models command/task dissemination, not science-data downlink.
            return False
        if typ == "central_node":
            if max_cn_copies_per_task and max_cn_copies_per_task > 0:
                return cn_copy_count.get(task_id, 0) < int(max_cn_copies_per_task)
            return True
        if typ == "observer":
            if receiver not in useful_observers_by_task.get(task_id, []):
                return False
            if max_observer_copies_per_task and max_observer_copies_per_task > 0:
                return observer_copy_count.get(task_id, 0) < int(max_observer_copies_per_task)
            return True
        return False

    def directed_transfer_allowed(sender: str, receiver: str, is_sat_gs: bool) -> bool:
        s_type = node_type.get(sender, "unknown")
        r_type = node_type.get(receiver, "unknown")

        if is_sat_gs:
            # Ground injects tasks into satellites. Do not model task downlink here.
            return s_type == "ground_station" and r_type in {"observer", "central_node"}

        # Satellite-satellite forwarding.
        if s_type == "ground_station" or r_type == "ground_station":
            return False
        # targeted_central: central nodes form the coordination backbone.
        if s_type == "central_node" and r_type in {"central_node", "observer"}:
            return True
        if s_type == "observer" and r_type == "central_node":
            return True
        return False

    def task_priority_score(task_id: str, receiver: str) -> Tuple:
        info = task_info[task_id]
        # Smaller tuple is higher priority.
        receiver_type = node_type.get(receiver, "unknown")
        target_bonus = 0 if receiver in useful_observers_by_task.get(task_id, []) else 1
        relay_bonus = 0 if receiver_type == "central_node" else 1
        access_time = best_access_by_task_observer.get((task_id, receiver), float("inf"))
        return (
            int(info["priority_rank"]),
            float(info["deadline_sec"]),
            int(target_bonus),
            int(relay_bonus),
            float(access_time),
            str(task_id),
            str(receiver),
        )

    transfer_rows: List[Dict] = []

    windows_sorted = windows_df.sort_values(["start_time_sec", "end_time_sec", "window_id"]).reset_index(drop=True)

    for _, window in windows_sorted.iterrows():
        node_a = str(window["node_i"])
        node_b = str(window["node_j"])
        window_start = float(window["start_time_sec"])
        window_end = float(window["end_time_sec"])
        data_rate_bps = float(window["data_rate_bps"])
        if data_rate_bps <= 0 or window_end <= window_start:
            continue
        is_sat_gs = bool(window.get("is_sat_gs", False))
        current_time = window_start
        capacity_remaining_mbits = float(window.get("capacity_mbits", data_rate_bps * (window_end - window_start) / 1e6))

        # Sequentially fill the contact window. Recompute candidates after each
        # transfer because a task received at the beginning of a long window can
        # be forwarded later in a future window, not instantaneously everywhere.
        while current_time < window_end and capacity_remaining_mbits > 1e-9:
            candidates = []
            for task_id, info in task_info.items():
                if current_time > float(info["deadline_sec"]):
                    continue
                size_mbits = float(info["task_size_mbits"])
                if size_mbits <= 0:
                    continue
                transfer_duration = size_mbits * 1e6 / data_rate_bps
                if current_time + transfer_duration > window_end + 1e-9:
                    continue
                if size_mbits > capacity_remaining_mbits + 1e-9:
                    continue

                for sender, receiver in ((node_a, node_b), (node_b, node_a)):
                    if not directed_transfer_allowed(sender, receiver, is_sat_gs):
                        continue
                    if has_received(task_id, receiver):
                        continue
                    if not has_received(task_id, sender):
                        continue
                    sender_time = recv_time(task_id, sender)
                    if sender_time > current_time + 1e-9:
                        continue
                    sender_hops = recv_hops(task_id, sender)
                    if max_forwarding_hops >= 0 and sender_hops >= int(max_forwarding_hops):
                        continue
                    if not receiver_allowed(task_id, receiver):
                        continue
                    complete_time = current_time + transfer_duration
                    if complete_time > float(info["deadline_sec"]) + 1e-9:
                        # Do not spend capacity on commands arriving after task deadline.
                        continue
                    candidates.append(
                        (
                            task_priority_score(task_id, receiver),
                            task_id,
                            sender,
                            receiver,
                            size_mbits,
                            transfer_duration,
                            sender_hops + 1,
                        )
                    )

            if not candidates:
                break

            candidates.sort(key=lambda x: x[0])
            _score, task_id, sender, receiver, size_mbits, transfer_duration, new_hops = candidates[0]
            start_tx = current_time
            end_tx = current_time + transfer_duration

            received[(task_id, receiver)] = {
                "time_sec": float(end_tx),
                "hop_count": int(new_hops),
                "from_node": sender,
                "via_window_id": str(window["window_id"]),
            }
            if node_type.get(receiver) == "observer":
                observer_copy_count[task_id] = observer_copy_count.get(task_id, 0) + 1
            elif node_type.get(receiver) == "central_node":
                cn_copy_count[task_id] = cn_copy_count.get(task_id, 0) + 1

            capacity_remaining_mbits -= size_mbits
            transfer_rows.append(
                {
                    "architecture_id": config.architecture_id,
                    "window_id": window["window_id"],
                    "task_id": task_id,
                    "from_node": sender,
                    "to_node": receiver,
                    "from_node_type": node_type.get(sender, "unknown"),
                    "to_node_type": node_type.get(receiver, "unknown"),
                    "transfer_start_time_utc": iso_utc(epoch + timedelta(seconds=float(start_tx))),
                    "transfer_end_time_utc": iso_utc(epoch + timedelta(seconds=float(end_tx))),
                    "transfer_time_utc": iso_utc(epoch + timedelta(seconds=float(end_tx))),
                    "transfer_start_time_sec": float(start_tx),
                    "transfer_end_time_sec": float(end_tx),
                    "transfer_time_sec": float(end_tx),
                    "transfer_duration_sec": float(transfer_duration),
                    "transferred_data_mbits": float(size_mbits),
                    "remaining_capacity_mbits_after_transfer": float(max(0.0, capacity_remaining_mbits)),
                    "data_rate_bps": float(data_rate_bps),
                    "hop_count_after_transfer": int(new_hops),
                    "link_type": window["link_type"],
                    "routing_policy": routing_policy,
                }
            )
            current_time = end_tx

    transfers_df = pd.DataFrame(transfer_rows)
    if transfers_df.empty:
        transfers_df = pd.DataFrame(
            columns=[
                "architecture_id",
                "window_id",
                "task_id",
                "from_node",
                "to_node",
                "from_node_type",
                "to_node_type",
                "transfer_start_time_utc",
                "transfer_end_time_utc",
                "transfer_time_utc",
                "transfer_start_time_sec",
                "transfer_end_time_sec",
                "transfer_time_sec",
                "transfer_duration_sec",
                "transferred_data_mbits",
                "remaining_capacity_mbits_after_transfer",
                "data_rate_bps",
                "hop_count_after_transfer",
                "link_type",
                "routing_policy",
            ]
        )

    # ------------------------------------------------------------------
    # Reception table.
    # ------------------------------------------------------------------
    reception_rows = []
    for task_id, info in task_info.items():
        sat_receive_records = [
            (node, rec)
            for (tid, node), rec in received.items()
            if tid == task_id and node in sat_ids
        ]
        observer_receive_records = [
            (node, rec)
            for (node, rec) in sat_receive_records
            if node in observer_nodes
        ]
        cn_receive_records = [
            (node, rec)
            for (node, rec) in sat_receive_records
            if node in central_nodes
        ]
        max_task_hops = max(
            (int(rec["hop_count"]) for _, rec in sat_receive_records),
            default=np.nan,
        )
        useful_observers = useful_observers_by_task.get(task_id, [])
        useful_observer_receive_records = [
            (node, rec)
            for (node, rec) in observer_receive_records
            if node in useful_observers
        ]
        useful_satellites = useful_satellites_by_task.get(task_id, [])
        useful_recipient_receive_records = [
            (node, rec)
            for (node, rec) in sat_receive_records
            if node in useful_satellites
        ]

        if sat_receive_records:
            first_node, first_rec = min(sat_receive_records, key=lambda x: x[1]["time_sec"])
            first_sec = float(first_rec["time_sec"])
            first_utc = iso_utc(epoch + timedelta(seconds=first_sec))
            first_latency_min = (first_sec - float(info["created_sec"])) / 60.0
            first_hops = int(first_rec["hop_count"])
        else:
            first_node = ""
            first_sec = np.nan
            first_utc = ""
            first_latency_min = np.nan
            first_hops = -1

        reception_rows.append(
            {
                "architecture_id": config.architecture_id,
                "task_id": task_id,
                "priority_class": info["priority_class"],
                "region": info["region"],
                "created_time_sec": info["created_sec"],
                "deadline_time_sec": info["deadline_sec"],
                "deadline_min": (float(info["deadline_sec"]) - float(info["created_sec"])) / 60.0,
                "task_size_mbits": info["task_size_mbits"],
                "satellites_reached": len(sat_receive_records),
                "central_nodes_reached": len(cn_receive_records),
                "observers_reached": len(observer_receive_records),
                "useful_observers_total": len(useful_observers),
                "useful_observers_reached": len(useful_observer_receive_records),
                "useful_recipients_total": len(useful_satellites),
                "useful_recipients_reached": len(useful_recipient_receive_records),
                "first_satellite_reception_node": first_node,
                "first_satellite_reception_time_utc": first_utc,
                "first_satellite_reception_time_sec": first_sec,
                "first_reception_latency_min": first_latency_min,
                "first_reception_hop_count": first_hops,
                "max_hops": max_task_hops,
                "received_by_satellite_before_deadline": bool(sat_receive_records and first_sec <= info["deadline_sec"]),
                "received_by_useful_observer_before_deadline": bool(
                    any(float(rec["time_sec"]) <= info["deadline_sec"] for _, rec in useful_observer_receive_records)
                ),
                "received_by_useful_recipient_before_deadline": bool(
                    any(float(rec["time_sec"]) <= info["deadline_sec"] for _, rec in useful_recipient_receive_records)
                ),
            }
        )
    reception_df = pd.DataFrame(reception_rows)

    # ------------------------------------------------------------------
    # Observation fulfilment after real reception time.
    # ------------------------------------------------------------------
    obs_rows = []
    failure_rows = []
    opp_by_task = {tid: group.copy() for tid, group in opportunities_df.groupby("task_id")} if not opportunities_df.empty else {}

    for task_id, info in task_info.items():
        deadline_sec = float(info["deadline_sec"])
        task_opps = opp_by_task.get(task_id, pd.DataFrame())
        possible_before_deadline = bool(possible_before_deadline_by_task.get(task_id, False))

        best_obs = None
        for (tid, node), rec in received.items():
            if tid != task_id or node not in sat_ids:
                continue
            recv_sec = float(rec["time_sec"])
            if recv_sec > deadline_sec:
                continue
            if task_opps.empty:
                continue
            sat_opps = task_opps[task_opps["node_id"] == node]
            if sat_opps.empty:
                continue
            valid_opps = sat_opps[
                (sat_opps["earliest_observation_time_sec"] >= recv_sec)
                & (sat_opps["earliest_observation_time_sec"] <= deadline_sec)
            ]
            if valid_opps.empty:
                continue
            candidate = valid_opps.sort_values("earliest_observation_time_sec").iloc[0]
            obs_sec = float(candidate["earliest_observation_time_sec"])
            if best_obs is None or obs_sec < best_obs["observation_time_sec"]:
                best_obs = {
                    "satellite_id": node,
                    "satellite_type": node_type.get(node, "unknown"),
                    "observation_time_sec": obs_sec,
                    "observation_time_utc": candidate["earliest_observation_time_utc"],
                    "reception_time_sec": recv_sec,
                    "reception_to_observation_min": (obs_sec - recv_sec) / 60.0,
                    "min_distance_km": float(candidate["min_distance_km"]),
                    "hop_count": int(rec["hop_count"]),
                }

        success = best_obs is not None
        rec_row = reception_df.loc[reception_df["task_id"] == task_id].iloc[0]
        reached_sat_before_deadline = bool(rec_row["received_by_satellite_before_deadline"])
        reached_useful_before_deadline = bool(rec_row["received_by_useful_recipient_before_deadline"])

        if success:
            failure_class = "success"
        elif not possible_before_deadline:
            failure_class = "geometry_failure"
        elif not reached_sat_before_deadline:
            failure_class = "communication_failure_no_satellite_received"
        elif not reached_useful_before_deadline:
            failure_class = "communication_failure_no_useful_observer_received"
        else:
            failure_class = "routing_or_timing_failure"

        obs_rows.append(
            {
                "architecture_id": config.architecture_id,
                "task_id": task_id,
                "priority_class": info["priority_class"],
                "region": info["region"],
                "observed_before_deadline": success,
                "observing_satellite_id": best_obs["satellite_id"] if best_obs else "",
                "observing_satellite_type": best_obs["satellite_type"] if best_obs else "",
                "observation_time_utc": best_obs["observation_time_utc"] if best_obs else "",
                "observation_time_sec": best_obs["observation_time_sec"] if best_obs else np.nan,
                "reception_to_observation_min": best_obs["reception_to_observation_min"] if best_obs else np.nan,
                "observation_hop_count": best_obs["hop_count"] if best_obs else np.nan,
                "min_distance_km": best_obs["min_distance_km"] if best_obs else np.nan,
                "failure_class": failure_class,
            }
        )
        failure_rows.append(
            {
                "architecture_id": config.architecture_id,
                "task_id": task_id,
                "priority_class": info["priority_class"],
                "region": info["region"],
                "failure_class": failure_class,
                "possible_before_deadline": possible_before_deadline,
                "reached_satellite_before_deadline": reached_sat_before_deadline,
                "reached_useful_recipient_before_deadline": reached_useful_before_deadline,
            }
        )

    obs_df = pd.DataFrame(obs_rows)
    failure_df = pd.DataFrame(failure_rows)

    # ------------------------------------------------------------------
    # Summary.
    # ------------------------------------------------------------------
    n_tasks = len(tasks_df)
    n_success = int((obs_df["observed_before_deadline"] == True).sum()) if n_tasks else 0
    communication_failures = int(failure_df["failure_class"].str.startswith("communication_failure").sum()) if n_tasks else 0
    routing_failures = int((failure_df["failure_class"] == "routing_or_timing_failure").sum()) if n_tasks else 0
    geometry_failures = int((failure_df["failure_class"] == "geometry_failure").sum()) if n_tasks else 0

    def percentile(series: pd.Series, q: float) -> float:
        values = pd.to_numeric(series, errors="coerce").dropna()
        return float(values.quantile(q)) if not values.empty else np.nan

    priority_metrics: Dict[str, Any] = {}
    for priority in ["Critical", "High", "Medium", "Low"]:
        slug = priority.lower()
        rec_priority = reception_df[reception_df["priority_class"] == priority]
        obs_priority = obs_df[obs_df["priority_class"] == priority]
        priority_metrics[f"n_tasks_{slug}"] = int(len(rec_priority))
        priority_metrics[f"{slug}_dissemination_success_percent"] = (
            100.0 * float(rec_priority["received_by_useful_recipient_before_deadline"].mean())
            if not rec_priority.empty else np.nan
        )
        priority_metrics[f"{slug}_observation_success_percent"] = (
            100.0 * float(obs_priority["observed_before_deadline"].mean())
            if not obs_priority.empty else np.nan
        )
        priority_metrics[f"{slug}_p50_first_reception_latency_min"] = percentile(
            rec_priority["first_reception_latency_min"], 0.50
        ) if not rec_priority.empty else np.nan
        priority_metrics[f"{slug}_p90_first_reception_latency_min"] = percentile(
            rec_priority["first_reception_latency_min"], 0.90
        ) if not rec_priority.empty else np.nan
        priority_metrics[f"{slug}_p95_first_reception_latency_min"] = percentile(
            rec_priority["first_reception_latency_min"], 0.95
        ) if not rec_priority.empty else np.nan

    summary_df = pd.DataFrame(
        [
            {
                "architecture_id": config.architecture_id,
                "n_tasks": n_tasks,
                "n_satellites": config.n_satellites,
                "n_planes": config.n_planes,
                "cn_fraction_percent": config.cn_fraction_percent,
                "n_central_nodes": config.n_central_nodes,
                "routing_policy": routing_policy,
                "max_forwarding_hops": int(max_forwarding_hops),
                "max_observer_copies_per_task": int(max_observer_copies_per_task),
                "max_cn_copies_per_task": int(max_cn_copies_per_task),
                "task_size_mode": task_size_mode,
                "initial_holder_mode": "ground_station",
                "initial_ground_station_id": source_ground_station,
                "dissemination_target": "observation_capable_recipients",
                "mean_task_size_mbits": float(np.mean([v["task_size_mbits"] for v in task_info.values()])) if task_info else 0.0,
                "mean_reach_percent": 100.0 * reception_df["satellites_reached"].mean() / config.n_satellites if n_tasks else 0.0,
                "mean_observer_reach_count": float(reception_df["observers_reached"].mean()) if n_tasks else 0.0,
                "mean_cn_reach_count": float(reception_df["central_nodes_reached"].mean()) if n_tasks else 0.0,
                "mean_useful_observer_reach_count": float(reception_df["useful_observers_reached"].mean()) if n_tasks else 0.0,
                "mean_useful_recipient_reach_count": float(reception_df["useful_recipients_reached"].mean()) if n_tasks else 0.0,
                "tasks_reached_before_deadline_percent": 100.0 * reception_df["received_by_satellite_before_deadline"].mean() if n_tasks else 0.0,
                "tasks_reached_useful_recipient_before_deadline_percent": 100.0 * reception_df["received_by_useful_recipient_before_deadline"].mean() if n_tasks else 0.0,
                "dissemination_success_percent": 100.0 * reception_df["received_by_useful_recipient_before_deadline"].mean() if n_tasks else 0.0,
                "observation_success_percent": 100.0 * obs_df["observed_before_deadline"].mean() if n_tasks else 0.0,
                "mean_first_reception_latency_min": float(reception_df["first_reception_latency_min"].mean(skipna=True)) if n_tasks else np.nan,
                "p50_first_reception_latency_min": percentile(reception_df["first_reception_latency_min"], 0.50) if n_tasks else np.nan,
                "p90_first_reception_latency_min": percentile(reception_df["first_reception_latency_min"], 0.90) if n_tasks else np.nan,
                "p95_first_reception_latency_min": percentile(reception_df["first_reception_latency_min"], 0.95) if n_tasks else np.nan,
                "mean_reception_to_observation_min": float(obs_df["reception_to_observation_min"].mean(skipna=True)) if n_tasks else np.nan,
                "p95_reception_to_observation_latency_min": percentile(obs_df["reception_to_observation_min"], 0.95) if n_tasks else np.nan,
                "max_observed_hops": int(pd.to_numeric(reception_df["max_hops"], errors="coerce").max()) if n_tasks and reception_df["max_hops"].notna().any() else 0,
                "n_transfer_events": len(transfers_df),
                "total_transferred_data_mbits": float(transfers_df["transferred_data_mbits"].sum()) if not transfers_df.empty else 0.0,
                "geometry_failures": geometry_failures,
                "communication_failures": communication_failures,
                "routing_failures": routing_failures,
                "successes": n_success,
                "communication_model": "capacity_aware_targeted_ground_injection",
                **priority_metrics,
            }
        ]
    )

    return transfers_df, reception_df, obs_df, failure_df, summary_df



# =============================================================================
# V19-style communication model adapted to analytical Walker states
# =============================================================================

class CommSubsystem:
    """
    V19 communication link-budget model.

    Supported bands: UHF, S-band, X-band.
    Computes free-space path loss, thermal noise, SNR, Shannon-limited rate,
    BER-adjusted effective rate, and sensitivity-limited link feasibility.
    """

    def __init__(self, band: str = "UHF"):
        self.band = band
        if band == "UHF":
            self.POWER = 2.0
            self.RXGAIN = 1.0
            self.RXLOSS = 0.5
            self.TXGAIN = 1.0
            self.TXLOSS = 3.0
            self.FREQUENCY = 437e6
            self.BANDWIDTH = 9600.0
            self.SYMBOLRATE = 9600.0
            self.MODULATIONORDER = 4
            self.SENSITIVITY = -151.0
            self.RX_HALF_BEAM_DEG = 90.0
            self.TX_HALF_BEAM_DEG = 90.0
        elif band == "S-band":
            self.POWER = 5.0
            self.RXGAIN = 2.0
            self.RXLOSS = 1.0
            self.TXGAIN = 2.0
            self.TXLOSS = 2.0
            self.FREQUENCY = 2e9
            self.BANDWIDTH = 1e6
            self.SYMBOLRATE = 1e6
            self.MODULATIONORDER = 4
            self.SENSITIVITY = -130.0
            self.RX_HALF_BEAM_DEG = 70.0
            self.TX_HALF_BEAM_DEG = 70.0
        elif band == "X-band":
            self.POWER = 10.0
            self.RXGAIN = 5.0
            self.RXLOSS = 1.0
            self.TXGAIN = 5.0
            self.TXLOSS = 1.0
            self.FREQUENCY = 8e9
            self.BANDWIDTH = 10e6
            self.SYMBOLRATE = 10e6
            self.MODULATIONORDER = 4
            self.SENSITIVITY = -120.0
            self.RX_HALF_BEAM_DEG = 45.0
            self.TX_HALF_BEAM_DEG = 45.0
        else:
            raise ValueError("Unsupported band. Use one of: UHF, S-band, X-band")

    def calculateFreeSpaceLoss(self, dist_m: float) -> float:
        dist_m = max(float(dist_m), 1.0)
        fsllinear = (4.0 * np.pi * dist_m * self.FREQUENCY / 3e8) ** 2
        return 10.0 * np.log10(fsllinear)

    def calculateNoise(self) -> float:
        return 10.0 * np.log10(1.38e-23 * 290.0 * self.BANDWIDTH)

    def calculateSNR(self, dist_m: float, tx_boost_dB: float = 0.0, rx_boost_dB: float = 0.0) -> float:
        return (
            10.0 * np.log10(self.POWER)
            + self.RXGAIN + self.TXGAIN
            - self.RXLOSS - self.TXLOSS
            - self.calculateFreeSpaceLoss(dist_m)
            + tx_boost_dB + rx_boost_dB
            - self.calculateNoise()
        )

    def calculateIdealDataRate(self, dist_m: float, tx_boost_dB: float = 0.0, rx_boost_dB: float = 0.0) -> float:
        snr_linear = 10.0 ** (self.calculateSNR(dist_m, tx_boost_dB, rx_boost_dB) / 10.0)
        ideal = self.BANDWIDTH * np.log2(1.0 + snr_linear)
        max_rate = self.SYMBOLRATE * np.log2(self.MODULATIONORDER)
        return float(min(ideal, max_rate))

    def calculateBER(self, dist_m: float, tx_boost_dB: float = 0.0, rx_boost_dB: float = 0.0) -> float:
        bitrate = self.SYMBOLRATE * np.log2(self.MODULATIONORDER)
        efficiency = bitrate / self.BANDWIDTH
        snr_linear = 10.0 ** (self.calculateSNR(dist_m, tx_boost_dB, rx_boost_dB) / 10.0)
        ebn0 = snr_linear / max(efficiency, 1e-12)
        return float((1.0 / np.log2(self.MODULATIONORDER)) * math.erfc(np.sqrt(2.0 * ebn0)))

    def calculateEffectiveDataRate(self, dist_m: float, tx_boost_dB: float = 0.0, rx_boost_dB: float = 0.0) -> float:
        received_power = (
            10.0 * np.log10(self.POWER)
            + self.RXGAIN + self.TXGAIN
            - self.RXLOSS - self.TXLOSS
            - self.calculateFreeSpaceLoss(dist_m)
            + tx_boost_dB + rx_boost_dB
        )
        if received_power < self.SENSITIVITY:
            return 0.0
        return float(self.calculateIdealDataRate(dist_m, tx_boost_dB, rx_boost_dB) * (1.0 - self.calculateBER(dist_m, tx_boost_dB, rx_boost_dB)))


def max_comm_distance(comm: CommSubsystem, tx_boost_dB: float = 0.0, rx_boost_dB: float = 0.0) -> float:
    max_fsl_dB = (
        10.0 * np.log10(comm.POWER)
        + comm.TXGAIN + tx_boost_dB
        + comm.RXGAIN + rx_boost_dB
        - comm.TXLOSS - comm.RXLOSS
        - comm.SENSITIVITY
    )
    return float((10.0 ** (max_fsl_dB / 20.0)) * 3e8 / (4.0 * np.pi * comm.FREQUENCY))


def boost_factor_to_db(boost_factor: float) -> float:
    if boost_factor <= 0:
        return 0.0
    return 20.0 * math.log10(boost_factor)


def nominal_data_rate_bps(comm_band: str) -> float:
    return float(NOMINAL_LINK_DATA_RATE_BPS.get(str(comm_band), NOMINAL_LINK_DATA_RATE_BPS["UHF"]))


def satellite_eci_state(row: pd.Series, t_sec: float) -> Tuple[np.ndarray, np.ndarray]:
    """Circular-orbit ECI position [km] and velocity [km/s]."""
    a = float(row["semi_major_axis_km"])
    inc = deg2rad(float(row["inclination_deg"]))
    raan = deg2rad(float(row["raan_deg"]))
    mean_anomaly0 = deg2rad(float(row["mean_anomaly_deg"]))
    argp = deg2rad(float(row.get("arg_perigee_deg", 0.0)))
    n_rad_s = math.sqrt(EARTH_MU_KM3_S2 / (a**3))
    u = mean_anomaly0 + n_rad_s * t_sec + argp
    r_pf = np.array([a * math.cos(u), a * math.sin(u), 0.0])
    v_pf = np.array([-a * n_rad_s * math.sin(u), a * n_rad_s * math.cos(u), 0.0])
    rot = rotation_matrix_3(raan) @ rotation_matrix_1(inc)
    return rot @ r_pf, rot @ v_pf


def precompute_satellite_velocities(
    nodes_df: pd.DataFrame,
    time_seconds: np.ndarray,
) -> np.ndarray:
    """Precompute ECI velocity once per base architecture for all CN cases."""

    sat_rows = [row for _, row in nodes_df.reset_index(drop=True).iterrows()]
    velocities = np.zeros((len(time_seconds), len(sat_rows), 3), dtype=float)
    for ti, t in enumerate(time_seconds):
        for si, row in enumerate(sat_rows):
            _position, velocity = satellite_eci_state(row, float(t))
            velocities[ti, si, :] = velocity
    return velocities


def link_geometry_and_rate_analytical(
    rx_pos_km: np.ndarray,
    rx_vel_km_s: np.ndarray,
    tx_pos_km: np.ndarray,
    tx_vel_km_s: np.ndarray,
    comm: CommSubsystem,
    dist_threshold_m: float,
    rx_halfbeam_deg: float,
    tx_halfbeam_deg: float,
    tx_boost_dB: float = 0.0,
    rx_boost_dB: float = 0.0,
) -> Tuple[bool, float, float, float, float]:
    """V19 link_geometry_and_rate adapted from Skyfield states to analytical states."""
    rx_pos_m = rx_pos_km.astype(float) * 1000.0
    tx_pos_m = tx_pos_km.astype(float) * 1000.0
    rx_vel_m_s = rx_vel_km_s.astype(float) * 1000.0
    tx_vel_m_s = tx_vel_km_s.astype(float) * 1000.0

    los = tx_pos_m - rx_pos_m
    dist_m = float(np.linalg.norm(los))
    if dist_m <= 0:
        return False, dist_m, np.nan, np.nan, 0.0

    rx_bore_norm = float(np.linalg.norm(rx_vel_m_s))
    tx_bore_norm = float(np.linalg.norm(tx_vel_m_s))
    if rx_bore_norm <= 0 or tx_bore_norm <= 0:
        return False, dist_m, np.nan, np.nan, 0.0
    rx_bore = rx_vel_m_s / rx_bore_norm
    tx_bore = tx_vel_m_s / tx_bore_norm

    rx_angle = float(np.degrees(np.arccos(np.clip(np.dot(los, rx_bore) / dist_m, -1.0, 1.0))))
    tx_angle = float(np.degrees(np.arccos(np.clip(np.dot(-los, tx_bore) / dist_m, -1.0, 1.0))))

    denom = float(np.dot(los, los))
    tparam = -float(np.dot(rx_pos_m, los)) / denom
    tparam = np.clip(tparam, 0.0, 1.0)
    closest = rx_pos_m + tparam * los
    earth_radius_m = EARTH_RADIUS_KM * 1000.0
    los_clear = float(np.dot(closest, closest)) > earth_radius_m ** 2

    effective_rate_bps = comm.calculateEffectiveDataRate(dist_m, tx_boost_dB=tx_boost_dB, rx_boost_dB=rx_boost_dB)
    contact_ok = (
        dist_m <= dist_threshold_m
        and rx_angle <= rx_halfbeam_deg
        and tx_angle <= tx_halfbeam_deg
        and los_clear
        and effective_rate_bps > 0.0
    )
    return contact_ok, dist_m, rx_angle, tx_angle, effective_rate_bps


def _normalize_sat_sat_mode(mode: str) -> str:
    m = str(mode).strip().lower().replace("_", "-")
    if m in {"central-only", "centralonly", "central"}:
        return "central-only"
    if m in {"all", "all-to-all", "alltoall"}:
        return "all"
    if m in {"none", "off", "false", "0"}:
        return "none"
    raise ValueError("sat_sat_mode must be central_only, all, or none")


def _v19_candidate_pairs(nodes_df: pd.DataFrame, sat_sat_mode: str) -> List[Tuple[int, int]]:
    mode = _normalize_sat_sat_mode(sat_sat_mode)
    if mode == "none":
        return []
    nodes = nodes_df.reset_index(drop=True)
    central_indices = [int(i) for i, row in nodes.iterrows() if bool(row["is_central_node"])]
    all_indices = list(range(len(nodes)))
    pairs: List[Tuple[int, int]] = []
    if mode == "central-only":
        if not central_indices:
            # For CN=0 decentralized cases, no central backbone exists. Use peer-to-peer links
            # rather than producing a degenerate no-communication architecture.
            mode = "all"
        else:
            for c in central_indices:
                for other in all_indices:
                    if other != c:
                        pairs.append(tuple(sorted((c, other))))
    if mode == "all":
        for a in range(len(nodes)):
            for b in range(a + 1, len(nodes)):
                pairs.append((a, b))
    return sorted(set(pairs))


def add_window_capacity_model(
    windows: pd.DataFrame,
    comm_band: str = "UHF",
    sat_gs_data_rate_bps: float = 0.0,
    capacity_utilization_limit: float = DEFAULT_CAPACITY_UTILIZATION_LIMIT,
) -> pd.DataFrame:
    """V19 finite-capacity window model."""
    if windows.empty:
        return windows.copy()
    out = windows.copy()
    out["duration_sec"] = pd.to_numeric(out["duration_sec"], errors="coerce").fillna(0.0)
    if "mean_data_rate_bps" not in out.columns:
        out["mean_data_rate_bps"] = np.nan
    if "total_data_mbits" not in out.columns:
        out["total_data_mbits"] = np.nan

    band_rate = nominal_data_rate_bps(comm_band)
    gs_rate = float(sat_gs_data_rate_bps) if sat_gs_data_rate_bps and sat_gs_data_rate_bps > 0 else band_rate
    rates, capacities, sources = [], [], []
    for _, row in out.iterrows():
        link_type = str(row.get("link_type", "")).lower()
        duration = max(float(row.get("duration_sec", 0.0)), 0.0)
        mean_rate = row.get("mean_data_rate_bps", np.nan)
        total_mbits = row.get("total_data_mbits", np.nan)
        if pd.notna(total_mbits) and float(total_mbits) > 0:
            rate = float(mean_rate) if pd.notna(mean_rate) and float(mean_rate) > 0 else float(total_mbits) * 1e6 / max(duration, 1.0)
            capacity = float(total_mbits)
            src = "existing_total_data_mbits"
        elif pd.notna(mean_rate) and float(mean_rate) > 0:
            rate = float(mean_rate)
            capacity = rate * duration / 1e6
            src = "existing_mean_data_rate"
        elif link_type == "sat_gs":
            rate = gs_rate
            capacity = rate * duration / 1e6
            src = "assigned_sat_gs_band_rate"
        else:
            rate = band_rate
            capacity = rate * duration / 1e6
            src = "assigned_band_rate_fallback"
        capacity *= max(min(float(capacity_utilization_limit), 1.0), 0.0)
        rates.append(rate)
        capacities.append(max(capacity, 0.0))
        sources.append(src)
    out["mean_data_rate_bps"] = rates
    out["window_capacity_mbits"] = capacities
    out["capacity_mbits"] = capacities
    out["data_rate_bps"] = rates
    out["capacity_source"] = sources
    out["capacity_utilization_limit"] = float(capacity_utilization_limit)
    out["total_data_mbits"] = out["window_capacity_mbits"]
    return out


def generate_communication_windows(
    config: ArchitectureConfig,
    nodes_df: pd.DataFrame,
    ground_stations: List[GroundStation],
    epoch: datetime,
    duration_hours: float,
    time_step_sec: int,
    sat_sat_max_range_km: float,
    sat_sat_data_rate_bps: float,
    sat_gs_data_rate_bps: float,
    sat_sat_mode: str,
    positions_eci: Optional[np.ndarray] = None,
    time_seconds: Optional[np.ndarray] = None,
    comm_band: str = "UHF",
    central_power_boost_factor: float = 1.5,
    min_window_data_mbits: float = 0.0,
    capacity_utilization_limit: float = DEFAULT_CAPACITY_UTILIZATION_LIMIT,
    velocities_eci: Optional[np.ndarray] = None,
) -> pd.DataFrame:
    """
    Generate communication windows using the V19 communication logic.

    Sat-sat windows use link-budget feasibility, beam constraints, Earth obstruction,
    BER-adjusted effective data rate, and central-node boost.
    Sat-GS windows are visibility windows with V19-style capacity assignment.
    """
    if time_seconds is None:
        time_seconds = np.arange(0.0, duration_hours * 3600.0 + 0.1, time_step_sec)

    nodes = nodes_df.reset_index(drop=True)
    sat_rows = [row for _, row in nodes.iterrows()]
    n_sats = len(sat_rows)
    sim_end_sec = float(duration_hours * 3600.0)

    # Precompute analytical ECI position and velocity for V19 link geometry.
    pos = np.zeros((len(time_seconds), n_sats, 3), dtype=float)
    vel = np.zeros((len(time_seconds), n_sats, 3), dtype=float)
    if positions_eci is not None and positions_eci.shape[:2] == (len(time_seconds), n_sats):
        pos[:] = positions_eci
    else:
        for ti, t in enumerate(time_seconds):
            for si, row in enumerate(sat_rows):
                r, _v = satellite_eci_state(row, float(t))
                pos[ti, si, :] = r
    if velocities_eci is not None and velocities_eci.shape[:2] == (len(time_seconds), n_sats):
        vel[:] = velocities_eci
    else:
        for ti, t in enumerate(time_seconds):
            for si, row in enumerate(sat_rows):
                _r, v = satellite_eci_state(row, float(t))
                vel[ti, si, :] = v

    comm = CommSubsystem(comm_band)
    base_max_distance_m = max_comm_distance(comm)
    if sat_sat_max_range_km and sat_sat_max_range_km > 0:
        base_max_distance_m = float(sat_sat_max_range_km) * 1000.0
    rx_halfbeam_deg = comm.RX_HALF_BEAM_DEG
    tx_halfbeam_deg = comm.TX_HALF_BEAM_DEG
    min_window_data_bits = float(min_window_data_mbits) * 1e6

    pairs = _v19_candidate_pairs(nodes, sat_sat_mode)
    print("V19-style realistic sat-sat communication model:")
    print(f"  Band: {comm_band}")
    print(f"  Base max distance: {base_max_distance_m/1000.0:.1f} km")
    print(f"  RX/TX half-beam: {rx_halfbeam_deg:.1f} / {tx_halfbeam_deg:.1f} deg")
    print(f"  Central boost factor: {central_power_boost_factor:.2f}")
    print(f"  Sat-sat mode: {sat_sat_mode}")
    print(f"  Candidate pairs: {len(pairs)}")

    rows: List[Dict[str, Any]] = []
    active: Dict[Tuple[int, int], Optional[Dict[str, Any]]] = {pair: None for pair in pairs}

    for ti, t in enumerate(time_seconds):
        current_dt = epoch + timedelta(seconds=float(t))
        for i, j in pairs:
            row_i = sat_rows[i]
            row_j = sat_rows[j]
            i_is_central = bool(row_i["is_central_node"])
            j_is_central = bool(row_j["is_central_node"])

            threshold_m = base_max_distance_m
            if i_is_central:
                threshold_m *= float(central_power_boost_factor)
            if j_is_central:
                threshold_m *= float(central_power_boost_factor)
            i_boost_db = boost_factor_to_db(float(central_power_boost_factor)) if i_is_central else 0.0
            j_boost_db = boost_factor_to_db(float(central_power_boost_factor)) if j_is_central else 0.0

            ok_ij, dist_ij, rx_angle_ij, tx_angle_ij, rate_ij = link_geometry_and_rate_analytical(
                rx_pos_km=pos[ti, i, :], rx_vel_km_s=vel[ti, i, :],
                tx_pos_km=pos[ti, j, :], tx_vel_km_s=vel[ti, j, :],
                comm=comm, dist_threshold_m=threshold_m,
                rx_halfbeam_deg=rx_halfbeam_deg, tx_halfbeam_deg=tx_halfbeam_deg,
                tx_boost_dB=j_boost_db, rx_boost_dB=i_boost_db,
            )
            ok_ji, dist_ji, rx_angle_ji, tx_angle_ji, rate_ji = link_geometry_and_rate_analytical(
                rx_pos_km=pos[ti, j, :], rx_vel_km_s=vel[ti, j, :],
                tx_pos_km=pos[ti, i, :], tx_vel_km_s=vel[ti, i, :],
                comm=comm, dist_threshold_m=threshold_m,
                rx_halfbeam_deg=rx_halfbeam_deg, tx_halfbeam_deg=tx_halfbeam_deg,
                tx_boost_dB=i_boost_db, rx_boost_dB=j_boost_db,
            )
            in_contact = ok_ij or ok_ji
            rate_bps = max(rate_ij, rate_ji)
            distance_m = min(dist_ij, dist_ji)
            rx_angle = rx_angle_ij if ok_ij else rx_angle_ji
            tx_angle = tx_angle_ij if ok_ij else tx_angle_ji

            pair = (i, j)
            if in_contact:
                if active[pair] is None:
                    active[pair] = {
                        "start_sec": float(t), "last_sec": float(t),
                        "rates": [], "distances_m": [], "rx_angles": [], "tx_angles": [],
                        "threshold_m": threshold_m,
                    }
                active[pair]["last_sec"] = float(t)
                active[pair]["rates"].append(rate_bps)
                active[pair]["distances_m"].append(distance_m)
                active[pair]["rx_angles"].append(rx_angle)
                active[pair]["tx_angles"].append(tx_angle)
            else:
                if active[pair] is not None:
                    rec = active[pair]
                    start_t = float(rec["start_sec"])
                    end_t = min(float(rec["last_sec"] + time_step_sec), sim_end_sec)
                    duration_sec = max(0.0, end_t - start_t)
                    mean_rate_bps = float(np.mean(rec["rates"])) if rec["rates"] else 0.0
                    total_data_bits = mean_rate_bps * duration_sec
                    if duration_sec > 0 and total_data_bits >= min_window_data_bits:
                        link_type = link_type_for_sat_pair(row_i, row_j)
                        rows.append({
                            "architecture_id": config.architecture_id,
                            "node_i": row_i["node_id"], "node_j": row_j["node_id"],
                            "receiver": row_i["node_id"], "transmitter": row_j["node_id"],
                            "from_node": row_i["node_id"], "to_node": row_j["node_id"],
                            "node_i_type": row_i["node_type"], "node_j_type": row_j["node_type"],
                            "plane_i": int(row_i["plane_id"]), "plane_j": int(row_j["plane_id"]),
                            "link_type": "sat_sat", "sat_pair_type": link_type,
                            "is_sat_sat": True, "is_sat_gs": False,
                            "t_start": epoch + timedelta(seconds=start_t),
                            "t_end": epoch + timedelta(seconds=end_t),
                            "start_time_utc": iso_utc(epoch + timedelta(seconds=start_t)),
                            "end_time_utc": iso_utc(epoch + timedelta(seconds=end_t)),
                            "start_time_sec": start_t, "end_time_sec": end_t,
                            "duration_sec": duration_sec,
                            "comm_band": comm_band,
                            "distance_threshold_km": float(rec["threshold_m"]) / 1000.0,
                            "mean_distance_km": float(np.mean(rec["distances_m"])) / 1000.0,
                            "min_distance_km": float(np.min(rec["distances_m"])) / 1000.0,
                            "max_distance_km": float(np.max(rec["distances_m"])) / 1000.0,
                            "mean_range_km": float(np.mean(rec["distances_m"])) / 1000.0,
                            "min_range_km": float(np.min(rec["distances_m"])) / 1000.0,
                            "max_range_km": float(np.max(rec["distances_m"])) / 1000.0,
                            "mean_rx_angle_deg": float(np.nanmean(rec["rx_angles"])),
                            "mean_tx_angle_deg": float(np.nanmean(rec["tx_angles"])),
                            "mean_data_rate_bps": mean_rate_bps,
                            "data_rate_bps": mean_rate_bps,
                            "total_data_mbits": total_data_bits / 1e6,
                            "capacity_mbits": total_data_bits / 1e6,
                            "central_link": bool(i_is_central or j_is_central),
                            "central_central_link": bool(i_is_central and j_is_central),
                            "earth_obstruction_checked": True,
                            "beam_checked": True,
                        })
                    active[pair] = None

    for pair, rec in active.items():
        if rec is None:
            continue
        i, j = pair
        row_i = sat_rows[i]
        row_j = sat_rows[j]
        i_is_central = bool(row_i["is_central_node"])
        j_is_central = bool(row_j["is_central_node"])
        start_t = float(rec["start_sec"])
        end_t = min(float(rec["last_sec"] + time_step_sec), sim_end_sec)
        duration_sec = max(0.0, end_t - start_t)
        mean_rate_bps = float(np.mean(rec["rates"])) if rec["rates"] else 0.0
        total_data_bits = mean_rate_bps * duration_sec
        if duration_sec > 0 and total_data_bits >= min_window_data_bits:
            link_type = link_type_for_sat_pair(row_i, row_j)
            rows.append({
                "architecture_id": config.architecture_id,
                "node_i": row_i["node_id"], "node_j": row_j["node_id"],
                "receiver": row_i["node_id"], "transmitter": row_j["node_id"],
                "from_node": row_i["node_id"], "to_node": row_j["node_id"],
                "node_i_type": row_i["node_type"], "node_j_type": row_j["node_type"],
                "plane_i": int(row_i["plane_id"]), "plane_j": int(row_j["plane_id"]),
                "link_type": "sat_sat", "sat_pair_type": link_type,
                "is_sat_sat": True, "is_sat_gs": False,
                "t_start": epoch + timedelta(seconds=start_t),
                "t_end": epoch + timedelta(seconds=end_t),
                "start_time_utc": iso_utc(epoch + timedelta(seconds=start_t)),
                "end_time_utc": iso_utc(epoch + timedelta(seconds=end_t)),
                "start_time_sec": start_t, "end_time_sec": end_t,
                "duration_sec": duration_sec,
                "comm_band": comm_band,
                "distance_threshold_km": float(rec["threshold_m"]) / 1000.0,
                "mean_distance_km": float(np.mean(rec["distances_m"])) / 1000.0,
                "min_distance_km": float(np.min(rec["distances_m"])) / 1000.0,
                "max_distance_km": float(np.max(rec["distances_m"])) / 1000.0,
                "mean_range_km": float(np.mean(rec["distances_m"])) / 1000.0,
                "min_range_km": float(np.min(rec["distances_m"])) / 1000.0,
                "max_range_km": float(np.max(rec["distances_m"])) / 1000.0,
                "mean_rx_angle_deg": float(np.nanmean(rec["rx_angles"])),
                "mean_tx_angle_deg": float(np.nanmean(rec["tx_angles"])),
                "mean_data_rate_bps": mean_rate_bps,
                "data_rate_bps": mean_rate_bps,
                "total_data_mbits": total_data_bits / 1e6,
                "capacity_mbits": total_data_bits / 1e6,
                "central_link": bool(i_is_central or j_is_central),
                "central_central_link": bool(i_is_central and j_is_central),
                "earth_obstruction_checked": True,
                "beam_checked": True,
            })

    # Satellite-ground windows: visibility plus V19 capacity assignment.
    if ground_stations:
        gs_rate = float(sat_gs_data_rate_bps) if sat_gs_data_rate_bps and sat_gs_data_rate_bps > 0 else nominal_data_rate_bps(comm_band)
        for si, sat_row in enumerate(sat_rows):
            for gs in ground_stations:
                valid, elevations, ranges = [], [], []
                gs_ecef = geodetic_to_ecef_spherical(gs.latitude_deg, gs.longitude_deg, gs.altitude_km)
                for ti, t in enumerate(time_seconds):
                    sat_eci = pos[ti, si, :]
                    sat_ecef = eci_to_ecef(sat_eci, float(t))
                    elev = elevation_deg_from_gs(sat_eci, gs, float(t))
                    rng = float(np.linalg.norm(sat_ecef - gs_ecef))
                    elevations.append(elev)
                    ranges.append(rng)
                    valid.append(elev >= gs.elevation_mask_deg)
                for start_idx, end_idx in group_boolean_windows(valid):
                    start_t = float(time_seconds[start_idx])
                    end_t = min(float(time_seconds[end_idx] + time_step_sec), sim_end_sec)
                    duration_sec = max(0.0, end_t - start_t)
                    if duration_sec <= 0:
                        continue
                    elev_segment = np.array(elevations[start_idx:end_idx + 1], dtype=float)
                    rng_segment = np.array(ranges[start_idx:end_idx + 1], dtype=float)
                    total_data_mbits = gs_rate * duration_sec / 1e6
                    rows.append({
                        "architecture_id": config.architecture_id,
                        "node_i": sat_row["node_id"], "node_j": gs.node_id,
                        "receiver": sat_row["node_id"], "transmitter": gs.node_id,
                        "from_node": sat_row["node_id"], "to_node": gs.node_id,
                        "node_i_type": sat_row["node_type"], "node_j_type": "ground_station",
                        "plane_i": int(sat_row["plane_id"]), "plane_j": -1,
                        "link_type": "sat_gs", "sat_pair_type": "sat_gs",
                        "is_sat_sat": False, "is_sat_gs": True,
                        "t_start": epoch + timedelta(seconds=start_t),
                        "t_end": epoch + timedelta(seconds=end_t),
                        "start_time_utc": iso_utc(epoch + timedelta(seconds=start_t)),
                        "end_time_utc": iso_utc(epoch + timedelta(seconds=end_t)),
                        "start_time_sec": start_t, "end_time_sec": end_t,
                        "duration_sec": duration_sec,
                        "comm_band": comm_band,
                        "mean_data_rate_bps": gs_rate,
                        "data_rate_bps": gs_rate,
                        "total_data_mbits": total_data_mbits,
                        "capacity_mbits": total_data_mbits,
                        "min_range_km": float(np.min(rng_segment)),
                        "max_range_km": float(np.max(rng_segment)),
                        "mean_range_km": float(np.mean(rng_segment)),
                        "min_distance_km": float(np.min(rng_segment)),
                        "max_distance_km": float(np.max(rng_segment)),
                        "mean_distance_km": float(np.mean(rng_segment)),
                        "max_elevation_deg": float(np.max(elev_segment)),
                        "central_link": bool(sat_row["is_central_node"]),
                        "central_central_link": False,
                        "earth_obstruction_checked": False,
                        "beam_checked": False,
                    })

    windows_df = pd.DataFrame(rows)
    if windows_df.empty:
        return windows_df
    windows_df = add_window_capacity_model(
        windows_df,
        comm_band=comm_band,
        sat_gs_data_rate_bps=sat_gs_data_rate_bps,
        capacity_utilization_limit=capacity_utilization_limit,
    )
    windows_df = windows_df.sort_values(["start_time_sec", "end_time_sec", "node_i", "node_j"]).reset_index(drop=True)
    windows_df["window_id"] = [f"W{i:08d}" for i in range(len(windows_df))]
    print(f"Communication windows generated: {len(windows_df)}")
    if not windows_df.empty:
        print(windows_df.groupby("link_type", dropna=False)["window_capacity_mbits"].agg(["count", "mean", "sum"]).to_string())
    return windows_df


def estimate_csv_row_task_sizes(tasks: pd.DataFrame, overhead_factor: float) -> pd.Series:
    """V19 csv-row task packet-size model."""
    sizes = []
    for _, row in tasks.iterrows():
        encoded = row.to_json(date_format="iso", force_ascii=False).encode("utf-8")
        sizes.append(len(encoded) * 8.0 * float(overhead_factor) / 1e6)
    return pd.Series(sizes, index=tasks.index, dtype=float)


def add_task_size_model(
    tasks: pd.DataFrame,
    task_size_mode: str = DEFAULT_TASK_SIZE_MODE,
    task_overhead_factor: float = DEFAULT_TASK_OVERHEAD_FACTOR,
    fixed_task_size_mbits: float = DEFAULT_FIXED_TASK_SIZE_MBITS,
) -> pd.DataFrame:
    """V19 task-size model: csv-row, fixed, or priority."""
    out = tasks.copy()
    mode = str(task_size_mode).strip().lower().replace("_", "-")
    if mode in {"csv-row", "csvrow", "csv"}:
        out["task_size_mbits"] = estimate_csv_row_task_sizes(out, task_overhead_factor)
        out["task_size_source"] = "csv_row_size_with_overhead"
    elif mode == "fixed":
        out["task_size_mbits"] = float(fixed_task_size_mbits)
        out["task_size_source"] = "fixed"
    elif mode in {"priority", "priority-scaled", "priority_scaled"}:
        out["task_size_mbits"] = out["priority_class"].astype(str).map(PRIORITY_TASK_SIZE_MBITS).fillna(float(fixed_task_size_mbits)).astype(float)
        out["task_size_source"] = "priority_default"
    elif mode in {"csv-or-default", "csv_or_default"}:
        if "task_size_mbits" in out.columns:
            out["task_size_mbits"] = pd.to_numeric(out["task_size_mbits"], errors="coerce").fillna(float(fixed_task_size_mbits))
            out["task_size_source"] = "input_csv_or_default"
        else:
            out["task_size_mbits"] = float(fixed_task_size_mbits)
            out["task_size_source"] = "default_no_input_size"
    else:
        raise ValueError("task_size_mode must be csv-row, fixed, priority, or csv_or_default")
    out["task_size_mbits"] = pd.to_numeric(out["task_size_mbits"], errors="coerce").fillna(float(fixed_task_size_mbits))
    out.loc[out["task_size_mbits"] <= 0, "task_size_mbits"] = float(fixed_task_size_mbits)
    return out


def add_priority_packaging(tasks: pd.DataFrame, packaging_mode: str = "two-level") -> pd.DataFrame:
    """V19 priority packaging: urgent/routine, four-level, or none."""
    if tasks.empty:
        return tasks.copy()
    out = tasks.copy()
    out["priority_class"] = out["priority_class"].astype(str)
    out["priority_rank"] = out["priority_class"].map(PRIORITY_RANK).fillna(9).astype(int)
    if "task_priority_score" not in out.columns:
        out["task_priority_score"] = 0.0
    mode = str(packaging_mode).strip().lower().replace("_", "-")
    if mode in {"none", "off", "false", "0"}:
        out["priority_package"] = "unpackaged"
        out["priority_package_rank"] = 0
        out = out.sort_values(["timestamp_utc", "priority_rank", "task_priority_score"], ascending=[True, True, False]).reset_index(drop=True)
    elif mode in {"two-level", "twolevel", "urgent-routine"}:
        out["priority_package"] = out["priority_class"].map(PRIORITY_PACKAGE_TWO_LEVEL).fillna("unknown")
        out["priority_package_rank"] = out["priority_package"].map(PRIORITY_PACKAGE_RANK_TWO_LEVEL).fillna(9).astype(int)
        out = out.sort_values(["priority_package_rank", "priority_rank", "task_priority_score", "timestamp_utc"], ascending=[True, True, False, True]).reset_index(drop=True)
    elif mode in {"four-level", "fourlevel", "strict"}:
        out["priority_package"] = out["priority_class"].map(PRIORITY_PACKAGE_FOUR_LEVEL).fillna("unknown")
        out["priority_package_rank"] = out["priority_package"].map(PRIORITY_PACKAGE_RANK_FOUR_LEVEL).fillna(9).astype(int)
        out = out.sort_values(["priority_package_rank", "task_priority_score", "timestamp_utc"], ascending=[True, False, True]).reset_index(drop=True)
    else:
        raise ValueError("priority_packaging must be two-level, four-level, or none")
    out["priority_package_mode"] = mode
    out["priority_dispatch_order"] = np.arange(1, len(out) + 1, dtype=int)
    out["task_sequence_in_package"] = out.groupby("priority_package").cumcount() + 1
    return out


def prepare_tasks_for_v19_allocation(
    tasks_df: pd.DataFrame,
    task_size_mode: str,
    task_overhead_factor: float,
    fixed_task_size_mbits: float,
    priority_packaging: str,
) -> pd.DataFrame:
    """Normalize this script's wildfire-task table to the V19 allocation schema."""
    out = tasks_df.copy()
    out["timestamp_utc"] = pd.to_datetime(out["timestamp_utc"], utc=True, errors="coerce")
    out["deadline_time_utc"] = pd.to_datetime(out["deadline_time_utc"], utc=True, errors="coerce")
    out["required_response_time_min"] = (out["deadline_time_utc"] - out["timestamp_utc"]).dt.total_seconds() / 60.0
    if "centroid_lat" not in out.columns and "latitude" in out.columns:
        out["centroid_lat"] = out["latitude"]
    if "centroid_lon" not in out.columns and "longitude" in out.columns:
        out["centroid_lon"] = out["longitude"]
    if "task_priority_score" not in out.columns:
        # Use priority rank as a deterministic fallback. Real score columns from the user's CSV are preserved if present.
        out["task_priority_score"] = out["priority_class"].astype(str).map({"Critical": 1.0, "High": 0.75, "Medium": 0.5, "Low": 0.25}).fillna(0.0)
    out = add_priority_packaging(out, priority_packaging)
    out = add_task_size_model(out, task_size_mode=task_size_mode, task_overhead_factor=task_overhead_factor, fixed_task_size_mbits=fixed_task_size_mbits)
    return out


def run_allocation_capacity_aware_v19(
    tasks: pd.DataFrame,
    windows: pd.DataFrame,
    sat_names: List[str],
    central_names: List[str],
    gs_names: List[str],
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """V19 run_allocation_capacity_aware adapted to node-name lists."""
    if tasks.empty:
        return pd.DataFrame(), pd.DataFrame()
    if windows.empty:
        raise RuntimeError("No communication windows available for capacity-aware allocation.")

    tasks = tasks.copy()
    windows = windows.copy()
    tasks["timestamp_utc"] = pd.to_datetime(tasks["timestamp_utc"], utc=True, errors="coerce")
    windows["t_start"] = pd.to_datetime(windows["t_start"], utc=True, errors="coerce")
    windows["t_end"] = pd.to_datetime(windows["t_end"], utc=True, errors="coerce")
    if "window_capacity_mbits" not in windows.columns:
        windows["window_capacity_mbits"] = pd.to_numeric(windows.get("capacity_mbits", 0.0), errors="coerce").fillna(0.0)
    windows["window_capacity_mbits"] = pd.to_numeric(windows["window_capacity_mbits"], errors="coerce").fillna(0.0)
    if "priority_rank" not in tasks.columns:
        tasks["priority_rank"] = tasks["priority_class"].astype(str).map(PRIORITY_RANK).fillna(9).astype(int)
    if "priority_package_rank" not in tasks.columns:
        tasks["priority_package_rank"] = 0
    if "task_size_mbits" not in tasks.columns:
        tasks = add_task_size_model(tasks)
    if "task_priority_score" not in tasks.columns:
        tasks["task_priority_score"] = 0.0

    tasks = tasks.sort_values(["priority_package_rank", "priority_rank", "timestamp_utc", "task_priority_score"], ascending=[True, True, True, False]).reset_index(drop=True)
    windows = windows.sort_values("t_start").reset_index(drop=True)

    all_node_names = list(sat_names) + list(gs_names)
    initial_holder = central_names[0] if central_names else sat_names[0]
    central_set = set(central_names)
    task_records = tasks.set_index("task_id").to_dict("index")

    known_time: Dict[str, Dict[str, pd.Timestamp]] = {node: {} for node in all_node_names}
    known_from: Dict[str, Dict[str, str]] = {node: {} for node in all_node_names}
    known_hops: Dict[str, Dict[str, int]] = {node: {} for node in all_node_names}
    for _, task in tasks.iterrows():
        tid = str(task["task_id"])
        created = pd.to_datetime(task["timestamp_utc"], utc=True)
        known_time[initial_holder][tid] = created
        known_from[initial_holder][tid] = "TASK_CREATED"
        known_hops[initial_holder][tid] = 0

    logs: List[Dict[str, Any]] = []
    print("\nRunning V19 capacity-aware priority allocation...")
    print(f"  Initial holder: {initial_holder}")
    print(f"  Tasks: {len(tasks)}")
    print(f"  Windows: {len(windows)}")

    for win_idx, window in enumerate(windows.itertuples(index=False), start=1):
        if win_idx % 1000 == 0 or win_idx == 1 or win_idx == len(windows):
            print(f"  Processing window {win_idx}/{len(windows)}")
        t_start = pd.to_datetime(getattr(window, "t_start"), utc=True)
        t_end = pd.to_datetime(getattr(window, "t_end"), utc=True)
        if pd.isna(t_start) or pd.isna(t_end) or t_end <= t_start:
            continue
        node_a = str(getattr(window, "receiver"))
        node_b = str(getattr(window, "transmitter"))
        if node_a not in known_time or node_b not in known_time:
            continue
        capacity_mbits = float(getattr(window, "window_capacity_mbits", 0.0))
        if capacity_mbits <= 0:
            continue
        duration_sec = max((t_end - t_start).total_seconds(), 1.0)
        rate_mbits_per_sec = capacity_mbits / duration_sec
        if rate_mbits_per_sec <= 0:
            continue

        candidates: List[Dict[str, Any]] = []
        def add_candidates(sender: str, receiver: str, direction: str) -> None:
            for tid, available_time in known_time[sender].items():
                if available_time <= t_start and tid not in known_time[receiver]:
                    rec = task_records[tid]
                    created_time = pd.to_datetime(rec["timestamp_utc"], utc=True)
                    if created_time <= t_start:
                        candidates.append({
                            "direction": direction,
                            "sender": sender,
                            "receiver": receiver,
                            "task_id": tid,
                            "priority_class": rec.get("priority_class", None),
                            "priority_rank": int(rec.get("priority_rank", 9)),
                            "priority_package": rec.get("priority_package", None),
                            "priority_package_rank": int(rec.get("priority_package_rank", 9)),
                            "priority_dispatch_order": int(rec.get("priority_dispatch_order", 10**9)),
                            "task_sequence_in_package": int(rec.get("task_sequence_in_package", 10**9)),
                            "task_priority_score": float(rec.get("task_priority_score", 0.0)),
                            "task_size_mbits": float(rec.get("task_size_mbits", DEFAULT_FIXED_TASK_SIZE_MBITS)),
                            "created_time": created_time,
                            "sender_received_time": available_time,
                            "sender_hops": int(known_hops[sender].get(tid, 0)),
                        })
        add_candidates(node_a, node_b, "receiver_to_transmitter")
        add_candidates(node_b, node_a, "transmitter_to_receiver")
        if not candidates:
            continue
        cand_df = pd.DataFrame(candidates).sort_values(
            ["priority_package_rank", "priority_rank", "priority_dispatch_order", "created_time", "task_priority_score"],
            ascending=[True, True, True, True, False],
        ).reset_index(drop=True)

        remaining_capacity = capacity_mbits
        used_capacity = 0.0
        current_tx_time = t_start
        for _, cand in cand_df.iterrows():
            task_size = float(cand["task_size_mbits"])
            if task_size <= 0 or task_size > remaining_capacity:
                continue
            tx_duration_sec = task_size / rate_mbits_per_sec
            tx_start = current_tx_time
            tx_end = tx_start + timedelta(seconds=tx_duration_sec)
            if tx_end > t_end:
                continue
            sender = str(cand["sender"])
            receiver = str(cand["receiver"])
            tid = str(cand["task_id"])
            if tid in known_time[receiver]:
                continue
            known_time[receiver][tid] = tx_end
            known_from[receiver][tid] = sender
            known_hops[receiver][tid] = int(cand["sender_hops"]) + 1
            remaining_capacity -= task_size
            used_capacity += task_size
            current_tx_time = tx_end
            logs.append({
                "task_id": tid,
                "priority_class": cand.get("priority_class", None),
                "priority_rank": cand.get("priority_rank", np.nan),
                "priority_package": cand.get("priority_package", None),
                "priority_package_rank": cand.get("priority_package_rank", np.nan),
                "priority_dispatch_order": cand.get("priority_dispatch_order", np.nan),
                "task_sequence_in_package": cand.get("task_sequence_in_package", np.nan),
                "task_size_mbits": task_size,
                "transferred_data_mbits": task_size,
                "from_node": sender,
                "to_node": receiver,
                "transfer_time": tx_end,
                "tx_start": tx_start,
                "tx_end": tx_end,
                "tx_duration_sec": tx_duration_sec,
                "window_id": getattr(window, "window_id", win_idx),
                "window_start": t_start,
                "window_end": t_end,
                "window_duration_sec": duration_sec,
                "link_type": getattr(window, "link_type", None),
                "mean_data_rate_bps": getattr(window, "mean_data_rate_bps", np.nan),
                "window_capacity_mbits": capacity_mbits,
                "window_capacity_used_mbits_after_transfer": used_capacity,
                "window_remaining_capacity_mbits_after_transfer": remaining_capacity,
                "capacity_utilization_percent_after_transfer": 100.0 * used_capacity / capacity_mbits if capacity_mbits > 0 else np.nan,
                "hops": known_hops[receiver][tid],
                "from_is_central_node": sender in central_set,
                "to_is_central_node": receiver in central_set,
                "central_node_involved": (sender in central_set) or (receiver in central_set),
            })
            if remaining_capacity <= 1e-12:
                break

    results: List[Dict[str, Any]] = []
    for _, task in tasks.iterrows():
        tid = str(task["task_id"])
        created_time = pd.to_datetime(task["timestamp_utc"], utc=True)
        sat_receptions = []
        node_receptions = []
        for node in all_node_names:
            if tid in known_time[node]:
                t = pd.to_datetime(known_time[node][tid], utc=True)
                hops = known_hops[node].get(tid, np.nan)
                node_receptions.append((node, t, hops))
                if node in sat_names:
                    sat_receptions.append((node, t, hops))
        sat_receptions = sorted(sat_receptions, key=lambda x: x[1])
        node_receptions = sorted(node_receptions, key=lambda x: x[1])
        first_satellite = sat_receptions[0][0] if sat_receptions else None
        first_satellite_time = sat_receptions[0][1] if sat_receptions else pd.NaT
        non_initial_receptions = [r for r in sat_receptions if r[0] != initial_holder]
        first_non_initial_satellite = non_initial_receptions[0][0] if non_initial_receptions else None
        first_non_initial_satellite_time = non_initial_receptions[0][1] if non_initial_receptions else pd.NaT
        def latency_minutes(t: Any) -> float:
            if pd.isna(t):
                return np.nan
            return (pd.to_datetime(t, utc=True) - created_time).total_seconds() / 60.0
        def time_to_reach_fraction(frac: float) -> float:
            if not sat_receptions:
                return np.nan
            target_count = int(math.ceil(frac * len(sat_names)))
            if len(sat_receptions) < target_count:
                return np.nan
            return latency_minutes(sat_receptions[target_count - 1][1])
        deadline_min = float(task.get("required_response_time_min", np.nan))
        first_non_initial_latency_min = latency_minutes(first_non_initial_satellite_time)
        time_to_50_percent_min = time_to_reach_fraction(0.50)
        time_to_80_percent_min = time_to_reach_fraction(0.80)
        time_to_90_percent_min = time_to_reach_fraction(0.90)
        time_to_100_percent_min = time_to_reach_fraction(1.00)
        fulfilled_before_deadline = False if pd.isna(deadline_min) or pd.isna(first_non_initial_latency_min) else first_non_initial_latency_min <= deadline_min
        reached_50_before_deadline = False if pd.isna(deadline_min) or pd.isna(time_to_50_percent_min) else time_to_50_percent_min <= deadline_min
        reached_80_before_deadline = False if pd.isna(deadline_min) or pd.isna(time_to_80_percent_min) else time_to_80_percent_min <= deadline_min
        reached_90_before_deadline = False if pd.isna(deadline_min) or pd.isna(time_to_90_percent_min) else time_to_90_percent_min <= deadline_min
        reached_100_before_deadline = False if pd.isna(deadline_min) or pd.isna(time_to_100_percent_min) else time_to_100_percent_min <= deadline_min
        max_hops = max([r[2] for r in node_receptions if not pd.isna(r[2])], default=np.nan)
        sats_reached = [r[0] for r in sat_receptions]
        nodes_reached = [r[0] for r in node_receptions]
        results.append({
            "task_id": tid,
            "region": task.get("region", None),
            "created_time": created_time,
            "timestamp_utc": created_time,
            "original_created_time": task.get("timestamp_utc_original", None),
            "priority_class": task.get("priority_class", None),
            "priority_rank": task.get("priority_rank", np.nan),
            "priority_package": task.get("priority_package", None),
            "priority_package_rank": task.get("priority_package_rank", np.nan),
            "priority_package_mode": task.get("priority_package_mode", None),
            "priority_dispatch_order": task.get("priority_dispatch_order", np.nan),
            "task_sequence_in_package": task.get("task_sequence_in_package", np.nan),
            "task_priority_score": task.get("task_priority_score", np.nan),
            "task_size_mbits": task.get("task_size_mbits", np.nan),
            "task_size_source": task.get("task_size_source", None),
            "centroid_lat": task.get("centroid_lat", np.nan),
            "centroid_lon": task.get("centroid_lon", np.nan),
            "initial_holder": initial_holder,
            "nodes_reached": len(nodes_reached),
            "satellites_reached": len(sats_reached),
            "total_satellites": len(sat_names),
            "satellite_reach_percent": 100.0 * len(sats_reached) / max(len(sat_names), 1),
            "first_satellite": first_satellite,
            "first_satellite_time": first_satellite_time,
            "allocation_latency_min_initial_included": latency_minutes(first_satellite_time),
            "first_non_initial_satellite": first_non_initial_satellite,
            "first_non_initial_satellite_time": first_non_initial_satellite_time,
            "first_non_initial_latency_min": first_non_initial_latency_min,
            "time_to_50_percent_satellites_min": time_to_50_percent_min,
            "time_to_80_percent_satellites_min": time_to_80_percent_min,
            "time_to_90_percent_satellites_min": time_to_90_percent_min,
            "time_to_100_percent_satellites_min": time_to_100_percent_min,
            "last_reached_satellite": sat_receptions[-1][0] if sat_receptions else None,
            "last_reached_satellite_time": sat_receptions[-1][1] if sat_receptions else pd.NaT,
            "last_reached_latency_min": latency_minutes(sat_receptions[-1][1]) if sat_receptions else np.nan,
            "deadline_min": deadline_min,
            "fulfilled_before_deadline": fulfilled_before_deadline,
            "reached_50_percent_before_deadline": reached_50_before_deadline,
            "reached_80_percent_before_deadline": reached_80_before_deadline,
            "reached_90_percent_before_deadline": reached_90_before_deadline,
            "reached_100_percent_before_deadline": reached_100_before_deadline,
            "max_hops": max_hops,
        })
    results_df = pd.DataFrame(results)
    logs_df = pd.DataFrame(logs)
    print("\nV19 capacity-aware allocation complete:")
    print(f"  Transfer events: {len(logs_df)}")
    if not logs_df.empty:
        print(f"  Data transferred: {logs_df['task_size_mbits'].sum():.3f} Mbits")
        print(f"  Central-node involved transfers: {100.0 * logs_df['central_node_involved'].mean():.1f}%")
    return results_df, logs_df


def _reconstruct_reception_map_from_v19(
    allocation_results: pd.DataFrame,
    transfer_logs: pd.DataFrame,
    sat_names: List[str],
) -> Dict[Tuple[str, str], pd.Timestamp]:
    rec: Dict[Tuple[str, str], pd.Timestamp] = {}
    if not allocation_results.empty:
        for _, row in allocation_results.iterrows():
            tid = str(row["task_id"])
            initial_holder = str(row.get("initial_holder", ""))
            created = pd.to_datetime(row.get("timestamp_utc", row.get("created_time", pd.NaT)), utc=True, errors="coerce")
            if initial_holder in sat_names and pd.notna(created):
                rec[(tid, initial_holder)] = created
    if not transfer_logs.empty:
        logs = transfer_logs.copy()
        logs["transfer_time"] = pd.to_datetime(logs["transfer_time"], utc=True, errors="coerce")
        for _, row in logs.iterrows():
            tid = str(row["task_id"])
            node = str(row["to_node"])
            if node not in sat_names:
                continue
            t = pd.to_datetime(row["transfer_time"], utc=True, errors="coerce")
            if pd.isna(t):
                continue
            key = (tid, node)
            if key not in rec or t < rec[key]:
                rec[key] = t
    return rec


def evaluate_observation_after_v19_reception(
    config: ArchitectureConfig,
    tasks_df: pd.DataFrame,
    opportunities_df: pd.DataFrame,
    allocation_results: pd.DataFrame,
    transfer_logs: pd.DataFrame,
    sat_names: List[str],
    epoch: datetime,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Evaluate wildfire observation success after V19 capacity-aware task reception."""
    reception_map = _reconstruct_reception_map_from_v19(allocation_results, transfer_logs, sat_names)
    opp_by_task = {str(tid): g.copy() for tid, g in opportunities_df.groupby("task_id")} if not opportunities_df.empty else {}
    alloc_by_task = allocation_results.set_index("task_id").to_dict("index") if not allocation_results.empty else {}
    obs_rows: List[Dict[str, Any]] = []
    failure_rows: List[Dict[str, Any]] = []

    for _, task in tasks_df.iterrows():
        tid = str(task["task_id"])
        created_dt = pd.to_datetime(task["timestamp_utc"], utc=True)
        deadline_dt = pd.to_datetime(task["deadline_time_utc"], utc=True)
        deadline_sec = (deadline_dt.to_pydatetime() - epoch).total_seconds()
        task_opps = opp_by_task.get(tid, pd.DataFrame())
        possible_before_deadline = bool(not task_opps.empty and (pd.to_numeric(task_opps["earliest_observation_time_sec"], errors="coerce") <= deadline_sec).any())
        best_obs: Optional[Dict[str, Any]] = None
        reached_before_deadline = False

        for sat in sat_names:
            key = (tid, sat)
            if key not in reception_map:
                continue
            recv_dt = pd.to_datetime(reception_map[key], utc=True)
            if recv_dt <= deadline_dt:
                reached_before_deadline = True
            recv_sec = (recv_dt.to_pydatetime() - epoch).total_seconds()
            if task_opps.empty:
                continue
            sat_opps = task_opps[task_opps["node_id"].astype(str) == sat]
            if sat_opps.empty:
                continue
            valid = sat_opps[
                (pd.to_numeric(sat_opps["earliest_observation_time_sec"], errors="coerce") >= recv_sec)
                & (pd.to_numeric(sat_opps["earliest_observation_time_sec"], errors="coerce") <= deadline_sec)
            ].copy()
            if valid.empty:
                continue
            candidate = valid.sort_values("earliest_observation_time_sec").iloc[0]
            obs_sec = float(candidate["earliest_observation_time_sec"])
            if best_obs is None or obs_sec < float(best_obs["observation_time_sec"]):
                best_obs = {
                    "satellite_id": sat,
                    "reception_time_sec": recv_sec,
                    "reception_time_utc": iso_utc(epoch + timedelta(seconds=recv_sec)),
                    "observation_time_sec": obs_sec,
                    "observation_time_utc": candidate["earliest_observation_time_utc"],
                    "reception_to_observation_min": (obs_sec - recv_sec) / 60.0,
                    "min_distance_km": float(candidate.get("min_distance_km", np.nan)),
                }
        success = best_obs is not None
        if success:
            failure_class = "success"
        elif not possible_before_deadline:
            failure_class = "geometry_failure"
        elif not reached_before_deadline:
            failure_class = "communication_failure"
        else:
            failure_class = "routing_failure"
        alloc = alloc_by_task.get(tid, {})
        obs_rows.append({
            "architecture_id": config.architecture_id,
            "task_id": tid,
            "priority_class": task.get("priority_class", None),
            "region": task.get("region", None),
            "observed_before_deadline": bool(success),
            "observing_satellite_id": best_obs["satellite_id"] if best_obs else "",
            "reception_time_utc": best_obs["reception_time_utc"] if best_obs else "",
            "observation_time_utc": best_obs["observation_time_utc"] if best_obs else "",
            "observation_time_sec": best_obs["observation_time_sec"] if best_obs else np.nan,
            "reception_to_observation_min": best_obs["reception_to_observation_min"] if best_obs else np.nan,
            "min_distance_km": best_obs["min_distance_km"] if best_obs else np.nan,
            "failure_class": failure_class,
            "satellites_reached": alloc.get("satellites_reached", np.nan),
            "satellite_reach_percent": alloc.get("satellite_reach_percent", np.nan),
            "first_non_initial_latency_min": alloc.get("first_non_initial_latency_min", np.nan),
            "time_to_50_percent_satellites_min": alloc.get("time_to_50_percent_satellites_min", np.nan),
            "time_to_80_percent_satellites_min": alloc.get("time_to_80_percent_satellites_min", np.nan),
            "time_to_90_percent_satellites_min": alloc.get("time_to_90_percent_satellites_min", np.nan),
            "time_to_100_percent_satellites_min": alloc.get("time_to_100_percent_satellites_min", np.nan),
            "reached_100_percent_before_deadline": alloc.get("reached_100_percent_before_deadline", False),
            "max_hops": alloc.get("max_hops", np.nan),
        })
        failure_rows.append({
            "architecture_id": config.architecture_id,
            "task_id": tid,
            "priority_class": task.get("priority_class", None),
            "region": task.get("region", None),
            "failure_class": failure_class,
            "possible_before_deadline": possible_before_deadline,
            "reached_before_deadline": reached_before_deadline,
        })
    obs_df = pd.DataFrame(obs_rows)
    failure_df = pd.DataFrame(failure_rows)
    summary_df = pd.DataFrame([{
        "architecture_id": config.architecture_id,
        "n_tasks": int(len(tasks_df)),
        "n_satellites": int(config.n_satellites),
        "n_planes": int(config.n_planes),
        "cn_fraction_percent": float(config.cn_fraction_percent),
        "n_central_nodes": int(config.n_central_nodes),
        "mean_reach_percent": float(allocation_results["satellite_reach_percent"].mean()) if not allocation_results.empty else 0.0,
        "tasks_reached_before_deadline_percent": float(100.0 * allocation_results["fulfilled_before_deadline"].mean()) if "fulfilled_before_deadline" in allocation_results.columns and not allocation_results.empty else 0.0,
        "tasks_reached_50_percent_before_deadline_percent": float(100.0 * allocation_results["reached_50_percent_before_deadline"].mean()) if "reached_50_percent_before_deadline" in allocation_results.columns and not allocation_results.empty else 0.0,
        "tasks_reached_80_percent_before_deadline_percent": float(100.0 * allocation_results["reached_80_percent_before_deadline"].mean()) if "reached_80_percent_before_deadline" in allocation_results.columns and not allocation_results.empty else 0.0,
        "tasks_reached_90_percent_before_deadline_percent": float(100.0 * allocation_results["reached_90_percent_before_deadline"].mean()) if "reached_90_percent_before_deadline" in allocation_results.columns and not allocation_results.empty else 0.0,
        "tasks_reached_100_percent_before_deadline_percent": float(100.0 * allocation_results["reached_100_percent_before_deadline"].mean()) if "reached_100_percent_before_deadline" in allocation_results.columns and not allocation_results.empty else 0.0,
        "observation_success_percent": float(100.0 * obs_df["observed_before_deadline"].mean()) if not obs_df.empty else 0.0,
        "mean_first_reception_latency_min": float(allocation_results["first_non_initial_latency_min"].mean(skipna=True)) if "first_non_initial_latency_min" in allocation_results.columns and not allocation_results.empty else np.nan,
        "mean_time_to_50_percent_satellites_min": float(allocation_results["time_to_50_percent_satellites_min"].mean(skipna=True)) if "time_to_50_percent_satellites_min" in allocation_results.columns and not allocation_results.empty else np.nan,
        "mean_time_to_80_percent_satellites_min": float(allocation_results["time_to_80_percent_satellites_min"].mean(skipna=True)) if "time_to_80_percent_satellites_min" in allocation_results.columns and not allocation_results.empty else np.nan,
        "mean_time_to_90_percent_satellites_min": float(allocation_results["time_to_90_percent_satellites_min"].mean(skipna=True)) if "time_to_90_percent_satellites_min" in allocation_results.columns and not allocation_results.empty else np.nan,
        "mean_time_to_100_percent_satellites_min": float(allocation_results["time_to_100_percent_satellites_min"].mean(skipna=True)) if "time_to_100_percent_satellites_min" in allocation_results.columns and not allocation_results.empty else np.nan,
        "mean_satellites_reached": float(allocation_results["satellites_reached"].mean()) if "satellites_reached" in allocation_results.columns and not allocation_results.empty else 0.0,
        "n_transfer_events": int(len(transfer_logs)),
        "total_transferred_data_mbits": float(transfer_logs["task_size_mbits"].sum()) if "task_size_mbits" in transfer_logs.columns and not transfer_logs.empty else 0.0,
        "geometry_failures": int((failure_df["failure_class"] == "geometry_failure").sum()) if not failure_df.empty else 0,
        "communication_failures": int((failure_df["failure_class"] == "communication_failure").sum()) if not failure_df.empty else 0,
        "routing_failures": int((failure_df["failure_class"] == "routing_failure").sum()) if not failure_df.empty else 0,
        "successes": int((failure_df["failure_class"] == "success").sum()) if not failure_df.empty else 0,
        "communication_model": "v19_capacity_aware_link_budget",
    }])
    return obs_df, failure_df, summary_df


def postprocess_legacy_unbounded_flood(
    config: ArchitectureConfig,
    nodes_df: pd.DataFrame,
    ground_stations: List[GroundStation],
    tasks_df: pd.DataFrame,
    windows_df: pd.DataFrame,
    opportunities_df: pd.DataFrame,
    epoch: datetime,
    routing_policy: str = "v19_capacity_aware",
    max_forwarding_hops: int = -1,
    max_observer_copies_per_task: int = 0,
    max_cn_copies_per_task: int = 0,
    task_size_policy: str = DEFAULT_TASK_SIZE_MODE,
    default_task_size_mbits: float = DEFAULT_FIXED_TASK_SIZE_MBITS,
    critical_task_size_mbits: float = 2.0,
    high_task_size_mbits: float = 1.5,
    medium_task_size_mbits: float = 1.0,
    low_task_size_mbits: float = 0.5,
    task_size_mode: Optional[str] = None,
    task_overhead_factor: float = DEFAULT_TASK_OVERHEAD_FACTOR,
    fixed_task_size_mbits: Optional[float] = None,
    priority_packaging: str = "two-level",
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    V19-style capacity-aware allocation + observation evaluation.

    This intentionally does not use the custom targeted-routing replay from the previous version.
    It follows V19: tasks are initially held by the first central node, or by the first satellite
    when CN fraction is 0; windows are processed chronologically with finite capacity.
    """
    sat_names = list(nodes_df["node_id"].astype(str))
    central_names = list(nodes_df.loc[nodes_df["is_central_node"].astype(bool), "node_id"].astype(str))
    gs_names = [gs.node_id for gs in ground_stations]

    # Allow old CLI names to map to V19 task-size names.
    mode = task_size_mode or task_size_policy
    mode_norm = str(mode).strip().lower().replace("_", "-")
    if mode_norm == "priority-scaled":
        mode_norm = "priority"
    if mode_norm == "csv-or-default":
        mode_norm = "csv_or_default"
    fixed_size = float(fixed_task_size_mbits) if fixed_task_size_mbits is not None else float(default_task_size_mbits)

    # Update priority task-size table from CLI-compatible values.
    PRIORITY_TASK_SIZE_MBITS.update({
        "Critical": float(critical_task_size_mbits),
        "High": float(high_task_size_mbits),
        "Medium": float(medium_task_size_mbits),
        "Low": float(low_task_size_mbits),
    })

    tasks_prepared = prepare_tasks_for_v19_allocation(
        tasks_df,
        task_size_mode=mode_norm,
        task_overhead_factor=float(task_overhead_factor),
        fixed_task_size_mbits=fixed_size,
        priority_packaging=priority_packaging,
    )
    allocation_results, transfer_logs = run_allocation_capacity_aware_v19(
        tasks=tasks_prepared,
        windows=windows_df,
        sat_names=sat_names,
        central_names=central_names,
        gs_names=gs_names,
    )
    obs_df, failure_df, summary_df = evaluate_observation_after_v19_reception(
        config=config,
        tasks_df=tasks_prepared,
        opportunities_df=opportunities_df,
        allocation_results=allocation_results,
        transfer_logs=transfer_logs,
        sat_names=sat_names,
        epoch=epoch,
    )
    allocation_results.insert(0, "architecture_id", config.architecture_id)
    if not transfer_logs.empty:
        transfer_logs.insert(0, "architecture_id", config.architecture_id)
    else:
        transfer_logs = pd.DataFrame(columns=["architecture_id", "task_id", "from_node", "to_node", "transfer_time", "task_size_mbits"])
    return transfer_logs, allocation_results, obs_df, failure_df, summary_df


def postprocess_dissemination_and_observation(
    config: ArchitectureConfig,
    nodes_df: pd.DataFrame,
    ground_stations: List[GroundStation],
    tasks_df: pd.DataFrame,
    windows_df: pd.DataFrame,
    opportunities_df: pd.DataFrame,
    epoch: datetime,
    routing_policy: str = "targeted_central",
    max_forwarding_hops: int = 3,
    max_observer_copies_per_task: int = 3,
    max_cn_copies_per_task: int = 5,
    task_size_mode: str = "priority",
    default_task_size_mbits: float = DEFAULT_FIXED_TASK_SIZE_MBITS,
    critical_task_size_mbits: float = 2.0,
    high_task_size_mbits: float = 1.5,
    medium_task_size_mbits: float = 1.0,
    low_task_size_mbits: float = 0.5,
    initial_ground_station_id: Optional[str] = None,
    **_legacy_kwargs: Any,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Run the thesis-default targeted, capacity- and hop-constrained model.

    The former V19 unbounded flood is retained only as a named legacy helper for
    reproducibility of old campaigns. New campaigns always use ground-station
    injection and enforce the configured routing, copy, and hop limits.
    """
    if str(routing_policy).strip().lower() != "targeted_central":
        raise ValueError(
            "New thesis campaigns require --routing-policy targeted_central. "
            "The old unbounded flooding model is intentionally not selectable."
        )
    return postprocess_targeted_dissemination_and_observation(
        config=config,
        nodes_df=nodes_df,
        ground_stations=ground_stations,
        tasks_df=tasks_df,
        windows_df=windows_df,
        opportunities_df=opportunities_df,
        epoch=epoch,
        routing_policy="targeted_central",
        max_forwarding_hops=max_forwarding_hops,
        max_observer_copies_per_task=max_observer_copies_per_task,
        max_cn_copies_per_task=max_cn_copies_per_task,
        task_size_mode=task_size_mode,
        default_task_size_mbits=default_task_size_mbits,
        critical_task_size_mbits=critical_task_size_mbits,
        high_task_size_mbits=high_task_size_mbits,
        medium_task_size_mbits=medium_task_size_mbits,
        low_task_size_mbits=low_task_size_mbits,
        initial_ground_station_id=initial_ground_station_id,
    )

# =============================================================================
# Export and validation helpers
# =============================================================================

def write_df(df: pd.DataFrame, path: Path) -> None:
    ensure_dir(path.parent)
    df.to_csv(path, index=False)


def write_matrix(df: pd.DataFrame, path: Path) -> None:
    ensure_dir(path.parent)
    df.to_csv(path)


def write_json(data: Dict, path: Path) -> None:
    ensure_dir(path.parent)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def compress_csv_verified(path: Path, compression_level: int = 6) -> Optional[Path]:
    """Compress a CSV and remove it only after a decompressed SHA-256 match."""
    if not path.exists():
        return None
    target = Path(f"{path}.gz")
    temporary = Path(f"{target}.tmp")
    source_digest = hashlib.sha256()
    with path.open("rb") as source, gzip.open(temporary, "wb", compresslevel=int(compression_level)) as destination:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            source_digest.update(block)
            destination.write(block)
    restored_digest = hashlib.sha256()
    with gzip.open(temporary, "rb") as restored:
        for block in iter(lambda: restored.read(1024 * 1024), b""):
            restored_digest.update(block)
    if source_digest.digest() != restored_digest.digest():
        temporary.unlink(missing_ok=True)
        raise IOError(f"Compression verification failed for {path}")
    os.replace(temporary, target)
    path.unlink()
    return target


def print_summary(output_dir: Path) -> None:
    print("\nGenerated files:")
    for path in sorted(output_dir.rglob("*")):
        if path.is_file():
            print(f"  {path}")


# =============================================================================
# Main pipeline
# =============================================================================

def run_pipeline(args: argparse.Namespace) -> None:
    output_dir = Path(args.output_dir)
    ensure_dir(output_dir)

    # Resolve the task file before heavy computation. This avoids wasting time
    # generating windows when the CSV path is wrong. Architecture/windows-only
    # modes do not need the task file.
    resolved_tasks_file: Optional[Path] = None
    if args.mode in {"all", "postprocess"}:
        resolved_tasks_file = resolve_task_file_path(Path(args.tasks_file) if args.tasks_file else DEFAULT_TASKS_FILE)
        args.tasks_file = str(resolved_tasks_file)

    # Epoch handling.
    # For final science runs, the default is to start the simulation at the actual
    # earliest task timestamp. This keeps the orbital/Earth-rotation geometry
    # tied to the real event date instead of 2026-01-01. For manifest-only or
    # geometry-only modes without a task file, use --epoch-utc.
    if str(args.epoch_mode).strip().lower() == "task_start" and resolved_tasks_file is not None:
        epoch = infer_epoch_from_task_file(resolved_tasks_file, args.epoch_floor)
        args.epoch_utc = iso_utc(epoch)
        print(f"Inferred simulation epoch from earliest task timestamp: {args.epoch_utc}")
        if args.task_time_mode != "original":
            print("WARNING: --epoch-mode task_start is normally used with --task-time-mode original. "
                  "Current run will still remap task times because --task-time-mode is not original.")
    else:
        epoch = parse_utc(args.epoch_utc)
        if str(args.epoch_mode).strip().lower() == "task_start" and resolved_tasks_file is None:
            print(f"No task file available in mode={args.mode}; using fixed epoch: {args.epoch_utc}")

    config = ArchitectureConfig(
        n_satellites=args.n_satellites,
        n_planes=args.n_planes,
        altitude_km=args.altitude_km,
        inclination_deg=args.inclination_deg,
        cn_fraction_percent=args.cn_fraction_percent,
        walker_f=args.walker_f,
        raan0_deg=args.raan0_deg,
    )


    write_json(
        {
            "script": "walker_standard_arch_hpc_v19_cn_sweep_0_100_final.py",
            "purpose": "Walker architecture sweep with V19-style link-budget communication and capacity-aware allocation",
            "architecture_config": asdict(config),
            "epoch_utc": args.epoch_utc,
            "epoch_mode": args.epoch_mode,
            "epoch_floor": args.epoch_floor,
            "duration_hours": args.duration_hours,
            "time_step_sec": args.time_step_sec,
            "sat_sat_max_range_km": args.sat_sat_max_range_km,
            "sat_sat_data_rate_bps": args.sat_sat_data_rate_bps,
            "sat_gs_data_rate_bps": args.sat_gs_data_rate_bps,
            "sat_sat_mode": args.sat_sat_mode,
            "comm_band": args.comm_band,
            "central_power_boost_factor": args.central_power_boost_factor,
            "min_window_data_mbits": args.min_window_data_mbits,
            "capacity_utilization_limit": args.capacity_utilization_limit,
            "ground_station_set": args.ground_station_set,
            "routing_policy": args.routing_policy,
            "max_forwarding_hops": args.max_forwarding_hops,
            "max_observer_copies_per_task": args.max_observer_copies_per_task,
            "max_cn_copies_per_task": args.max_cn_copies_per_task,
            "task_size_mode": args.task_size_mode,
            "initial_holder_mode": "ground_station",
            "initial_ground_station_id": args.initial_ground_station_id,
            "compress_large_csvs": bool(args.compress_large_csvs),
            "default_task_size_mbits": args.default_task_size_mbits,
            "critical_task_size_mbits": args.critical_task_size_mbits,
            "high_task_size_mbits": args.high_task_size_mbits,
            "medium_task_size_mbits": args.medium_task_size_mbits,
            "low_task_size_mbits": args.low_task_size_mbits,
            "observation_radius_km": args.observation_radius_km,
            "tasks_file": str(resolved_tasks_file) if resolved_tasks_file else None,
            "max_tasks": args.max_tasks,
            "require_min_tasks": args.require_min_tasks,
            "task_time_mode": args.task_time_mode,
            "task_spacing_minutes": args.task_spacing_minutes,
            "mode": args.mode,
        },
        output_dir / "run_config.json",
    )

    # -------------------------------------------------------------------------
    # 1. Architecture generation
    # -------------------------------------------------------------------------
    manifest_df, nodes_df, cn_dist_df, validation_df = generate_walker_constellation(config)

    if args.mode in {"architectures", "all", "windows", "postprocess", "validate"}:
        write_df(manifest_df, output_dir / "architecture_manifest.csv")
        write_df(nodes_df, output_dir / "constellation_nodes.csv")
        write_df(cn_dist_df, output_dir / "central_node_distribution.csv")
        write_df(validation_df, output_dir / "architecture_validation_report.csv")

    failed_checks = validation_df[~validation_df["passed"]]
    if not failed_checks.empty:
        print("Architecture validation failed:")
        print(failed_checks.to_string(index=False))
        raise RuntimeError("Architecture validation failed. Fix architecture generation before continuing.")

    if args.mode == "architectures" or args.mode == "validate":
        print("Architecture generation/validation completed successfully.")
        print_summary(output_dir)
        return

    # -------------------------------------------------------------------------
    # Shared time grid and propagated positions.
    # -------------------------------------------------------------------------
    time_seconds = np.arange(0.0, args.duration_hours * 3600.0 + 0.1, args.time_step_sec)
    print(f"Precomputing satellite positions: {len(nodes_df)} satellites × {len(time_seconds)} time steps")
    positions_eci, sub_lat, sub_lon = precompute_satellite_positions(nodes_df, time_seconds)

    # Ground stations.
    ground_stations = default_ground_stations(args.ground_station_set)
    gs_df = pd.DataFrame([asdict(gs) for gs in ground_stations])
    write_df(gs_df, output_dir / "ground_stations.csv")

    # -------------------------------------------------------------------------
    # 2. Communication-window generation
    # -------------------------------------------------------------------------
    windows_df = pd.DataFrame()
    if args.mode in {"windows", "all", "postprocess"}:
        print("Generating communication windows...")
        windows_df = generate_communication_windows(
            config=config,
            nodes_df=nodes_df,
            ground_stations=ground_stations,
            epoch=epoch,
            duration_hours=args.duration_hours,
            time_step_sec=args.time_step_sec,
            sat_sat_max_range_km=args.sat_sat_max_range_km,
            sat_sat_data_rate_bps=args.sat_sat_data_rate_bps,
            sat_gs_data_rate_bps=args.sat_gs_data_rate_bps,
            sat_sat_mode=args.sat_sat_mode,
            comm_band=args.comm_band,
            central_power_boost_factor=args.central_power_boost_factor,
            min_window_data_mbits=args.min_window_data_mbits,
            capacity_utilization_limit=args.capacity_utilization_limit,
            positions_eci=positions_eci,
            time_seconds=time_seconds,
        )
        if windows_df.empty:
            windows_df = pd.DataFrame(
                columns=[
                    "window_id",
                    "node_i",
                    "node_j",
                    "start_time_sec",
                    "end_time_sec",
                    "data_rate_bps",
                    "capacity_mbits",
                    "is_sat_gs",
                    "link_type",
                ]
            )
        write_df(windows_df, output_dir / "communication_windows.csv")

        node_ids, node_index = make_node_index(nodes_df, ground_stations)
        window_matrices = adjacency_from_windows(windows_df, node_ids, node_index)
        for filename, matrix_df in window_matrices.items():
            write_matrix(matrix_df, output_dir / filename)

    if args.mode == "windows":
        print("Communication-window generation completed successfully.")
        print_summary(output_dir)
        return

    # -------------------------------------------------------------------------
    # 3. Observation-opportunity generation
    # -------------------------------------------------------------------------
    if resolved_tasks_file is None:
        raise RuntimeError("Internal error: task file was not resolved for this mode.")
    tasks_df = load_tasks(
        resolved_tasks_file,
        epoch=epoch,
        max_tasks=args.max_tasks,
        task_time_mode=args.task_time_mode,
        task_spacing_minutes=args.task_spacing_minutes,
    )

    # Safety check: for full-task runs, never allow the pipeline to continue
    # if only a tiny task set was loaded. This prevents accidental demo-like runs.
    if int(args.max_tasks) == 0 and len(tasks_df) < int(args.require_min_tasks):
        raise RuntimeError(
            f"Only {len(tasks_df)} tasks were loaded from {resolved_tasks_file}. "
            f"Expected at least {args.require_min_tasks} for a full real-task run. "
            "Check the task CSV path and columns."
        )

    write_df(tasks_df, output_dir / "wildfire_tasks_used.csv")
    applied_deadline_distribution = {
        str(priority): sorted(
            float(v)
            for v in pd.to_numeric(group["applied_response_time_min"], errors="coerce").dropna().unique()
        )
        for priority, group in tasks_df.groupby("priority_class", dropna=False)
    }
    write_json(
        {
            "resolved_tasks_file": str(resolved_tasks_file),
            "loaded_tasks": int(len(tasks_df)),
            "max_tasks_argument": int(args.max_tasks),
            "input_columns_normalized": list(tasks_df.columns),
            "min_task_requirement": int(args.require_min_tasks),
            "task_time_mode": str(args.task_time_mode),
            "task_spacing_minutes": float(args.task_spacing_minutes),
            "approved_deadline_minutes": sorted(EXPECTED_DEADLINE_MINUTES),
            "applied_deadline_minutes_by_priority": applied_deadline_distribution,
            "created_time_sec_min": float(tasks_df["created_time_sec"].min()) if not tasks_df.empty else None,
            "created_time_sec_max": float(tasks_df["created_time_sec"].max()) if not tasks_df.empty else None,
            "deadline_time_sec_min": float(tasks_df["deadline_time_sec"].min()) if not tasks_df.empty else None,
            "deadline_time_sec_max": float(tasks_df["deadline_time_sec"].max()) if not tasks_df.empty else None,
            "simulation_duration_sec": float(args.duration_hours * 3600.0),
            "deadline_exceeds_simulation_end": bool((tasks_df["deadline_time_sec"] > args.duration_hours * 3600.0).any()) if not tasks_df.empty else False,
        },
        output_dir / "task_loading_report.json",
    )

    if not tasks_df.empty:
        sim_end_sec = float(args.duration_hours * 3600.0)
        latest_deadline = float(tasks_df["deadline_time_sec"].max())
        latest_created = float(tasks_df["created_time_sec"].max())
        print(
            f"Task timing after normalization: latest creation={latest_created/3600.0:.2f} h, "
            f"latest deadline={latest_deadline/3600.0:.2f} h, simulation duration={args.duration_hours:.2f} h"
        )
        if latest_deadline > sim_end_sec:
            print(
                "WARNING: Some task deadlines extend beyond the simulation duration. "
                "The run is still valid for generated windows, but observation fulfilment for late tasks may be conservative. "
                "Increase --duration-hours if you want the full deadline horizon covered."
            )
    validate_task_horizon(
        tasks_df,
        duration_hours=float(args.duration_hours),
        task_time_mode=args.task_time_mode,
        allow_truncated_task_horizon=bool(args.allow_truncated_task_horizon),
    )

    opportunities_df = pd.DataFrame()
    if args.mode in {"all", "postprocess"}:
        print(f"Generating observation opportunities for {len(tasks_df)} tasks...")
        opportunities_df = generate_observation_opportunities(
            config=config,
            nodes_df=nodes_df,
            tasks_df=tasks_df,
            epoch=epoch,
            duration_hours=args.duration_hours,
            time_step_sec=args.time_step_sec,
            observation_radius_km=args.observation_radius_km,
            subpoint_lat=sub_lat,
            subpoint_lon=sub_lon,
            time_seconds=time_seconds,
        )
        if opportunities_df.empty:
            opportunities_df = pd.DataFrame(
                columns=[
                    "architecture_id",
                    "task_id",
                    "node_id",
                    "earliest_observation_time_sec",
                    "earliest_observation_time_utc",
                    "min_distance_km",
                ]
            )
        write_df(opportunities_df, output_dir / "observation_opportunities.csv")

    # -------------------------------------------------------------------------
    # 4. Optional post-processing replay
    # -------------------------------------------------------------------------
    if args.mode in {"all", "postprocess"} and not args.skip_postprocess:
        if windows_df.empty and args.mode == "postprocess":
            windows_path = output_dir / "communication_windows.csv"
            if windows_path.exists():
                windows_df = pd.read_csv(windows_path)
            else:
                raise FileNotFoundError("communication_windows.csv not found for postprocess mode.")

        print("Running V19 capacity-aware post-processing replay...")
        transfers_df, reception_df, obs_df, failure_df, summary_df = postprocess_dissemination_and_observation(
            config=config,
            nodes_df=nodes_df,
            ground_stations=ground_stations,
            tasks_df=tasks_df,
            windows_df=windows_df,
            opportunities_df=opportunities_df,
            epoch=epoch,
            routing_policy=args.routing_policy,
            max_forwarding_hops=args.max_forwarding_hops,
            max_observer_copies_per_task=args.max_observer_copies_per_task,
            max_cn_copies_per_task=args.max_cn_copies_per_task,
            default_task_size_mbits=args.default_task_size_mbits,
            critical_task_size_mbits=args.critical_task_size_mbits,
            high_task_size_mbits=args.high_task_size_mbits,
            medium_task_size_mbits=args.medium_task_size_mbits,
            low_task_size_mbits=args.low_task_size_mbits,
            task_size_mode=args.task_size_mode,
            initial_ground_station_id=args.initial_ground_station_id,
        )

        write_df(transfers_df, output_dir / "transfer_events.csv")
        write_df(reception_df, output_dir / "task_reception_times.csv")
        write_df(obs_df, output_dir / "task_observation_results.csv")
        write_df(failure_df, output_dir / "failure_classification.csv")
        write_df(summary_df, output_dir / "performance_summary.csv")

        node_ids, node_index = make_node_index(nodes_df, ground_stations)
        transfer_matrices = adjacency_from_transfers(transfers_df, node_ids, node_index)
        for filename, matrix_df in transfer_matrices.items():
            write_matrix(matrix_df, output_dir / filename)

    if args.compress_large_csvs and args.mode == "all":
        compressed = []
        for filename in (
            "communication_windows.csv",
            "observation_opportunities.csv",
            "transfer_events.csv",
        ):
            target = compress_csv_verified(output_dir / filename, compression_level=6)
            if target is not None:
                compressed.append(target.name)
        if compressed:
            print(f"Compressed large raw outputs after verified round-trip: {', '.join(compressed)}")

    print("Pipeline completed successfully.")
    print_summary(output_dir)



# =============================================================================
# Architecture sweep / controlled parallel execution
# =============================================================================

def parse_number_list(value: str, cast_func=float) -> List:
    """Parse comma-separated CLI lists such as '60,120,180'."""
    if value is None:
        return []
    items = [x.strip() for x in str(value).split(",") if x.strip() != ""]
    return [cast_func(x) for x in items]


def safe_float_code(value: float, scale: int = 10, width: int = 4) -> str:
    """Create compact filename-safe numeric codes, e.g. 98.6 -> 0986."""
    return f"{int(round(float(value) * scale)):0{width}d}"


def case_output_name(
    n_satellites: int,
    n_planes: int,
    altitude_km: float,
    inclination_deg: float,
    cn_fraction_percent: float,
    walker_f: int,
) -> str:
    """Consistent case folder name for architecture-sweep runs."""
    return (
        f"T{int(n_satellites):03d}_"
        f"P{int(n_planes):02d}_"
        f"H{int(round(float(altitude_km))):04d}_"
        f"I{safe_float_code(inclination_deg, scale=10, width=4)}_"
        f"CN{int(round(float(cn_fraction_percent))):03d}_"
        f"F{int(walker_f)}"
    )


def build_architecture_cases(args: argparse.Namespace) -> List[Dict]:
    """
    Build independent architecture cases for controlled parallel execution.

    Each case is just a normal single-run argument set with a unique output folder.
    The heavy simulation is not parallelized inside one case; instead, many cases
    are executed in parallel as independent processes.
    """
    n_sats_list = parse_number_list(args.sweep_n_satellites, int)
    n_planes_list = parse_number_list(args.sweep_n_planes, int)
    altitudes = parse_number_list(args.sweep_altitudes_km, float)
    inclinations = parse_number_list(args.sweep_inclinations_deg, float)
    # CN fraction sweep for the final centrality study.
    # Default: 0,10,20,...,100. Can be overridden from the CLI.
    cn_fractions = parse_number_list(args.sweep_cn_fractions_percent, float)

    cases: List[Dict] = []
    root = Path(args.output_dir)

    for n_sat in n_sats_list:
        for n_planes in n_planes_list:
            if n_sat % n_planes != 0:
                # Skip invalid Walker cases. They are not useful for this design space.
                continue
            for altitude in altitudes:
                for inc in inclinations:
                    for cn_frac in cn_fractions:
                        case_args = vars(args).copy()
                        case_args["campaign"] = "single"
                        case_args["n_satellites"] = int(n_sat)
                        case_args["n_planes"] = int(n_planes)
                        case_args["altitude_km"] = float(altitude)
                        case_args["inclination_deg"] = float(inc)
                        case_args["cn_fraction_percent"] = float(cn_frac)

                        case_name = case_output_name(
                            n_satellites=int(n_sat),
                            n_planes=int(n_planes),
                            altitude_km=float(altitude),
                            inclination_deg=float(inc),
                            cn_fraction_percent=float(cn_frac),
                            walker_f=int(args.walker_f),
                        )
                        case_args["case_id"] = case_name
                        case_args["output_dir"] = str(root / case_name)
                        cases.append(case_args)

    if getattr(args, "case_index", 0) and int(args.case_index) > 0:
        idx = int(args.case_index) - 1  # User-facing case index is 1-based.
        if idx < 0 or idx >= len(cases):
            raise RuntimeError(f"--case-index={args.case_index} is outside the valid range 1..{len(cases)}")
        cases = [cases[idx]]

    if args.case_limit and args.case_limit > 0:
        cases = cases[: int(args.case_limit)]

    return cases


def case_is_completed(case_output_dir: Path) -> bool:
    """Completion test for resume/skip logic."""
    status_path = case_output_dir / "case_status.json"
    if status_path.exists():
        try:
            status = json.loads(status_path.read_text())
            if status.get("status") == "COMPLETED":
                return True
        except Exception:
            pass

    # Fallback for older runs.
    return (case_output_dir / "performance_summary.csv").exists()


def write_case_status(case_output_dir: Path, status: str, case_id: str, message: str = "") -> None:
    ensure_dir(case_output_dir)
    payload = {
        "case_id": case_id,
        "status": status,
        "message": message,
        "timestamp_utc": iso_utc(datetime.now(timezone.utc)),
    }
    write_json(payload, case_output_dir / "case_status.json")


def run_one_case_worker(case_args_dict: Dict) -> Dict:
    """
    Worker used by ProcessPoolExecutor.

    It redirects stdout/stderr into the case output folder so parallel runs do
    not mix console text. The return dictionary is used to build the campaign
    manifest/status table.
    """
    case_id = str(case_args_dict.get("case_id", "UNKNOWN_CASE"))
    output_dir = Path(str(case_args_dict["output_dir"]))
    ensure_dir(output_dir)
    log_path = output_dir / "run_stdout_stderr.log"

    if case_args_dict.get("resume", True) and not case_args_dict.get("force_rerun", False) and case_is_completed(output_dir):
        return {
            "case_id": case_id,
            "status": "SKIPPED_COMPLETED",
            "output_dir": str(output_dir),
            "message": "Existing completed output found.",
        }

    args = argparse.Namespace(**case_args_dict)
    start = datetime.now(timezone.utc)
    try:
        write_case_status(output_dir, "RUNNING", case_id)
        with open(log_path, "w", encoding="utf-8") as log_file:
            with redirect_stdout(log_file), redirect_stderr(log_file):
                print(f"Starting case {case_id} at {iso_utc(start)}")
                run_pipeline(args)
                print(f"Finished case {case_id} at {iso_utc(datetime.now(timezone.utc))}")

        end = datetime.now(timezone.utc)
        runtime_sec = (end - start).total_seconds()
        write_case_status(output_dir, "COMPLETED", case_id, f"runtime_sec={runtime_sec:.1f}")
        return {
            "case_id": case_id,
            "status": "COMPLETED",
            "output_dir": str(output_dir),
            "runtime_sec": runtime_sec,
            "message": "",
        }
    except Exception as exc:
        err = traceback.format_exc()
        with open(log_path, "a", encoding="utf-8") as log_file:
            log_file.write("\n\nERROR:\n")
            log_file.write(err)
        write_case_status(output_dir, "FAILED", case_id, str(exc))
        return {
            "case_id": case_id,
            "status": "FAILED",
            "output_dir": str(output_dir),
            "runtime_sec": (datetime.now(timezone.utc) - start).total_seconds(),
            "message": str(exc),
        }


def write_campaign_manifest(cases: List[Dict], output_dir: Path, filename: str = "architecture_sweep_manifest.csv") -> None:
    """Write the planned case list before execution."""
    rows = []
    for c in cases:
        rows.append(
            {
                "case_id": c.get("case_id"),
                "n_satellites": c.get("n_satellites"),
                "n_planes": c.get("n_planes"),
                "altitude_km": c.get("altitude_km"),
                "inclination_deg": c.get("inclination_deg"),
                "cn_fraction_percent": c.get("cn_fraction_percent"),
                "walker_f": c.get("walker_f"),
                "mode": c.get("mode"),
                "duration_hours": c.get("duration_hours"),
                "time_step_sec": c.get("time_step_sec"),
                "sat_sat_data_rate_bps": c.get("sat_sat_data_rate_bps"),
                "sat_gs_data_rate_bps": c.get("sat_gs_data_rate_bps"),
                "ground_station_set": c.get("ground_station_set"),
                "routing_policy": c.get("routing_policy"),
                "max_forwarding_hops": c.get("max_forwarding_hops"),
                "max_observer_copies_per_task": c.get("max_observer_copies_per_task"),
                "max_cn_copies_per_task": c.get("max_cn_copies_per_task"),
                "task_size_mode": c.get("task_size_mode"),
                "default_task_size_mbits": c.get("default_task_size_mbits"),
                "max_tasks": c.get("max_tasks"),
                "tasks_file": c.get("tasks_file"),
                "require_min_tasks": c.get("require_min_tasks"),
                "force_rerun": c.get("force_rerun"),
                "output_dir": c.get("output_dir"),
            }
        )
    ensure_dir(output_dir)
    pd.DataFrame(rows).to_csv(output_dir / filename, index=False)




def collect_campaign_outputs(root: Path) -> None:
    """Collect per-case summaries into root-level CSV files."""
    rows_summary = []
    rows_arch = []
    rows_cn = []

    for case_dir in sorted([p for p in root.iterdir() if p.is_dir()]):
        perf = case_dir / "performance_summary.csv"
        arch = case_dir / "architecture_manifest.csv"
        cn = case_dir / "central_node_distribution.csv"

        if perf.exists():
            df = pd.read_csv(perf)
            df.insert(0, "case_id", case_dir.name)
            rows_summary.append(df)
        if arch.exists():
            df = pd.read_csv(arch)
            df.insert(0, "case_id", case_dir.name)
            rows_arch.append(df)
        if cn.exists():
            df = pd.read_csv(cn)
            df.insert(0, "case_id", case_dir.name)
            rows_cn.append(df)

    if rows_summary:
        pd.concat(rows_summary, ignore_index=True).to_csv(root / "combined_performance_summary.csv", index=False)
    if rows_arch:
        pd.concat(rows_arch, ignore_index=True).to_csv(root / "combined_architecture_manifest.csv", index=False)
    if rows_cn:
        pd.concat(rows_cn, ignore_index=True).to_csv(root / "combined_central_node_distribution.csv", index=False)

def run_architecture_sweep(args: argparse.Namespace) -> None:
    """Run many architecture cases with user-controlled parallelism."""
    root = Path(args.output_dir)
    ensure_dir(root)

    # For actual execution, resolve the task CSV once in the parent process and
    # pass the absolute path to every worker. Manifest-only does not require the file.
    if not args.manifest_only and args.mode in {"all", "postprocess"}:
        args.tasks_file = str(resolve_task_file_path(Path(args.tasks_file) if args.tasks_file else DEFAULT_TASKS_FILE))

    cases = build_architecture_cases(args)

    # Default behavior is a Walker architecture screening sweep. CN fraction is hard-fixed at 30% for this screening file.
    if not cases:
        raise RuntimeError("No valid architecture cases were generated. Check sweep variables.")

    write_campaign_manifest(cases, root)
    print(f"Generated {len(cases)} valid architecture cases.")
    print(f"Output root: {root}")
    print(f"Manifest: {root / 'architecture_sweep_manifest.csv'}")

    if args.manifest_only:
        print("Manifest-only mode: not running cases.")
        return

    max_workers = max(1, int(args.max_parallel_runs))
    print(f"Running with max_parallel_runs={max_workers}")

    results = []
    if max_workers == 1:
        for case in cases:
            result = run_one_case_worker(case)
            results.append(result)
            print(f"[{result['status']}] {result['case_id']} -> {result['output_dir']}")
    else:
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            future_to_case = {executor.submit(run_one_case_worker, case): case for case in cases}
            for future in as_completed(future_to_case):
                result = future.result()
                results.append(result)
                print(f"[{result['status']}] {result['case_id']} -> {result['output_dir']}")

    results_df = pd.DataFrame(results).sort_values(["status", "case_id"])
    # For SLURM array jobs, multiple tasks may write simultaneously.
    # Each case still writes its own case_status.json; this root-level status is best-effort.
    results_df.to_csv(root / "architecture_sweep_status.csv", index=False)

    n_completed = int((results_df["status"] == "COMPLETED").sum())
    n_skipped = int((results_df["status"] == "SKIPPED_COMPLETED").sum())
    n_failed = int((results_df["status"] == "FAILED").sum())
    print("Campaign finished.")
    print(f"Completed: {n_completed}")
    print(f"Skipped completed: {n_skipped}")
    print(f"Failed: {n_failed}")
    print(f"Status table: {root / 'architecture_sweep_status.csv'}")
    if not getattr(args, "no_collect", False):
        collect_campaign_outputs(root)
        if (root / "combined_performance_summary.csv").exists():
            print(f"Combined performance: {root / 'combined_performance_summary.csv'}")
        if (root / "combined_architecture_manifest.csv").exists():
            print(f"Combined architecture manifest: {root / 'combined_architecture_manifest.csv'}")
    else:
        print("Skipping root-level collection because --no-collect was used.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="HPC-ready standard Walker architecture sweep with V19-style communication, CN swept from 0% to 100%, and 100% dissemination metrics."
    )

    parser.add_argument(
        "--mode",
        choices=["architectures", "windows", "postprocess", "all", "validate"],
        default="all",
        help="Which part of the pipeline to run.",
    )
    parser.add_argument("--output-dir", default="runs_standard_architecture_sweep_v19_comm_cn30", help="Output folder or output root for the Walker architecture sweep.")
    parser.add_argument(
        "--campaign",
        choices=["single", "architecture_sweep"],
        default="architecture_sweep",
        help="single = run one architecture; architecture_sweep = run the standard architecture grid with controlled parallelism.",
    )
    parser.add_argument(
        "--max-parallel-runs",
        type=int,
        default=3,
        help="Number of independent architecture cases to run in parallel. Use 1 on a laptop if memory/CPU becomes limiting.",
    )
    parser.add_argument(
        "--manifest-only",
        action="store_true",
        help="For architecture_sweep: only write the manifest, do not run cases.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=True,
        help="For architecture_sweep: skip cases that already have completed outputs.",
    )
    parser.add_argument(
        "--force-rerun",
        action="store_true",
        help="For architecture_sweep: rerun cases even if completed outputs already exist.",
    )
    parser.add_argument(
        "--case-limit",
        type=int,
        default=0,
        help="For architecture_sweep: run only the first N generated cases. Use 0 for all cases.",
    )
    parser.add_argument(
        "--case-index",
        type=int,
        default=0,
        help="For HPC/SLURM arrays: run only one 1-based case index from the generated architecture manifest. Use 0 for all cases.",
    )
    parser.add_argument(
        "--no-collect",
        action="store_true",
        help="Skip root-level combined CSV collection. Useful when many SLURM array jobs write to the same output root.",
    )

    # Architecture variables.
    parser.add_argument("--n-satellites", type=int, default=60)
    parser.add_argument("--n-planes", type=int, default=5)
    parser.add_argument("--altitude-km", type=float, default=500.0)
    parser.add_argument("--inclination-deg", type=float, default=98.6)
    parser.add_argument("--cn-fraction-percent", type=float, default=30.0)
    parser.add_argument("--walker-f", type=int, default=1)
    parser.add_argument("--raan0-deg", type=float, default=0.0)

    # Sweep variables used only when --campaign architecture_sweep is selected.
    # Default sweep for this file: user-defined standard architecture grid
    # and CN fraction from 0% to 100% in 10% increments.
    parser.add_argument("--sweep-n-satellites", default="60,120,180")
    parser.add_argument("--sweep-n-planes", default="3,5,10")
    parser.add_argument("--sweep-altitudes-km", default="500,800,1000")
    parser.add_argument("--sweep-inclinations-deg", default="60,80,98.6")
    parser.add_argument("--sweep-cn-fractions-percent", default="0,10,20,30,40,50,60,70,80,90,100")
    # Time and propagation.
    parser.add_argument(
        "--epoch-utc",
        default=DEFAULT_EPOCH_UTC,
        help=(
            "Fixed simulation epoch used when --epoch-mode fixed is selected, or when no task file is available. "
            "For final real-task runs the default --epoch-mode task_start overrides this value."
        ),
    )
    parser.add_argument(
        "--epoch-mode",
        choices=["task_start", "fixed"],
        default="task_start",
        help=(
            "task_start: infer the simulation epoch from the earliest real task timestamp in the task CSV. "
            "fixed: use --epoch-utc exactly. Default is task_start for final real-task runs."
        ),
    )
    parser.add_argument(
        "--epoch-floor",
        choices=["none", "minute", "hour", "day"],
        default="none",
        help=(
            "Optional rounding applied to the inferred task-start epoch. "
            "Use none for exact earliest task epoch; day starts at 00:00 UTC of the first task date."
        ),
    )
    parser.add_argument("--duration-hours", type=float, default=30.0)
    parser.add_argument("--time-step-sec", type=int, default=60)

    # Communication model.
    parser.add_argument(
        "--sat-sat-mode",
        choices=["central_only", "all", "none"],
        default="central_only",
        help="Allowed satellite-satellite topology.",
    )
    parser.add_argument("--sat-sat-max-range-km", type=float, default=0.0, help="Optional hard max sat-sat range. Use 0 to let the V19 link budget determine max range.")
    parser.add_argument("--comm-band", choices=["UHF", "S-band", "X-band"], default="UHF", help="V19 communication band for sat-sat link budget.")
    parser.add_argument("--central-power-boost-factor", type=float, default=1.5, help="V19 central-node range/rate boost factor.")
    parser.add_argument("--min-window-data-mbits", type=float, default=0.0, help="Drop communication windows whose total data capacity is below this threshold.")
    parser.add_argument("--capacity-utilization-limit", type=float, default=1.0, help="Fraction of each window capacity usable for task transfer.")
    parser.add_argument(
        "--sat-sat-data-rate-bps",
        type=float,
        default=250_000.0,
        help="Satellite-satellite data rate. Default 250 kbps to create real capacity pressure.",
    )
    parser.add_argument(
        "--sat-gs-data-rate-bps",
        type=float,
        default=100_000.0,
        help="Satellite-ground command uplink rate. Default 100 kbps.",
    )
    parser.add_argument(
        "--ground-station-set",
        choices=["munich_only", "tempe_only", "munich_tempe", "global_4"],
        default="munich_only",
        help="Ground station set. Default is one GS to avoid unrealistically easy dissemination.",
    )
    parser.add_argument(
        "--routing-policy",
        choices=["targeted_central"],
        default="targeted_central",
        help="Store-and-forward policy. targeted_central prevents flood-to-all and uses CNs as relays.",
    )
    parser.add_argument(
        "--initial-ground-station-id",
        default=None,
        help="Ground station that initially holds each ground-generated task. Default: first station in the selected set.",
    )
    parser.add_argument(
        "--max-forwarding-hops",
        type=int,
        default=3,
        help="Maximum store-and-forward hops after ground-source creation. Use -1 for unlimited.",
    )
    parser.add_argument(
        "--max-observer-copies-per-task",
        type=int,
        default=3,
        help="Maximum useful observer satellites that may receive each task. Use 0 for unlimited.",
    )
    parser.add_argument(
        "--max-cn-copies-per-task",
        type=int,
        default=5,
        help="Maximum central-node relay copies per task. Use 0 for unlimited.",
    )

    # Observation and tasks.
    parser.add_argument("--observation-radius-km", type=float, default=700.0)
    parser.add_argument(
        "--tasks-file",
        default=str(DEFAULT_TASKS_FILE),
        help=(
            "Wildfire task CSV. Defaults to the final Sentinel-3 task file. "
            "The CSV is loaded directly; no additional deduplication is applied."
        ),
    )
    parser.add_argument(
        "--max-tasks",
        type=int,
        default=0,
        help="Maximum number of tasks to load. Use 0 for all tasks. Default is 0 = use all real Sentinel-3 tasks.",
    )
    parser.add_argument(
        "--require-min-tasks",
        type=int,
        default=100,
        help="Safety check for full real-task runs. If --max-tasks 0 and fewer than this many tasks are loaded, stop instead of silently running a tiny dataset.",
    )
    parser.add_argument(
        "--task-time-mode",
        choices=["remap_to_sim_start", "original"],
        default="original",
        help=(
            "How to assign task times. Default is original, so the simulation uses the actual task timestamps. "
            "Use remap_to_sim_start only for debugging or synthetic validation runs."
        ),
    )
    parser.add_argument(
        "--task-spacing-minutes",
        type=float,
        default=1.0,
        help="Spacing between tasks when --task-time-mode remap_to_sim_start is used. Default: 1 minute.",
    )
    parser.add_argument(
        "--allow-truncated-task-horizon",
        action="store_true",
        help=(
            "Allow runs to continue even if task creation times or deadlines extend beyond the simulation horizon. "
            "Leave this off for thesis optimization runs."
        ),
    )
    parser.add_argument(
        "--task-size-mode",
        choices=["priority"],
        default=DEFAULT_TASK_SIZE_MODE,
        help="Single authoritative task-packet size model. Default: priority.",
    )
    parser.add_argument("--default-task-size-mbits", type=float, default=DEFAULT_FIXED_TASK_SIZE_MBITS)
    parser.add_argument("--critical-task-size-mbits", type=float, default=2.0)
    parser.add_argument("--high-task-size-mbits", type=float, default=1.5)
    parser.add_argument("--medium-task-size-mbits", type=float, default=1.0)
    parser.add_argument("--low-task-size-mbits", type=float, default=0.5)

    parser.add_argument(
        "--skip-postprocess",
        action="store_true",
        help="Generate architecture/windows/opportunities but skip replay post-processing.",
    )
    parser.add_argument(
        "--compress-large-csvs",
        action="store_true",
        help=(
            "After each completed case, gzip communication windows, observation "
            "opportunities, and transfer events and delete the source only after "
            "a SHA-256 verified decompression round-trip."
        ),
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.campaign == "architecture_sweep":
        run_architecture_sweep(args)
    else:
        run_pipeline(args)


if __name__ == "__main__":
    main()
