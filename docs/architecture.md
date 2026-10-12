# A small homework agent

The system separates the grading loop from LMS updates. This keeps reviewable drafts useful even without Canvas or a particular model provider.

| Component | Role |
| --- | --- |
| `grader/requirements.py` | Validate teacher Markdown; parse an explicit points table |
| `grader/canvas.py` | Read account-accessible Canvas resources; perform explicit reviewed updates |
| `grader/materials.py` | Bounded, read-only extraction of supported student files |
| `grader/agent.py` | OpenAI-compatible model adapter, homework-only environment, mini-swe-agent subclass |
| `grader/_vendor/mini_swe_agent/default.py` | Actual upstream `DefaultAgent` linear loop |
| `grader/workflow.py` | Eligible submissions, parallel drafting, cache, review records, guarded writeback |
| `grader/web.py` | Local review API with same-origin request checks |
| `grader/index.html` | Requirements input, scoring evidence, editing, explicit review/publish actions |

The loop starts with the assignment requirements, rubric, and submission text. A model can submit grade JSON immediately, request a bounded text excerpt, or ask for AST syntax information about an exact original snippet. The environment validates the result and returns errors as observations for repair. A grade must match every scoring item, provide evidence, and stay within each item's points. The program sums the score. There are at most three model calls per submission.

The model adapter serializes only its model name. It does not serialize provider or Canvas credentials. A trajectory preserves the linear message/tool history for auditing; because it contains homework text, it is private course data.

The workflow chooses a rubric in this order: explicit custom rubric, teacher Markdown table, Canvas rubric, provisional criteria. Missing requirements fail before constructing a Canvas client. Provisional scoring is labeled for human review.

Teacher review is separate from the loop. The environment cannot approve or publish. The publication path acquires a process-shared file lock, rechecks the assignment and submission snapshot, records the in-progress state, and sends one Canvas PUT. Optional student feedback is separate from internal reasons and evidence. Empty feedback omits the comment payload entirely, and nonempty feedback includes only the reviewed problem text. No technical marker is appended. Uncertain new requests remain pending for human inspection after reconciliation; older marker-based records can still be confirmed. Neither path retries blindly.

The framework adaptation is intentionally small. The vendored core contains only `DefaultAgent`, exceptions, and merge utilities; the local protocol module removes upstream global configuration side effects. The exact upstream commit and modifications are recorded in [UPSTREAM.md](../grader/_vendor/mini_swe_agent/UPSTREAM.md). The project does not claim to be an upstream mini-swe-agent release.
