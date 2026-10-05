from __future__ import annotations

import numpy as np
import pandas as pd

from thesis_postprocessing.analysis import _exact_pareto, _minmax
from thesis_postprocessing.archive import parse_case_id
from thesis_postprocessing.canonical import _gini
from thesis_postprocessing.validation import expected_case_ids


def test_expected_grid_has_891_unique_cases() -> None:
    cases = expected_case_ids()
    assert len(cases) == 891
    assert "T060_P03_H0500_I0600_CN000_F1" in cases
    assert "T180_P10_H1000_I0986_CN100_F1" in cases


def test_case_id_parser_preserves_inclination_tenths() -> None:
    parsed = parse_case_id("T180_P10_H1000_I0986_CN100_F1")
    assert parsed["inclination_deg"] == 98.6
    assert parsed["cn_fraction_percent"] == 100


def test_gini_boundary_cases() -> None:
    assert _gini(np.array([0.0, 0.0])) == 0.0
    assert abs(_gini(np.array([1.0, 1.0, 1.0]))) < 1e-12
    assert _gini(np.array([0.0, 0.0, 3.0])) > 0.6


def test_exact_pareto_marks_only_nondominated_rows() -> None:
    frame = pd.DataFrame({"benefit": [1.0, 2.0, 2.0], "cost": [3.0, 3.0, 1.0]})
    result = _exact_pareto(frame, maximize=["benefit"], minimize=["cost"])
    assert result.tolist() == [False, False, True]


def test_minmax_inverse() -> None:
    values = pd.Series([1.0, 2.0, 3.0])
    assert _minmax(values).tolist() == [0.0, 0.5, 1.0]
    assert _minmax(values, invert=True).tolist() == [1.0, 0.5, 0.0]
