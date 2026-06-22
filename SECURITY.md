# Security Policy

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Report them privately via GitHub:
**Security → Report a vulnerability** (button on the repo page)

Or email directly: danikdanik20166@gmail.com

You'll get a response within 48 hours.

## Scope

amlint is a static analysis tool that reads YAML files — it does not make network
requests, execute code from configs, or require elevated permissions.
The main relevant risk is malicious YAML input (e.g. YAML bombs).
