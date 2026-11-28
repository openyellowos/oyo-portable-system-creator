#!/usr/bin/env python3
"""Fail-closed checks for the live GitHub tag and its immutable-tag ruleset."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable


SHA_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
RELEASE_TAG_RE = re.compile(r"^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
REQUIRED_RULES = {"update", "deletion"}
REQUIRED_PATTERN = "refs/tags/v*"
MAX_ANNOTATED_TAG_DEPTH = 16


class SafetyError(ValueError):
    """A live GitHub release precondition is missing or unverifiable."""


def _sha(value: object, description: str) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise SafetyError(f"invalid {description} SHA from GitHub")
    return value


def resolve_remote_tag_commit(
    repository: str,
    tag: str,
    api_get: Callable[[str], object],
) -> str:
    """Resolve a current remote tag ref, peeling nested annotated tag objects."""
    if not RELEASE_TAG_RE.fullmatch(tag):
        raise SafetyError(f"invalid release tag: {tag!r}")
    ref = api_get(f"repos/{repository}/git/ref/tags/{tag}")
    if not isinstance(ref, dict) or ref.get("ref") != f"refs/tags/{tag}":
        raise SafetyError("GitHub returned a missing or unexpected tag ref")
    obj = ref.get("object")
    seen: set[str] = set()
    for _ in range(MAX_ANNOTATED_TAG_DEPTH + 1):
        if not isinstance(obj, dict):
            raise SafetyError("GitHub tag ref has no valid object")
        object_sha = _sha(obj.get("sha"), "tag object")
        object_type = obj.get("type")
        if object_type == "commit":
            return object_sha
        if object_type != "tag":
            raise SafetyError(f"GitHub release tag resolves to unsupported object type {object_type!r}")
        if object_sha in seen:
            raise SafetyError("cycle detected while peeling annotated tag objects")
        seen.add(object_sha)
        tag_object = api_get(f"repos/{repository}/git/tags/{object_sha}")
        if not isinstance(tag_object, dict):
            raise SafetyError("GitHub returned an invalid annotated tag object")
        obj = tag_object.get("object")
    raise SafetyError("annotated tag nesting exceeds the supported limit")


def _list_ruleset_summaries(
    repository: str,
    api_get: Callable[[str], object],
) -> list[object]:
    summaries: list[object] = []
    for page in range(1, 11):
        endpoint = (
            f"repos/{repository}/rulesets?includes_parents=true&targets=tag"
            f"&per_page=100&page={page}"
        )
        result = api_get(endpoint)
        if not isinstance(result, list):
            raise SafetyError("GitHub rulesets response is not a list")
        summaries.extend(result)
        if len(result) < 100:
            return summaries
    raise SafetyError("GitHub ruleset pagination exceeded the safety limit")


def _ruleset_protects_release_tags(ruleset: object) -> bool:
    if not isinstance(ruleset, dict):
        return False
    if ruleset.get("target") != "tag" or ruleset.get("enforcement") != "active":
        return False
    conditions = ruleset.get("conditions")
    ref_name = conditions.get("ref_name") if isinstance(conditions, dict) else None
    if not isinstance(ref_name, dict):
        return False
    includes, excludes = ref_name.get("include"), ref_name.get("exclude")
    if not isinstance(includes, list) or REQUIRED_PATTERN not in includes:
        return False
    if excludes != []:
        return False
    bypass = ruleset.get("bypass_actors")
    if not isinstance(bypass, list) or bypass:
        return False
    rules = ruleset.get("rules")
    if not isinstance(rules, list):
        return False
    rule_types = {rule.get("type") for rule in rules if isinstance(rule, dict)}
    return REQUIRED_RULES <= rule_types


def verify_active_immutable_tag_ruleset(
    repository: str,
    api_get: Callable[[str], object],
) -> None:
    """Require an active v* tag ruleset with update+delete blocked and no bypass."""
    summaries = _list_ruleset_summaries(repository, api_get)
    if not summaries:
        raise SafetyError("no GitHub tag Ruleset is visible; automatic release is blocked")

    checked: list[object] = []
    for summary in summaries:
        if not isinstance(summary, dict) or not isinstance(summary.get("id"), (int, str)):
            raise SafetyError("GitHub returned a malformed Ruleset summary")
        ruleset = api_get(f"repos/{repository}/rulesets/{summary['id']}")
        checked.append(ruleset)
    if not any(_ruleset_protects_release_tags(item) for item in checked):
        raise SafetyError(
            "no verifiable active tag Ruleset covers refs/tags/v* with update and deletion restrictions and zero bypass actors"
        )


def verify_release_state(
    repository: str,
    tag: str,
    expected_commit: str,
    api_get: Callable[[str], object],
) -> None:
    if not SHA_RE.fullmatch(expected_commit):
        raise SafetyError("expected commit is not a full lowercase Git SHA")
    verify_active_immutable_tag_ruleset(repository, api_get)
    verify_remote_tag_commit(repository, tag, expected_commit, api_get)


def verify_remote_tag_commit(
    repository: str,
    tag: str,
    expected_commit: str,
    api_get: Callable[[str], object],
) -> None:
    if not SHA_RE.fullmatch(expected_commit):
        raise SafetyError("expected commit is not a full lowercase Git SHA")
    remote_commit = resolve_remote_tag_commit(repository, tag, api_get)
    if remote_commit != expected_commit:
        raise SafetyError(
            f"live GitHub tag target changed: expected {expected_commit}, found {remote_commit}"
        )


def gh_api_get(endpoint: str) -> object:
    env = os.environ.copy()
    if "/rulesets" in endpoint:
        audit_token = env.get("RELEASE_RULESET_AUDIT_TOKEN", "")
        if not audit_token:
            raise SafetyError(
                "RELEASE_RULESET_AUDIT_TOKEN is required to inspect Ruleset bypass actors"
            )
        # Keep the read-only audit identity separate from the publishing GITHUB_TOKEN.
        env["GH_TOKEN"] = audit_token
    command = ["gh", "api", endpoint]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True, env=env)
        return json.loads(result.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        # Do not echo subprocess diagnostics, which could contain environment details.
        raise SafetyError(f"GitHub API read failed for {endpoint}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, help="owner/repository")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument(
        "--tag-only",
        action="store_true",
        help="check only the current remote tag target (never checks or permits publication)",
    )
    args = parser.parse_args()
    try:
        if args.tag_only:
            verify_remote_tag_commit(args.repository, args.tag, args.expected_commit, gh_api_get)
        else:
            verify_release_state(args.repository, args.tag, args.expected_commit, gh_api_get)
    except SafetyError as exc:
        print(f"Release safety gate rejected publication: {exc}", file=sys.stderr)
        return 1
    print(f"Remote tag {args.tag} is protected and resolves to {args.expected_commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
