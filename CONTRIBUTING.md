# Contributing to OwlPath

## Repository status

This repository is the OwlPath `v0.1.0` public competition research release. Contributions are welcome through GitHub Issues and pull requests, subject to the research-use, privacy, and third-party-rights boundaries below.

## Before contributing

Please read:

- `README.md` or `README.en.md`;
- `MEDICAL_DISCLAIMER.md`;
- `SECURITY.md`;
- `CODE_OF_CONDUCT.md`;
- the intended-use, privacy, output-contract, and validation documents under `docs/`.

Do not submit patient data, confidential institutional material, API keys, database files, raw provider responses, hidden chain-of-thought, or content whose redistribution rights are unclear.

## Development setup

From the repository root:

```bash
make setup
make doctor
make test
```

For interactive development:

```bash
make dev
```

The tests use simulated providers unless a command explicitly states that a real provider will be called. Never add a real-provider call to the default test suite.

## Change principles

1. Preserve the distinction between development mode and any future clinical workflow.
2. Do not present uncalibrated model scores as probabilities.
3. Do not weaken privacy redaction, secret handling, result contracts, provenance, or audit logging without an explicit design review.
4. Public traces may contain structured findings and evidence references, but must not expose hidden reasoning, unfiltered provider payloads, secrets, or health information.
5. New medical claims require a source, scope, date, and limitations.
6. New examples must be fully synthetic and clearly labeled.
7. External datasets, models, guidelines, taxonomies, and assets retain their own terms; MIT does not relicense them.
8. A prompt-only fix is not sufficient when deterministic validation can enforce the same requirement.

## Suggested change workflow

1. Open a narrowly scoped GitHub Issue without patient information, credentials, or confidential material.
2. Create a short-lived branch.
3. Add or update tests before changing behavior.
4. Update schemas, prompt versions, documentation, and examples when the contract changes.
5. Run `make test` from a clean environment.
6. Review generated traces for secrets, personal data, raw provider output, and unsupported medical claims.
7. Submit a pull request describing behavior before and after the change.

## Pull-request checklist

- [ ] The change has one clear purpose.
- [ ] Tests cover the changed behavior and failure path.
- [ ] Default tests do not make paid or external model calls.
- [ ] No personal or clinical data was added.
- [ ] No secret, token, local database, or machine-specific path was added.
- [ ] Public output contains no hidden chain-of-thought or raw provider response.
- [ ] User-facing medical language remains within the research-use boundary.
- [ ] Prompt, schema, configuration, API, and documentation versions remain consistent.
- [ ] Third-party material has documented provenance and compatible terms.
- [ ] The contributor has permission to license the submitted work under the repository license.

## Coding and documentation style

- Prefer small, reviewable changes.
- Use plain language for user-facing clinical and technical explanations.
- Keep deterministic rules in testable code rather than relying only on prompt wording.
- Record assumptions and known limitations.
- Use UTF-8 and the line-ending rules in `.editorconfig` and `.gitattributes`.
- Do not commit build output, local runtime databases, virtual environments, dependency directories, logs, or test recordings.

## Medical and benchmark changes

Changes to labels, Top-k definitions, adjudication, time zero, cohort membership, exclusion rules, or benchmark denominators require a versioned protocol update. Report the number of independent cases separately from repeated reader-case judgments. Do not infer general clinical utility from a software regression test or a small retrospective benchmark.

## Security issues

Do not disclose a suspected vulnerability, leaked secret, or patient data in a public issue. Follow `SECURITY.md`.

## Attribution

Contribution does not automatically determine academic authorship. Public acknowledgement, software contribution credit, and manuscript authorship will be reviewed separately and must be approved by the relevant people.
