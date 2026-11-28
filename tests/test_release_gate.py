import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from release_gate import GateError, ensure_no_version_collision, validate


PACKAGE = "oyo-portable-system-creator"
COMMIT = "f62580c1ed4c78c25058db000a843d0077b7e53e"
VERSION = "0.6.3"


def checksum_line(path):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f" {digest} {path.stat().st_size} {path.name}"


class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.changelog = self.root / "debian" / "changelog"
        self.changelog.parent.mkdir()
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir()
        self.write_changelog("unstable")
        self.create_artifacts()

    def tearDown(self):
        self.temp.cleanup()

    def write_changelog(self, distribution):
        self.changelog.write_text(
            f"{PACKAGE} ({VERSION}) {distribution}; urgency=medium\n\n"
            "  * Release candidate test fixture\n\n"
            " -- OYO Team <devnull@example.com>  Sat, 28 Nov 2026 00:00:00 +0000\n",
            encoding="utf-8",
        )

    def create_artifacts(self):
        package_root = self.root / "deb-root"
        control_dir = package_root / "DEBIAN"
        control_dir.mkdir(parents=True)
        (control_dir / "control").write_text(
            f"Package: {PACKAGE}\nVersion: {VERSION}\nArchitecture: all\n"
            "Maintainer: OYO Team <devnull@example.com>\nDescription: fixture\n",
            encoding="utf-8",
        )
        deb = self.artifacts / f"{PACKAGE}_{VERSION}_all.deb"
        subprocess.run(["dpkg-deb", "--build", str(package_root), str(deb)], check=True, capture_output=True)

        buildinfo = self.artifacts / f"{PACKAGE}_{VERSION}_amd64.buildinfo"
        buildinfo.write_text("Build fixture\n", encoding="utf-8")
        source_tar = self.artifacts / f"{PACKAGE}_{VERSION}.tar.xz"
        source_tar.write_bytes(b"source fixture bytes\n")
        dsc = self.artifacts / f"{PACKAGE}_{VERSION}.dsc"
        dsc.write_text(
            f"Format: 3.0 (native)\nSource: {PACKAGE}\nBinary: {PACKAGE}\n"
            f"Architecture: all\nVersion: {VERSION}\n"
            "Maintainer: OYO Team <devnull@example.com>\n"
            "Checksums-Sha256:\n" + checksum_line(source_tar) + "\n",
            encoding="utf-8",
        )
        changes = self.artifacts / f"{PACKAGE}_{VERSION}_amd64.changes"
        entries = "\n".join(checksum_line(path) for path in (deb, buildinfo, dsc, source_tar))
        changes.write_text(
            f"Format: 1.8\nSource: {PACKAGE}\nBinary: {PACKAGE}\n"
            f"Architecture: source all\nVersion: {VERSION}\nDistribution: unstable\n"
            "Urgency: medium\nMaintainer: OYO Team <devnull@example.com>\n"
            "Description: fixture\n"
            "Checksums-Sha256:\n" + entries + "\n",
            encoding="utf-8",
        )

    def validate_candidate(self, **overrides):
        options = {
            "tag": f"v{VERSION}",
            "tag_commit": COMMIT,
            "checkout_commit": COMMIT,
            "changelog": self.changelog,
            "artifacts": self.artifacts,
        }
        options.update(overrides)
        return validate(**options)

    def test_valid_candidate_and_manifest_round_trip(self):
        manifest = self.root / "manifest.json"
        result = self.validate_candidate(manifest_output=manifest)
        self.assertEqual(result["version"], VERSION)
        self.assertEqual(len(result["assets"]), 5)
        self.validate_candidate(manifest=manifest)

    def test_tag_and_changelog_version_must_match(self):
        with self.assertRaisesRegex(GateError, "tag/changelog version mismatch"):
            self.validate_candidate(tag="v0.6.4")

    def test_deb_and_changes_versions_must_match_tag(self):
        changes = next(self.artifacts.glob("*.changes"))
        changes.write_text(changes.read_text().replace(f"Version: {VERSION}", "Version: 0.6.4"))
        with self.assertRaisesRegex(GateError, "tag/artifact version mismatch"):
            self.validate_candidate()

    def test_deb_version_must_match_changes_version(self):
        deb = next(self.artifacts.glob("*.deb"))
        extracted = self.root / "deb-mismatch"
        subprocess.run(["dpkg-deb", "--extract", str(deb), str(extracted)], check=True, capture_output=True)
        control = extracted / "DEBIAN" / "control"
        control.parent.mkdir(exist_ok=True)
        control.write_text(
            f"Package: {PACKAGE}\nVersion: 0.6.4\nArchitecture: all\n"
            "Maintainer: OYO Team <devnull@example.com>\nDescription: fixture\n",
            encoding="utf-8",
        )
        deb.unlink()
        subprocess.run(["dpkg-deb", "--build", str(extracted), str(deb)], check=True, capture_output=True)
        with self.assertRaisesRegex(GateError, ".deb identity mismatch"):
            self.validate_candidate()

    def test_dsc_version_must_match_tag(self):
        dsc = next(self.artifacts.glob("*.dsc"))
        dsc.write_text(dsc.read_text().replace(f"Version: {VERSION}", "Version: 0.6.4"))
        with self.assertRaisesRegex(GateError, "tag/artifact version mismatch"):
            self.validate_candidate()

    def test_missing_required_artifact_fails(self):
        next(self.artifacts.glob("*.dsc")).unlink()
        with self.assertRaisesRegex(GateError, "exactly one .dsc"):
            self.validate_candidate()

    def test_artifact_content_hash_mismatch_fails(self):
        next(self.artifacts.glob("*.tar.xz")).write_bytes(b"tampered source fixture\n")
        with self.assertRaisesRegex(GateError, "mismatch"):
            self.validate_candidate()

    def test_unreleased_changelog_fails(self):
        self.write_changelog("UNRELEASED")
        with self.assertRaisesRegex(GateError, "UNRELEASED"):
            self.validate_candidate()

    def test_unreleased_changes_distribution_fails(self):
        changes = next(self.artifacts.glob("*.changes"))
        changes.write_text(changes.read_text().replace("Distribution: unstable", "Distribution: UNRELEASED"))
        with self.assertRaisesRegex(GateError, "UNRELEASED"):
            self.validate_candidate()

    def test_unexpected_or_unknown_commit_fails(self):
        with self.assertRaisesRegex(GateError, "commit mismatch"):
            self.validate_candidate(checkout_commit="a" * 40)
        with self.assertRaisesRegex(GateError, "full 40- or 64-character"):
            self.validate_candidate(tag_commit="unknown")

    def test_existing_release_version_collision_fails(self):
        release_tags = self.root / "release-tags.txt"
        release_tags.write_text(f"v0.6.2\nv{VERSION}\n", encoding="utf-8")
        with self.assertRaisesRegex(GateError, "already has a published"):
            self.validate_candidate(release_tags_file=release_tags)
        with self.assertRaisesRegex(GateError, "already has a published"):
            ensure_no_version_collision(VERSION, ["v0.6.2", f"v{VERSION}"])
        with self.assertRaisesRegex(GateError, "already has a published"):
            ensure_no_version_collision("0.6.2", ["v0.6.2"])
        ensure_no_version_collision(VERSION, ["v0.6.2", "v0.6.4"])

    def test_unexpected_stale_artifact_fails(self):
        (self.artifacts / f"{PACKAGE}_0.6.2_amd64.deb").write_bytes(b"old package")
        with self.assertRaisesRegex(GateError, "unexpected/missing files"):
            self.validate_candidate()

    def test_manifest_hash_change_fails(self):
        manifest = self.root / "manifest.json"
        self.validate_candidate(manifest_output=manifest)
        recorded = json.loads(manifest.read_text())
        recorded["assets"][0]["sha256"] = "0" * 64
        manifest.write_text(json.dumps(recorded))
        with self.assertRaisesRegex(GateError, "manifest identity"):
            self.validate_candidate(manifest=manifest)


if __name__ == "__main__":
    unittest.main()
