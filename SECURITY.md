# Security policy

## Current status

OwlPath `v0.1.0` is a public competition research release. It is not security-certified, has not undergone penetration testing, and is not approved for processing identifiable health information. No response-time service-level agreement is promised.

## Supported versions

| Version | Security-report status |
|---|---|
| `0.1.x` | Receives best-effort review during the competition research phase |
| `< 0.1.0` and review snapshots | Not supported |

## Reporting a suspected vulnerability

Do **not** place a secret, exploit, patient information, raw clinical text, or sensitive log in an issue, chat room, screenshot, or public repository.

Use [GitHub Private Vulnerability Reporting](https://github.com/188097259-web/OwlPath/security/advisories/new) for a sensitive report. Private Vulnerability Reporting must be enabled in the GitHub repository settings when the repository is created. If that private form is temporarily unavailable, open a public [GitHub Issue](https://github.com/188097259-web/OwlPath/issues) containing only a request for private contact and no sensitive details.

Include the affected version, component, impact, and minimal safe reproduction steps. Do not include patient information, real API keys, provider responses, private URLs, or restricted data. General non-sensitive bugs may be reported through GitHub Issues.

## Security-sensitive areas

Reviewers should pay particular attention to:

- provider API-key storage, masking, rotation, and accidental logging;
- encryption-key placement and backup behavior;
- server-side request forgery and unsafe redirects in retrieval tools;
- prompt injection and untrusted retrieved content;
- cross-site scripting and unsafe rendering of model-generated text;
- authorization for governance or provider-configuration changes;
- database and trace access across run identifiers;
- leakage of full case narratives to unintended external services;
- exposure of raw provider responses or hidden model reasoning;
- unsafe file paths, archive extraction, subprocess calls, and shell arguments;
- vulnerable Python, Node.js, browser, and operating-system dependencies;
- denial of service through excessive agent calls, oversized input, or retrieval loops;
- accidental inclusion of runtime databases, master keys, logs, test recordings, or source datasets in a release.

## Secrets

- Never commit `.env`, provider tokens, cookies, private certificates, master keys, or database credentials.
- `.env.example` must contain documentation-safe placeholders only.
- Treat a committed secret as compromised even if it is later deleted from the latest file tree.
- Rotate the secret, inspect repository history and release archives, and document the remediation.
- A masked value in the web interface or log is not proof that the underlying storage and trace endpoints are safe.

## Health and research data

This candidate must not be used to process identifiable patient information. De-identification reduces risk but does not automatically satisfy a data-use agreement or permit redistribution. Public examples must be fully synthetic and reviewed for hidden metadata and realistic identifiers.

If clinical information is discovered in a candidate package, stop release preparation, quarantine the package, notify the authorized data custodian privately, and follow the applicable institutional incident process.

## Coordinated remediation

A security fix should include:

1. a minimal description of impact and affected versions;
2. a patch that avoids exposing exploit or patient details;
3. a regression test;
4. dependency and adjacent-path review;
5. secret rotation or data-response actions when applicable;
6. a changelog and release-note entry;
7. an independently rerun pre-publication scan.

Security review does not establish medical safety, and medical validation does not establish cybersecurity.
