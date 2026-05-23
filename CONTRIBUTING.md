# Contributing to TenaOS

Thanks for thinking about contributing. TenaOS targets primary-care clinics
in low- and middle-income countries, so simplicity and reliability come
before features.

## How to file an issue

1. Search existing issues first.
2. Describe what you saw, what you expected, and how to reproduce it.
3. For security issues, follow [`SECURITY.md`](SECURITY.md) — do not open a
   public issue.

## How to send a change

1. Fork the repo and branch from `main`. Branch names follow
   `topic/<short-name>` (e.g. `topic/kb-ciel-prefetch-limit`).
2. Keep PRs small. One conceptual change per PR.
3. Add or update tests for any behavior change.
4. Run the local checks (see below) before pushing.
5. Open a PR with a description that links the issue, summarizes the
   change, and lists any follow-ups.

## Coding conventions

- Python: type hints throughout, dataclasses for value objects, no global
  state. Public service modules live under
  `TenaAgent/service/tena_agent_service/`.
- TypeScript: strict mode, no implicit `any`, function components with
  named exports.
- Comments explain *why*, not *what*. Skip comments that restate the code.

## Local checks

```bash
# Backend / TenaAgent
cd TenaAgent/service
python -m pytest

# Frontend
cd TenaOS-Frontend
npm ci
npm run lint
npm run typecheck
npm test
npm run build

# Whole stack smoke test
cp demo.env.example .env
docker compose up -d
curl -fsS http://localhost:8095/health
```

## Commit messages

Use the imperative mood and keep the subject line under 72 characters:

```
Add CIEL bundle expansion to form-builder seed search

Why: form seeds without their answer sets cause repeated CIEL hops at
authoring time, slowing the UI noticeably for large concepts.
```

## Code review

Maintainers review PRs against three criteria:

1. **Does this make the system simpler to reason about?**
2. **Is the test coverage proportional to the risk?**
3. **Does it stay inside the TenaOS scope** (primary-care, LMIC, OpenMRS +
   Gemma 4)?

We will say no, kindly, to changes that move the system away from these.
