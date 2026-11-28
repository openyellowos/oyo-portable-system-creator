#!/usr/bin/env python3
"""Fail-closed validation for tagged Debian release candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


PACKAGE = "oyo-portable-system-creator"
TAG_RE = re.compile(r"^v((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))$")
SHA_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
FIELD_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9-]*):[ \t]*(.*)$")


class GateError(ValueError):
    """A release candidate failed a required validation."""


def parse_control(path: Path) -> dict[str, str]:
    fields: dict[str, str] = {}
    current: str | None = None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise GateError(f"cannot read control file {path}: {exc}") from exc
    for line in lines:
        if not line:
            if fields:
                break
            continue
        if line[0] in " \t":
            if current is None:
                raise GateError(f"orphan continuation line in {path}")
            fields[current] = (
                fields[current] + "\n" + line[1:]
                if fields[current]
                else line[1:]
            )
            continue
        match = FIELD_RE.match(line)
        if not match:
            raise GateError(f"invalid control field in {path}: {line!r}")
        key, value = match.groups()
        key = key.lower()
        if key in fields:
            raise GateError(f"duplicate {key} field in {path}")
        fields[key] = value
        current = key
    return fields


def required(fields: dict[str, str], key: str, path: Path) -> str:
    value = fields.get(key.lower(), "").strip()
    if not value:
        raise GateError(f"missing {key} field in {path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_checksums(fields: dict[str, str], path: Path) -> dict[str, tuple[str, int]]:
    raw = fields.get("checksums-sha256", "")
    if not raw:
        raise GateError(f"missing Checksums-Sha256 field in {path}")
    result: dict[str, tuple[str, int]] = {}
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) != 3:
            raise GateError(f"invalid Checksums-Sha256 entry in {path}: {line!r}")
        digest, size_text, name = parts
        if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise GateError(f"invalid SHA-256 in {path}: {digest!r}")
        if Path(name).name != name or name in ("", ".", ".."):
            raise GateError(f"unsafe filename in {path}: {name!r}")
        try:
            size = int(size_text)
        except ValueError as exc:
            raise GateError(f"invalid file size in {path}: {size_text!r}") from exc
        if size < 0 or name in result:
            raise GateError(f"invalid or duplicate checksum entry in {path}: {name}")
        result[name] = (digest.lower(), size)
    return result


def verify_checksums(
    directory: Path,
    checksums: dict[str, tuple[str, int]],
    expected_names: set[str],
    source: Path,
) -> None:
    if set(checksums) != expected_names:
        missing = sorted(expected_names - set(checksums))
        extra = sorted(set(checksums) - expected_names)
        raise GateError(
            f"checksum file list mismatch in {source}: missing={missing}, extra={extra}"
        )
    for name, (digest, size) in checksums.items():
        path = directory / name
        if not path.is_file() or path.is_symlink():
            raise GateError(f"checksum references missing/non-regular file: {name}")
        if path.stat().st_size != size or sha256(path) != digest:
            raise GateError(f"size or SHA-256 mismatch for {name} (from {source.name})")


def release_version(tag: str) -> str:
    match = TAG_RE.fullmatch(tag)
    if not match:
        raise GateError(f"tag must be vMAJOR.MINOR.PATCH, got {tag!r}")
    return match.group(1)


def ensure_no_version_collision(version: str, release_tags: list[str]) -> None:
    for tag in release_tags:
        normalized = tag.strip()
        if normalized.startswith("v"):
            normalized = normalized[1:]
        if normalized == version:
            raise GateError(f"version {version} already has a published GitHub Release")


def changelog_identity(path: Path) -> tuple[str, str, str]:
    try:
        first = path.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, UnicodeError, IndexError) as exc:
        raise GateError(f"cannot read top Debian changelog entry: {exc}") from exc
    match = re.fullmatch(
        r"([A-Za-z0-9][A-Za-z0-9+.-]*) \(([^()\s]+)\) ([^;]+); urgency=([^\s;]+)",
        first,
    )
    if not match:
        raise GateError(f"invalid first debian/changelog line: {first!r}")
    package, version, distribution, _urgency = match.groups()
    if any(word.upper() == "UNRELEASED" for word in distribution.replace(",", " ").split()):
        raise GateError("top changelog distribution is UNRELEASED")
    return package, version, distribution.strip()


def ensure_released_distribution(value: str, path: Path) -> str:
    distribution = value.strip()
    if not distribution:
        raise GateError(f"missing Distribution in {path}")
    if any(word.upper() == "UNRELEASED" for word in distribution.replace(",", " ").split()):
        raise GateError(f"{path.name} distribution is UNRELEASED")
    return distribution


def find_one(paths: list[Path], description: str) -> Path:
    if len(paths) != 1:
        raise GateError(f"expected exactly one {description}, found {len(paths)}")
    path = paths[0]
    if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
        raise GateError(f"{description} is empty or not a regular file: {path.name}")
    return path


def expected_assets(directory: Path, version: str) -> list[Path]:
    files = list(directory.iterdir())
    if any(not path.is_file() or path.is_symlink() for path in files):
        raise GateError("artifact directory must contain only regular files")
    prefix = f"{PACKAGE}_{version}"
    deb = find_one(
        [p for p in files if p.name.startswith(prefix + "_") and p.name.endswith(".deb")],
        ".deb",
    )
    changes = find_one(
        [p for p in files if p.name.startswith(prefix + "_") and p.name.endswith(".changes")],
        ".changes",
    )
    buildinfo = find_one(
        [p for p in files if p.name.startswith(prefix + "_") and p.name.endswith(".buildinfo")],
        ".buildinfo",
    )
    dsc = find_one([p for p in files if p.name == prefix + ".dsc"], ".dsc")
    source_tar = find_one(
        [p for p in files if re.fullmatch(re.escape(prefix) + r"\.tar\.[A-Za-z0-9]+", p.name)],
        "native source tarball",
    )
    expected = {deb.name, changes.name, buildinfo.name, dsc.name, source_tar.name}
    actual = {p.name for p in files}
    if actual != expected:
        raise GateError(
            f"artifact directory has unexpected/missing files: "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )
    return [deb, changes, buildinfo, dsc, source_tar]


def validate(
    *,
    tag: str,
    tag_commit: str,
    checkout_commit: str,
    changelog: Path,
    artifacts: Path,
    manifest_output: Path | None = None,
    manifest: Path | None = None,
    release_tags_file: Path | None = None,
) -> dict[str, object]:
    version = release_version(tag)
    if not SHA_RE.fullmatch(tag_commit) or not SHA_RE.fullmatch(checkout_commit):
        raise GateError("tag and checkout commits must be full 40- or 64-character lowercase SHAs")
    if tag_commit != checkout_commit:
        raise GateError(f"tag/checkout commit mismatch: tag={tag_commit}, checkout={checkout_commit}")
    if release_tags_file is not None:
        try:
            tags = release_tags_file.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            raise GateError(f"cannot read existing release tags: {exc}") from exc
        ensure_no_version_collision(version, tags)

    changelog_package, changelog_version, changelog_distribution = changelog_identity(changelog)
    if changelog_package != PACKAGE:
        raise GateError(f"changelog package mismatch: expected {PACKAGE}, got {changelog_package}")
    if changelog_version != version:
        raise GateError(
            f"tag/changelog version mismatch: tag={version}, changelog={changelog_version}"
        )

    if not artifacts.is_dir() or artifacts.is_symlink():
        raise GateError(f"artifact directory is missing or invalid: {artifacts}")
    deb, changes, buildinfo, dsc, source_tar = expected_assets(artifacts, version)

    try:
        deb_output = subprocess.run(
            ["dpkg-deb", "--field", str(deb), "Package", "Version", "Architecture"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GateError(f"cannot inspect .deb metadata: {exc}") from exc
    deb_fields: dict[str, str] = {}
    for line in deb_output:
        key, separator, value = line.partition(":")
        if not separator or key.lower() not in ("package", "version", "architecture"):
            raise GateError(f"unexpected dpkg-deb metadata output for {deb.name}: {line!r}")
        deb_fields[key.lower()] = value.strip()
    if set(deb_fields) != {"package", "version", "architecture"}:
        raise GateError(f"unexpected dpkg-deb metadata output for {deb.name}")
    deb_package, deb_version, deb_arch = (
        deb_fields["package"],
        deb_fields["version"],
        deb_fields["architecture"],
    )
    if deb_package != PACKAGE or deb_version != version:
        raise GateError(
            f".deb identity mismatch: package={deb_package}, version={deb_version}"
        )
    if deb.name != f"{PACKAGE}_{version}_{deb_arch}.deb":
        raise GateError(f".deb filename does not match its metadata: {deb.name}")

    changes_fields = parse_control(changes)
    dsc_fields = parse_control(dsc)
    changes_distribution = ensure_released_distribution(
        required(changes_fields, "Distribution", changes), changes
    )
    if changes_distribution != changelog_distribution:
        raise GateError(
            f"changelog/.changes distribution mismatch: "
            f"{changelog_distribution!r} != {changes_distribution!r}"
        )
    for path, fields in ((changes, changes_fields), (dsc, dsc_fields)):
        artifact_version = required(fields, "Version", path)
        if artifact_version != version:
            raise GateError(
                f"tag/artifact version mismatch in {path.name}: {artifact_version}"
            )
        source = required(fields, "Source", path).split(" ", 1)[0]
        if source != PACKAGE:
            raise GateError(f"source package mismatch in {path.name}: {source}")

    change_checksums = parse_checksums(changes_fields, changes)
    verify_checksums(
        artifacts,
        change_checksums,
        {deb.name, buildinfo.name, dsc.name, source_tar.name},
        changes,
    )
    dsc_checksums = parse_checksums(dsc_fields, dsc)
    verify_checksums(artifacts, dsc_checksums, {source_tar.name}, dsc)

    result: dict[str, object] = {
        "schema": 1,
        "tag": tag,
        "version": version,
        "commit": tag_commit,
        "assets": [
            {"name": path.name, "sha256": sha256(path), "size": path.stat().st_size}
            for path in sorted((deb, changes, buildinfo, dsc, source_tar), key=lambda p: p.name)
        ],
    }
    if manifest is not None:
        try:
            saved = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise GateError(f"cannot read artifact manifest: {exc}") from exc
        if saved != result:
            raise GateError("artifact manifest identity, filenames, sizes, or SHA-256 values differ")
    if manifest_output is not None:
        manifest_output.parent.mkdir(parents=True, exist_ok=True)
        manifest_output.write_text(
            json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--tag-commit", required=True)
    parser.add_argument("--checkout-commit", required=True)
    parser.add_argument("--changelog", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--release-tags-file", type=Path)
    args = parser.parse_args()
    try:
        result = validate(
            tag=args.tag,
            tag_commit=args.tag_commit,
            checkout_commit=args.checkout_commit,
            changelog=args.changelog,
            artifacts=args.artifacts,
            manifest_output=args.manifest_output,
            manifest=args.manifest,
            release_tags_file=args.release_tags_file,
        )
    except GateError as exc:
        print(f"release gate failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"release gate passed: tag={result['tag']} version={result['version']} "
        f"commit={result['commit']} assets={len(result['assets'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
