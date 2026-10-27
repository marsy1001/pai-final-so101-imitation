"""Approval screen and big screen (demo-plan 3b, ADR 0011).

A browser UI: this package serves static HTML/JS and a small HTTP / WebSocket API from
the one Python process that owns the runner, the `RunGate` and the sim, so robot commands
have a single source. `screen.ApprovalScreen` is the logic; `server.create_app` the HTTP
layer; `python -m so101_demo.ui` starts both.
"""
