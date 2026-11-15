# Security policy

## Supported versions

Only the latest release receives security fixes.

## Reporting a vulnerability

Report vulnerabilities privately through [GitHub's private vulnerability reporting](https://github.com/klconsultancy/ha-peak-basket/security/advisories/new), not in a public issue.

Include what an attacker can do, the steps to reproduce it, and the affected version or commit. You will get an answer within a week. Once a fix is released, the advisory is published with credit to you unless you prefer otherwise.

The integration only reads Home Assistant entities and publishes sensors; it talks to no device or service itself. Its output steers loads such as EV chargers through other systems, so issues that make it publish a higher allowed power than its inputs justify (in particular when an input is missing or stale, where it must fall back to a lower limit) are treated as the most severe.
