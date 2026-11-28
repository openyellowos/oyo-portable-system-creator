# Debian release workflow

The tag-triggered workflow is fail-closed. It resolves the pushed tag to a full
commit SHA, checks out and tests that exact commit, builds the Debian source
and binary packages, and validates tag/changelog/package versions, distribution,
required files, and embedded SHA-256 lists. The publisher verifies the
transferred candidate and then reads the live GitHub tag ref and its peeled
commit from the Git Data API before creating a Release and again immediately
before publishing. Missing refs, API errors, unsupported objects, or any SHA
mismatch stop the job.

Automatic publication also requires an active GitHub tag Ruleset covering
`refs/tags/v*`, with both `update` and `deletion` restrictions and no bypass
actors. The workflow lists repository and inherited rulesets (including parent
rulesets), fetches their details, and requires that policy before creating the
Draft Release and immediately before changing it from draft to published.
An empty result, inaccessible ruleset details, incomplete scope, disabled or
evaluate enforcement, missing restrictions, bypass actors, or API failure is a
hard failure. GitHub's API omits `bypass_actors` unless the caller has write
access to the Ruleset, so the publisher requires a separate
`RELEASE_RULESET_AUDIT_TOKEN` secret. It must be a repository-scoped,
read-only fine-grained token with Metadata: read, issued to an identity that
has write access to this Ruleset (typically repository admin); the workflow
rejects an absent token or an API response that omits the bypass list. The
token is used only for Ruleset inspection. Release writes continue to use the
separately scoped `GITHUB_TOKEN`; the audit token is not granted Administration:
write, contents write, or Ruleset-management capability.

No GitHub tag Ruleset is currently visible, so automatic Release
creation/publication is blocked. A verified audit token is also required; the
workflow refuses to continue if it is absent or cannot reveal the bypass list.
These are external prerequisites; the workflow does not create/change GitHub
settings or secrets.

The Ruleset protects the tag ref from ordinary updates and deletion while the
workflow runs; API rechecks alone cannot close a time-of-check/time-of-use gap.
GitHub does not provide an atomic “publish this Release only if this tag still
resolves to SHA X” operation. Therefore the remaining trust boundary is
privileged repository/organization administrators: an administrator able to
disable/remove the Ruleset or bypass governance can still invalidate the
assumption after a check. The checks reduce this to a GitHub settings/privileged
actor risk rather than claiming an application-level lock. Do not configure
bypass actors for the release tag Ruleset.

The publisher verifies the candidate manifest and asset digests, rejects an
existing Release for the version, creates a new Draft Release, verifies uploaded
digests, rechecks the live tag and Ruleset, and only then publishes it. Any
failure after Draft creation leaves the Release unpublished for manual review.

For a future release, maintainers must first prepare a new Debian changelog
version matching a vMAJOR.MINOR.PATCH tag and replace UNRELEASED with the
intended distribution. A previously released version is rejected. Before the
first Release attempt, a repository administrator must add an **Active** tag
Ruleset with target `tag`, include pattern `refs/tags/v*`, no exclusions,
`Restrict updates`, `Restrict deletions`, and no bypass actors. The required
settings are inspected at runtime and missing/inaccessible settings stop the
workflow. The `RELEASE_RULESET_AUDIT_TOKEN` secret must separately be provisioned
as described above; inability to see the bypass list blocks the workflow.
Creating/changing the Ruleset or adding the secret requires separate approval
and is outside this change. This change does not update the current changelog
or authorize a release.

The workflow runs the offline unit suite and Python compile check before
building. The release test runner rejects test failures, skipped tests, and a
suite that discovers no tests; `rsync` is installed so the existing rsync-backed
test is not silently skipped. Hardware, USB, and block-device operations are
not part of the release test job. A failure leaves publication unreachable; a
failure after draft creation leaves the Release unpublished for manual review.
