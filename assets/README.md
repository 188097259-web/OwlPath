# Visual assets

## Status

This directory belongs to the OwlPath `v0.1.0` public competition research release. The project owner has confirmed that the OwlPath-owned assets listed below may be published and redistributed under the repository MIT license.

This authorization covers only assets explicitly identified here as OwlPath-owned. It does not cover an institutional logo, journal figure, paper screenshot, third-party product mark, stock image, patient image, or model-generated image. Any new asset must document its creator or source, creation date, modification history, license, and permission status before publication.

## Current candidate inventory

| File | Intended use | Provenance status | Public-license status |
|---|---|---|---|
| `owlpath_logo.svg` | Project identity | Copied from OwlPath's own frontend favicon source; SHA-256 `04c960e...8dcb46` | Authorized by the project owner for public competition use under the repository MIT license on 2026-08-16 |
| `screenshots/workflow-expert-registry.png` | Interface/workflow illustration | Captured from the local OwlPath workflow page on 2026-08-16; no case/result panel; SHA-256 `66ded7b...10269`; UI layout and text are OwlPath-authored, while rendered interface icons come from `lucide-react 0.468.0` | OwlPath-authored composition is authorized by the project owner; Lucide icon portions remain under the upstream ISC license recorded in `THIRD_PARTY_NOTICES.md` |

The performance figures under `benchmarks/figures/` are documented separately in `benchmarks/FIGURE_PROVENANCE.md`. They should not be treated as decorative assets or clinical validation.

## Required metadata for new assets

Record the following in this file or an adjacent provenance file:

- filename and SHA-256 hash;
- creator or source;
- date created or retrieved;
- tool used, including model/tool version for generated imagery when applicable;
- edits performed;
- license and attribution text;
- permission to redistribute and modify;
- whether the asset contains a person, patient information, trademark, or third-party interface;
- reviewer and approval date.

## Format guidance

- Prefer SVG for diagrams and logos when all embedded resources are owned or licensed.
- Provide a high-resolution PNG fallback for competition documents and platforms that do not render SVG.
- Use high-contrast colors, direct labels, and shapes or line styles that remain distinguishable in grayscale.
- Do not encode the meaning of a performance figure by color alone.
- Provide nearby explanatory text or useful alternative text.
- Remove hidden metadata and local filesystem paths before publication.

## Ongoing release rule

The OwlPath-authored portions of the assets listed in the current inventory have project-owner publication approval. Embedded third-party elements retain their upstream terms. If the source or license of any new or modified asset is uncertain, exclude it from the public repository until resolved.
