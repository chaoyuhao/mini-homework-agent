# Separate assignment requirements and reusable grading rules

Assignments can have different structures: a weighted rubric, several unweighted
questions, or a single task. Reusing a previous assignment's dimensions adds
requirements the teacher did not set. Copying common score and comment rules
into every assignment also makes later policy changes inconsistent.

Keep three independent inputs:

1. Required teacher Markdown with the actual questions and their completion criteria.
2. Optional shared Markdown for leniency, late deductions, student comments, and review.
3. The current assignment's scoring structure, supplied as points or explicit holistic scoring.

The CLI accepts the second document with `--policy`; the review UI has a separate
input. Both sources and the resolved numeric policy are saved in each run.
Explicit policies in the task document override the common baseline. Legacy
single-document batches retain their saved instructions and remain supported.

Numbered question headings produce independent checks with teacher IDs, status,
feedback, and evidence. Checks carry no inferred points. Holistic scoring uses
one rubric item for the full assignment; explicit point allocations continue to
use their own items. An uncertain question withholds the automatic final score.
Question bodies are not duplicated in the model prompt.

Validation covers separate input routing, rubric precedence, two-question
evidence, uncertain material, policy bounds, late/comment behavior, legacy input,
and recovery using snapshots after source-file changes. Use only synthetic
submissions. Public examples contain no private coursework or student material.
