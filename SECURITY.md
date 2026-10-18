# Security Policy

## How secrets and code are handled

- `shadow-debugger` never runs code returned by an LLM (`eval`, `exec`, or subprocess). Code blocks in reports are only checked with `ast.parse()`.
- Before building a prompt, variable names, dict keys, HTTP headers, and string values are scrubbed for common secret patterns (passwords, tokens, API keys, JWTs, `Bearer` headers, PEM keys, and sensitive URL query params).
- If you want to avoid sending source code or local variables to a cloud API, set `send_source_code=False` and `send_locals=False` in `shadow_debugger.init()`, or use `provider="ollama"` to keep everything local.

## Reporting a vulnerability

If you find a security issue (for example, a secret pattern that bypasses redaction), please report it privately via GitHub's [Private Vulnerability Reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability) rather than opening a public issue.
