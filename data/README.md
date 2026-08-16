# Data boundary

## No clinical dataset is distributed here

This directory is documentation only. The OwlPath public competition research release must not contain patient-level data, runtime databases, model secrets, or database extracts.

In particular, do not place any of the following in the repository:

- MIMIC records or derived patient-level exports;
- DR.ECC records or derived patient-level exports;
- local hospital or research-cohort records;
- free-text notes derived from real patients;
- runtime SQLite databases, WAL/SHM files, backups, or master keys;
- raw provider requests or responses;
- literature-search logs containing sensitive source text.

Authorization to access or analyze a dataset does **not** automatically grant permission to redistribute it. De-identification does not by itself change a data-use agreement, authorship agreement, institutional policy, ethics approval, or database license.

## Allowed public examples

Only fully synthetic examples may be included in the public repository. Each example should:

- be labeled `synthetic` in both filename-adjacent documentation and machine-readable metadata;
- avoid real identifiers, realistic record numbers, exact admission timestamps, and copied narrative text;
- state its educational or test purpose;
- identify the schema version;
- have a documented expected validation behavior;
- be reviewed for hidden metadata and accidental source overlap.

Synthetic examples belong under `examples/`, not in this directory.

Synthetic time-gate tests may use fixed, exact timestamps solely as invented test clocks. Those timestamps are created for deterministic software checks and are not copied from an admission, encounter, or real person.

## Runtime data

Local runtime data should be stored in an ignored directory selected by configuration. Users are responsible for access control, backups, retention, deletion, encryption-key handling, incident response, and compliance with applicable policy.

Repository ignore rules are a convenience, not a privacy control. Always inspect the candidate file list and Git history before release.

## Reproducing research without redistributing data

Where permission allows, the project may publish:

- cohort definitions and time-zero rules;
- data dictionaries that do not reveal protected content;
- extraction or evaluation code;
- aggregate counts that have passed disclosure review;
- synthetic fixtures;
- instructions for an authorized researcher to run code in their own approved environment.

Any future MIMIC or DR.ECC validation package must separately document access requirements, database version, cohort definition, feature window, label adjudication, missingness, exclusions, and non-redistribution terms.

## Release gate

The public repository must contain zero patient-level records. If data provenance or redistribution permission is uncertain, exclude the material and obtain review from the authorized data custodian before proceeding.
