import re
import unittest
from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "build-release.yml"


def job_section(source, name):
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [A-Za-z_][A-Za-z0-9_-]*:\n|\Z)",
        source,
    )
    if not match:
        raise AssertionError(f"workflow job {name!r} is missing")
    return match.group(1)


class ReleaseWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")

    def test_failed_or_cancelled_validation_cannot_publish(self):
        publish = job_section(self.source, "publish")
        self.assertIn("needs: build_validate", publish)
        self.assertIn("if: $" + "{{ needs.build_validate.result == 'success' }}", publish)
        self.assertNotIn("always()", publish)

    def test_annotated_or_lightweight_tag_is_peeled_and_commit_rechecked(self):
        build = job_section(self.source, "build_validate")
        publish = job_section(self.source, "publish")
        resolver = 'git rev-parse --verify "$GITHUB_REF^{commit}"'
        self.assertEqual(self.source.count(resolver), 2)
        self.assertIn('[[ "$tag_commit" == "$checkout_commit" ]]', build)
        self.assertIn('[[ "$tag_commit" == "$GITHUB_SHA" ]]', build)
        self.assertIn('[[ "$tag_commit" == "$EXPECTED_BUILD_SHA" ]]', publish)

    def test_tests_compile_and_package_validation_precede_upload(self):
        build = job_section(self.source, "build_validate")
        tests = build.index("Run offline unit tests")
        self.assertIn("scripts/run_release_tests.py", build)
        self.assertIn("python3-pyqt6 rsync", build)
        compile_step = build.index("Compile Python")
        build_step = build.index("Build Debian source and binary packages")
        gate_step = build.index("Validate package identity and checksums")
        upload_step = build.index("actions/upload-artifact")
        self.assertLess(tests, compile_step)
        self.assertLess(compile_step, build_step)
        self.assertLess(build_step, gate_step)
        self.assertLess(gate_step, upload_step)
        self.assertNotIn("continue-on-error: true", build)

    def test_write_permission_is_limited_to_publish_job(self):
        self.assertRegex(self.source, r"(?m)^permissions:\n  contents: read$")
        build = job_section(self.source, "build_validate")
        publish = job_section(self.source, "publish")
        self.assertIn("contents: read", build)
        self.assertIn("contents: write", publish)
        self.assertNotIn("write-all", self.source)

    def test_release_is_created_new_as_draft_and_published_last(self):
        publish = job_section(self.source, "publish")
        build = job_section(self.source, "build_validate")
        self.assertIn("draft=true", publish)
        self.assertIn("draft=false", publish)
        self.assertIn("if-no-files-found: error", build)
        self.assertLess(publish.index("draft=true"), publish.index("draft=false"))
        self.assertNotIn("softprops/action-gh-release", publish)
        self.assertNotIn("|| true", self.source)

    def test_assets_use_the_release_upload_url_as_raw_binary(self):
        publish = job_section(self.source, "publish")
        self.assertIn(".upload_url", publish)
        self.assertIn("curl --fail-with-body", publish)
        self.assertIn("--data-binary", publish)
        self.assertNotIn('releases/$release_id/assets?name=', publish)


if __name__ == "__main__":
    unittest.main()
