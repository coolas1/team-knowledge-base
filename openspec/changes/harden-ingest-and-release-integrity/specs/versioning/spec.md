## ADDED Requirements

### Requirement: Release version propagates to the integration branch

After a release bump lands on the release branch, the same version SHALL be
merged back into the integration branch before the next release is cut, so
that a branch carrying newer content never reports an older semantic version
and the release branch remains an ancestor of the integration branch.

#### Scenario: Version order matches content order

- **WHEN** the integration branch carries every commit the release branch
  carries, and additional commits beyond them
- **THEN** the integration branch reports a version greater than or equal to
  the release branch's version

#### Scenario: Next release can fast-forward

- **WHEN** the next release merges the integration branch into the release
  branch
- **THEN** the merge succeeds without the previous release's version bump
  introducing a conflict

#### Scenario: Release-only fixes reach production

- **WHEN** a fix has been merged to the integration branch and the release
  branch has been resynced and re-released
- **THEN** the deployed production instance is built from a commit that
  contains that fix
