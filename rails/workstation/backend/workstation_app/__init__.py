"""Workstation terminal backend — a WebSocket ⇆ PTY-over-SSH bridge.

The platform's browser terminal. Speaks a tiny tagged-binary WS protocol to the
xterm.js frontend and an SSH client session to the workstation's own sshd.
"""
