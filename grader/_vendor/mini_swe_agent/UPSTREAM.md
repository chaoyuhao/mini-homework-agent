# Vendored mini-swe-agent core

Upstream: https://github.com/SWE-agent/mini-swe-agent
Commit: 04d809ceab9df28f9adaed044884180159172930
Version: 2.4.6
License: MIT (see LICENSE.md).

Copied agents/default.py, exceptions.py, and utils/serialize.py.
The three imports in default.py were made package-relative.
The local __init__.py defines protocols only; it omits upstream startup,
global configuration loading, CLI, provider adapters, and shell environments.
Our grader/agent.py supplies the Model and Environment and atomic trajectory saving.
