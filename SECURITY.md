# Security Policy

`discord-py-test` never connects to any network service: it runs entirely in-process and
requires no Discord tokens (the fake login accepts any string — never put a real token in
your tests).

If you believe you've found a security issue, please report it privately via
[GitHub security advisories](https://github.com/SilentHacks/discord-py-test/security/advisories/new)
rather than a public issue.
