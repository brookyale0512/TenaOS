# Risk Register

This register captures the most important remaining risks between the current implementation and the roadmap's target operating model.

## High Risks

### OpenMRS routing policy is still adapter-owned

- Risk:
  Queue, queue-room, queue-room-provider, service-level pricing, and stock-rule changes are now applied live through supported OpenMRS module handlers, but routing rules still live in an adapter-owned policy layer because the Queue module has no native persistence resource for durable route-policy definitions.
- Impact:
  End-to-end workflow editing is much closer to real runtime behavior, but routing still depends on control-plane policy semantics instead of native Queue-module metadata.
- Mitigation:
  Add an explicit runtime consumer/contract for route-policy files and document operator expectations around native vs adapter-owned workflow state.

### Verification runner is real but still partial

- Risk:
  `verification-plan.json` is now executed during `apply-change --run-verify` alongside `scripts/verify-backend.sh`, including OpenMRS runtime bundle checks and bundle-aware rollback verification, but some endpoint/auth checks are still delegated to the smoke script.
- Impact:
  Runtime confidence is much stronger than before, but verification is not yet a fully standalone product-specific validation service.
- Mitigation:
  Keep expanding the verification runner so fewer checks depend on delegated smoke coverage and more restored-state checks can run without the shared smoke wrapper.

### Conversational setup layer is not yet a true agent runtime

- Risk:
  The backend supports interview sections and bundle generation, but not a stateful natural-language conversational orchestration engine.
- Impact:
  The "configure by conversation in under an hour" success criterion is not yet demonstrated.
- Mitigation:
  Add a session engine that maps user dialogue into `ClinicConfigModel` deltas with approval and replay support.

## Medium Risks

### Concepts and concept sets are referenced more than generated

- Risk:
  The system now generates bounded local concept/concept-set packs for OpenMRS, but still relies heavily on existing CIEL references for broader terminology coverage.
- Impact:
  Some local workflow semantics may still require manual concept preparation.
- Mitigation:
  Extend the config model and OpenMRS compiler with bounded local terminology-pack support.

### Configuration repository boundary is still thin

- Risk:
  Control-plane state exists, but there is no dedicated versioned config repository service outside the source tree.
- Impact:
  Multi-operator governance and promotion across environments will be weaker than intended.
- Mitigation:
  Introduce a standalone config store with bundle versioning, promotion metadata, and history browsing.

### Hospital composition does not yet resolve advanced pack conflicts

- Risk:
  A reusable department-pack library and composition engine now exist, but advanced conflict resolution and multi-department orchestration are still missing.
- Impact:
  Scaling to many facility types is improved, but shared-resource contention and pack interaction rules may still need manual design.
- Mitigation:
  Extend the composition engine with resource-conflict checks, pack compatibility rules, and orchestration policy templates.

## Lower Risks

### Operator UX is file-oriented

- Risk:
  Operators currently consume JSON, Mermaid, and CLI outputs instead of a dedicated admin UX.
- Impact:
  The backend is usable, but not yet friendly for broad non-technical deployment.
- Mitigation:
  Build a decoupled frontend later on top of the existing control-plane artifacts and APIs.

### Runtime surface coverage may drift from shipped images

- Risk:
  The capability matrix is generated from the current repo/runtime artifacts and may drift as the distro evolves.
- Impact:
  A future image change could silently invalidate assumptions.
- Mitigation:
  Keep inventory generation in CI and compare module/runtime changes over time.
