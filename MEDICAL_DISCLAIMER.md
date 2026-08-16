# Medical and research-use disclaimer

## Status

OwlPath `v0.1.0` is a public competition research release. It has not been cleared, approved, certified, or registered as a medical device.

## Not for clinical care

OwlPath is intended only for:

- software development;
- competition demonstration;
- methods research;
- testing with fully synthetic or appropriately authorized, properly de-identified material.

It must not be used to:

- diagnose or rule out disease in a real patient;
- select, start, stop, or change antimicrobial or other treatment;
- delay emergency assessment, laboratory testing, imaging, source control, consultation, or other care;
- replace clinician judgment, microbiological confirmation, public-health reporting, or institutional policy;
- communicate a definitive diagnosis to a patient or family.

If a person may be seriously ill, use established emergency and clinical pathways. Do not rely on this software.

## Nature of the output

- A ranked pathogen list is a developmental hypothesis output, not a confirmed etiologic diagnosis.
- “Model score” means an uncalibrated software score. It is not a probability, prevalence estimate, confidence interval, or treatment recommendation.
- A fixed Top-5 development contract is a software-testing rule. It does not imply that infection is present or that the true pathogen must be among five candidates.
- Evidence retrieval may be incomplete, outdated, unavailable, or incorrectly interpreted.
- Model providers may produce inconsistent, fabricated, biased, or medically unsafe content.
- Contract validation confirms output form and selected terminology rules; it does not prove medical correctness.

## Validation limitations

The current candidate has not completed the evidence required for clinical deployment, including prospective validation, external-site validation, subgroup performance assessment, calibration, human-factors evaluation, clinical safety analysis, regulatory review, and post-deployment monitoring.

Any reported benchmark must be read with its population, reference standard, time window, sample size, missing-data rules, model versions, comparison protocol, and uncertainty. Performance on retrospective or synthetic cases does not establish performance in routine care.

## Data and external services

- Do not enter direct identifiers, unnecessary quasi-identifiers, or unapproved patient narratives.
- A user-selected external model provider may receive the full supplied narrative. Its own privacy, retention, security, geographic-processing, and billing terms apply.
- Literature-search services should receive generalized medical queries, but users must still review the configured data flow.
- Local hosting or a local model changes where information is processed; it does not by itself establish privacy, cybersecurity, or medical accuracy.
- Authorization to use MIMIC, DR.ECC, or a local clinical database for research does not automatically permit redistribution in this repository.

## Responsibility

Researchers and developers are responsible for applicable law, ethics approval, data-use agreements, institutional policy, third-party terms, validation design, and accurate reporting. Users assume the risks of running this research prototype.

The software license contains additional warranty and liability terms. If this disclaimer and local law differ, obtain qualified institutional, legal, regulatory, and clinical review before any use beyond software research.
