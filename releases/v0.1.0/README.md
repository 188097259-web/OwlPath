# OwlPath v0.1.0 release evidence

## Status: approved public competition research release

The project owner has approved the documented file scope as the OwlPath `v0.1.0` public competition research release. It remains a research-software release, **not** a clinical release or medical-device version.

As of 2026-08-16:

- the canonical repository is `https://github.com/188097259-web/OwlPath`; the actual remote push and `v0.1.0` tag still require execution and post-push verification;
- public attribution is **OwlPath Project Team**; no individual name, institution, private email, or DOI is asserted;
- the project founder and clinical lead is the founder of DR.ECC, with verified identity disclosed privately to the competition organizers;
- clinical effectiveness and safety have not been established;
- OwlPath-owned code, documentation, Logo, screenshot composition, and aggregate benchmark figures have project-owner publication approval under the repository MIT license; Lucide icons rendered in the screenshot retain their upstream ISC license, and wider third-party dependency license due diligence remains ongoing;
- a clean-copy installation/test, current-tree privacy scan and the single-commit local Git-history scan have been recorded; container, legal, independent-review and remote CI gates remain disclosed limitations.

## Current evidence files

This directory now contains:

```text
RELEASE_NOTES.md
MANIFEST.sha256
SOURCE_INVENTORY.csv
TEST_REPORT.md
PRIVACY_AUDIT.md
SECURITY_SCAN_SUMMARY.md
THIRD_PARTY_REVIEW.md
EXCLUSIONS.md
SBOM_README.md
sbom-python.cdx.json
sbom-frontend.cdx.json
```

Do not create an empty “PASS” report. Each report must identify the candidate version, date, environment, command or review method, result, limitations, and reviewer. Exact file identity is bound by the final per-file `MANIFEST.sha256`; a report must not embed a manifest hash that would create a circular reference.

## Release-maintenance gates

Before pushing the final commit and whenever preparing a later release:

1. complete `REPOSITORY_REVIEW_CHECKLIST.md`;
2. complete `RELEASE_CHECKLIST.md`;
3. preserve the approved collective attribution, citation, MIT scope, and third-party rights boundaries;
4. verify the repository from a clean copy with no local cache;
5. inspect the complete candidate file list and any Git history;
6. generate and verify the SHA-256 manifest after all final edits;
7. verify the remote repository, CI run, Private Vulnerability Reporting setting, and `v0.1.0` tag after upload.

## Version semantics

The approved software version and intended Git tag are `0.1.0` and `v0.1.0`. Release evidence must be regenerated if the final commit changes; the software must still not be cited as clinically validated.
