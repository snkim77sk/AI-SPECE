"""Bounded one-day/one-page source probes on fresh temporary SQLite.

This script is intentionally separate from production scheduling. It may issue a
small, hard-bounded number of live read requests only when ``--allow-live`` is
explicitly supplied and source credentials are present.
"""
from __future__ import annotations

import argparse
import datetime as dt
import functools
import json
import os
from pathlib import Path
import sys
import tempfile
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vnext_live_gate import CANARY_PROVENANCE_PURPOSE, runtime_source_sha
from vnext_provenance import seal_report
from vnext_source_guard import bounded_canary_source_context, operational_budget_source_context

G2B_PROBE_COUNT = 1
G2B_LOOKBACK_DAYS = 3
G2B_MAX_HTTP_REQUESTS = G2B_PROBE_COUNT * G2B_LOOKBACK_DAYS
LOFIN_MAX_HTTP_REQUESTS = 3
PAGE_SIZE = 10
APPROVAL_VERSION = 1


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-live", action="store_true")
    return parser


def run_bounded_canary(*, allow_live=False, now=None):
    out = ROOT / "verification"
    out.mkdir(exist_ok=True)
    now = now or dt.datetime.now(ZoneInfo("Asia/Seoul"))
    day = now.date() - dt.timedelta(days=1)
    source_sha = runtime_source_sha()
    if allow_live and not source_sha:
        raise RuntimeError("CANARY_RUNTIME_SOURCE_SHA_REQUIRED")

    # Override path before any application import; never reuse a serving DB.
    with tempfile.TemporaryDirectory(prefix="g2b-safe-canary-") as temp:
        os.environ["G2B_DB_PATH"] = str(Path(temp) / "canary.sqlite3")
        os.environ["G2B_TEST_MODE"] = "1"
        os.environ["G2B_AUTO_SYNC"] = "0"
        # The bounded canary must never attach any code to a production PostgreSQL
        # store. Force the supported test-only SQLite adapter and remove every
        # canonical/legacy URL alias before application storage modules are imported.
        os.environ["G2B_BUDGET_STORAGE"] = "sqlite"
        for name in (
            "G2B_DATABASE_URL",
            "G2B_BUDGET_DATABASE_URL",
            "DATABASE_URL",
            "POSTGRES_URL",
            "POSTGRESQL_URL",
        ):
            os.environ.pop(name, None)
        os.environ["G2B_VNEXT_API_DAILY_LIMIT"] = str(G2B_MAX_HTTP_REQUESTS)
        os.environ["LOFIN_VNEXT_API_DAILY_LIMIT"] = str(LOFIN_MAX_HTTP_REQUESTS)

        import db
        import budget_normalizer_v41
        import budget_snapshot_vnext
        import g2b_vnext_canary
        import lofin_vnext_http
        import vnext_http

        db.init_db()
        if len(g2b_vnext_canary.CANARY_DATASETS) != G2B_PROBE_COUNT:
            raise RuntimeError("bounded canary probe count drift detected")

        report = {
            "approval_version": APPROVAL_VERSION,
            "source_commit_sha": source_sha,
            "production_db_touched": False,
            "budget_validation_storage": "DISPOSABLE_SQLITE",
            "main_merge_hold": False,
            "deployment_state": "V4_BUDGET_CENTERED",
            "bulk_collection_attempted": False,
            "approval_scope": "bounded sample identity+schema+fact only",
            "python": sys.version,
            "live_allowed_for_this_invocation": bool(allow_live),
            "probe_day_kst": day.isoformat(),
            "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "g2b_probe_count": G2B_PROBE_COUNT,
            "g2b_lookback_days": G2B_LOOKBACK_DAYS,
            "g2b_max_http_requests": G2B_MAX_HTTP_REQUESTS,
            "g2b_page_size": PAGE_SIZE,
            "lofin_max_http_requests": LOFIN_MAX_HTTP_REQUESTS,
            "budget_probe_scope": "LOFIN_QWGJK_PLUS_CURRENT_AND_NEXT_YEAR_AIDFA_ONE_PAGE_EACH",
            "budget_probe_datasets": ["budget", "budget_appropriation"],
            "budget_sources_not_probed": [
                "education_budget:EDUINFO",
            ],
            "budget_all_sources_verified": False,
        }

        if not allow_live:
            report["g2b"] = {
                "status": "NOT_REQUESTED",
                "live_request_attempted": False,
            }
            report["budget"] = {
                "status": "NOT_REQUESTED",
                "live_request_attempted": False,
                "source": "LOFIN/QWGJK",
                "probe_scope": "one page only; not whole-source completeness",
                "source_collection_completeness_verified": False,
            }
            report["current_appropriation"] = {
                "status": "NOT_REQUESTED",
                "live_request_attempted": False,
                "source": "LOFIN/AIDFA",
                "fiscal_year": now.date().year,
                "probe_scope": "one page read-only; no persistence",
                "source_collection_completeness_verified": False,
            }
            report["future_budget"] = {
                "status": "NOT_REQUESTED",
                "live_request_attempted": False,
                "source": "LOFIN/AIDFA",
                "fiscal_year": now.date().year + 1,
                "probe_scope": "one page read-only; no persistence",
                "source_collection_completeness_verified": False,
            }
        else:
            # The two source families are diagnosed independently. A key/error on
            # one family must not prevent the other family from producing a result.
            with bounded_canary_source_context(
                validation_date=day.isoformat(),
                max_requests=G2B_MAX_HTTP_REQUESTS + 1,
            ):
                if os.getenv("G2B_SERVICE_KEY", "").strip():
                    try:
                        g2b_vnext_canary.shopping_vnext._request = functools.partial(
                            vnext_http.request, retries=1, timeout=20
                        )
                        report["g2b"] = g2b_vnext_canary.run_canary(
                            today=day,
                            rows=PAGE_SIZE,
                            lookback_days=G2B_LOOKBACK_DAYS,
                        )
                    except Exception as exc:
                        report["g2b"] = {
                            "status": "FAILED",
                            "error_code": type(exc).__name__,
                            "live_request_attempted": True,
                        }
                else:
                    report["g2b"] = {
                        "status": "BLOCKED",
                        "reason": "G2B_SERVICE_KEY_NOT_CONFIGURED",
                        "live_request_attempted": False,
                    }

                if lofin_vnext_http.get_lofin_key():
                    try:
                        report["budget"] = budget_snapshot_vnext.run_budget_canary(
                            snapshot_date=day,
                            rows=PAGE_SIZE,
                        )
                        report["budget"]["source_collection_completeness_verified"] = False
                    except Exception as exc:
                        report["budget"] = {
                            "status": "FAILED",
                            "error_code": type(exc).__name__,
                            "live_request_attempted": True,
                            "source": "LOFIN/QWGJK",
                            "source_collection_completeness_verified": False,
                        }
                else:
                    report["budget"] = {
                        "status": "BLOCKED",
                        "reason": "LOFIN_API_KEY_NOT_CONFIGURED",
                        "live_request_attempted": False,
                        "source": "LOFIN/QWGJK",
                        "source_collection_completeness_verified": False,
                    }

            today = now.date()

            def aidfa_probe(year):
                if not lofin_vnext_http.get_lofin_key():
                    return {
                        "status": "BLOCKED",
                        "reason": "LOFIN_API_KEY_NOT_CONFIGURED",
                        "live_request_attempted": False,
                        "source": "LOFIN/AIDFA",
                        "fiscal_year": int(year),
                        "probe_scope": "one page read-only; no persistence",
                        "source_collection_completeness_verified": False,
                    }
                try:
                    with operational_budget_source_context(
                        snapshot_date=today.isoformat(),
                        max_requests=1,
                    ):
                        rows, total, code, _message = (
                            lofin_vnext_http.fetch_appropriation_page(
                                int(year),
                                page=1,
                                size=PAGE_SIZE,
                                retries=1,
                            )
                        )
                        source_context = (
                            __import__(
                                "vnext_source_guard"
                            ).current_source_request_context()
                            or {}
                        )

                    mismatched = [
                        row for row in rows
                        if str(row.get("fyr") or "").strip()
                        and str(row.get("fyr")).strip() != str(int(year))
                    ]
                    if mismatched:
                        raise RuntimeError(
                            "CANARY_AIDFA_FISCAL_YEAR_MISMATCH"
                        )
                    normalized = (
                        budget_normalizer_v41.normalize_record(
                            "budget_appropriation",
                            rows[0],
                            source_date=today.isoformat(),
                        )
                        if rows else None
                    )
                    if (
                        normalized
                        and str(normalized.get("source_layer"))
                        != "APPROPRIATION"
                    ):
                        raise RuntimeError(
                            "CANARY_AIDFA_NORMALIZATION_INVALID"
                        )
                    return {
                        "status": "CONCLUSIVE" if rows else "NO_DATA",
                        "live_request_attempted": True,
                        "source": "LOFIN/AIDFA",
                        "fiscal_year": int(year),
                        "rows_sampled": len(rows),
                        "source_total": total,
                        "result_code": str(code or ""),
                        "schema_verified": bool(normalized),
                        "transport_successes": int(
                            source_context.get(
                                "transport_successes_used"
                            ) or 0
                        ),
                        "probe_scope": "one page read-only; no persistence",
                        "source_collection_completeness_verified": False,
                    }
                except Exception as exc:
                    return {
                        "status": "FAILED",
                        "error_code": type(exc).__name__,
                        "live_request_attempted": True,
                        "source": "LOFIN/AIDFA",
                        "fiscal_year": int(year),
                        "probe_scope": "one page read-only; no persistence",
                        "source_collection_completeness_verified": False,
                    }

            report["current_appropriation"] = aidfa_probe(today.year)
            report["future_budget"] = aidfa_probe(today.year + 1)

        report["all_sample_schemas_verified"] = (
            report["g2b"].get("status") == "CONCLUSIVE"
            and report["budget"].get("schema_verified") is True
            and report["current_appropriation"].get("status")
            in {"CONCLUSIVE", "NO_DATA"}
            and report["future_budget"].get("status")
            in {"CONCLUSIVE", "NO_DATA"}
        )
        report["current_appropriation_transport_verified"] = (
            report["current_appropriation"].get("status")
            in {"CONCLUSIVE", "NO_DATA"}
            and (
                not report["current_appropriation"].get(
                    "live_request_attempted"
                )
                or int(
                    report["current_appropriation"].get(
                        "transport_successes"
                    ) or 0
                ) == 1
            )
        )
        report["future_budget_transport_verified"] = (
            report["future_budget"].get("status") in {"CONCLUSIVE", "NO_DATA"}
            and (
                not report["future_budget"].get("live_request_attempted")
                or int(report["future_budget"].get("transport_successes") or 0) == 1
            )
        )
        report["whole_source_completeness_verified"] = False
        report = seal_report(report, purpose=CANARY_PROVENANCE_PURPOSE)
        text = json.dumps(report, ensure_ascii=False, indent=2)
        (out / "canary.json").write_text(text + "\n", encoding="utf-8")
        return report


def main(argv=None):
    args = build_parser().parse_args(argv)
    report = run_bounded_canary(allow_live=args.allow_live)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
