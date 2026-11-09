"""Focused regression coverage for the bundled Kanban dashboard UI guards."""

from pathlib import Path


BUNDLE = Path(__file__).resolve().parents[2] / "plugins/kanban/dashboard/dist/index.js"


def test_bundled_dashboard_tims_column_is_read_only_before_mutations():
    """The shipped IIFE must reject every Tim destination in the UI path.

    This deliberately inspects the artifact that dashboard hosts load, rather
    than only testing the backend's rejection of ``status=tim``.
    """
    source = BUNDLE.read_text(encoding="utf-8")

    assert 'const DERIVED_LANES = new Set(["tim"])' in source
    assert "if (isDerivedLane(newStatus)) return;" in source
    assert "if (body && isDerivedLane(body.status || body.column || body.columnName)) return" in source
    assert source.count("if (isDerivedLane(props.column.name)) return;") >= 1
    assert source.count("if (props.column && isDerivedLane(props.column.name)) return;") == 2
    assert "if (patch && isDerivedLane(patch.status)) return;" in source
    assert 'disabled: isDerivedLane(props.column.name)' in source
    assert 'showCreate && !isDerivedLane(props.column.name) ? h(InlineCreate' in source

    # The derived lane is a display partition, not a canonical status write.
    assert 'JSON.stringify(finalPatch)' in source
    assert 'status: "tim"' not in source
