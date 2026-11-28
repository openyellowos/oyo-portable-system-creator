# Debian release workflow

The tag-triggered workflow is fail-closed. Before creating a Release it resolves
the pushed tag to a full commit SHA, checks out and tests that exact commit,
builds the Debian source and binary packages, and validates the tag/changelog/
package versions, release distribution, required files, and embedded SHA-256
lists. The publisher then resolves the tag again, verifies the candidate
manifest and files, rejects an existing Release for the version, creates a new
draft Release, checks the uploaded asset digests, and only then publishes it.

For a future release, maintainers must first prepare a new Debian changelog
version matching a vMAJOR.MINOR.PATCH tag and replace UNRELEASED with the
intended distribution. A previously released version is rejected. This change
does not update the current changelog or authorize a release.

The workflow runs the offline unit suite and Python compile check before
building. The release test runner rejects test failures, skipped tests, and a
suite that discovers no tests; `rsync` is installed so the existing rsync-backed
test is not silently skipped. Hardware, USB, and block-device operations are
not part of the release test job. A failure leaves publication unreachable; a
failure after draft creation leaves the Release unpublished for manual review.
