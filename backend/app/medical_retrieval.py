"""Small, fail-open medical literature and taxonomy adapters for development runs.

The development Agent uses the pasted synthetic/de-identified narrative for
LLM reasoning, but never sends that narrative to a literature search service.
Only short, generalized medical queries produced from structured Agent output
are accepted here.  Network failure is returned as an observation warning and
must not block the run.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

import httpx


EUROPE_PMC_SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
NCBI_PUBMED_SEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
NCBI_PUBMED_SUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
NCBI_TAXONOMY_SEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
NCBI_TAXONOMY_SUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
WHO_DON_API_URL = "https://www.who.int/api/hubs/diseaseoutbreaknews"

AUTHORITATIVE_SOURCE_CATALOG_VERSION = "owlpath.authoritative-source-catalog.v1"
AUTHORITATIVE_SOURCE_CATALOG: Tuple[Dict[str, Any], ...] = (
    {
        "source_id": "who_don",
        "organization": "World Health Organization",
        "jurisdiction": "global",
        "source_kind": "public_health_outbreak_notice",
        "url": "https://www.who.int/emergencies/disease-outbreak-news",
        "coverage": ["outbreak", "event update", "risk assessment"],
        "catalog_mode": "live_connector",
        "coverage_limitation": "non_exhaustive_official_event_notices",
    },
    {
        "source_id": "who_guidelines",
        "organization": "World Health Organization",
        "jurisdiction": "global",
        "source_kind": "guideline_catalog",
        "url": "https://www.who.int/publications/who-guidelines",
        "coverage": ["guideline", "global public health"],
        "catalog_mode": "registered_reference_only",
        "coverage_limitation": "not_live_searched_by_owlpath",
    },
    {
        "source_id": "cdc_infectious_diseases",
        "organization": "US Centers for Disease Control and Prevention",
        "jurisdiction": "United States",
        "source_kind": "surveillance_and_guideline_catalog",
        "url": "https://www.cdc.gov/nndss/infectious-disease/index.html",
        "coverage": ["surveillance", "notifiable disease", "guidance"],
        "catalog_mode": "registered_reference_only",
        "coverage_limitation": "not_live_searched_by_owlpath",
    },
    {
        "source_id": "ecdc_surveillance_atlas",
        "organization": "European Centre for Disease Prevention and Control",
        "jurisdiction": "European Union and EEA",
        "source_kind": "surveillance_catalog",
        "url": "https://atlas.ecdc.europa.eu/public/index.aspx",
        "coverage": ["surveillance", "epidemiology"],
        "catalog_mode": "registered_reference_only",
        "coverage_limitation": "not_live_searched_by_owlpath",
    },
    {
        "source_id": "china_cdc",
        "organization": "Chinese Center for Disease Control and Prevention",
        "jurisdiction": "China",
        "source_kind": "public_health_catalog",
        "url": "https://www.chinacdc.cn/",
        "coverage": ["surveillance", "public health", "China"],
        "catalog_mode": "registered_reference_only",
        "coverage_limitation": "not_live_searched_by_owlpath",
    },
    {
        "source_id": "idsa_guidelines",
        "organization": "Infectious Diseases Society of America",
        "jurisdiction": "international clinical reference",
        "source_kind": "professional_guideline_catalog",
        "url": "https://www.idsociety.org/practice-guideline/practice-guidelines/",
        "coverage": ["infectious disease", "diagnosis", "treatment guideline"],
        "catalog_mode": "registered_reference_only",
        "coverage_limitation": "not_live_searched_by_owlpath",
    },
)

_TAXONOMY_CACHE_SCHEMA = "owlpath.taxonomy-positive-cache.v2"
_TAXONOMY_CACHE_FILENAME = "taxonomy-positive-cache.v2.json"
_REGISTRY_RANK_POLICY_SCHEMA = "owlpath.registry-taxonomy-rank-policy.v1"
_VIRUS_LINEAGE_ANCHOR = "NCBITaxon:10239"
_REGISTRY_PRODUCT_RANKS = frozenset({"species", "species_complex", "virus_type"})
_REGISTRY_RANK_RULES: Dict[str, Dict[str, Any]] = {
    "ncbi_species_to_product_species_v1": {
        "product_rank": "species",
        "accepted_ncbi_ranks": frozenset({"species", "subspecies"}),
        "required_lineage_anchor": None,
    },
    "ncbi_species_group_to_product_species_complex_v1": {
        "product_rank": "species_complex",
        "accepted_ncbi_ranks": frozenset({"species group", "species subgroup"}),
        "required_lineage_anchor": None,
    },
    "ncbi_unranked_virus_to_product_virus_type_v1": {
        "product_rank": "virus_type",
        "accepted_ncbi_ranks": frozenset({"no rank"}),
        "required_lineage_anchor": _VIRUS_LINEAGE_ANCHOR,
    },
}

_DIRECT_IDENTIFIER = re.compile(
    r"(?:\b\d{7,}\b|\b1[3-9]\d{9}\b|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})"
)
_DATE_LIKE = re.compile(r"\b(?:19|20)\d{2}[-/.]\d{1,2}(?:[-/.]\d{1,2})?\b")
_SPACE = re.compile(r"\s+")
_CJK = re.compile(r"[\u3400-\u9fff]")
_SAFE_CONCEPT_CHARS = re.compile(r"[^A-Za-z0-9\s'()+,./-]+")
_QUERY_STOPWORDS = frozenset({
    "and", "or", "the", "a", "an", "of", "in", "for", "after", "with",
    "human", "infection", "infectious", "pathogen", "diagnosis", "differential",
    "case", "report", "series", "guideline", "outbreak", "surveillance",
})
_HUMAN_CLINICAL_TITLE_CUE = re.compile(
    r"\b(?:human|patient|patients|case report|case series|clinical|hospital|"
    r"icu|intensive care|adult|adults|child|children|neonate|neonatal|"
    r"meningitis|bacteremia|bacteraemia|septicemia|septicaemia|sepsis|"
    r"pneumonia|liver abscess)\b",
    re.IGNORECASE,
)
# A generic syndrome word (for example, ``septicemia``) does not establish
# that a paper concerns people.  It is frequently used in fish, aquaculture,
# veterinary, and animal-model titles too.  These terms are deliberately used
# only for the candidate-specific *title metadata* gate below; they do not
# classify a paper's abstract or full text.
_NON_HUMAN_TITLE_CONTEXT = re.compile(
    r"\b(?:aquaculture|fish(?:es)?|zebrafish|tilapia|salmon|trout|carp|"
    r"shrimp|shellfish|fish disease|fish pathogen|veterinary|animal model|"
    r"animal experiment|murine|mouse|mice|rat|rats|rabbit|rabbits|porcine|"
    r"swine|bovine|ovine|livestock|poultry|chicken|canine|feline|in vitro|"
    r"in vivo|feed(?:ing)?|diet(?:ary)?|growth performance|immunomodulat\w*|"
    r"bactericidal effects?|experimental(?:ly)? infect\w*|challenge[sd]?)\b",
    re.IGNORECASE,
)
# Animal host names are not always accompanied by the word ``fish`` or
# ``animal``.  Experimental titles commonly use the form
# ``in <Genus species> infected/challenged with <pathogen>``.  The candidate
# binder works on normalized lower-case title text, so detect that narrow
# linguistic construction separately.  A title with an explicit human anchor
# is still allowed below.
_NON_HUMAN_BINOMIAL_HOST_EXPERIMENT = re.compile(
    r"\bin\s+[a-z][a-z0-9-]{2,}\s+[a-z][a-z0-9-]{2,}\s+"
    r"(?:infected|challenged|inoculated)\s+with\b",
    re.IGNORECASE,
)
_EXPLICIT_HUMAN_TITLE_ANCHOR = re.compile(
    r"\b(?:human|humans|person|people|patient|patients|case report|"
    r"adult|adults|child|children|"
    r"neonate|neonates|hospital|hospitals|icu|intensive care)\b",
    re.IGNORECASE,
)


def generalized_query(value: str, *, max_length: int = 220) -> str:
    """Return a short query and reject likely person-level identifiers.

    This is deliberately conservative.  The orchestrator should pass concepts
    such as ``sepsis fish handling Vibrio vulnificus`` rather than source prose.
    """

    text = _DIRECT_IDENTIFIER.sub(" ", str(value or ""))
    text = _DATE_LIKE.sub(" ", text)
    text = text.replace("\n", " ").replace("\r", " ")
    text = _SPACE.sub(" ", text).strip()
    return text[:max_length]


def query_id(value: str) -> str:
    return "query_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


@dataclass
class RetrievalBundle:
    citations: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    source_status: Dict[str, str] = field(default_factory=dict)

    @property
    def partial(self) -> bool:
        return bool(self.warnings) or any(value != "available" for value in self.source_status.values())

    def public_payload(self) -> Dict[str, Any]:
        # Search terms themselves are intentionally not returned.  A stable
        # hash lets the trace prove which query was used without exposing it.
        return {
            "citations": self.citations,
            "warnings": sorted(set(self.warnings)),
            "source_status": self.source_status,
            "retrieval_partial": self.partial,
        }


@dataclass(frozen=True)
class RetrievalQueryPlanItem:
    """One private, de-identified search instruction.

    ``query`` and ``concept_terms`` may be used for outbound requests but are
    deliberately excluded from :meth:`public_payload`.  The trace receives a
    stable hash and concept kinds, never source prose or the search phrase.
    """

    intent: str
    query: str
    concept_terms: Tuple[str, ...]
    concept_kinds: Tuple[str, ...]

    @property
    def plan_item_id(self) -> str:
        return query_id("%s\n%s" % (self.intent, self.query))

    def public_payload(self) -> Dict[str, Any]:
        return {
            "plan_item_id": self.plan_item_id,
            "intent": self.intent,
            "concept_kinds": list(self.concept_kinds),
            "concept_count": len(self.concept_terms),
            "query_text_omitted": True,
        }


@dataclass
class FederatedRetrievalBundle:
    """Trace-safe multi-source result used by the expanded Agent pipeline."""

    citations: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    source_status: Dict[str, str] = field(default_factory=dict)
    query_plan: List[RetrievalQueryPlanItem] = field(default_factory=list)
    query_source_status: Dict[str, Dict[str, str]] = field(default_factory=dict)
    catalog_version: str = AUTHORITATIVE_SOURCE_CATALOG_VERSION
    source_catalog: List[Dict[str, Any]] = field(default_factory=list)
    coverage_notes: List[str] = field(default_factory=list)

    @property
    def partial(self) -> bool:
        return bool(self.warnings) or any(
            value not in {"available", "not_needed", "registered_reference_only"}
            for value in self.source_status.values()
        )

    @property
    def evidence_sources(self) -> List[Dict[str, Any]]:
        """Semantic alias used by the execution engine/result compiler."""

        return self.citations

    def public_payload(self) -> Dict[str, Any]:
        literature = [
            item for item in self.citations
            if item.get("source_kind") != "public_health_outbreak_notice"
        ]
        public_health = [
            item for item in self.citations
            if item.get("source_kind") == "public_health_outbreak_notice"
        ]
        return {
            "schema_version": "owlpath.federated-medical-retrieval.v1",
            "citations": self.citations,
            "evidence_sources": self.citations,
            "literature": literature,
            "public_health": public_health,
            "warnings": sorted(set(self.warnings)),
            "source_status": self.source_status,
            "query_plan": [item.public_payload() for item in self.query_plan],
            "query_source_status": self.query_source_status,
            "retrieval_partial": self.partial,
            "coverage_notes": sorted(set(self.coverage_notes)),
            "authoritative_source_catalog": {
                "version": self.catalog_version,
                # Catalog entries are navigation/coverage metadata, never
                # fabricated evidence hits.
                "entries": self.source_catalog,
                "entries_are_search_hits": False,
            },
            "raw_case_text_sent": False,
            "search_query_text_omitted": True,
        }


@dataclass
class CandidateEvidenceBundle:
    """Candidate-specific citation metadata returned by targeted retrieval.

    The mapping is produced deterministically from citation titles.  A search
    hit is not attached to a pathogen merely because the search engine
    returned it: the title must explicitly contain the validated canonical
    pathogen name.  This deliberately favors precision over recall.
    """

    citations: List[Dict[str, Any]] = field(default_factory=list)
    citation_ids_by_candidate: Dict[str, List[str]] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    source_status: Dict[str, str] = field(default_factory=dict)
    unrelated_citation_count: int = 0

    @property
    def partial(self) -> bool:
        return bool(self.warnings) or any(
            value not in {"available", "not_needed"}
            for value in self.source_status.values()
        )

    def public_payload(self) -> Dict[str, Any]:
        return {
            "citations": self.citations,
            "candidate_coverage": [
                {
                    "canonical_latin_name": name,
                    "evidence_source_ids": list(source_ids),
                    "covered": bool(source_ids),
                }
                for name, source_ids in self.citation_ids_by_candidate.items()
            ],
            "warnings": sorted(set(self.warnings)),
            "source_status": self.source_status,
            "retrieval_partial": self.partial,
            "unrelated_citation_count": self.unrelated_citation_count,
            "raw_case_text_sent": False,
            "search_query_text_omitted": True,
        }


class MedicalEvidenceRetriever:
    """Retrieve citation metadata from Europe PMC and PubMed.

    No abstract or full text is persisted.  Titles and identifiers are enough
    for an auditable evidence link while keeping the runtime small and avoiding
    accidental redistribution of copyrighted text.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 7.0,
        max_results_per_query: int = 3,
        max_queries: int = 3,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_results_per_query = max(1, min(int(max_results_per_query), 5))
        self.max_queries = max(1, min(int(max_queries), 5))
        self.transport = transport

    async def retrieve(
        self,
        queries: Sequence[str],
        *,
        max_results_per_query: Optional[int] = None,
    ) -> RetrievalBundle:
        cleaned: List[str] = []
        for value in queries:
            query = generalized_query(value)
            if query and query.casefold() not in {item.casefold() for item in cleaned}:
                cleaned.append(query)
            if len(cleaned) >= self.max_queries:
                break
        if not cleaned:
            return RetrievalBundle(
                warnings=["retrieval_no_generalized_query"],
                source_status={"europe_pmc": "not_queried", "pubmed": "not_queried"},
            )

        effective_max_results = self.max_results_per_query
        if max_results_per_query is not None:
            effective_max_results = max(1, min(int(max_results_per_query), 10))

        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=self.transport,
        ) as client:
            europe_task = self._retrieve_europe_pmc(
                client, cleaned, effective_max_results
            )
            pubmed_task = self._retrieve_pubmed(
                client, cleaned, effective_max_results
            )
            europe, pubmed = await asyncio.gather(europe_task, pubmed_task)

        citations = _deduplicate_publications([*europe.citations, *pubmed.citations])
        return RetrievalBundle(
            citations=citations,
            warnings=[*europe.warnings, *pubmed.warnings],
            source_status={**europe.source_status, **pubmed.source_status},
        )

    async def _retrieve_europe_pmc(
        self,
        client: httpx.AsyncClient,
        queries: Sequence[str],
        max_results_per_query: int,
    ) -> RetrievalBundle:
        bundle = RetrievalBundle(source_status={"europe_pmc": "available"})
        try:
            for query in queries:
                response = await client.get(EUROPE_PMC_SEARCH_URL, params={
                    "query": query,
                    "format": "json",
                    "pageSize": max_results_per_query,
                    "resultType": "core",
                })
                response.raise_for_status()
                payload = response.json()
                for result in ((payload.get("resultList") or {}).get("result") or []):
                    if not isinstance(result, dict):
                        continue
                    source_id = str(result.get("pmid") or result.get("pmcid") or result.get("id") or "").strip()
                    if not source_id:
                        continue
                    doi = str(result.get("doi") or "").strip() or None
                    url = (
                        "https://pubmed.ncbi.nlm.nih.gov/%s/" % source_id
                        if result.get("pmid")
                        else "https://europepmc.org/article/%s/%s" % (
                            str(result.get("source") or "MED"), source_id,
                        )
                    )
                    bundle.citations.append({
                        "citation_id": "epmc_%s" % re.sub(r"[^A-Za-z0-9._-]", "", source_id),
                        "source": "Europe PMC",
                        "source_id": source_id,
                        "title": str(result.get("title") or "Untitled record")[:600],
                        "journal": str(result.get("journalTitle") or "")[:240] or None,
                        "year": str(result.get("pubYear") or "")[:12] or None,
                        "doi": doi,
                        "url": url,
                        "query_id": query_id(query),
                    })
        except (httpx.HTTPError, ValueError, TypeError):
            bundle.source_status["europe_pmc"] = "unavailable"
            bundle.warnings.append("retrieval_europe_pmc_unavailable")
        return bundle

    async def _retrieve_pubmed(
        self,
        client: httpx.AsyncClient,
        queries: Sequence[str],
        max_results_per_query: int,
    ) -> RetrievalBundle:
        bundle = RetrievalBundle(source_status={"pubmed": "available"})
        try:
            for query in queries:
                search = await client.get(NCBI_PUBMED_SEARCH_URL, params={
                    "db": "pubmed",
                    "term": query,
                    "retmode": "json",
                    "retmax": max_results_per_query,
                    "tool": "owlpath-development-agent",
                })
                search.raise_for_status()
                ids = [str(item) for item in ((search.json().get("esearchresult") or {}).get("idlist") or [])]
                if not ids:
                    continue
                summary = await client.get(NCBI_PUBMED_SUMMARY_URL, params={
                    "db": "pubmed",
                    "id": ",".join(ids),
                    "retmode": "json",
                    "tool": "owlpath-development-agent",
                })
                summary.raise_for_status()
                result_map = summary.json().get("result") or {}
                for pmid in ids:
                    item = result_map.get(pmid) or {}
                    if not isinstance(item, dict):
                        continue
                    bundle.citations.append({
                        "citation_id": "pubmed_%s" % pmid,
                        "source": "PubMed",
                        "source_id": pmid,
                        "title": str(item.get("title") or "Untitled record")[:600],
                        "journal": str(item.get("fulljournalname") or item.get("source") or "")[:240] or None,
                        "year": str(item.get("pubdate") or "")[:24] or None,
                        "doi": _summary_doi(item),
                        "url": "https://pubmed.ncbi.nlm.nih.gov/%s/" % pmid,
                        "query_id": query_id(query),
                    })
        except (httpx.HTTPError, ValueError, TypeError):
            bundle.source_status["pubmed"] = "unavailable"
            bundle.warnings.append("retrieval_pubmed_unavailable")
        return bundle


def _summary_doi(item: Dict[str, Any]) -> Optional[str]:
    for article_id in item.get("articleids") or []:
        if isinstance(article_id, dict) and str(article_id.get("idtype") or "").lower() == "doi":
            value = str(article_id.get("value") or "").strip()
            if value:
                return value
    return None


def _normalized_doi(value: Any) -> str:
    """Return a comparison key for a DOI without changing its display value."""

    text = str(value or "").strip().casefold()
    text = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", text)
    text = re.sub(r"^doi:\s*", "", text)
    return text.rstrip(".,;:)")


def _publication_identity_tokens(item: Mapping[str, Any]) -> Set[str]:
    """Return stable identity tokens for one citation metadata record.

    PMID and DOI are the only cross-catalog publication identities used here.
    A catalog-local fallback is retained only to collapse duplicate pages from
    the same source.  We intentionally do not infer identity from a similar
    title, journal, or year.
    """

    tokens: Set[str] = set()
    source = str(item.get("source") or "").strip().casefold()
    source_id = str(item.get("source_id") or "").strip()
    if source in {"pubmed", "europe pmc"} and re.fullmatch(r"\d{1,10}", source_id):
        tokens.add("pmid:%s" % source_id)
    doi = _normalized_doi(item.get("doi"))
    if doi:
        tokens.add("doi:%s" % doi)
    if not tokens:
        tokens.add("source-record:%s:%s" % (source, source_id or str(item.get("url") or "")))
    return tokens


def _source_provenance(item: Mapping[str, Any]) -> Dict[str, Any]:
    """Keep source metadata when equivalent records are published once."""

    return {
        "source": str(item.get("source") or "").strip(),
        "source_id": str(item.get("source_id") or "").strip(),
        "citation_id": str(item.get("citation_id") or "").strip(),
        "url": str(item.get("url") or "").strip(),
        "query_id": str(item.get("query_id") or "").strip(),
    }


def _deduplicate_publications(citations: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Publish one metadata record per PMID/DOI while retaining provenance.

    Europe PMC and PubMed routinely return the same paper.  Counting both as
    independent evidence would overstate support.  The returned record keeps
    its preferred display source plus ``source_provenance`` for every merged
    catalog record.  This is metadata-level de-duplication only; it is not an
    abstract or full-text semantic validation step.
    """

    groups: List[Dict[str, Any]] = []
    for index, raw in enumerate(citations):
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        identities = _publication_identity_tokens(item)
        matches = [group for group in groups if group["identities"].intersection(identities)]
        if not matches:
            groups.append({"identities": set(identities), "items": [(index, item)]})
            continue
        target = matches[0]
        target["identities"].update(identities)
        target["items"].append((index, item))
        for duplicate in matches[1:]:
            target["identities"].update(duplicate["identities"])
            target["items"].extend(duplicate["items"])
            groups.remove(duplicate)

    def source_rank(item: Mapping[str, Any]) -> int:
        source = str(item.get("source") or "").casefold()
        return 0 if source == "pubmed" else 1 if source == "europe pmc" else 2

    merged: List[Dict[str, Any]] = []
    for group in groups:
        ordered = sorted(
            group["items"],
            key=lambda pair: (source_rank(pair[1]), pair[0], str(pair[1].get("citation_id") or "")),
        )
        primary = dict(ordered[0][1])
        if len(ordered) > 1:
            primary["source_provenance"] = [
                _source_provenance(item) for _, item in ordered
            ]
            cross_catalog_identities = sorted(
                token for token in group["identities"]
                if token.startswith("pmid:") or token.startswith("doi:")
            )
            if cross_catalog_identities:
                primary["publication_identity"] = cross_catalog_identities[0]
        merged.append(primary)
    return merged


def build_retrieval_queries(
    specialist_outputs: Iterable[Dict[str, Any]], *, max_queries: int = 3
) -> List[str]:
    """Build non-patient-level searches from normalized specialist output."""

    candidates: List[str] = []
    syndromes: List[str] = []
    exposures: List[str] = []
    for output in specialist_outputs:
        for item in output.get("candidate_pool") or output.get("pathogen_candidates") or []:
            if isinstance(item, dict):
                # ``owlpath.specialist.v1`` uses canonical_latin_name.  Keep
                # the older aliases for read compatibility, but prefer the
                # v3 contract so a populated specialist candidate pool cannot
                # accidentally degrade into retrieval_no_generalized_query.
                name = (
                    item.get("canonical_latin_name")
                    or item.get("canonical_name")
                    or item.get("name")
                )
            else:
                name = item
            value = generalized_query(str(name or ""), max_length=100)
            if value:
                candidates.append(value)
        for item in output.get("syndromes") or []:
            if isinstance(item, dict):
                item = item.get("name") or item.get("label")
            value = generalized_query(str(item or ""), max_length=100)
            if value:
                syndromes.append(value)
        for item in output.get("exposure_concepts") or []:
            value = generalized_query(str(item or ""), max_length=100)
            if value:
                exposures.append(value)

    base = " OR ".join(list(dict.fromkeys(candidates))[:5])
    context = " ".join(list(dict.fromkeys([*syndromes, *exposures]))[:4])
    queries: List[str] = []
    if base:
        queries.append("%s %s pathogen infection" % (base, context))
    for name in list(dict.fromkeys(candidates))[: max(0, max_queries - len(queries))]:
        queries.append("%s human infection diagnosis" % name)
    if not queries and context:
        queries.append("%s infectious pathogen differential diagnosis" % context)
    return [generalized_query(item) for item in queries[:max_queries] if generalized_query(item)]


_FEDERATED_CONCEPT_KINDS = frozenset({
    "pathogen",
    "syndrome",
    "exposure",
    "host_factor",
    "anatomy",
    "acquisition",
    "test_context",
    "epidemiology",
    "geography",
    "season",
    "geo_season",
    "clinical_problem",
    "mechanism",
})


def _as_mapping(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(mode="json")
        except TypeError:
            dumped = model_dump()
        if isinstance(dumped, Mapping):
            return dict(dumped)
    return {}


def _safe_external_concept(value: Any, *, max_words: int = 10) -> str:
    """Accept only short normalized English/Latin concepts for search.

    This deliberately refuses CJK and prose-like values.  The external-search
    contract is ``term_en``/canonical terminology, not a translated or sliced
    patient narrative.  Identifiers are removed before the remaining
    allow-list and word-count checks are applied.
    """

    raw = generalized_query(str(value or ""), max_length=120)
    if not raw or _CJK.search(raw):
        return ""
    cleaned = _SAFE_CONCEPT_CHARS.sub(" ", raw)
    cleaned = _SPACE.sub(" ", cleaned).strip(" ,./-")
    words = cleaned.split()
    if not words or len(words) > max_words:
        return ""
    # A concept made solely of boilerplate cannot drive a meaningful search.
    if all(word.casefold().strip("'()+,./-") in _QUERY_STOPWORDS for word in words):
        return ""
    return cleaned[:100]


def _append_federated_concept(
    concepts: List[Tuple[str, str]], kind: str, value: Any
) -> None:
    normalized_kind = str(kind or "").strip().casefold()
    if normalized_kind not in _FEDERATED_CONCEPT_KINDS:
        return
    term = _safe_external_concept(value)
    if not term:
        return
    key = (normalized_kind, term.casefold())
    if key not in {(item_kind, item_term.casefold()) for item_kind, item_term in concepts}:
        concepts.append((normalized_kind, term))


def _extract_federated_concepts(
    specialist_outputs: Iterable[Any],
    *,
    valid_fragment_ids: Optional[Set[str]] = None,
) -> List[Tuple[str, str]]:
    """Read only explicit normalized concepts and canonical pathogen names.

    Summaries, observations, rationales and source fragments are intentionally
    ignored even when present on the specialist payload.
    """

    concepts: List[Tuple[str, str]] = []
    for raw_output in specialist_outputs:
        output = _as_mapping(raw_output)
        for raw_concept in output.get("retrieval_concepts") or []:
            concept = _as_mapping(raw_concept)
            if not concept or concept.get("negated") is True:
                continue
            source_fragment_ids = {
                str(item) for item in concept.get("source_fragment_ids") or []
                if str(item)
            }
            if valid_fragment_ids is not None and (
                not source_fragment_ids
                or bool(source_fragment_ids.difference(valid_fragment_ids))
            ):
                continue
            kind = (
                concept.get("kind")
                or concept.get("concept_kind")
                or concept.get("type")
            )
            term = (
                concept.get("canonical_term_en")
                or concept.get("term_en")
                or concept.get("canonical_term")
                or concept.get("label_en")
                or concept.get("name_en")
            )
            _append_federated_concept(concepts, str(kind or ""), term)

        for raw_candidate in (
            output.get("candidate_pool")
            or output.get("candidate_hypotheses")
            or output.get("pathogen_candidates")
            or []
        ):
            candidate = _as_mapping(raw_candidate)
            source_fragment_ids = {
                str(item) for item in candidate.get("source_fragment_ids") or []
                if str(item)
            }
            if valid_fragment_ids is not None and (
                not source_fragment_ids
                or bool(source_fragment_ids.difference(valid_fragment_ids))
            ):
                continue
            name = (
                candidate.get("canonical_latin_name")
                or candidate.get("canonical_name")
                or candidate.get("name")
            ) if candidate else raw_candidate
            _append_federated_concept(concepts, "pathogen", name)

        # Read-compatible structured fields.  As above, only normalized values
        # are accepted; free-text observations and summaries are never read.
        for raw_syndrome in output.get("syndromes") or []:
            syndrome = _as_mapping(raw_syndrome)
            value = (
                syndrome.get("term_en")
                or syndrome.get("canonical_term")
                or syndrome.get("name")
                or syndrome.get("label")
            ) if syndrome else raw_syndrome
            _append_federated_concept(concepts, "syndrome", value)
        for raw_exposure in output.get("exposure_concepts") or []:
            exposure = _as_mapping(raw_exposure)
            value = (
                exposure.get("term_en")
                or exposure.get("canonical_term")
                or exposure.get("name")
                or exposure.get("label")
            ) if exposure else raw_exposure
            _append_federated_concept(concepts, "exposure", value)
    return concepts


def build_federated_query_plan(
    specialist_outputs: Iterable[Any],
    *,
    max_queries_per_intent: int = 3,
    valid_fragment_ids: Optional[Set[str]] = None,
) -> List[RetrievalQueryPlanItem]:
    """Create separate literature, similar-case and public-health intents."""

    limit = max(1, min(int(max_queries_per_intent), 5))
    concepts = _extract_federated_concepts(
        specialist_outputs,
        valid_fragment_ids=valid_fragment_ids,
    )
    if not concepts:
        return []

    by_kind: Dict[str, List[str]] = {}
    for kind, term in concepts:
        by_kind.setdefault(kind, []).append(term)
    pathogens = list(dict.fromkeys(by_kind.get("pathogen") or []))
    contextual = list(dict.fromkeys([
        *(by_kind.get("syndrome") or []),
        *(by_kind.get("exposure") or []),
        *(by_kind.get("anatomy") or []),
        *(by_kind.get("host_factor") or []),
        *(by_kind.get("test_context") or []),
        *(by_kind.get("acquisition") or []),
        *(by_kind.get("geo_season") or []),
        *(by_kind.get("epidemiology") or []),
    ]))
    primary = list(dict.fromkeys([*pathogens, *contextual]))
    plans: List[RetrievalQueryPlanItem] = []

    def add(intent: str, terms: Sequence[str], suffix: str) -> None:
        safe_terms = tuple(term for term in terms if _safe_external_concept(term))
        if not safe_terms:
            return
        kinds = tuple(dict.fromkeys(
            kind for kind, value in concepts if value in safe_terms
        ))
        quoted = " AND ".join('"%s"' % term for term in safe_terms[:4])
        query = generalized_query("%s %s" % (quoted, suffix))
        if query and not any(item.intent == intent and item.query.casefold() == query.casefold() for item in plans):
            plans.append(RetrievalQueryPlanItem(
                intent=intent,
                query=query,
                concept_terms=safe_terms[:4],
                concept_kinds=kinds,
            ))

    # Start with the combined clinical concept, then widen through individual
    # pathogen or contextual concepts.  This permits retrieval even before any
    # specialist has proposed a named pathogen.
    add("literature", primary[:4], "AND (human OR clinical OR infection)")
    for term in primary:
        if len([item for item in plans if item.intent == "literature"]) >= limit:
            break
        add("literature", [term], "AND (human OR clinical OR diagnosis)")

    case_terms = list(dict.fromkeys([*contextual, *pathogens]))
    add("similar_case", case_terms[:4], 'AND ("case report" OR "case series")')
    for term in case_terms:
        if len([item for item in plans if item.intent == "similar_case"]) >= limit:
            break
        add("similar_case", [term], 'AND ("case report" OR "case series")')

    public_terms = list(dict.fromkeys([
        *pathogens,
        *(by_kind.get("exposure") or []),
        *(by_kind.get("syndrome") or []),
        *(by_kind.get("host_factor") or []),
        *(by_kind.get("acquisition") or []),
        *(by_kind.get("geography") or []),
        *(by_kind.get("geo_season") or []),
        *(by_kind.get("epidemiology") or []),
    ]))
    for term in public_terms[:limit]:
        add(
            "public_health_guideline",
            [term],
            "AND (outbreak OR surveillance OR guideline)",
        )
    return plans


def _relevance_validation(
    title: str, concept_terms: Sequence[str]
) -> Dict[str, Any]:
    normalized_title = _normalize_evidence_text(title)
    exact_count = 0
    token_overlap = 0
    for term in concept_terms:
        normalized_term = _normalize_evidence_text(term)
        if not normalized_term:
            continue
        if normalized_term in normalized_title:
            exact_count += 1
            continue
        meaningful_tokens = {
            token for token in normalized_term.split()
            if len(token) >= 4 and token not in _QUERY_STOPWORDS
        }
        if meaningful_tokens.intersection(normalized_title.split()):
            token_overlap += 1
    if exact_count:
        status = "title_exact_concept_match"
    elif token_overlap:
        status = "title_token_overlap"
    else:
        status = "unverified_search_rank"
    return {
        "status": status,
        "method": "deterministic_title_concept_overlap_v1",
        "exact_concept_match_count": exact_count,
        "token_overlap_concept_count": token_overlap,
        "requires_human_review": status == "unverified_search_rank",
    }


def _aggregate_source_status(values: Sequence[str]) -> str:
    statuses = set(values)
    if not statuses:
        return "not_queried"
    if statuses == {"available"}:
        return "available"
    if "available" in statuses:
        return "partial"
    if statuses == {"not_needed"}:
        return "not_needed"
    if statuses == {"registered_reference_only"}:
        return "registered_reference_only"
    if statuses == {"not_queried"}:
        return "not_queried"
    return "unavailable"


class FederatedMedicalEvidenceRetriever:
    """Intent-aware, fail-open retrieval across literature and WHO notices.

    Europe PMC and PubMed are invoked one query-plan item at a time.  A broken
    endpoint or malformed response for one item therefore cannot prevent later
    searches.  WHO Disease Outbreak News is an official but explicitly
    non-exhaustive public-health source; absence of a result is never treated
    as evidence that an outbreak or pathogen is absent.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 7.0,
        max_results_per_query: int = 3,
        max_queries_per_intent: int = 3,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        literature_retriever: Optional[Any] = None,
    ) -> None:
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.max_results_per_query = max(1, min(int(max_results_per_query), 5))
        self.max_queries_per_intent = max(1, min(int(max_queries_per_intent), 5))
        self.transport = transport
        self.literature_retriever = literature_retriever or MedicalEvidenceRetriever(
            timeout_seconds=self.timeout_seconds,
            max_results_per_query=self.max_results_per_query,
            max_queries=1,
            transport=transport,
        )
        # A plan may contain up to nine items.  Bound fan-out so the NCBI and
        # Europe PMC public services are not hit by an avoidable request burst.
        self._literature_query_concurrency = 3

    async def retrieve_from_specialists(
        self,
        specialist_outputs: Iterable[Any],
        *,
        valid_fragment_ids: Optional[Set[str]] = None,
    ) -> FederatedRetrievalBundle:
        plan = build_federated_query_plan(
            specialist_outputs,
            max_queries_per_intent=self.max_queries_per_intent,
            valid_fragment_ids=valid_fragment_ids,
        )
        return await self.retrieve_query_plan(plan)

    async def retrieve_query_plan(
        self, query_plan: Sequence[RetrievalQueryPlanItem]
    ) -> FederatedRetrievalBundle:
        plan = list(query_plan)
        catalog = [dict(item) for item in AUTHORITATIVE_SOURCE_CATALOG]
        if not plan:
            return FederatedRetrievalBundle(
                warnings=["federated_retrieval_no_normalized_concept"],
                source_status={
                    "europe_pmc": "not_queried",
                    "pubmed": "not_queried",
                    "who_don": "not_queried",
                    "authoritative_source_catalog": "registered_reference_only",
                },
                query_plan=[],
                source_catalog=catalog,
                coverage_notes=[
                    "catalog_entries_are_registered_references_not_search_hits",
                    "who_don_is_official_but_non_exhaustive",
                ],
            )

        async def literature_one(
            item: RetrievalQueryPlanItem,
        ) -> Tuple[RetrievalQueryPlanItem, RetrievalBundle]:
            async with literature_semaphore:
                try:
                    result = await self.literature_retriever.retrieve([item.query])
                    if isinstance(result, RetrievalBundle):
                        return item, result
                except Exception:
                    pass
            return item, RetrievalBundle(
                warnings=["federated_literature_query_unexpected_failure"],
                source_status={
                    "europe_pmc": "unavailable",
                    "pubmed": "unavailable",
                },
            )

        literature_semaphore = asyncio.Semaphore(
            self._literature_query_concurrency
        )
        public_plan = [
            item for item in plan if item.intent == "public_health_guideline"
        ]
        literature_results, who_results = await asyncio.gather(
            asyncio.gather(*(literature_one(item) for item in plan)),
            self._retrieve_who_don(public_plan),
        )

        citations: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        warnings: List[str] = []
        status_values: Dict[str, List[str]] = {
            "europe_pmc": [],
            "pubmed": [],
            "who_don": [],
        }
        query_source_status: Dict[str, Dict[str, str]] = {}

        for item, result in literature_results:
            per_query = query_source_status.setdefault(item.plan_item_id, {})
            for source in ("europe_pmc", "pubmed"):
                status = result.source_status.get(source, "unavailable")
                status_values[source].append(status)
                per_query[source] = status
                if status != "available":
                    warnings.append(
                        "federated_%s_query_unavailable:%s"
                        % (source, item.plan_item_id)
                    )
            warnings.extend(result.warnings)
            per_query["who_don"] = (
                "pending" if item.intent == "public_health_guideline" else "not_needed"
            )
            source_kind = {
                "literature": "biomedical_literature",
                "similar_case": "similar_case_literature",
                "public_health_guideline": "guideline_or_public_health_literature",
            }.get(item.intent, "biomedical_literature")
            for raw_citation in result.citations:
                citation = dict(raw_citation)
                citation.update({
                    "intent": item.intent,
                    "source_kind": source_kind,
                    "relevance_validation": _relevance_validation(
                        str(citation.get("title") or ""), item.concept_terms
                    ),
                })
                key = (
                    str(citation.get("source") or ""),
                    str(citation.get("source_id") or citation.get("url") or ""),
                    item.intent,
                )
                citations.setdefault(key, citation)

        for item, result in who_results:
            per_query = query_source_status.setdefault(item.plan_item_id, {})
            status = result.source_status.get("who_don", "unavailable")
            status_values["who_don"].append(status)
            per_query["who_don"] = status
            if status != "available":
                warnings.append(
                    "federated_who_don_query_unavailable:%s" % item.plan_item_id
                )
            warnings.extend(result.warnings)
            for raw_citation in result.citations:
                citation = dict(raw_citation)
                key = (
                    str(citation.get("source") or ""),
                    str(citation.get("source_id") or citation.get("url") or ""),
                    item.intent,
                )
                citations.setdefault(key, citation)

        if not public_plan:
            status_values["who_don"].append("not_needed")
        source_status = {
            source: _aggregate_source_status(values)
            for source, values in status_values.items()
        }
        source_status["authoritative_source_catalog"] = "registered_reference_only"
        return FederatedRetrievalBundle(
            citations=list(citations.values()),
            warnings=list(dict.fromkeys(warnings)),
            source_status=source_status,
            query_plan=plan,
            query_source_status=query_source_status,
            source_catalog=catalog,
            coverage_notes=[
                "catalog_entries_are_registered_references_not_search_hits",
                "who_don_is_official_but_non_exhaustive",
                "no_search_hit_must_not_be_interpreted_as_pathogen_absence",
            ],
        )

    async def _retrieve_who_don(
        self, query_plan: Sequence[RetrievalQueryPlanItem]
    ) -> List[Tuple[RetrievalQueryPlanItem, RetrievalBundle]]:
        if not query_plan:
            return []

        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=self.transport,
        ) as client:
            async def retrieve_one(
                item: RetrievalQueryPlanItem,
            ) -> Tuple[RetrievalQueryPlanItem, RetrievalBundle]:
                bundle = RetrievalBundle(source_status={"who_don": "available"})
                # Use at most two already-normalized concepts, never the
                # literature query string or source narrative.
                concepts = [
                    _safe_external_concept(value)
                    for value in item.concept_terms[:2]
                ]
                concepts = [value for value in concepts if value]
                if not concepts:
                    bundle.source_status["who_don"] = "not_queried"
                    bundle.warnings.append("retrieval_who_don_no_safe_concept")
                    return item, bundle
                escaped = [value.replace("'", "''") for value in concepts]
                title_filter = " or ".join(
                    "contains(Title,'%s')" % value for value in escaped
                )
                try:
                    response = await client.get(WHO_DON_API_URL, params={
                        "$select": "Title,PublicationDate,ItemDefaultUrl,DonId",
                        "$top": self.max_results_per_query,
                        "$orderby": "PublicationDate desc",
                        "$filter": title_filter,
                    })
                    response.raise_for_status()
                    payload = response.json()
                    records = payload.get("value") if isinstance(payload, dict) else []
                    if not isinstance(records, list):
                        raise ValueError("WHO DON response does not contain a value list")
                    for record in records:
                        if not isinstance(record, dict):
                            continue
                        title = str(record.get("Title") or "").strip()[:600]
                        if not title:
                            continue
                        relevance = _relevance_validation(title, concepts)
                        # A broad official feed record is not silently promoted
                        # to patient-specific evidence without title overlap.
                        if relevance["status"] == "unverified_search_rank":
                            continue
                        source_id = str(record.get("DonId") or "").strip()
                        if not source_id:
                            source_id = hashlib.sha256(title.encode("utf-8")).hexdigest()[:16]
                        url = str(record.get("ItemDefaultUrl") or "").strip()
                        if re.fullmatch(r"/\d{4}-DON\d+", url, re.IGNORECASE):
                            url = (
                                "https://www.who.int/emergencies/"
                                "disease-outbreak-news/item" + url
                            )
                        elif url.startswith("/"):
                            url = "https://www.who.int" + url
                        if not url.startswith("https://www.who.int/"):
                            url = "https://www.who.int/emergencies/disease-outbreak-news"
                        bundle.citations.append({
                            "citation_id": "who_don_%s" % re.sub(
                                r"[^A-Za-z0-9._-]", "", source_id
                            ),
                            "source": "WHO Disease Outbreak News",
                            "source_id": source_id,
                            "title": title,
                            "journal": None,
                            "year": str(record.get("PublicationDate") or "")[:24] or None,
                            "doi": None,
                            "url": url,
                            "query_id": item.plan_item_id,
                            "intent": item.intent,
                            "source_kind": "public_health_outbreak_notice",
                            "relevance_validation": relevance,
                            "coverage_limitation": "official_but_non_exhaustive",
                        })
                except Exception:
                    # The connector is deliberately fail-open.  Even an
                    # unforeseen parser/adapter error must remain scoped to
                    # this WHO query and must not discard literature results.
                    bundle.source_status["who_don"] = "unavailable"
                    bundle.warnings.append("retrieval_who_don_unavailable")
                return item, bundle

            return list(await asyncio.gather(*(retrieve_one(item) for item in query_plan)))


def build_candidate_retrieval_queries(
    canonical_names: Sequence[str], *, max_candidates: int = 5
) -> List[Tuple[str, str]]:
    """Build one de-identified literature query per validated pathogen name.

    The orchestrator calls this only after taxonomy and Top-5 validation.  No
    case prose, syndrome narrative, dates, or identifiers are accepted as
    query context here; only the short canonical taxon name crosses the search
    boundary.
    """

    pairs: List[Tuple[str, str]] = []
    seen: set[str] = set()
    for raw_name in canonical_names:
        name = generalized_query(str(raw_name or ""), max_length=120)
        normalized = _normalize_taxon_name(name)
        if not name or not normalized or normalized in seen:
            continue
        seen.add(normalized)
        # Use an explicit boolean conjunction.  A plain suffix such as
        # ``human infection`` is interpreted too loosely by Europe PMC and may
        # rank papers about other organisms above the requested species.
        pairs.append((name, generalized_query(
            '"%s" AND (human OR patient OR clinical OR infection)' % name
        )))
        if len(pairs) >= max(1, min(int(max_candidates), 5)):
            break
    return [(name, query) for name, query in pairs if query]


def map_candidate_specific_citations(
    canonical_names: Sequence[str], citations: Sequence[Dict[str, Any]]
) -> Dict[str, List[str]]:
    """Map only title-verifiable citations to a canonical pathogen.

    Search rank alone is not evidence of relevance.  Requiring both the
    complete normalized canonical name and a human-clinical cue in the title
    prevents unrelated animal/basic-science records from being silently
    attached to a Top-5 candidate.  In particular, a paper that is explicitly
    about fish, aquaculture, veterinary work, or an animal experiment is
    excluded unless its title also has an explicit human anchor.  This is a
    title-metadata filter, not an abstract/full-text relevance judgement.
    """

    mapping: Dict[str, List[str]] = {
        str(name): [] for name in canonical_names if str(name).strip()
    }
    for canonical_name in mapping:
        normalized_name = _normalize_evidence_text(canonical_name)
        if not normalized_name:
            continue
        for item in citations:
            if not isinstance(item, dict):
                continue
            title = _normalize_evidence_text(str(item.get("title") or ""))
            if (
                not title
                or normalized_name not in title
                or not _candidate_title_is_human_clinically_relevant(title)
            ):
                continue
            source_id = str(item.get("citation_id") or item.get("source_id") or "").strip()[:160]
            if source_id and source_id not in mapping[canonical_name]:
                mapping[canonical_name].append(source_id)
    return mapping


def _candidate_title_is_human_clinically_relevant(normalized_title: str) -> bool:
    """Apply the deterministic human-context gate used for Top-5 citations.

    ``normalized_title`` must be the title only.  The purpose is narrow: do
    not let an animal/aquaculture paper become a patient-evidence link merely
    because it contains a generic disease word such as ``septicemia``.
    """

    if not _HUMAN_CLINICAL_TITLE_CUE.search(normalized_title):
        return False
    if (
        _NON_HUMAN_TITLE_CONTEXT.search(normalized_title)
        or _NON_HUMAN_BINOMIAL_HOST_EXPERIMENT.search(normalized_title)
    ):
        return bool(_EXPLICIT_HUMAN_TITLE_ANCHOR.search(normalized_title))
    return True


def _candidate_title_relevance_validation(title: str) -> Dict[str, Any]:
    """Describe a candidate binding without claiming abstract/full-text review."""

    normalized_title = _normalize_evidence_text(title)
    return {
        "status": "title_exact_concept_match",
        "method": "deterministic_title_candidate_human_context_v2",
        "evidence_scope": "title_metadata_only",
        "requires_human_review": True,
        "non_human_context_detected": bool(
            _NON_HUMAN_TITLE_CONTEXT.search(normalized_title)
            or _NON_HUMAN_BINOMIAL_HOST_EXPERIMENT.search(normalized_title)
        ),
    }


async def retrieve_candidate_evidence(
    retriever: Any, canonical_names: Sequence[str]
) -> CandidateEvidenceBundle:
    """Retrieve and precision-filter evidence for each final Top-5 candidate.

    Calls run concurrently and fail open.  Individual endpoint failures and
    absent relevant titles are recorded as warnings; they never invalidate an
    otherwise contract-valid development result.
    """

    pairs = build_candidate_retrieval_queries(canonical_names)
    if not pairs:
        return CandidateEvidenceBundle(
            warnings=["targeted_retrieval_no_validated_candidate_name"],
            source_status={"europe_pmc": "not_queried", "pubmed": "not_queried"},
        )

    async def retrieve_one(query: str) -> RetrievalBundle:
        try:
            if isinstance(retriever, MedicalEvidenceRetriever):
                # Targeted enrichment favors precision, but needs a deeper
                # result window because the latest few search hits may mention
                # the organism only in abstracts rather than in titles.
                result = await retriever.retrieve(
                    [query], max_results_per_query=10
                )
            else:
                result = await retriever.retrieve([query])
            if isinstance(result, RetrievalBundle):
                return result
        except Exception:
            pass
        return RetrievalBundle(
            warnings=["targeted_retrieval_unexpected_failure"],
            source_status={"europe_pmc": "unavailable", "pubmed": "unavailable"},
        )

    retrieved = await asyncio.gather(*(retrieve_one(query) for _, query in pairs))
    warnings: List[str] = []
    statuses: Dict[str, List[str]] = {}
    coverage: Dict[str, List[str]] = {}
    relevant_citations: Dict[Tuple[str, str], Dict[str, Any]] = {}
    unrelated_count = 0

    for (canonical_name, _), bundle in zip(pairs, retrieved):
        warnings.extend(bundle.warnings)
        for source, status in bundle.source_status.items():
            statuses.setdefault(source, []).append(status)
        candidate_mapping = map_candidate_specific_citations(
            [canonical_name], bundle.citations
        )
        source_ids = candidate_mapping.get(canonical_name, [])
        coverage[canonical_name] = source_ids
        relevant_id_set = set(source_ids)
        for item in bundle.citations:
            if not isinstance(item, dict):
                continue
            evidence_id = str(item.get("citation_id") or item.get("source_id") or "").strip()[:160]
            if evidence_id in relevant_id_set:
                key = (str(item.get("source") or ""), evidence_id)
                # Candidate-specific publication binding is deliberately a
                # deterministic title-level check.  Preserve that scope in
                # the trace rather than implying abstract/full-text review.
                bound_item = dict(item)
                bound_item["relevance_validation"] = _candidate_title_relevance_validation(
                    str(item.get("title") or "")
                )
                relevant_citations.setdefault(key, bound_item)
            elif evidence_id:
                unrelated_count += 1

        # Only report a coverage miss when at least one literature endpoint
        # answered this candidate query.  A fully offline search already has a
        # more accurate transport warning.
        if not source_ids and any(value == "available" for value in bundle.source_status.values()):
            warnings.append("targeted_retrieval_no_candidate_specific_source")

    aggregated_status: Dict[str, str] = {}
    for source, values in statuses.items():
        unique = set(values)
        if unique == {"available"}:
            aggregated_status[source] = "available"
        elif "available" in unique:
            aggregated_status[source] = "partial"
        elif unique == {"not_queried"}:
            aggregated_status[source] = "not_queried"
        else:
            aggregated_status[source] = "unavailable"

    return CandidateEvidenceBundle(
        citations=list(relevant_citations.values()),
        citation_ids_by_candidate=coverage,
        warnings=list(dict.fromkeys(warnings)),
        source_status=aggregated_status,
        unrelated_citation_count=unrelated_count,
    )


def _normalize_evidence_text(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


class TaxonomyResolver:
    """Resolve model-proposed names against a versioned cache, then NCBI.

    The resolver never invents an identifier.  A failed network lookup remains
    visibly unresolved so the synthesis repair/fallback logic can make an
    explicit technical decision.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 6.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        terms_path: Optional[Path] = None,
        cache_path: Optional[Path] = None,
        max_attempts: int = 3,
        retry_backoff_seconds: float = 0.05,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        default_terms_path = (
            Path(__file__).resolve().parents[2] / "config" / "clinical_terms.zh-en.v1.json"
        )
        self.terms_path = terms_path or default_terms_path
        self.max_attempts = max(1, min(int(max_attempts), 5))
        self.retry_backoff_seconds = max(0.0, min(float(retry_backoff_seconds), 2.0))

        # Production persistence is enabled only for the default versioned
        # registry and an explicitly configured OwlPath data directory.  Tests
        # commonly inject a temporary terms_path; that must never create files
        # in the real application data directory unless cache_path is also
        # explicitly injected.
        configured_data_dir = os.getenv("OWLPATH_DATA_DIR")
        if cache_path is not None:
            self.cache_path: Optional[Path] = Path(cache_path)
        elif terms_path is None and configured_data_dir:
            self.cache_path = Path(configured_data_dir) / _TAXONOMY_CACHE_FILENAME
        else:
            self.cache_path = None

        self.registry_load_audit: Dict[str, Any] = {}
        self._registry_cache = self._load_registry_cache()
        self._positive_cache = self._load_positive_cache()
        self._cache_write_lock = threading.Lock()

    @staticmethod
    def _compiled_registry_policy_matches(payload: Dict[str, Any]) -> bool:
        """Require the human-readable registry policy to match code exactly.

        The JSON policy is documentation and release metadata, not executable
        policy.  Comparing it with the compiled allow-list prevents a registry
        edit from silently weakening rank checks.
        """

        policy = payload.get("taxonomy_rank_policy")
        if not isinstance(policy, dict):
            return False
        if policy.get("schema_version") != _REGISTRY_RANK_POLICY_SCHEMA:
            return False
        accepted_product_ranks = policy.get("accepted_product_ranks")
        if not isinstance(accepted_product_ranks, list) or {
            str(value) for value in accepted_product_ranks
        } != set(_REGISTRY_PRODUCT_RANKS):
            return False
        declared_rules = policy.get("mapping_rules")
        if not isinstance(declared_rules, dict) or set(declared_rules) != set(
            _REGISTRY_RANK_RULES
        ):
            return False
        for rule_id, compiled in _REGISTRY_RANK_RULES.items():
            declared = declared_rules.get(rule_id)
            if not isinstance(declared, dict):
                return False
            if declared.get("product_rank") != compiled["product_rank"]:
                return False
            declared_ncbi_ranks = declared.get("accepted_ncbi_ranks")
            if not isinstance(declared_ncbi_ranks, list) or {
                str(value) for value in declared_ncbi_ranks
            } != set(compiled["accepted_ncbi_ranks"]):
                return False
            if declared.get("required_lineage_anchor") != compiled[
                "required_lineage_anchor"
            ]:
                return False
        return True

    @staticmethod
    def _validate_registry_rank_assertion(
        term: Dict[str, Any],
    ) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
        """Validate an explicit rank assertion without inspecting the name.

        In particular, a string containing ``virus`` is never enough to turn
        NCBI ``no rank`` into a product ``virus_type``.  The versioned entry
        must select the dedicated mapping rule and carry the Viruses lineage
        anchor.
        """

        product_rank = term.get("taxonomic_rank")
        if not isinstance(product_rank, str) or not product_rank.strip():
            return None, "registry_entry_product_rank_missing"
        product_rank = product_rank.strip()
        if product_rank not in _REGISTRY_PRODUCT_RANKS:
            return None, "registry_entry_product_rank_non_concrete"

        ncbi_rank = term.get("ncbi_taxonomy_rank")
        if not isinstance(ncbi_rank, str) or not ncbi_rank.strip():
            return None, "registry_entry_ncbi_rank_missing"
        ncbi_rank = ncbi_rank.strip().casefold()

        mapping_rule = term.get("rank_mapping_rule")
        if not isinstance(mapping_rule, str) or not mapping_rule.strip():
            return None, "registry_entry_rank_mapping_rule_missing"
        mapping_rule = mapping_rule.strip()
        compiled_rule = _REGISTRY_RANK_RULES.get(mapping_rule)
        if compiled_rule is None:
            return None, "registry_entry_rank_mapping_rule_unknown"
        if product_rank != compiled_rule["product_rank"]:
            return None, "registry_entry_rank_mapping_conflict"
        if ncbi_rank not in compiled_rule["accepted_ncbi_ranks"]:
            return None, "registry_entry_ncbi_rank_conflict"

        raw_lineage_anchor = term.get("lineage_anchor")
        lineage_anchor = (
            raw_lineage_anchor.strip()
            if isinstance(raw_lineage_anchor, str) and raw_lineage_anchor.strip()
            else None
        )
        required_anchor = compiled_rule["required_lineage_anchor"]
        if lineage_anchor != required_anchor:
            return None, "registry_entry_lineage_anchor_conflict"

        return {
            "product_rank": product_rank,
            "ncbi_rank": ncbi_rank,
            "mapping_rule": mapping_rule,
            "lineage_anchor": lineage_anchor or "",
        }, None

    def _load_registry_cache(self) -> Dict[str, Dict[str, Any]]:
        try:
            payload = json.loads(self.terms_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            self.registry_load_audit = {
                "schema_version": "owlpath.registry-load-audit.v1",
                "status": "rejected",
                "accepted_entry_count": 0,
                "rejected_entry_count": 0,
                "rejection_reason_counts": {"registry_unreadable": 1},
            }
            return {}
        if not isinstance(payload, dict) or not self._compiled_registry_policy_matches(payload):
            self.registry_load_audit = {
                "schema_version": "owlpath.registry-load-audit.v1",
                "status": "rejected",
                "registry_version": payload.get("version") if isinstance(payload, dict) else None,
                "accepted_entry_count": 0,
                "rejected_entry_count": len(payload.get("pathogens") or [])
                if isinstance(payload, dict)
                else 0,
                "rejection_reason_counts": {"registry_rank_policy_invalid": 1},
            }
            return {}
        cache: Dict[str, Dict[str, Any]] = {}
        accepted_entries = 0
        rejected_entries = 0
        rejection_reason_counts: Dict[str, int] = {}

        def reject(reason_code: str) -> None:
            nonlocal rejected_entries
            rejected_entries += 1
            rejection_reason_counts[reason_code] = (
                rejection_reason_counts.get(reason_code, 0) + 1
            )

        for term in payload.get("pathogens") or []:
            if not isinstance(term, dict):
                reject("registry_entry_not_object")
                continue
            canonical_id = str(term.get("canonical_id") or "")
            match = re.fullmatch(r"NCBITaxon:(\d+)", canonical_id)
            if not match:
                reject("registry_entry_canonical_id_invalid")
                continue
            scientific_name = term.get("ncbi_scientific_name")
            if not isinstance(scientific_name, str) or not scientific_name.strip():
                reject("registry_entry_ncbi_scientific_name_missing")
                continue
            display_name = term.get("en")
            if not isinstance(display_name, str) or not display_name.strip():
                reject("registry_entry_display_name_missing")
                continue
            rank_assertion, rank_error = self._validate_registry_rank_assertion(term)
            if rank_assertion is None:
                reject(rank_error or "registry_entry_rank_assertion_invalid")
                continue
            record = {
                "ncbi_taxonomy_id": int(match.group(1)),
                "taxonomy_resolution_status": "cache_resolved",
                "canonical_latin_name": scientific_name.strip(),
                "name_i18n": {
                    "zh_cn": term.get("zh_cn"),
                    "en": display_name.strip(),
                    "status": "complete" if term.get("zh_cn") else "partial",
                },
                "product_taxonomic_rank": rank_assertion["product_rank"],
                "ncbi_taxonomy_rank": rank_assertion["ncbi_rank"],
                "taxonomy_rank_mapping_rule": rank_assertion["mapping_rule"],
                "taxonomy_lineage_anchor": rank_assertion["lineage_anchor"] or None,
                "taxonomy_resolution_reason_code": (
                    "versioned_registry_name_id_rank_verified"
                ),
            }
            aliases = [
                display_name,
                scientific_name,
                *(term.get("aliases") or []),
            ]
            normalized_aliases = {
                _normalize_taxon_name(str(alias or "")) for alias in aliases
            }
            normalized_aliases.discard("")
            if any(
                normalized in cache
                and cache[normalized]["ncbi_taxonomy_id"] != record["ncbi_taxonomy_id"]
                for normalized in normalized_aliases
            ):
                reject("registry_entry_alias_conflict")
                continue
            for normalized in normalized_aliases:
                cache[normalized] = record
            accepted_entries += 1

        self.registry_load_audit = {
            "schema_version": "owlpath.registry-load-audit.v1",
            "status": "loaded" if accepted_entries else "rejected",
            "registry_version": payload.get("version"),
            "policy_schema_version": _REGISTRY_RANK_POLICY_SCHEMA,
            "accepted_entry_count": accepted_entries,
            "rejected_entry_count": rejected_entries,
            "rejection_reason_counts": dict(sorted(rejection_reason_counts.items())),
        }
        return cache

    def _load_positive_cache(self) -> Dict[str, Dict[str, Any]]:
        if self.cache_path is None:
            return {}
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        if not isinstance(payload, dict) or payload.get("schema_version") != _TAXONOMY_CACHE_SCHEMA:
            return {}
        entries = payload.get("entries")
        if not isinstance(entries, dict):
            return {}

        cache: Dict[str, Dict[str, Any]] = {}
        for raw_key, raw_record in entries.items():
            if not isinstance(raw_record, dict):
                continue
            normalized = _normalize_taxon_name(str(raw_key or ""))
            taxonomy_id = raw_record.get("ncbi_taxonomy_id")
            canonical_name = str(raw_record.get("canonical_latin_name") or "").strip()
            verified_rank = str(raw_record.get("ncbi_taxonomy_rank") or "").strip().casefold()
            rank_assertion, _ = self._validate_registry_rank_assertion({
                "taxonomic_rank": raw_record.get("product_taxonomic_rank"),
                "ncbi_taxonomy_rank": verified_rank,
                "rank_mapping_rule": raw_record.get("taxonomy_rank_mapping_rule"),
                "lineage_anchor": raw_record.get("taxonomy_lineage_anchor"),
            })
            if (
                not normalized
                or not isinstance(taxonomy_id, int)
                or taxonomy_id <= 0
                or not canonical_name
                or rank_assertion is None
                or _normalize_taxon_name(canonical_name) != normalized
            ):
                continue
            cache[normalized] = {
                "ncbi_taxonomy_id": taxonomy_id,
                "taxonomy_resolution_status": "cache_resolved",
                "canonical_latin_name": canonical_name,
                "name_i18n": {"en": canonical_name, "status": "partial"},
                "ncbi_taxonomy_rank": verified_rank,
                "product_taxonomic_rank": rank_assertion["product_rank"],
                "taxonomy_rank_mapping_rule": rank_assertion["mapping_rule"],
                "taxonomy_lineage_anchor": rank_assertion["lineage_anchor"] or None,
                "taxonomy_resolution_reason_code": "persistent_verified_cache_match",
            }
        return cache

    async def resolve(self, names: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        resolved: Dict[str, Dict[str, Any]] = {}
        pending: List[Tuple[str, str]] = []
        for raw_name in names:
            name = str(raw_name or "").strip()
            normalized = _normalize_taxon_name(name)
            if not normalized or normalized in resolved:
                continue
            cached = self._registry_cache.get(normalized) or self._positive_cache.get(normalized)
            if cached:
                resolved[normalized] = dict(cached)
            else:
                pending.append((normalized, name))
        if not pending:
            return resolved

        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=self.transport,
        ) as client:
            results = await asyncio.gather(
                *(self._resolve_one(client, normalized, name) for normalized, name in pending)
            )
        for normalized, record in results:
            resolved[normalized] = record
        verified_records = {
            normalized: record
            for normalized, record in results
            if record.get("taxonomy_resolution_status") == "resolved"
        }
        if verified_records:
            await self._persist_verified_records(verified_records)
        return resolved

    @staticmethod
    def _is_concrete_rank(
        rank: str,
        scientific_name: str = "",
        *,
        product_rank: Optional[str] = None,
        lineage_anchor: Optional[str] = None,
        verification_reason_code: Optional[str] = None,
    ) -> bool:
        """Check a rank mapping without deriving specificity from the name.

        ``scientific_name`` remains as a compatibility parameter for existing
        callers, but is deliberately ignored.  An NCBI ``no rank`` record is
        concrete only when it is explicitly mapped to ``virus_type`` and its
        Viruses lineage was verified by the registry or NCBI resolver.
        """

        normalized_rank = str(rank or "").strip().casefold()
        normalized_product_rank = (
            str(product_rank).strip() if product_rank is not None else None
        )
        if normalized_rank in {"species", "subspecies"}:
            return normalized_product_rank in {None, "species"}
        if normalized_rank in {"species group", "species subgroup"}:
            return normalized_product_rank in {None, "species_complex"}
        if normalized_rank != "no rank":
            return False
        verified_lineage = lineage_anchor == _VIRUS_LINEAGE_ANCHOR or (
            verification_reason_code
            in {
                "versioned_registry_name_id_rank_verified",
                "ncbi_name_rank_lineage_verified",
                "persistent_verified_cache_match",
            }
        )
        return normalized_product_rank == "virus_type" and verified_lineage

    @staticmethod
    def _rank_assertion_from_ncbi_summary(
        ncbi_rank: str,
        ncbi_division: str,
    ) -> Optional[Dict[str, str]]:
        normalized_rank = str(ncbi_rank or "").strip().casefold()
        normalized_division = str(ncbi_division or "").strip().casefold()
        if normalized_rank in {"species", "subspecies"}:
            return {
                "product_rank": "species",
                "ncbi_rank": normalized_rank,
                "mapping_rule": "ncbi_species_to_product_species_v1",
                "lineage_anchor": "",
            }
        if normalized_rank in {"species group", "species subgroup"}:
            return {
                "product_rank": "species_complex",
                "ncbi_rank": normalized_rank,
                "mapping_rule": "ncbi_species_group_to_product_species_complex_v1",
                "lineage_anchor": "",
            }
        if normalized_rank == "no rank" and normalized_division == "viruses":
            return {
                "product_rank": "virus_type",
                "ncbi_rank": normalized_rank,
                "mapping_rule": "ncbi_unranked_virus_to_product_virus_type_v1",
                "lineage_anchor": _VIRUS_LINEAGE_ANCHOR,
            }
        return None

    async def _request_with_retry(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        params: Dict[str, Any],
        stage: str,
    ) -> Tuple[Optional[httpx.Response], Optional[str]]:
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = await client.get(url, params=params)
            except httpx.HTTPError:
                if attempt >= self.max_attempts:
                    return None, "ncbi_%s_network_error_exhausted" % stage
            else:
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt >= self.max_attempts:
                        return None, "ncbi_%s_retryable_http_exhausted" % stage
                elif response.is_error:
                    return None, "ncbi_%s_nonretryable_http_error" % stage
                else:
                    return response, None
            if self.retry_backoff_seconds:
                await asyncio.sleep(self.retry_backoff_seconds * (2 ** (attempt - 1)))
        return None, "ncbi_%s_retry_exhausted" % stage

    async def _resolve_one(
        self, client: httpx.AsyncClient, normalized: str, name: str
    ) -> Tuple[str, Dict[str, Any]]:
        def unresolved(reason_code: str) -> Tuple[str, Dict[str, Any]]:
            return normalized, {
                "ncbi_taxonomy_id": None,
                "taxonomy_resolution_status": "unresolved",
                "canonical_latin_name": name,
                "name_i18n": {"en": name, "status": "partial"},
                "ncbi_taxonomy_rank": None,
                "taxonomy_resolution_reason_code": reason_code,
            }

        search_response, search_error = await self._request_with_retry(
            client,
            NCBI_TAXONOMY_SEARCH_URL,
            params={
                "db": "taxonomy",
                "term": '"%s"[Scientific Name]' % name.replace('"', ""),
                "retmode": "json",
                "retmax": 2,
                "tool": "owlpath-development-agent",
            },
            stage="taxonomy_search",
        )
        if search_response is None:
            return unresolved(search_error or "ncbi_taxonomy_search_failed")
        try:
            ids = [
                str(value)
                for value in (
                    (search_response.json().get("esearchresult") or {}).get("idlist") or []
                )
            ]
        except (ValueError, TypeError, AttributeError):
            return unresolved("ncbi_taxonomy_search_invalid_json")
        if not ids:
            return unresolved("ncbi_taxonomy_search_no_exact_match")
        if len(ids) != 1 or not ids[0].isdigit() or int(ids[0]) <= 0:
            return unresolved("ncbi_taxonomy_search_ambiguous_or_invalid_id")

        taxonomy_id = int(ids[0])
        summary_response, summary_error = await self._request_with_retry(
            client,
            NCBI_TAXONOMY_SUMMARY_URL,
            params={
                "db": "taxonomy",
                "id": str(taxonomy_id),
                "retmode": "json",
                "tool": "owlpath-development-agent",
            },
            stage="taxonomy_summary",
        )
        if summary_response is None:
            return unresolved(summary_error or "ncbi_taxonomy_summary_failed")
        try:
            result = summary_response.json().get("result") or {}
            summary = result.get(str(taxonomy_id)) or {}
            returned_uid = str(summary.get("uid") or "")
            scientific_name = str(summary.get("scientificname") or "").strip()
            verified_rank = str(summary.get("rank") or "").strip().casefold()
            ncbi_division = str(summary.get("division") or "").strip()
        except (ValueError, TypeError, AttributeError):
            return unresolved("ncbi_taxonomy_summary_invalid_json")
        # ESummary may serialize NCBI's displayed ``no rank`` as an empty rank
        # for viral records.  Normalization is allowed only when the same NCBI
        # response explicitly identifies the Viruses division.
        if not verified_rank and ncbi_division.casefold() == "viruses":
            verified_rank = "no rank"
        if returned_uid != str(taxonomy_id) or not scientific_name or not verified_rank:
            return unresolved("ncbi_taxonomy_summary_incomplete_record")
        if _normalize_taxon_name(scientific_name) != normalized:
            return unresolved("ncbi_taxonomy_summary_scientific_name_mismatch")
        rank_assertion = self._rank_assertion_from_ncbi_summary(
            verified_rank,
            ncbi_division,
        )
        if rank_assertion is None:
            return unresolved("ncbi_taxonomy_summary_non_concrete_rank")

        return normalized, {
            "ncbi_taxonomy_id": taxonomy_id,
            "taxonomy_resolution_status": "resolved",
            "canonical_latin_name": scientific_name,
            "name_i18n": {"en": scientific_name, "status": "partial"},
            "ncbi_taxonomy_rank": verified_rank,
            "product_taxonomic_rank": rank_assertion["product_rank"],
            "taxonomy_rank_mapping_rule": rank_assertion["mapping_rule"],
            "taxonomy_lineage_anchor": rank_assertion["lineage_anchor"] or None,
            "taxonomy_resolution_reason_code": (
                "ncbi_name_rank_lineage_verified"
                if verified_rank == "no rank"
                else "ncbi_name_and_rank_verified"
            ),
        }

    async def _persist_verified_records(
        self, records: Dict[str, Dict[str, Any]]
    ) -> None:
        if self.cache_path is None or not records:
            return
        with self._cache_write_lock:
            for normalized, record in records.items():
                self._positive_cache[normalized] = {
                    "ncbi_taxonomy_id": record["ncbi_taxonomy_id"],
                    "taxonomy_resolution_status": "cache_resolved",
                    "canonical_latin_name": record["canonical_latin_name"],
                    "name_i18n": {
                        "en": record["canonical_latin_name"],
                        "status": "partial",
                    },
                    "ncbi_taxonomy_rank": record["ncbi_taxonomy_rank"],
                    "product_taxonomic_rank": record["product_taxonomic_rank"],
                    "taxonomy_rank_mapping_rule": record["taxonomy_rank_mapping_rule"],
                    "taxonomy_lineage_anchor": record.get("taxonomy_lineage_anchor"),
                    "taxonomy_resolution_reason_code": "persistent_verified_cache_match",
                }
            payload = {
                "schema_version": _TAXONOMY_CACHE_SCHEMA,
                "entries": {
                    normalized: {
                        "ncbi_taxonomy_id": record["ncbi_taxonomy_id"],
                        "canonical_latin_name": record["canonical_latin_name"],
                        "ncbi_taxonomy_rank": record["ncbi_taxonomy_rank"],
                        "product_taxonomic_rank": record["product_taxonomic_rank"],
                        "taxonomy_rank_mapping_rule": record[
                            "taxonomy_rank_mapping_rule"
                        ],
                        "taxonomy_lineage_anchor": record.get(
                            "taxonomy_lineage_anchor"
                        ),
                    }
                    for normalized, record in sorted(self._positive_cache.items())
                },
            }
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_name: Optional[str] = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=str(self.cache_path.parent),
                    prefix=".%s." % self.cache_path.name,
                    suffix=".tmp",
                    delete=False,
                ) as handle:
                    temporary_name = handle.name
                    json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary_name, 0o600)
                os.replace(temporary_name, self.cache_path)
                temporary_name = None
            finally:
                if temporary_name:
                    try:
                        Path(temporary_name).unlink()
                    except OSError:
                        pass


def _normalize_taxon_name(value: str) -> str:
    return " ".join(value.strip().casefold().replace("_", " ").split())
