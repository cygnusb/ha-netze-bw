"""Regression tests for config flow and sensor metadata."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CONFIG_FLOW_PATH = ROOT / "custom_components" / "netze_bw_portal" / "config_flow.py"
SENSOR_PATH = ROOT / "custom_components" / "netze_bw_portal" / "sensor.py"


def _get_class_method(tree: ast.AST, class_name: str, method_name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for class_node in node.body:
                if isinstance(class_node, ast.FunctionDef) and class_node.name == method_name:
                    return class_node
    raise AssertionError(f"Could not find {class_name}.{method_name}")


def test_options_flow_uses_private_config_entry_reference() -> None:
    """Avoid assigning to OptionsFlow.config_entry, which is read-only."""
    tree = ast.parse(CONFIG_FLOW_PATH.read_text())
    init_method = _get_class_method(tree, "NetzeBwPortalOptionsFlow", "__init__")

    assignments = [
        node
        for node in ast.walk(init_method)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Attribute)
        and isinstance(node.targets[0].value, ast.Name)
        and node.targets[0].value.id == "self"
    ]

    assigned_attrs = {node.targets[0].attr for node in assignments}

    assert "_config_entry" in assigned_attrs
    assert "config_entry" not in assigned_attrs


def test_period_energy_sensors_do_not_set_measurement_state_class() -> None:
    """Period energy values must not advertise measurement state class."""
    tree = ast.parse(SENSOR_PATH.read_text())

    invalid_keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "NetzeBwSensorDescription":
            continue

        key = None
        has_measurement_state_class = False
        for keyword in node.keywords:
            if keyword.arg == "key" and isinstance(keyword.value, ast.Constant):
                key = keyword.value.value
            if keyword.arg == "state_class" and isinstance(keyword.value, ast.Attribute):
                has_measurement_state_class = keyword.value.attr == "MEASUREMENT"

        if has_measurement_state_class and key in {"daily_value", "sum_7d", "sum_30d"}:
            invalid_keys.add(key)

    assert invalid_keys == set()


def test_history_diagnostic_sensors_are_declared() -> None:
    """History visibility sensors should exist as separate entity descriptions."""
    tree = ast.parse(SENSOR_PATH.read_text())

    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "NetzeBwSensorDescription":
            continue
        for keyword in node.keywords:
            if keyword.arg == "key" and isinstance(keyword.value, ast.Constant):
                keys.add(keyword.value.value)

    assert {
        "history_status",
        "history_last_daily_point",
        "history_last_hourly_point",
        "history_last_backfill",
        "history_open_gaps",
        "last_fetch",
        "next_fetch",
    }.issubset(keys)


HISTORY_PATH = ROOT / "custom_components" / "netze_bw_portal" / "history.py"


def test_sensor_feedin_translation_uses_direction() -> None:
    """Feed-in naming must key off the logical direction, not raw value_types."""
    source = SENSOR_PATH.read_text()
    assert ".direction" in source
    assert "VALUE_TYPE_FEEDIN in" not in source


def test_history_value_type_uses_direction() -> None:
    """History fetches must follow the logical direction, not FEEDIN preference."""
    source = HISTORY_PATH.read_text()
    assert "VALUE_TYPE_FEEDIN in meter.value_types" not in source
    assert "meter.direction" in source


def test_history_statistic_id_uses_logical_key() -> None:
    """Statistic ids must include the direction so both directions never collide."""
    source = HISTORY_PATH.read_text()
    assert "meter.key" in source
    assert "f'{meter.id}_{resolution}'" not in source


def test_history_api_calls_use_installation_id() -> None:
    """API fetches must use the installation id, not the logical dict key."""
    source = HISTORY_PATH.read_text()
    tree = ast.parse(source)
    method = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_update_histories"
    )
    source_segment = ast.get_source_segment(source, method)
    assert "snapshot.meter.id, history_value_type" in source_segment
