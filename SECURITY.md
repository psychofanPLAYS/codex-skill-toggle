# Security Policy

## Reporting a vulnerability

Please do not open a public issue for a security vulnerability. Use GitHub's private vulnerability reporting for this repository, or contact the repository owner through GitHub.

Include the affected command, operating system, reproduction steps, and impact. Do not include credentials, private configuration, or live Codex databases.

## Scope

This tool can move local files and edit Codex configuration when explicitly invoked. Review the target and receipt before restoring artifacts. Never run it against an untrusted `CODEX_HOME` without inspecting the path first.
