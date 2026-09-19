import pytest

import education_budget_vnext


def test_default_education_vnext_transport_is_fail_closed_while_live_source_is_hold():
    with pytest.raises(RuntimeError, match="EDUCATION_BUDGET_VNEXT_LIVE_TRANSPORT_HOLD"):
        education_budget_vnext.fetch_page(2026, page=1, size=10)


def test_collection_still_requires_explicit_live_intent_before_transport_stage():
    with pytest.raises(RuntimeError, match="EDUCATION_BUDGET_LIVE_COLLECTION_REQUIRES_EXPLICIT_ALLOW"):
        education_budget_vnext.collect_full_education_budget(2026)


def test_explicit_request_type_does_not_bypass_live_transport_hold():
    with pytest.raises(RuntimeError, match="EDUCATION_BUDGET_VNEXT_LIVE_TRANSPORT_HOLD"):
        education_budget_vnext.fetch_page(
            2026, page=1, size=10, request_type="explicitBudgetType"
        )
