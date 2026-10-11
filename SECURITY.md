# Security

mini-homework-agent is a local grading assistant. Treat tokens, downloaded student work, drafts, trajectories, and exported JSON as course data.

The current `0.2.x` release receives fixes. Please report vulnerabilities through GitHub's **Report a vulnerability** option on this repository's Security tab. Avoid publishing credentials or actual student content in an issue.

Security boundaries:

- Canvas tokens are used only for the configured Canvas origin. Cross-origin attachment downloads drop the token.
- Remote model endpoints require HTTPS; HTTP is permitted for localhost model services only.
- The review server binds to `127.0.0.1` and checks Host, CSRF token, and Origin.
- The homework environment exposes bounded content reading, AST syntax inspection, and grade validation. It provides no shell or student-code execution.
- Model suggestions cannot approve themselves or call writeback tools.
- Writeback rechecks the assignment and submission snapshot and does not automatically retry an ambiguous PUT.

Student text itself may contain names or personal details even when Canvas metadata is omitted. Use a model provider appropriate for your course data, and remove unnecessary personal information before sending submissions. This project does not claim that static review proves program correctness or that a model's confidence is a calibrated probability.
