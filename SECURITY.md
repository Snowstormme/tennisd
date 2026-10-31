# Security Policy

## Supported versions

Security fixes are applied to the `main` branch and to the production deployment built from it. Older commits and forks are not supported.

## Reporting a vulnerability

Please do not open a public issue or pull request for security problems.

Preferred: use **Report a vulnerability** in the Security tab of this repository.
Alternative: email `notsnowstorm@proton.me`, which is also listed on the Privacy page.

Please include:
- the affected URL or file;
- the expected and observed behavior;
- steps to reproduce;
- the impact as you understand it.

Do not include real user data, passwords, tokens, or database contents. Redact screenshots and logs. Do not access accounts or data that are not yours, and stop testing once the issue is confirmed.

## What to expect

- We aim to acknowledge reports within 7 days.
- We will assess the impact, prepare a fix, and coordinate disclosure after the production deployment is updated.
- With your permission, we will credit you in the fix notes.

## Out of scope

- Denial-of-service or high-volume testing.
- Social engineering of maintainers or users.
- Vulnerabilities in third-party services (Render, Neon, Google, Vercel).
- Scanner output without a demonstrated, exploitable issue.
- Missing headers alone, without a practical impact.
