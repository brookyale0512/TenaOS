# Security policy

## Scope

TenaOS is a **research and challenge-submission codebase** targeting
deployment in primary-care clinics in low- and middle-income countries.

- It is **not** a HIPAA-regulated product.
- It is **not** a CE-marked or FDA-cleared medical device.
- It is **not** safety-of-life software.

Operators deploying TenaOS in real clinical settings remain solely
responsible for local regulatory compliance, data protection, and any
clinical risk management that their jurisdiction requires.

## Reporting a vulnerability

If you believe you have found a security issue, please email
**security@tenaos.org** with:

- A description of the issue and its impact.
- Steps to reproduce, ideally with a minimal example.
- Affected version (commit hash or release tag).

Please do **not** open a public GitHub issue for security problems.

We aim to acknowledge reports within 5 working days and to ship a fix or
mitigation within 30 days for confirmed high-severity issues. Coordinated
disclosure timelines are case-by-case.

## What is in scope

- Authentication or authorization bypasses in `TenaOS-Frontend`,
  `TenaOS-Backend`, or `TenaAgent`.
- Server-side request forgery, command injection, path traversal, or
  deserialization issues in `TenaAgent` tool handlers.
- Issues in `TenaOS-LLM` or `TenaOS-KnowledgeBase` containers that allow
  unauthenticated access to OpenMRS data.
- Secrets accidentally committed to `main`.

## What is out of scope

- Vulnerabilities in the upstream OpenMRS reference application — please
  report those to the OpenMRS project directly.
- Vulnerabilities in `llama.cpp`, `qdrant`, or other third-party services
  embedded as containers — report to their respective maintainers.
- LLM behavioral issues (hallucinations, prompt drift). Those are quality
  issues, not security issues.
