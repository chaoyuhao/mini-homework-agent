# Changelog

## Unreleased

- Default to one grading worker and five-second Canvas request spacing.
- Respect full `Retry-After` delays; preserve HTTP status and stop on Canvas 401/403.
- Resume failed/incomplete downloads with saved instructions and attachment reuse, preserving drafts and human reviews.
- Add a local UI retry action and guard against overlapping recovery and writeback during grading.
- Request file bytes instead of JSON at download URLs, stop redirect cycles, and verify download sizes.
- Recover looping downloads through Canvas's authorized signed-file URL endpoint.
- Support project-only direct networking for VPN connections without changing system proxies or TLS checks.
- Read RAR Python/Word submissions in memory through optional system libarchive, with size and entry limits.
- Give specific JSON delimiter/escaping repair instructions after malformed model output.
- Allow a different configured model for failed items while preserving completed drafts and recording the actual model per result.

## 0.2.0

- Initial public release of mini-homework-agent for Canvas LMS and Fudan eLearning.
- Reuse the mini-swe-agent 2.4.6 core loop with homework-only tools and a three-step limit.
- Accept required Markdown assignment instructions and explicit scoring tables.
- Generate validated scores, evidence, feedback, and an auditable trajectory.
- Review and edit drafts locally before explicit Canvas writeback.
- Include a credentials-free offline demo, local-file grading, and provider-neutral configuration.
- Preserve read-only diagnostics, bounded attachment extraction, snapshot checks, and uncertain-write reconciliation.
