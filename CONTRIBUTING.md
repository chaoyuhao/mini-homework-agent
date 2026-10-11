# Contributing

Contributions are welcome: Canvas compatibility fixes, clearer feedback, Markdown rubric formats, offline fixtures, documentation, and accessibility improvements.

```bash
git clone https://github.com/chaoyuhao/mini-homework-agent.git
cd mini-homework-agent
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[pdf]'
.venv/bin/python -m unittest discover -s tests -q
.venv/bin/python -m mini_homework_agent demo --offline
```

Keep changes focused and explain the user-visible behavior and validation in your pull request. Use synthetic homework for examples and tests. Preserve the reviewed-writeback boundary and the upstream mini-swe-agent license.

Useful starting points:

- Add a Markdown rubric fixture that demonstrates a format we do not support yet.
- Improve warnings for partially readable PDFs or archives.
- Add an OpenAI-compatible provider fixture without live credentials.
- Translate usage guidance or improve the Canvas/Fudan eLearning setup guide.

Before posting logs, remove credentials, student names/IDs, submissions, comments, and signed download URLs. Use GitHub Issues for reproducible bugs and feature proposals. Security reports belong in the private vulnerability reporting flow described in [SECURITY.md](SECURITY.md).
