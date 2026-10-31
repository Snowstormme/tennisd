# Accessibility

Tennisd aims to be usable with different devices, input methods, and assistive technologies. Accessibility improvements and reports are welcome.

## Contribution checklist

For user-interface changes, check the affected page with keyboard-only navigation and a screen reader when possible:

- use semantic headings, landmarks, buttons, and form labels;
- provide meaningful `alt` text for informative images and empty alt text for decorative ones;
- keep keyboard focus visible and preserve a logical tab order;
- maintain readable color contrast and do not communicate meaning by color alone;
- respect `prefers-reduced-motion` and avoid unexpected focus or scrolling.

Describe the manual checks in the pull request. Use the [Accessibility report](.github/ISSUE_TEMPLATE/accessibility_report.md) template to report a barrier.

## Reporting a barrier

Include the affected page, steps to reproduce, expected and observed behavior, browser and device details, and assistive technology details when relevant. Redact private data, credentials, cookies, and tokens from screenshots or logs.

