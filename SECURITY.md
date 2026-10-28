# Security reporting

Please use this repository's **Security → Report a vulnerability** entry when
private vulnerability reporting is enabled. If that entry is unavailable,
contact the maintainer through the contact method on the `pamelah-233` GitHub
profile before sending sensitive details. Do not post credentials, private
research data, or an exploitable disclosure in a public issue.

Include the application version, platform, minimal reproduction, and impact.
Use synthetic input when possible. A maintainer will confirm the affected scope;
this volunteer project does not promise a response-time or support SLA.

Current community source is based on 1.2.2. Old installers are historical
snapshots and are not automatically security-maintained.

Before creating a public repository, enable private vulnerability reporting and
available secret-scanning/push-protection features. Public CI should use read-only
permissions for contribution checks and must not expose release/signing secrets
to pull-request code.
