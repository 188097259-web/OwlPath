# OwlPath

**A development-first, multi-specialty agent system for pathogen hypothesis generation**

> **Repository status:** public competition research release `v0.1.0`. The project owner has authorized publication within the boundaries documented here. The canonical repository is [188097259-web/OwlPath](https://github.com/188097259-web/OwlPath).

The public repository is attributed to the **OwlPath Project Team**. The project founder and clinical lead is the founder of **DR.ECC**. Their verified real-world identity has been disclosed to the competition organizers; the public repository does not display their name, institution, or private email for privacy reasons.

OwlPath is a locally hosted web research prototype. Given a fully synthetic or properly de-identified clinical narrative, it coordinates core and dynamically selected specialty agents, performs evidence retrieval, synthesizes a ranked list of concrete pathogen hypotheses, validates a deterministic output contract, and runs an independent review step.

OwlPath is intended for software development, competition demonstration, and methods research only. It is **not a medical device**, has not completed clinical validation, and must not be used to diagnose or treat a patient. See [MEDICAL_DISCLAIMER.md](MEDICAL_DISCLAIMER.md).

## What the current development workflow does

```text
Full case narrative
  -> stable source fragments and input snapshot
  -> completeness, applicability, and data-quality observations
  -> complexity and specialty router
  -> five core specialty agents in parallel
  -> up to six dynamically selected specialty agents
  -> evidence-board deduplication and retrieval planning
  -> literature, public-health, and guideline retrieval
  -> evidence verification
  -> pathogen diagnostician agent
  -> deterministic terminology and Top-5 contract validation
  -> independent reviewer agent
  -> at most one revision when required
  -> bilingual result, structured trace, and integrity hashes
```

The development result contract is `owlpath.result.v3`. A completed development result contains exactly five unique, concrete pathogen names. Broad categories such as “bacterium,” “virus,” or “unknown pathogen” cannot occupy a Top-5 position. Displayed model scores are uncalibrated model scores, not clinical probabilities.

## Important limitations

- The development workflow records applicability, out-of-distribution, calibration, and data-conflict concerns as non-blocking observations.
- This development behavior must not be interpreted as a future clinical release rule.
- A successful provider connection proves only basic connectivity and response formatting; it does not establish medical accuracy.
- External model calls may send the full supplied narrative to the provider selected by the user and may incur cost.
- Literature search receives generalized medical queries rather than the full case narrative.
- Local execution changes the data boundary but does not guarantee privacy, security, or diagnostic performance.

## Requirements

- Python 3.10 or later (CI covers 3.10, 3.11, and 3.12; 3.11 is the local reference environment)
- Node.js 22.18 or later (the frontend contract tests import TypeScript source directly)
- macOS, Linux, or another environment capable of running the backend and frontend dependencies
- At least one configured provider for real-model runs; automated tests use simulated providers and do not call paid APIs

## Local setup

From the repository root:

```bash
make setup
make test
make start
```

The default local web address is:

```text
http://127.0.0.1:8000
```

The validation environment did not have a Docker CLI. The Dockerfile and Compose configuration received static review, but a clean container build has not been executed. This is a disclosed engineering limitation and must not be represented as a passed Docker test.

For development with frontend hot reload:

```bash
make dev
```

Run the environment diagnostic when setup fails:

```bash
make doctor
```

Do not copy real API keys into tracked files. The root `.env.example` is documentation only; real secrets must remain outside version control.

## Provider support

The current code includes adapters for:

- OpenAI Responses API;
- Anthropic Messages API;
- Google Gemini `generateContent`;
- OpenAI-compatible endpoints, including hosted or self-managed gateways;
- local Ollama endpoints.

Real provider calls may be billed and are subject to provider availability, rate limits, privacy terms, and content policies. Use synthetic input for connection tests.

## Traceability

Run identifiers are encoded in result URLs. The public-facing trace is intended to expose structured findings, candidates, evidence references, reviewer findings, model/provider metadata, timing, warnings, and integrity hashes. It must not expose API keys, unfiltered provider responses, personal health information, or hidden chain-of-thought.

## Repository map

```text
backend/     FastAPI backend, orchestration, providers, retrieval, persistence, and tests
frontend/    React and TypeScript user interface and tests
config/      Versioned architecture, terminology, governance, and output configuration
docs/        Intended use, contracts, privacy, validation, prompts, and architecture
examples/    Synthetic contract examples only
assets/      Project-owned or explicitly licensed visual assets
prompts/     Prompt publication policy and, when approved, versioned templates
schemas/     Public schema documentation and versioning policy
data/        Data-boundary documentation only; no patient-level data
releases/    Versioned release evidence, SBOMs, and SHA-256 manifest
scripts/     Setup, launch, diagnostics, and regression utilities
```

## Data and privacy boundary

This repository must not contain MIMIC records, DR.ECC records, local hospital data, identifiable or de-identified patient-level exports, runtime SQLite databases, encryption keys, provider secrets, or raw model responses. Permission to analyze a dataset does not automatically grant permission to redistribute it.

Only clearly labeled, fully synthetic examples may be included in the public repository. See [data/README.md](data/README.md) and the privacy documentation under `docs/`.

## Testing

```bash
make test
```

The command is intended to run backend tests, frontend tests, and the frontend production build. A release claim must be supported by a dated test report produced from a clean copy; the existence of tests alone is not evidence that a particular release passed them.

## Contribution and security

For general questions, use [GitHub Issues](https://github.com/188097259-web/OwlPath/issues). For sensitive security reports, use [GitHub Private Vulnerability Reporting](https://github.com/188097259-web/OwlPath/security/advisories/new); never post credentials, exploit details, or clinical text in a public issue. Read:

- [CONTRIBUTING.md](CONTRIBUTING.md)
- [SECURITY.md](SECURITY.md)
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- [REPOSITORY_REVIEW_CHECKLIST.md](REPOSITORY_REVIEW_CHECKLIST.md)
- [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md)

## Citation and authorship

Use the collective attribution **OwlPath Project Team** and the metadata in `CITATION.cff`. The repository intentionally does not publish an individual's name, affiliation, or private email. The project founder and clinical lead is identified publicly by role as the founder of DR.ECC, with verified identity disclosed to the competition organizers.

## License

The project owner has authorized OwlPath-owned code, documentation, and repository assets identified as project-owned for publication under the MIT license. That license does not override the separate terms of external models, APIs, publications, taxonomies, guidelines, datasets, or third-party dependencies. Ongoing third-party license due diligence is documented rather than presented as complete legal certification.
