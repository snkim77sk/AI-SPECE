import pytest

import budget_read_vnext


CHILD_VIEWS = (
    "current_rows",
    "target_rows",
    "appropriation_context",
    "procurement_candidates",
    "procurement_lifecycle",
    "project_pipelines",
    "prebid_rows",
)


def _capture_children(monkeypatch):
    calls = {}
    function_names = {
        "current_rows": "current_budget_rows",
        "target_rows": "target_budget_rows",
        "appropriation_context": "appropriation_context_rows",
        "procurement_candidates": "procurement_candidate_rows",
        "procurement_lifecycle": "procurement_lifecycle_rows",
        "project_pipelines": "budget_project_rows",
        "prebid_rows": "prebid_budget_rows",
    }

    for view, function_name in function_names.items():
        def fake(*, _view=view, **kwargs):
            calls[_view] = dict(kwargs)
            return [{"view": _view}]
        monkeypatch.setattr(budget_read_vnext, function_name, fake)

    monkeypatch.setattr(
        budget_read_vnext, "budget_status", lambda **kwargs: {"read_only": True}
    )
    return calls


def test_read_model_global_offset_applies_only_to_primary_current_rows(monkeypatch):
    calls = _capture_children(monkeypatch)

    payload = budget_read_vnext.budget_read_model(
        fiscal_year=2026,
        limit=10,
        offset=20,
    )

    assert payload["pagination"]["current_rows"] == {"limit": 10, "offset": 20}
    for view in CHILD_VIEWS:
        assert calls[view]["limit"] == 10
        expected_offset = 20 if view == "current_rows" else 0
        assert calls[view]["offset"] == expected_offset
        assert payload["pagination"][view]["offset"] == expected_offset


def test_read_model_child_views_accept_independent_limit_and_offset(monkeypatch):
    calls = _capture_children(monkeypatch)

    payload = budget_read_vnext.budget_read_model(
        fiscal_year=2026,
        limit=25,
        offset=50,
        view_pagination={
            "target_rows": {"limit": 3, "offset": 6},
            "appropriation_context": {"offset": 2},
            "prebid_rows": {"limit": 4, "offset": 8},
        },
    )

    assert calls["current_rows"]["limit"] == 25
    assert calls["current_rows"]["offset"] == 50
    assert calls["target_rows"]["limit"] == 3
    assert calls["target_rows"]["offset"] == 6
    assert calls["appropriation_context"]["limit"] == 25
    assert calls["appropriation_context"]["offset"] == 2
    assert calls["prebid_rows"]["limit"] == 4
    assert calls["prebid_rows"]["offset"] == 8
    assert calls["project_pipelines"]["offset"] == 0
    assert payload["pagination"]["target_rows"] == {"limit": 3, "offset": 6}


def test_read_model_rejects_unknown_or_malformed_child_pagination():
    with pytest.raises(ValueError, match="unknown budget read-model pagination view"):
        budget_read_vnext._read_model_pagination(
            view_pagination={"not_a_view": {"offset": 1}}
        )

    with pytest.raises(TypeError, match="view_pagination must be a mapping"):
        budget_read_vnext._read_model_pagination(view_pagination=[])

    with pytest.raises(TypeError, match="pagination for target_rows must be a mapping"):
        budget_read_vnext._read_model_pagination(
            view_pagination={"target_rows": 7}
        )


def test_child_pagination_values_are_bounded_like_existing_page_helper():
    specs = budget_read_vnext._read_model_pagination(
        limit=99999,
        offset=-9,
        view_pagination={"target_rows": {"limit": 0, "offset": -5}},
    )

    assert specs["current_rows"] == {"limit": 5000, "offset": 0}
    assert specs["target_rows"] == {"limit": 1, "offset": 0}
    assert specs["prebid_rows"] == {"limit": 5000, "offset": 0}
