## ADDED Requirements

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
