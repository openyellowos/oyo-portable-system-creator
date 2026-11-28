import unittest
from unittest.mock import patch

from scripts.verify_release_safety import (
    SafetyError,
    gh_api_get,
    resolve_remote_tag_commit,
    verify_active_immutable_tag_ruleset,
    verify_release_state,
)


COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
TAG_OBJECT_A = "c" * 40
TAG_OBJECT_B = "d" * 40
TAG = "v1.2.3"
REPOSITORY = "openyellowos/oyo-portable-system-creator"


def protected_ruleset(**changes):
    value = {
        "id": 7,
        "target": "tag",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
        "bypass_actors": [],
        "rules": [{"type": "update"}, {"type": "deletion"}],
    }
    value.update(changes)
    return value


class FakeGitHub:
    def __init__(self, commit=COMMIT_A, *, annotated=False, ruleset=None):
        self.commit = commit
        self.annotated = annotated
        self.ruleset = ruleset if ruleset is not None else protected_ruleset()
        self.fail_endpoint = None
        self.calls = []

    def get(self, endpoint):
        self.calls.append(endpoint)
        if endpoint == self.fail_endpoint:
            raise SafetyError("injected API failure")
        if endpoint == f"repos/{REPOSITORY}/rulesets?includes_parents=true&targets=tag&per_page=100&page=1":
            return [{"id": 7}]
        if endpoint == f"repos/{REPOSITORY}/rulesets/7":
            return self.ruleset
        if endpoint == f"repos/{REPOSITORY}/git/ref/tags/{TAG}":
            obj = (
                {"sha": TAG_OBJECT_A, "type": "tag"}
                if self.annotated
                else {"sha": self.commit, "type": "commit"}
            )
            return {"ref": f"refs/tags/{TAG}", "object": obj}
        if endpoint == f"repos/{REPOSITORY}/git/tags/{TAG_OBJECT_A}":
            return {"object": {"sha": self.commit, "type": "commit"}}
        if endpoint == f"repos/{REPOSITORY}/git/tags/{TAG_OBJECT_B}":
            return {"object": {"sha": COMMIT_B, "type": "commit"}}
        raise AssertionError(f"unexpected API request: {endpoint}")


class ReleaseSafetyTests(unittest.TestCase):
    def test_unchanged_lightweight_tag_is_allowed_with_active_ruleset(self):
        github = FakeGitHub()
        verify_release_state(REPOSITORY, TAG, COMMIT_A, github.get)

    def test_unchanged_annotated_tag_is_peeled_and_allowed(self):
        github = FakeGitHub(annotated=True)
        self.assertEqual(resolve_remote_tag_commit(REPOSITORY, TAG, github.get), COMMIT_A)
        verify_release_state(REPOSITORY, TAG, COMMIT_A, github.get)

    def test_remote_move_before_draft_creation_is_rejected(self):
        github = FakeGitHub(commit=COMMIT_B)
        with self.assertRaisesRegex(SafetyError, "tag target changed"):
            verify_release_state(REPOSITORY, TAG, COMMIT_A, github.get)

    def test_remote_move_after_draft_creation_is_rejected_before_publish(self):
        github = FakeGitHub()
        verify_release_state(REPOSITORY, TAG, COMMIT_A, github.get)  # pre-draft
        github.commit = COMMIT_B  # tag moves while draft assets are uploaded
        with self.assertRaisesRegex(SafetyError, "tag target changed"):
            verify_release_state(REPOSITORY, TAG, COMMIT_A, github.get)  # pre-publish

    def test_old_local_checkout_only_gate_would_miss_remote_move(self):
        # The old gate compared the event/checkouts only, so local SHA A still passed.
        local_tag_commit = checkout_commit = event_sha = COMMIT_A
        self.assertEqual(local_tag_commit, checkout_commit)
        self.assertEqual(local_tag_commit, event_sha)
        with self.assertRaisesRegex(SafetyError, "tag target changed"):
            verify_release_state(REPOSITORY, TAG, COMMIT_A, FakeGitHub(commit=COMMIT_B).get)

    def test_deleted_remote_tag_or_ref_fetch_failure_is_rejected(self):
        github = FakeGitHub()
        github.fail_endpoint = f"repos/{REPOSITORY}/git/ref/tags/{TAG}"
        with self.assertRaisesRegex(SafetyError, "API failure"):
            verify_release_state(REPOSITORY, TAG, COMMIT_A, github.get)

    def test_annotated_tag_target_change_is_rejected(self):
        github = FakeGitHub(annotated=True)
        github.get = lambda endpoint: (
            {"ref": f"refs/tags/{TAG}", "object": {"sha": TAG_OBJECT_B, "type": "tag"}}
            if endpoint == f"repos/{REPOSITORY}/git/ref/tags/{TAG}"
            else FakeGitHub(annotated=True, commit=COMMIT_B).get(endpoint)
        )
        with self.assertRaisesRegex(SafetyError, "tag target changed"):
            verify_release_state(REPOSITORY, TAG, COMMIT_A, github.get)

    def test_ruleset_list_api_failure_is_rejected_closed(self):
        github = FakeGitHub()
        github.fail_endpoint = f"repos/{REPOSITORY}/rulesets?includes_parents=true&targets=tag&per_page=100&page=1"
        with self.assertRaisesRegex(SafetyError, "API failure"):
            verify_release_state(REPOSITORY, TAG, COMMIT_A, github.get)

    def test_missing_ruleset_is_rejected(self):
        with self.assertRaisesRegex(SafetyError, "no GitHub tag Ruleset"):
            verify_active_immutable_tag_ruleset(REPOSITORY, lambda _endpoint: [])

    def test_disabled_or_evaluate_ruleset_is_rejected(self):
        for state in ("disabled", "evaluate"):
            with self.subTest(state=state):
                ruleset = protected_ruleset(enforcement=state)
                github = FakeGitHub(ruleset=ruleset)
                with self.assertRaisesRegex(SafetyError, "no verifiable active tag Ruleset"):
                    verify_active_immutable_tag_ruleset(REPOSITORY, github.get)

    def test_missing_update_or_deletion_rule_is_rejected(self):
        ruleset = protected_ruleset(rules=[{"type": "update"}])
        with self.assertRaisesRegex(SafetyError, "no verifiable active tag Ruleset"):
            verify_active_immutable_tag_ruleset(REPOSITORY, FakeGitHub(ruleset=ruleset).get)

    def test_bypass_actor_or_narrowed_scope_is_rejected(self):
        cases = [
            protected_ruleset(bypass_actors=[{"actor_type": "RepositoryRole", "actor_id": 5}]),
            protected_ruleset(conditions={"ref_name": {"include": ["refs/tags/v1.*"], "exclude": []}}),
            protected_ruleset(conditions={"ref_name": {"include": ["refs/tags/v*"], "exclude": ["refs/tags/v1.*"]}}),
        ]
        for ruleset in cases:
            with self.subTest(ruleset=ruleset):
                with self.assertRaises(SafetyError):
                    verify_active_immutable_tag_ruleset(REPOSITORY, FakeGitHub(ruleset=ruleset).get)

    def test_detail_api_failure_is_rejected_closed(self):
        github = FakeGitHub()
        github.fail_endpoint = f"repos/{REPOSITORY}/rulesets/7"
        with self.assertRaisesRegex(SafetyError, "API failure"):
            verify_active_immutable_tag_ruleset(REPOSITORY, github.get)

    def test_ruleset_bypass_visibility_requires_explicit_read_only_audit_token(self):
        with patch.dict("os.environ", {"RELEASE_RULESET_AUDIT_TOKEN": ""}, clear=False):
            with patch("scripts.verify_release_safety.subprocess.run") as run:
                with self.assertRaisesRegex(SafetyError, "RELEASE_RULESET_AUDIT_TOKEN is required"):
                    gh_api_get(f"repos/{REPOSITORY}/rulesets/7")
                run.assert_not_called()

        with patch.dict("os.environ", {"RELEASE_RULESET_AUDIT_TOKEN": "read-only-test-token"}, clear=False):
            with patch("scripts.verify_release_safety.subprocess.run") as run:
                run.return_value.stdout = "{}"
                result = gh_api_get(f"repos/{REPOSITORY}/rulesets/7")
                self.assertEqual(result, {})
                self.assertEqual(run.call_args.kwargs["env"]["GH_TOKEN"], "read-only-test-token")

    def test_hidden_bypass_field_is_not_assumed_to_mean_no_bypass(self):
        ruleset = protected_ruleset()
        del ruleset["bypass_actors"]
        with self.assertRaisesRegex(SafetyError, "no verifiable active tag Ruleset"):
            verify_active_immutable_tag_ruleset(REPOSITORY, FakeGitHub(ruleset=ruleset).get)


if __name__ == "__main__":
    unittest.main()
