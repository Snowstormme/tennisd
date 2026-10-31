# Contributing to Tennisd

Thank you for helping improve Tennisd. Please read the [Code of Conduct](CODE_OF_CONDUCT.md) before participating.

## Before you start

- For security issues, do not open a public issue or pull request. Follow [SECURITY.md](SECURITY.md).
- Check existing Issues and Pull Requests before starting duplicate work.
- Keep changes focused and explain the user or maintenance problem they solve.

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` for local configuration. Never commit `.env`, database dumps, API keys, passwords, connection URLs, or other credentials.

Run the test suite before submitting changes:

```bash
python -m pytest -q
```

For security-sensitive changes, also run the relevant checks described in `docs/security-checklist.md`.

## Accessibility checklist

For user-interface changes, check the affected page with keyboard-only navigation and a screen reader when possible. Keep these basics intact:

- use semantic headings, landmarks, buttons, and form labels;
- provide meaningful `alt` text for informative images and empty alt text for decorative ones;
- keep keyboard focus visible and preserve a logical tab order;
- maintain readable color contrast and do not communicate meaning by color alone;
- respect `prefers-reduced-motion` and avoid unexpected focus or scrolling.

Describe the manual checks in the pull request. Report barriers with the [Accessibility report](.github/ISSUE_TEMPLATE/accessibility_report.md) template.

## Branches and pull requests

Create a focused branch from the current `main` branch. Do not commit directly to `main` in normal contribution work.

Pull requests should include:

- a concise summary of the change;
- tests run and their result;
- screenshots or a short manual-check description for visible UI changes;
- notes about migrations, environment variables, or deployment steps, when applicable.

Keep one logical change per pull request. Do not include unrelated formatting or generated files. Wait for CI to pass and respond to review comments before merging.

## Code and security expectations

- Preserve authentication, authorization, CSRF, rate limiting, and input validation behavior.
- Escape user-controlled content and avoid introducing inline scripts or styles that weaken the Content Security Policy.
- Use environment variables for secrets and redact credentials from logs, screenshots, and issue reports.
- Add or update tests for changed behavior, especially for account, database, and security flows.

## Commit messages

Use a short, imperative subject that describes the change, for example:

```text
Document AdSense CSP approach
```

Keep commits reviewable and avoid committing temporary debugging changes.

