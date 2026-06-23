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

## Output and sensitive data

amlint is safe to use in public CI pipelines. Finding messages reference only
structural information from the config (receiver names, label keys/values,
duration strings, route paths). amlint **never prints the actual values** of
sensitive fields such as `api_url`, `routing_key`, `api_key`, `webhook_url`,
or SMTP credentials — it only checks for their presence or absence.
