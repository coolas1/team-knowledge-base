# container-build Specification

## Purpose

Defines the properties of the container image build: a minimal, allowlisted build context; a pinned toolchain obtained without build-time network fetches; and layer ordering that keeps expensive layers cached across source edits.

## Requirements

### Requirement: Build context is allowlisted
The image build context SHALL include only the inputs the build recipe copies into the image (application source, configuration, and dependency lock inputs). All other content — version-control data, tests, benchmark corpora, uploads, caches, dependency trees, build outputs, and Python bytecode — SHALL be excluded from the context, and the exclusion SHALL fail closed: a path must be explicitly allowlisted to enter the context, so newly added directories never silently inflate it.

#### Scenario: Context contains only build inputs
- **WHEN** the build context is assembled
- **THEN** it contains the application source tree, configuration, and lock inputs, and its size is on the order of a few megabytes

#### Scenario: New untracked directory appears
- **WHEN** a new large directory is created in the repository
- **THEN** it is excluded from the build context without any new ignore rule

#### Scenario: Host bytecode stays out
- **WHEN** the source tree contains `__pycache__` directories or compiled `.pyc` files from local runs
- **THEN** none of them enter the image build context or the final image

### Requirement: Toolchain installs without build-time network fetches
The build recipe SHALL obtain the package manager (uv) from a digest-pinned official image reference rather than downloading an installer at build time. No build step SHALL depend on fetching install scripts from the open internet.

#### Scenario: Toolchain layer builds offline
- **WHEN** the image builds without general internet access but with the pinned image available in local storage
- **THEN** the uv toolchain layer builds successfully from cache

#### Scenario: Toolchain is bit-stable
- **WHEN** the same digest-pinned reference is used across builds
- **THEN** the uv binary is identical without relying on a mutable upstream installer URL

### Requirement: Layers are ordered by change frequency
Image layers SHALL be ordered so that the most frequently changed inputs are copied last: system packages, then toolchain, then dependency installation (from lock inputs only), then the SPA build (from client source only), then application source. A change to application source SHALL NOT invalidate the dependency or SPA layers, and a change to SPA client source SHALL NOT invalidate the Python dependency layers.

#### Scenario: Python-only edit
- **WHEN** only a Python file under the application source changes
- **THEN** the dependency and SPA build layers are cache hits and only the final copy layers re-run

#### Scenario: SPA-only edit
- **WHEN** only SPA client source changes
- **THEN** the Python dependency layer is a cache hit

#### Scenario: Dependency edit
- **WHEN** the lock inputs change
- **THEN** the dependency layer re-runs but system and toolchain layers remain cached

### Requirement: Built artifacts survive the source overlay
The final application-source copy SHALL NOT overwrite build artifacts produced in earlier layers (the built SPA output), and the build recipe SHALL NOT leave build-only dependencies (SPA `node_modules`) in the final image.

#### Scenario: SPA output survives the overlay
- **WHEN** the final source copy executes after the SPA build layer
- **THEN** the built SPA output remains the served artifact, because build outputs are excluded from the copy context

#### Scenario: No node_modules in the image
- **WHEN** the image build completes
- **THEN** the SPA build's `node_modules` are absent from the final image, having been installed and removed within a single layer

### Requirement: Dependency fetches use configurable registries

The image build SHALL direct its package-manager fetches at registry endpoints
that are configurable at build time, and SHALL NOT depend on the build host
reaching an upstream registry such as `registry.npmjs.org` directly. With no
explicit configuration the build SHALL fetch installable packages from a
regional mirror, so that a build on a host with an unreliable route to the open
internet still completes.

The fetch used for security metadata SHALL be configurable independently of the
install fetch and SHALL default to the authoritative registry rather than a
mirror, because mirrors do not uniformly implement the audit API and the
security gate fails closed. A build SHALL NOT be blocked by a mirror's inability
to serve security metadata.

Any proxy used for build-time fetches SHALL be opt-in: unset by default, and
applied only when explicitly configured for a local build.

Registry configuration SHALL participate in layer-cache invalidation, so that a
build whose source and registry configuration are unchanged reuses the cached
dependency layer.

#### Scenario: Build with default registry configuration

- **WHEN** the image builds with no registry arguments supplied
- **THEN** installable packages are fetched from the configured regional mirror and the build does not contact the upstream registry for them

#### Scenario: Mirror cannot serve security metadata

- **WHEN** the install registry is a mirror that does not implement the audit API
- **THEN** the security check still runs against the authoritative registry and reports its result, instead of failing on a registry error

#### Scenario: No proxy configured

- **WHEN** no proxy is configured for the build
- **THEN** package fetches are attempted without a proxy

#### Scenario: Proxy explicitly configured

- **WHEN** a proxy is configured for a local build
- **THEN** build-time package fetches use it

#### Scenario: Unchanged registry configuration keeps the dependency layer cached

- **WHEN** an image rebuilds with unchanged source and unchanged registry configuration
- **THEN** the dependency layer is a cache hit and is not re-fetched

#### Scenario: Registry configuration change invalidates the dependency layer

- **WHEN** an image rebuilds with a changed registry configuration
- **THEN** the dependency layer is rebuilt rather than reused
