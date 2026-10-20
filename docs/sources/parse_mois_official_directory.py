"""Extract a dated MOIS directory snapshot from a saved official HTML response.

No budget entity or institution codes are inferred. Fetching the official page is
a separate, visible step; this script works only on the supplied local HTML.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import re
from pathlib import Path

SOURCE_URL = "https://www.mois.go.kr/frt/sub/a04/localGovernment/screen.do"


def parse_directory(raw: bytes, retrieved_date: str, source_filename: str) -> dict:
    text = raw.decode("utf-8")
    headings = list(re.finditer(
        r'<h3 class="location_title">\s*<a\b([^>]*)>(.*?)</a>\s*</h3>',
        text, re.S,
    ))
    regions = []
    for index, heading in enumerate(headings):
        attrs, text_label = heading.groups()
        url = html.unescape(re.search(r'href="([^"]+)"', attrs).group(1))
        label = html.unescape(re.sub("<[^>]+>", "", text_label)).strip()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        segment = text[heading.end():end]
        block = re.search(r'<ul class="location_list">(.*?)</ul>', segment, re.S)
        children = []
        if block:
            for anchor in re.finditer(r'<a\b([^>]*)>(.*?)</a>', block.group(1), re.S):
                child_attrs, child_text = anchor.groups()
                child_url = html.unescape(
                    re.search(r'href="([^"]+)"', child_attrs).group(1)
                )
                child_name = html.unescape(re.sub("<[^>]+>", "", child_text)).strip()
                kind = (
                    "administrative_city" if label == "제주특별자치도" else
                    "autonomous_district" if child_name.endswith("구") else
                    "county" if child_name.endswith("군") else "city"
                )
                children.append({"label": child_name, "url": child_url, "kind": kind})
        regions.append({
            "label": label, "url": url,
            "kind": "top_level_local_government", "children": children,
        })
    return {
        "source_url": SOURCE_URL,
        "source_title": "행정안전부 지자체 누리집",
        "retrieved_at": retrieved_date,
        "source_method": "Original official UTF-8 HTML; labels and actual href values extracted from location_title/location_list.",
        "raw_source_file": source_filename,
        "raw_source_sha256": hashlib.sha256(raw).hexdigest(),
        "region_count": len(regions),
        "child_count": sum(len(region["children"]) for region in regions),
        "child_directory_count_not_budget_institution_count": True,
        "administrative_city_count": sum(
            child["kind"] == "administrative_city"
            for region in regions for child in region["children"]
        ),
        "notice": "The directory verifies municipality identity and parent region, not a separate budget entity or financial-data availability. No financial API or institution codes are invented. Jeju's two cities are administrative cities, not local government corporations.",
        "classification_sources": [
            {
                "url": "https://www.law.go.kr/LSW/lsLawLinkInfo.do?chrClsCd=010202&lsId=001656&lsJoLnkSeq=1000854590&print=print",
                "description": "지방자치법 제3조: 시도, 시군자치구 및 일반구 구분",
            },
            {
                "url": "https://www.law.go.kr/LSW/lsLawLinkInfo.do?chrClsCd=010202&lsJoLnkSeq=1000026182",
                "description": "제주특별법 제10조제2항: 지방자치단체가 아닌 시(행정시)",
            },
        ],
        "regions": regions,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--retrieved-date", default=dt.datetime.now(dt.timezone.utc).date().isoformat()
    )
    parser.add_argument("--expected-regions", type=int, default=16)
    parser.add_argument("--expected-children", type=int, default=229)
    args = parser.parse_args()
    result = parse_directory(
        args.html.read_bytes(), args.retrieved_date, args.html.name
    )
    if (
        result["region_count"] != args.expected_regions
        or result["child_count"] != args.expected_children
    ):
        raise SystemExit(
            "Official directory changed or HTML extraction failed: "
            f'{result["region_count"]} regions/{result["child_count"]} children; '
            "inspect the source and create a new dated snapshot."
        )
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "region_count": result["region_count"],
        "child_count": result["child_count"],
        "administrative_city_count": result["administrative_city_count"],
        "raw_source_sha256": result["raw_source_sha256"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
