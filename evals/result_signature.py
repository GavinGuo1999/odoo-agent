from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any


RESULT_SIGNATURE_VERSION = "result-signature-v1"


@dataclass(frozen=True, slots=True)
class ResultDifference:
    path: str
    message: str


@dataclass(frozen=True, slots=True)
class ResultComparison:
    matches: bool
    actual_signature: str
    expected_signature: str
    diffs: list[ResultDifference]


def _normalized_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return str(value)
        return round(value, 12)
    if value is None or isinstance(value, (bool, int, str)):
        return value
    return str(value)


def _normalized_rows(
    columns: list[str],
    rows: list[dict[str, Any]],
    *,
    order_sensitive: bool,
) -> list[list[Any]]:
    normalized = [
        [_normalized_value(row.get(column)) for column in columns]
        for row in rows
    ]
    if not order_sensitive:
        normalized.sort(
            key=lambda row: json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    return normalized


def result_signature(
    columns: list[str],
    rows: list[dict[str, Any]],
    *,
    order_sensitive: bool = True,
) -> str:
    payload = {
        "version": RESULT_SIGNATURE_VERSION,
        "columns": columns,
        "rows": _normalized_rows(columns, rows, order_sensitive=order_sensitive),
        "order_sensitive": order_sensitive,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _values_match(
    actual: Any,
    expected: Any,
    *,
    absolute_tolerance: float,
    relative_tolerance: float,
) -> bool:
    numeric_types = (int, float, Decimal)
    if (
        isinstance(actual, numeric_types)
        and not isinstance(actual, bool)
        and isinstance(expected, numeric_types)
        and not isinstance(expected, bool)
    ):
        return math.isclose(
            float(actual),
            float(expected),
            abs_tol=absolute_tolerance,
            rel_tol=relative_tolerance,
        )
    actual_text = _normalized_value(actual)
    expected_text = _normalized_value(expected)
    if actual_text == expected_text:
        return True
    if isinstance(actual_text, str) and isinstance(expected_text, str):
        return actual_text.startswith(expected_text + "T00:00:00") or expected_text.startswith(
            actual_text + "T00:00:00"
        )
    return False


def compare_results(
    *,
    actual_columns: list[str],
    actual_rows: list[dict[str, Any]],
    expected_columns: list[str],
    expected_rows: list[dict[str, Any]],
    absolute_tolerance: float = 0.000001,
    relative_tolerance: float = 0.000001,
    order_sensitive: bool = True,
) -> ResultComparison:
    actual_hash = result_signature(
        actual_columns,
        actual_rows,
        order_sensitive=order_sensitive,
    )
    expected_hash = result_signature(
        expected_columns,
        expected_rows,
        order_sensitive=order_sensitive,
    )
    diffs: list[ResultDifference] = []
    if actual_columns != expected_columns:
        diffs.append(ResultDifference("columns", "结果列或列顺序不一致。"))
    if len(actual_rows) != len(expected_rows):
        diffs.append(ResultDifference("rows", "结果行数不一致。"))

    comparable_columns = expected_columns if actual_columns == expected_columns else []
    actual_values = _normalized_rows(
        comparable_columns,
        actual_rows,
        order_sensitive=order_sensitive,
    )
    expected_values = _normalized_rows(
        comparable_columns,
        expected_rows,
        order_sensitive=order_sensitive,
    )
    for row_index, (actual_row, expected_row) in enumerate(
        zip(actual_values, expected_values, strict=False)
    ):
        for column_index, column in enumerate(comparable_columns):
            actual_value = actual_row[column_index]
            expected_value = expected_row[column_index]
            if not _values_match(
                actual_value,
                expected_value,
                absolute_tolerance=absolute_tolerance,
                relative_tolerance=relative_tolerance,
            ):
                numeric = (
                    isinstance(actual_value, (int, float, Decimal))
                    and not isinstance(actual_value, bool)
                    and isinstance(expected_value, (int, float, Decimal))
                    and not isinstance(expected_value, bool)
                )
                diffs.append(
                    ResultDifference(
                        f"rows[{row_index}].{column}",
                        "数值超出允许误差。" if numeric else "结果值不一致。",
                    )
                )
    return ResultComparison(
        matches=not diffs,
        actual_signature=actual_hash,
        expected_signature=expected_hash,
        diffs=diffs[:20],
    )
