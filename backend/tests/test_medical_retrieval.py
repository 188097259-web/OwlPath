import asyncio
import json
from pathlib import Path
from typing import Any, List

import httpx

from app.medical_retrieval import (
    AUTHORITATIVE_SOURCE_CATALOG_VERSION,
    EUROPE_PMC_SEARCH_URL,
    FederatedMedicalEvidenceRetriever,
    NCBI_PUBMED_SEARCH_URL,
    NCBI_PUBMED_SUMMARY_URL,
    NCBI_TAXONOMY_SEARCH_URL,
    NCBI_TAXONOMY_SUMMARY_URL,
    WHO_DON_API_URL,
    MedicalEvidenceRetriever,
    TaxonomyResolver,
    build_candidate_retrieval_queries,
    build_federated_query_plan,
    build_retrieval_queries,
    generalized_query,
    map_candidate_specific_citations,
    retrieve_candidate_evidence,
)
from app.models import DevelopmentSpecialistResult

SYNTHETIC_PHONE = "139" + ("0" * 8)


def _ranked_registry_payload(pathogens: List[dict[str, Any]]) -> dict[str, Any]:
    return {
        "version": "test-registry-v1",
        "taxonomy_rank_policy": {
            "schema_version": "owlpath.registry-taxonomy-rank-policy.v1",
            "accepted_product_ranks": ["species", "species_complex", "virus_type"],
            "mapping_rules": {
                "ncbi_species_to_product_species_v1": {
                    "product_rank": "species",
                    "accepted_ncbi_ranks": ["species", "subspecies"],
                    "required_lineage_anchor": None,
                },
                "ncbi_species_group_to_product_species_complex_v1": {
                    "product_rank": "species_complex",
                    "accepted_ncbi_ranks": ["species group", "species subgroup"],
                    "required_lineage_anchor": None,
                },
                "ncbi_unranked_virus_to_product_virus_type_v1": {
                    "product_rank": "virus_type",
                    "accepted_ncbi_ranks": ["no rank"],
                    "required_lineage_anchor": "NCBITaxon:10239",
                },
            },
        },
        "pathogens": pathogens,
    }


def test_v3_specialist_candidates_build_generalized_queries_without_case_text() -> None:
    """Only normalized v3 candidates may influence literature searches."""

    specialist_output = DevelopmentSpecialistResult.model_validate({
        "schema_version": "owlpath.specialist.v1",
        "role": "exposure_epidemiology",
        "summary_i18n": {
            "zh_cn": "水体相关暴露后的重症感染",
            "en": "Severe infection after water-associated exposure",
            "status": "complete",
        },
        "observations": [{
            "observation_id": "obs_sensitive_source_prose",
            "kind": "key_fact",
            "statement_i18n": {
                "zh_cn": f"病例原文：测试甲 {SYNTHETIC_PHONE} 于2099-01-02清洗虚构淡水景观水池后发热",
                "en": "Source prose: nobody@example.invalid MRN TEST-0001 became febrile",
                "status": "complete",
            },
            "source_fragment_ids": ["fragment_001"],
            "importance": "high",
        }],
        "candidate_pool": [
            {
                "canonical_latin_name": "Vibrio vulnificus",
                "name_i18n": {
                    "zh_cn": "创伤弧菌",
                    "en": "Vibrio vulnificus",
                    "status": "complete",
                },
                "taxonomic_rank": "species",
                "category": "bacteria",
                "model_score": 0.82,
                "rationale_i18n": {
                    "zh_cn": "结合水体暴露与重症感染",
                    "en": "Water exposure with severe infection",
                    "status": "complete",
                },
                "source_fragment_ids": ["fragment_001"],
            },
            {
                "canonical_latin_name": "Aeromonas hydrophila",
                "name_i18n": {
                    "zh_cn": "嗜水气单胞菌",
                    "en": "Aeromonas hydrophila",
                    "status": "complete",
                },
                "taxonomic_rank": "species",
                "category": "bacteria",
                "model_score": 0.71,
                "rationale_i18n": {
                    "zh_cn": "淡水相关感染候选",
                    "en": "Freshwater-associated infection candidate",
                    "status": "complete",
                },
                "source_fragment_ids": ["fragment_001"],
            },
        ],
        "warnings": [],
    }).model_dump(mode="json")

    queries = build_retrieval_queries([specialist_output], max_queries=3)

    assert 1 <= len(queries) <= 3
    serialized = " ".join(queries)
    assert "Vibrio vulnificus" in serialized
    assert "Aeromonas hydrophila" in serialized
    for restricted_text in (
        "测试甲",
        SYNTHETIC_PHONE,
        "2099-01-02",
        "nobody@example.invalid",
        "87654321",
        "清洗虚构淡水景观水池后发热",
    ):
        assert restricted_text not in serialized


def test_generalized_queries_remove_direct_identifiers_before_http_request() -> None:
    direct_identifiers = [
        "nobody@example.invalid",
        SYNTHETIC_PHONE,
        "87654321",
        "2099-01-02",
    ]
    unsafe_query = (
        "sepsis after fish handling "
        f"nobody@example.invalid {SYNTHETIC_PHONE} MRN TEST-0001 on 2099-01-02"
    )
    requested_urls: List[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if str(request.url).startswith(EUROPE_PMC_SEARCH_URL):
            return httpx.Response(200, json={"resultList": {"result": []}})
        if str(request.url).startswith(NCBI_PUBMED_SEARCH_URL):
            return httpx.Response(200, json={"esearchresult": {"idlist": []}})
        raise AssertionError("Unexpected HTTP request: %s" % request.url)

    cleaned = generalized_query(unsafe_query)
    bundle = asyncio.run(
        MedicalEvidenceRetriever(
            transport=httpx.MockTransport(handler),
            max_queries=1,
        ).retrieve([unsafe_query])
    )

    assert "sepsis after fish handling" in cleaned
    assert requested_urls
    for identifier in direct_identifiers:
        assert identifier not in cleaned
        assert all(identifier not in url for url in requested_urls)
    # The trace-safe return contract exposes only stable query hashes, never
    # the search phrase itself.
    assert unsafe_query not in json.dumps(bundle.public_payload(), ensure_ascii=False)


def test_retrieval_parses_europe_pmc_and_pubmed_metadata_without_full_text() -> None:
    requested_urls: List[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if str(request.url).startswith(EUROPE_PMC_SEARCH_URL):
            assert request.url.params["query"] == "Vibrio vulnificus human infection"
            return httpx.Response(
                200,
                json={
                    "resultList": {
                        "result": [
                            {
                                "pmid": "11111111",
                                "title": "Vibrio vulnificus infection after marine exposure",
                                "journalTitle": "Journal of Mock Infectious Diseases",
                                "pubYear": "2025",
                                "doi": "10.1000/epmc.mock",
                                "abstractText": "This full abstract must not be persisted.",
                            }
                        ]
                    }
                },
            )
        if str(request.url).startswith(NCBI_PUBMED_SEARCH_URL):
            assert request.url.params["db"] == "pubmed"
            assert request.url.params["term"] == "Vibrio vulnificus human infection"
            return httpx.Response(200, json={"esearchresult": {"idlist": ["22222222"]}})
        if str(request.url).startswith(NCBI_PUBMED_SUMMARY_URL):
            assert request.url.params["db"] == "pubmed"
            assert request.url.params["id"] == "22222222"
            return httpx.Response(
                200,
                json={
                    "result": {
                        "uids": ["22222222"],
                        "22222222": {
                            "title": "Severe sepsis caused by Vibrio vulnificus",
                            "fulljournalname": "Mock Clinical Microbiology",
                            "pubdate": "2024 Dec",
                            "articleids": [
                                {"idtype": "pubmed", "value": "22222222"},
                                {"idtype": "doi", "value": "10.1000/pubmed.mock"},
                            ],
                            "abstract": "This unsupported field must not be persisted.",
                        },
                    }
                },
            )
        raise AssertionError("Unexpected HTTP request: %s" % request.url)

    bundle = asyncio.run(
        MedicalEvidenceRetriever(
            transport=httpx.MockTransport(handler),
            max_results_per_query=2,
            max_queries=1,
        ).retrieve(["Vibrio vulnificus human infection"])
    )

    assert len(requested_urls) == 3
    assert bundle.source_status == {"europe_pmc": "available", "pubmed": "available"}
    assert bundle.partial is False
    assert bundle.warnings == []

    by_source = {item["source"]: item for item in bundle.citations}
    assert by_source["Europe PMC"] == {
        "citation_id": "epmc_11111111",
        "source": "Europe PMC",
        "source_id": "11111111",
        "title": "Vibrio vulnificus infection after marine exposure",
        "journal": "Journal of Mock Infectious Diseases",
        "year": "2025",
        "doi": "10.1000/epmc.mock",
        "url": "https://pubmed.ncbi.nlm.nih.gov/11111111/",
        "query_id": by_source["Europe PMC"]["query_id"],
    }
    assert by_source["PubMed"] == {
        "citation_id": "pubmed_22222222",
        "source": "PubMed",
        "source_id": "22222222",
        "title": "Severe sepsis caused by Vibrio vulnificus",
        "journal": "Mock Clinical Microbiology",
        "year": "2024 Dec",
        "doi": "10.1000/pubmed.mock",
        "url": "https://pubmed.ncbi.nlm.nih.gov/22222222/",
        "query_id": by_source["PubMed"]["query_id"],
    }
    serialized = json.dumps(bundle.public_payload(), ensure_ascii=False)
    assert "full abstract" not in serialized.lower()
    assert "unsupported field" not in serialized.lower()


def test_retrieval_is_partial_and_non_blocking_when_both_sources_are_offline() -> None:
    requested_urls: List[str] = []

    def offline(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        raise httpx.ConnectError("mock network is offline", request=request)

    bundle = asyncio.run(
        MedicalEvidenceRetriever(
            transport=httpx.MockTransport(offline),
            max_queries=1,
        ).retrieve(["freshwater exposure severe sepsis"])
    )

    assert len(requested_urls) == 2
    assert bundle.citations == []
    assert bundle.partial is True
    assert bundle.public_payload()["retrieval_partial"] is True
    assert bundle.source_status == {
        "europe_pmc": "unavailable",
        "pubmed": "unavailable",
    }
    assert set(bundle.warnings) == {
        "retrieval_europe_pmc_unavailable",
        "retrieval_pubmed_unavailable",
    }


def test_federated_plan_uses_syndrome_and_exposure_without_pathogen_candidate() -> None:
    outputs = [{
        "role": "exposure_epidemiology",
        "retrieval_concepts": [
            {
                "kind": "syndrome",
                "term_en": "septic shock with meningoencephalitis",
                "source_fragment_ids": ["fragment_002"],
            },
            {
                "kind": "exposure",
                "term_en": "freshwater fish handling",
                "source_fragment_ids": ["fragment_001"],
            },
        ],
        "candidate_pool": [],
    }]

    plan = build_federated_query_plan(outputs, max_queries_per_intent=2)

    assert {item.intent for item in plan} == {
        "literature", "similar_case", "public_health_guideline",
    }
    assert any("septic shock with meningoencephalitis" in item.query for item in plan)
    assert any("freshwater fish handling" in item.query for item in plan)
    assert all(item.plan_item_id.startswith("query_") for item in plan)
    public_plan = [item.public_payload() for item in plan]
    serialized = json.dumps(public_plan, ensure_ascii=False)
    assert "freshwater fish handling" not in serialized
    assert all(item["query_text_omitted"] is True for item in public_plan)


def test_federated_retrieval_never_sends_observations_or_source_prose() -> None:
    sensitive_markers = [
        "Test Person", "nobody@example.invalid", SYNTHETIC_PHONE, "MRNTEST0001",
        "2099-01-02", "full pasted narrative",
    ]
    outputs = [{
        "role": "exposure_ecology",
        "summary_i18n": {
            "en": "Test Person full pasted narrative nobody@example.invalid",
        },
        "observations": [{
            "statement_i18n": {
                "en": (
                    f"Test Person nobody@example.invalid {SYNTHETIC_PHONE} MRNTEST0001 "
                    "on 2099-01-02 full pasted narrative"
                ),
            },
            "source_fragment_ids": ["fragment_001"],
        }],
        "retrieval_concepts": [
            {"kind": "syndrome", "term_en": "septic shock"},
            {"kind": "exposure", "term_en": "freshwater fish handling"},
        ],
        "candidate_pool": [],
    }]
    requested_urls: List[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if str(request.url).startswith(EUROPE_PMC_SEARCH_URL):
            return httpx.Response(200, json={"resultList": {"result": []}})
        if str(request.url).startswith(NCBI_PUBMED_SEARCH_URL):
            return httpx.Response(200, json={"esearchresult": {"idlist": []}})
        if str(request.url).startswith(WHO_DON_API_URL):
            assert "freshwater fish handling" in request.url.params["$filter"] or (
                "septic shock" in request.url.params["$filter"]
            )
            return httpx.Response(200, json={"value": [{
                "DonId": "2026-DON999",
                "Title": "Freshwater fish handling outbreak update",
                "PublicationDate": "2026-08-01T00:00:00Z",
                "ItemDefaultUrl": "/2026-DON999",
            }]})
        raise AssertionError("Unexpected HTTP request: %s" % request.url)

    bundle = asyncio.run(FederatedMedicalEvidenceRetriever(
        transport=httpx.MockTransport(handler),
        max_queries_per_intent=2,
    ).retrieve_from_specialists(outputs))

    assert requested_urls
    outbound = " ".join(requested_urls)
    public_payload = json.dumps(bundle.public_payload(), ensure_ascii=False)
    for marker in sensitive_markers:
        assert marker not in outbound
        assert marker not in public_payload
    assert bundle.public_payload()["raw_case_text_sent"] is False
    assert bundle.public_payload()["search_query_text_omitted"] is True
    assert len(bundle.public_payload()["public_health"]) == 1
    who_source = bundle.public_payload()["public_health"][0]
    assert who_source["intent"] == "public_health_guideline"
    assert who_source["source_kind"] == "public_health_outbreak_notice"
    assert who_source["relevance_validation"]["status"] == (
        "title_exact_concept_match"
    )
    assert who_source["url"] == (
        "https://www.who.int/emergencies/disease-outbreak-news/item/2026-DON999"
    )
    assert who_source["coverage_limitation"] == "official_but_non_exhaustive"


def test_federated_literature_failure_is_isolated_per_query() -> None:
    outputs = [{
        "retrieval_concepts": [
            {"kind": "syndrome", "term_en": "septic shock"},
        ],
        "candidate_pool": [{
            "canonical_latin_name": "Vibrio vulnificus",
        }],
    }]
    europe_queries: List[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(EUROPE_PMC_SEARCH_URL):
            query = request.url.params["query"]
            europe_queries.append(query)
            if "Vibrio vulnificus" in query and "septic shock" in query:
                return httpx.Response(503, json={"error": "one query failed"})
            return httpx.Response(200, json={
                "resultList": {"result": [{
                    "pmid": str(45000000 + len(europe_queries)),
                    "title": "Vibrio vulnificus clinical infection case report",
                    "journalTitle": "Mock Infectious Diseases",
                    "pubYear": "2026",
                }]},
            })
        if str(request.url).startswith(NCBI_PUBMED_SEARCH_URL):
            return httpx.Response(200, json={"esearchresult": {"idlist": []}})
        if str(request.url).startswith(WHO_DON_API_URL):
            return httpx.Response(200, json={"value": []})
        raise AssertionError("Unexpected HTTP request: %s" % request.url)

    bundle = asyncio.run(FederatedMedicalEvidenceRetriever(
        transport=httpx.MockTransport(handler),
        max_queries_per_intent=2,
    ).retrieve_from_specialists(outputs))

    assert len(europe_queries) > 1
    assert bundle.source_status["europe_pmc"] == "partial"
    assert bundle.source_status["pubmed"] == "available"
    assert bundle.citations, "later successful queries must survive one failed query"
    assert any("federated_europe_pmc_query_unavailable:" in item for item in bundle.warnings)
    for citation in bundle.citations:
        assert citation["intent"] in {
            "literature", "similar_case", "public_health_guideline",
        }
        assert citation["source_kind"]
        assert citation["relevance_validation"]["method"] == (
            "deterministic_title_concept_overlap_v1"
        )


def test_who_don_failure_is_non_blocking_and_catalog_is_not_a_fake_hit() -> None:
    outputs = [{
        "retrieval_concepts": [
            {"kind": "syndrome", "term_en": "viral hemorrhagic fever"},
            {"kind": "geo_season", "term_en": "East Africa"},
        ],
        "candidate_pool": [],
    }]

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(EUROPE_PMC_SEARCH_URL):
            return httpx.Response(200, json={"resultList": {"result": []}})
        if str(request.url).startswith(NCBI_PUBMED_SEARCH_URL):
            return httpx.Response(200, json={"esearchresult": {"idlist": []}})
        if str(request.url).startswith(WHO_DON_API_URL):
            raise httpx.ConnectError("WHO offline", request=request)
        raise AssertionError("Unexpected HTTP request: %s" % request.url)

    bundle = asyncio.run(FederatedMedicalEvidenceRetriever(
        transport=httpx.MockTransport(handler),
        max_queries_per_intent=1,
    ).retrieve_from_specialists(outputs))
    payload = bundle.public_payload()

    assert bundle.source_status == {
        "europe_pmc": "available",
        "pubmed": "available",
        "who_don": "unavailable",
        "authoritative_source_catalog": "registered_reference_only",
    }
    assert payload["retrieval_partial"] is True
    assert "who_don_is_official_but_non_exhaustive" in payload["coverage_notes"]
    assert payload["authoritative_source_catalog"]["version"] == (
        AUTHORITATIVE_SOURCE_CATALOG_VERSION
    )
    assert payload["authoritative_source_catalog"]["entries_are_search_hits"] is False
    assert payload["evidence_sources"] == []
    organizations = {
        item["organization"]
        for item in payload["authoritative_source_catalog"]["entries"]
    }
    assert {
        "World Health Organization",
        "US Centers for Disease Control and Prevention",
        "European Centre for Disease Prevention and Control",
        "Chinese Center for Disease Control and Prevention",
        "Infectious Diseases Society of America",
    }.issubset(organizations)


def test_targeted_candidate_retrieval_covers_each_pathogen_without_case_text_or_unrelated_binding() -> None:
    pathogen_names = [
        "Aeromonas hydrophila",
        "Edwardsiella tarda",
        "Vibrio vulnificus",
        "Streptococcus suis",
        "Klebsiella pneumoniae",
    ]
    sensitive_case_markers = [
        "清洗虚构淡水景观水池", "51岁", "肝内感染灶", SYNTHETIC_PHONE, "nobody@example.invalid",
    ]
    requested_urls: List[str] = []
    relevant_ids = {
        name: str(31000000 + index) for index, name in enumerate(pathogen_names, start=1)
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if str(request.url).startswith(EUROPE_PMC_SEARCH_URL):
            query = request.url.params["query"]
            matched = next(name for name in pathogen_names if name in query)
            assert query == '"%s" AND (human OR patient OR clinical OR infection)' % matched
            assert request.url.params["pageSize"] == "10"
            return httpx.Response(200, json={
                "resultList": {"result": [
                    {
                        "pmid": relevant_ids[matched],
                        "title": "Clinical review of %s human infection" % matched,
                        "journalTitle": "Mock Journal of Infectious Diseases",
                        "pubYear": "2026",
                    },
                    {
                        "pmid": "8%s" % relevant_ids[matched][1:],
                        "title": "Unrelated antimicrobial stewardship study",
                        "journalTitle": "Mock General Medicine",
                        "pubYear": "2026",
                    },
                    {
                        "pmid": "6%s" % relevant_ids[matched][1:],
                        "title": "Experimental %s infection in zebrafish" % matched,
                        "journalTitle": "Mock Animal Infection Journal",
                        "pubYear": "2026",
                    },
                ]},
            })
        if str(request.url).startswith(NCBI_PUBMED_SEARCH_URL):
            query = request.url.params["term"]
            matched = next(name for name in pathogen_names if name in query)
            assert request.url.params["retmax"] == "10"
            return httpx.Response(200, json={
                "esearchresult": {"idlist": ["7%s" % relevant_ids[matched][1:]]},
            })
        if str(request.url).startswith(NCBI_PUBMED_SUMMARY_URL):
            pmid = request.url.params["id"]
            return httpx.Response(200, json={
                "result": {
                    "uids": [pmid],
                    pmid: {
                        "title": "Unrelated critical care cohort",
                        "fulljournalname": "Mock Critical Care",
                        "pubdate": "2026",
                    },
                },
            })
        raise AssertionError("Unexpected HTTP request: %s" % request.url)

    retriever = MedicalEvidenceRetriever(
        transport=httpx.MockTransport(handler),
        max_results_per_query=2,
        # Candidate enrichment must cover all five even though the broad
        # pre-synthesis retriever normally caps a batch at three queries.
        max_queries=3,
    )
    bundle = asyncio.run(retrieve_candidate_evidence(retriever, pathogen_names))

    assert set(bundle.citation_ids_by_candidate) == set(pathogen_names)
    assert all(len(bundle.citation_ids_by_candidate[name]) == 1 for name in pathogen_names)
    assert len(bundle.citations) == 5
    assert bundle.unrelated_citation_count == 15
    assert bundle.warnings == []
    assert all(
        name.casefold() in citation["title"].casefold()
        for name, source_ids in bundle.citation_ids_by_candidate.items()
        for citation in bundle.citations
        if citation["citation_id"] in source_ids
    )

    public_payload = json.dumps(bundle.public_payload(), ensure_ascii=False)
    request_payload = " ".join(requested_urls)
    for marker in sensitive_case_markers:
        assert marker not in request_payload
        assert marker not in public_payload
    assert "Unrelated antimicrobial stewardship" not in public_payload
    assert "Unrelated critical care" not in public_payload
    assert "zebrafish" not in public_payload

    # The standalone mapper uses the same conservative rule: a search hit that
    # does not explicitly name the pathogen in its title is never bound.
    mapping = map_candidate_specific_citations(pathogen_names, [
        *bundle.citations,
        {
            "citation_id": "pubmed_irrelevant",
            "title": "Septic shock after water exposure",
        },
    ])
    assert all(mapping[name] == bundle.citation_ids_by_candidate[name] for name in pathogen_names)

    query_pairs = build_candidate_retrieval_queries([
        f"Vibrio vulnificus nobody@example.invalid {SYNTHETIC_PHONE}",
    ])
    assert query_pairs == [
        ("Vibrio vulnificus", '"Vibrio vulnificus" AND (human OR patient OR clinical OR infection)'),
    ]


def test_candidate_title_mapping_rejects_fish_disease_without_human_anchor() -> None:
    """Generic septicemia language must not bind a fish-disease paper to a patient."""

    mapping = map_candidate_specific_citations(["Shewanella putrefaciens"], [
        {
            "citation_id": "fish_only",
            "title": "Shewanella putrefaciens fish hemorrhagic septicemia",
        },
        {
            "citation_id": "aquaculture_only",
            "title": "Aquaculture outbreak of Shewanella putrefaciens infection",
        },
        {
            "citation_id": "fish_binomial_experiment",
            "title": (
                "Glycyrrhiza extract alleviates hemorrhagic septicemia in "
                "Triplophysa yarkandensis infected with Shewanella putrefaciens: "
                "integrated bactericidal and immunomodulatory effects."
            ),
        },
        {
            "citation_id": "human_case",
            "title": "Shewanella putrefaciens human infection: a clinical case report",
        },
        {
            "citation_id": "human_fish_worker",
            "title": "Shewanella putrefaciens septicemia in a human fish-farm worker",
        },
    ])

    assert mapping == {
        "Shewanella putrefaciens": ["human_case", "human_fish_worker"],
    }


def test_targeted_retrieval_deduplicates_same_pmid_across_catalogs_with_provenance() -> None:
    """Europe PMC and PubMed copies are one title-level published-evidence lead."""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(EUROPE_PMC_SEARCH_URL):
            return httpx.Response(200, json={
                "resultList": {"result": [{
                    "pmid": "42424242",
                    "title": "Shewanella putrefaciens human infection case report",
                    "journalTitle": "Mock Infectious Diseases",
                    "pubYear": "2026",
                    "doi": "10.1000/SHEWANELLA.4242",
                }]},
            })
        if str(request.url).startswith(NCBI_PUBMED_SEARCH_URL):
            return httpx.Response(200, json={
                "esearchresult": {"idlist": ["42424242"]},
            })
        if str(request.url).startswith(NCBI_PUBMED_SUMMARY_URL):
            return httpx.Response(200, json={
                "result": {
                    "uids": ["42424242"],
                    "42424242": {
                        "title": "Shewanella putrefaciens human infection case report",
                        "fulljournalname": "Mock Infectious Diseases",
                        "pubdate": "2026",
                        "articleids": [
                            {"idtype": "pubmed", "value": "42424242"},
                            {"idtype": "doi", "value": "10.1000/shewanella.4242"},
                        ],
                    },
                },
            })
        raise AssertionError("Unexpected HTTP request: %s" % request.url)

    retriever = MedicalEvidenceRetriever(
        transport=httpx.MockTransport(handler), max_queries=1,
    )
    raw_bundle = asyncio.run(retriever.retrieve([
        '"Shewanella putrefaciens" AND (human OR patient OR clinical OR infection)',
    ]))
    assert len(raw_bundle.citations) == 1
    citation = raw_bundle.citations[0]
    assert citation["citation_id"] == "pubmed_42424242"
    assert citation["publication_identity"] == "doi:10.1000/shewanella.4242"
    assert {entry["source"] for entry in citation["source_provenance"]} == {
        "Europe PMC", "PubMed",
    }

    candidate_bundle = asyncio.run(retrieve_candidate_evidence(
        retriever, ["Shewanella putrefaciens"],
    ))
    assert len(candidate_bundle.citations) == 1
    assert candidate_bundle.citation_ids_by_candidate == {
        "Shewanella putrefaciens": ["pubmed_42424242"],
    }
    validation = candidate_bundle.citations[0]["relevance_validation"]
    assert validation == {
        "status": "title_exact_concept_match",
        "method": "deterministic_title_candidate_human_context_v2",
        "evidence_scope": "title_metadata_only",
        "requires_human_review": True,
        "non_human_context_detected": False,
    }


def test_taxonomy_resolver_uses_local_cache_without_network(tmp_path: Path) -> None:
    terms_path = tmp_path / "terms.json"
    terms_path.write_text(
        json.dumps(
            _ranked_registry_payload([
                {
                    "canonical_id": "NCBITaxon:1313",
                    "ncbi_scientific_name": "Streptococcus pneumoniae",
                    "taxonomic_rank": "species",
                    "ncbi_taxonomy_rank": "species",
                    "rank_mapping_rule": "ncbi_species_to_product_species_v1",
                    "aliases": ["S. pneumoniae"],
                    "zh_cn": "肺炎链球菌",
                    "en": "Streptococcus pneumoniae",
                }
            ]),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def no_network(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Local taxonomy cache unexpectedly used HTTP: %s" % request.url)

    resolver = TaxonomyResolver(
        terms_path=terms_path,
        transport=httpx.MockTransport(no_network),
    )
    result = asyncio.run(resolver.resolve(["S. pneumoniae", "s. pneumoniae"]))

    assert list(result) == ["s. pneumoniae"]
    assert result["s. pneumoniae"] == {
        "ncbi_taxonomy_id": 1313,
        "taxonomy_resolution_status": "cache_resolved",
        "canonical_latin_name": "Streptococcus pneumoniae",
        "name_i18n": {
            "zh_cn": "肺炎链球菌",
            "en": "Streptococcus pneumoniae",
            "status": "complete",
        },
        "product_taxonomic_rank": "species",
        "ncbi_taxonomy_rank": "species",
        "taxonomy_rank_mapping_rule": "ncbi_species_to_product_species_v1",
        "taxonomy_lineage_anchor": None,
        "taxonomy_resolution_reason_code": "versioned_registry_name_id_rank_verified",
    }
    assert resolver.registry_load_audit["accepted_entry_count"] == 1
    assert resolver.registry_load_audit["rejected_entry_count"] == 0


def test_versioned_registry_resolves_listeria_without_network() -> None:
    def no_network(request: httpx.Request) -> httpx.Response:
        raise AssertionError(
            "Versioned Listeria taxonomy entry unexpectedly used HTTP: %s"
            % request.url
        )

    resolver = TaxonomyResolver(transport=httpx.MockTransport(no_network))
    result = asyncio.run(resolver.resolve(["Listeria monocytogenes"]))

    assert result["listeria monocytogenes"] == {
        "ncbi_taxonomy_id": 1639,
        "taxonomy_resolution_status": "cache_resolved",
        "canonical_latin_name": "Listeria monocytogenes",
        "name_i18n": {
            "zh_cn": "单核细胞增生李斯特菌",
            "en": "Listeria monocytogenes",
            "status": "complete",
        },
        "product_taxonomic_rank": "species",
        "ncbi_taxonomy_rank": "species",
        "taxonomy_rank_mapping_rule": "ncbi_species_to_product_species_v1",
        "taxonomy_lineage_anchor": None,
        "taxonomy_resolution_reason_code": "versioned_registry_name_id_rank_verified",
    }


def test_versioned_registry_resolves_dengue_virus_without_network() -> None:
    """The verified current NCBI name/TaxID must survive transient NCBI failure."""

    def no_network(request: httpx.Request) -> httpx.Response:
        raise AssertionError(
            "Versioned Dengue virus taxonomy entry unexpectedly used HTTP: %s"
            % request.url
        )

    resolver = TaxonomyResolver(transport=httpx.MockTransport(no_network))
    result = asyncio.run(resolver.resolve(["Dengue virus"]))

    assert result["dengue virus"] == {
        "ncbi_taxonomy_id": 12637,
        "taxonomy_resolution_status": "cache_resolved",
        "canonical_latin_name": "Dengue virus",
        "name_i18n": {
            "zh_cn": "登革病毒",
            "en": "Dengue virus",
            "status": "complete",
        },
        "product_taxonomic_rank": "virus_type",
        "ncbi_taxonomy_rank": "no rank",
        "taxonomy_rank_mapping_rule": (
            "ncbi_unranked_virus_to_product_virus_type_v1"
        ),
        "taxonomy_lineage_anchor": "NCBITaxon:10239",
        "taxonomy_resolution_reason_code": "versioned_registry_name_id_rank_verified",
    }


def test_versioned_registry_resolves_common_icu_pathogens_without_network() -> None:
    """Common ICU organisms must not fail a whole run when NCBI is offline."""

    def no_network(request: httpx.Request) -> httpx.Response:
        raise AssertionError(
            "Versioned common-pathogen taxonomy entry unexpectedly used HTTP: %s"
            % request.url
        )

    resolver = TaxonomyResolver(transport=httpx.MockTransport(no_network))
    result = asyncio.run(resolver.resolve([
        "Enterococcus faecium",
        "Candida albicans",
        "Acinetobacter baumannii",
    ]))

    expected = {
        "enterococcus faecium": (1352, "屎肠球菌", "Enterococcus faecium"),
        "candida albicans": (5476, "白色念珠菌", "Candida albicans"),
        "acinetobacter baumannii": (470, "鲍曼不动杆菌", "Acinetobacter baumannii"),
    }
    assert set(result) == set(expected)
    for key, (taxonomy_id, zh_cn, latin_name) in expected.items():
        assert result[key] == {
            "ncbi_taxonomy_id": taxonomy_id,
            "taxonomy_resolution_status": "cache_resolved",
            "canonical_latin_name": latin_name,
            "name_i18n": {
                "zh_cn": zh_cn,
                "en": latin_name,
                "status": "complete",
            },
            "product_taxonomic_rank": "species",
            "ncbi_taxonomy_rank": "species",
            "taxonomy_rank_mapping_rule": "ncbi_species_to_product_species_v1",
            "taxonomy_lineage_anchor": None,
            "taxonomy_resolution_reason_code": (
                "versioned_registry_name_id_rank_verified"
            ),
        }


def test_versioned_registry_common_concrete_pathogens_are_unique() -> None:
    """Verified TaxIDs and names must remain unique across registry entries."""

    terms_path = Path(__file__).resolve().parents[2] / "config" / "clinical_terms.zh-en.v1.json"
    payload = json.loads(terms_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "owlpath.clinical-terms.zh-en.v1"
    assert payload["version"] == "1.2.0-research"

    expected_taxa = {
        "Clostridioides difficile": 1496,
        "Klebsiella oxytoca": 571,
        "Campylobacter jejuni": 197,
        "Shigella sonnei": 624,
        "Streptococcus sanguinis": 1305,
        "Coxiella burnetii": 777,
        "Bartonella henselae": 38323,
        "Staphylococcus epidermidis": 1282,
        "Cutibacterium acnes": 1747,
        "Streptococcus oralis": 1303,
    }
    entries = payload["pathogens"]
    canonical_ids = [entry["canonical_id"] for entry in entries]
    assert len(canonical_ids) == len(set(canonical_ids))

    owner_by_name: dict[str, str] = {}
    for entry in entries:
        canonical_id = entry["canonical_id"]
        names = {
            str(entry.get("en") or "").strip().casefold(),
            *(str(alias).strip().casefold() for alias in entry.get("aliases") or []),
        }
        names.discard("")
        for name in names:
            previous_owner = owner_by_name.setdefault(name, canonical_id)
            assert previous_owner == canonical_id, (
                f"Registry name {name!r} maps to both {previous_owner} and {canonical_id}"
            )

    by_english_name = {entry["en"]: entry for entry in entries}
    for scientific_name, taxonomy_id in expected_taxa.items():
        assert by_english_name[scientific_name]["canonical_id"] == (
            f"NCBITaxon:{taxonomy_id}"
        )


def test_versioned_registry_rank_assertions_are_complete_and_audited() -> None:
    terms_path = Path(__file__).resolve().parents[2] / "config" / "clinical_terms.zh-en.v1.json"
    payload = json.loads(terms_path.read_text(encoding="utf-8"))
    resolver = TaxonomyResolver(terms_path=terms_path)

    assert resolver.registry_load_audit == {
        "schema_version": "owlpath.registry-load-audit.v1",
        "status": "loaded",
        "registry_version": "1.2.0-research",
        "policy_schema_version": "owlpath.registry-taxonomy-rank-policy.v1",
        "accepted_entry_count": 39,
        "rejected_entry_count": 0,
        "rejection_reason_counts": {},
    }
    assert len(payload["pathogens"]) == 39
    for entry in payload["pathogens"]:
        assertion, error = resolver._validate_registry_rank_assertion(entry)
        assert error is None
        assert assertion is not None
        assert assertion["product_rank"] in {"species", "species_complex", "virus_type"}
        assert entry["ncbi_scientific_name"]

    # TaxID 10508 is Adenoviridae (family), not a concrete human adenovirus.
    assert all(entry["canonical_id"] != "NCBITaxon:10508" for entry in payload["pathogens"])
    assert all(entry["en"] != "Human adenovirus" for entry in payload["pathogens"])

    by_id = {entry["canonical_id"]: entry for entry in payload["pathogens"]}
    assert by_id["NCBITaxon:2104"]["ncbi_scientific_name"] == (
        "Mycoplasmoides pneumoniae"
    )
    assert "mycoplasma pneumoniae" in by_id["NCBITaxon:2104"]["aliases"]
    for taxonomy_id in ("2697049", "11320", "12637", "11250", "162145"):
        entry = by_id[f"NCBITaxon:{taxonomy_id}"]
        assert entry["taxonomic_rank"] == "virus_type"
        assert entry["ncbi_taxonomy_rank"] == "no rank"
        assert entry["rank_mapping_rule"] == (
            "ncbi_unranked_virus_to_product_virus_type_v1"
        )
        assert entry["lineage_anchor"] == "NCBITaxon:10239"


def test_registry_rejects_missing_non_concrete_and_conflicting_rank_assertions(
    tmp_path: Path,
) -> None:
    base_entry: dict[str, Any] = {
        "canonical_id": "NCBITaxon:900001",
        "ncbi_scientific_name": "Example pathogen",
        "taxonomic_rank": "species",
        "ncbi_taxonomy_rank": "species",
        "rank_mapping_rule": "ncbi_species_to_product_species_v1",
        "aliases": ["Example pathogen"],
        "zh_cn": None,
        "en": "Example pathogen",
    }
    invalid_variants = [
        ("registry_entry_product_rank_missing", {}, {"taxonomic_rank"}),
        ("registry_entry_product_rank_non_concrete", {"taxonomic_rank": "genus"}, set()),
        ("registry_entry_product_rank_non_concrete", {"taxonomic_rank": "category"}, set()),
        ("registry_entry_product_rank_non_concrete", {"taxonomic_rank": "unknown"}, set()),
        ("registry_entry_ncbi_rank_missing", {}, {"ncbi_taxonomy_rank"}),
        ("registry_entry_ncbi_rank_conflict", {"ncbi_taxonomy_rank": "genus"}, set()),
        ("registry_entry_rank_mapping_rule_missing", {}, {"rank_mapping_rule"}),
        (
            "registry_entry_rank_mapping_rule_unknown",
            {"rank_mapping_rule": "invented_mapping"},
            set(),
        ),
        (
            "registry_entry_rank_mapping_conflict",
            {
                "taxonomic_rank": "species",
                "ncbi_taxonomy_rank": "no rank",
                "rank_mapping_rule": "ncbi_unranked_virus_to_product_virus_type_v1",
                "lineage_anchor": "NCBITaxon:10239",
            },
            set(),
        ),
        (
            "registry_entry_lineage_anchor_conflict",
            {
                "taxonomic_rank": "virus_type",
                "ncbi_taxonomy_rank": "no rank",
                "rank_mapping_rule": "ncbi_unranked_virus_to_product_virus_type_v1",
                "en": "Example virus",
                "ncbi_scientific_name": "Example virus",
            },
            set(),
        ),
        (
            "registry_entry_lineage_anchor_conflict",
            {"lineage_anchor": "NCBITaxon:10239"},
            set(),
        ),
        (
            "registry_entry_ncbi_scientific_name_missing",
            {},
            {"ncbi_scientific_name"},
        ),
    ]

    for index, (expected_reason, overrides, removals) in enumerate(invalid_variants):
        entry = {**base_entry, **overrides}
        for key in removals:
            entry.pop(key, None)
        terms_path = tmp_path / f"invalid-registry-{index}.json"
        terms_path.write_text(
            json.dumps(_ranked_registry_payload([entry]), ensure_ascii=False),
            encoding="utf-8",
        )
        resolver = TaxonomyResolver(terms_path=terms_path)
        assert resolver._registry_cache == {}
        assert resolver.registry_load_audit["accepted_entry_count"] == 0
        assert resolver.registry_load_audit["rejected_entry_count"] == 1
        assert resolver.registry_load_audit["rejection_reason_counts"] == {
            expected_reason: 1
        }


def test_no_rank_is_never_classified_from_a_virus_like_name() -> None:
    assert not TaxonomyResolver._is_concrete_rank(
        "no rank",
        "This name contains virus",
        product_rank="virus_type",
    )
    assert TaxonomyResolver._is_concrete_rank(
        "no rank",
        "A name without viral words",
        product_rank="virus_type",
        lineage_anchor="NCBITaxon:10239",
    )
    assert TaxonomyResolver._is_concrete_rank(
        "no rank",
        "A name without viral words",
        product_rank="virus_type",
        verification_reason_code="versioned_registry_name_id_rank_verified",
    )
    assert not TaxonomyResolver._is_concrete_rank(
        "species",
        "Staphylococcus aureus",
        product_rank="virus_type",
    )


def test_versioned_registry_resolves_common_concrete_pathogens_without_network() -> None:
    """The verified common-pathogen registry must be sufficient while NCBI is offline."""

    def no_network(request: httpx.Request) -> httpx.Response:
        raise AssertionError(
            "Versioned common-pathogen entry unexpectedly used HTTP: %s" % request.url
        )

    expected = {
        "Clostridioides difficile": (1496, "艰难梭菌"),
        "Klebsiella oxytoca": (571, "产酸克雷伯菌"),
        "Campylobacter jejuni": (197, "空肠弯曲菌"),
        "Shigella sonnei": (624, "宋内志贺菌"),
        "Streptococcus sanguinis": (1305, "血链球菌"),
        "Coxiella burnetii": (777, None),
        "Bartonella henselae": (38323, None),
        "Staphylococcus epidermidis": (1282, "表皮葡萄球菌"),
        "Cutibacterium acnes": (1747, None),
        "Streptococcus oralis": (1303, "口腔链球菌"),
    }
    resolver = TaxonomyResolver(transport=httpx.MockTransport(no_network))
    result = asyncio.run(resolver.resolve(list(expected)))

    assert set(result) == {name.casefold() for name in expected}
    for scientific_name, (taxonomy_id, zh_cn) in expected.items():
        normalized = scientific_name.casefold()
        assert result[normalized] == {
            "ncbi_taxonomy_id": taxonomy_id,
            "taxonomy_resolution_status": "cache_resolved",
            "canonical_latin_name": scientific_name,
            "name_i18n": {
                "zh_cn": zh_cn,
                "en": scientific_name,
                "status": "complete" if zh_cn else "partial",
            },
            "product_taxonomic_rank": "species",
            "ncbi_taxonomy_rank": "species",
            "taxonomy_rank_mapping_rule": "ncbi_species_to_product_species_v1",
            "taxonomy_lineage_anchor": None,
            "taxonomy_resolution_reason_code": (
                "versioned_registry_name_id_rank_verified"
            ),
        }


def test_taxonomy_resolver_uses_exact_ncbi_scientific_name_query(tmp_path: Path) -> None:
    empty_terms_path = tmp_path / "empty-terms.json"
    empty_terms_path.write_text('{"pathogens": []}', encoding="utf-8")
    requested_urls: List[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        assert request.url.params["db"] == "taxonomy"
        if "esearch.fcgi" in str(request.url):
            assert str(request.url).startswith(NCBI_TAXONOMY_SEARCH_URL)
            assert request.url.params["term"] == '"Aeromonas hydrophila"[Scientific Name]'
            assert request.url.params["retmax"] == "2"
            return httpx.Response(200, json={"esearchresult": {"idlist": ["644"]}})
        assert str(request.url).startswith(NCBI_TAXONOMY_SUMMARY_URL)
        assert request.url.params["id"] == "644"
        return httpx.Response(200, json={
            "result": {
                "uids": ["644"],
                "644": {
                    "uid": "644",
                    "scientificname": "Aeromonas hydrophila",
                    "rank": "species",
                },
            },
        })

    resolver = TaxonomyResolver(
        terms_path=empty_terms_path,
        transport=httpx.MockTransport(handler),
    )
    result = asyncio.run(resolver.resolve(["Aeromonas hydrophila"]))

    assert len(requested_urls) == 2
    assert result["aeromonas hydrophila"] == {
        "ncbi_taxonomy_id": 644,
        "taxonomy_resolution_status": "resolved",
        "canonical_latin_name": "Aeromonas hydrophila",
        "name_i18n": {
            "en": "Aeromonas hydrophila",
            "status": "partial",
        },
        "ncbi_taxonomy_rank": "species",
        "product_taxonomic_rank": "species",
        "taxonomy_rank_mapping_rule": "ncbi_species_to_product_species_v1",
        "taxonomy_lineage_anchor": None,
        "taxonomy_resolution_reason_code": "ncbi_name_and_rank_verified",
    }


def test_taxonomy_resolver_rejects_summary_name_or_rank_mismatch(tmp_path: Path) -> None:
    empty_terms_path = tmp_path / "empty-terms.json"
    empty_terms_path.write_text('{"pathogens": []}', encoding="utf-8")

    def resolver_for_summary(scientific_name: str, rank: str) -> TaxonomyResolver:
        def handler(request: httpx.Request) -> httpx.Response:
            if "esearch.fcgi" in str(request.url):
                return httpx.Response(200, json={"esearchresult": {"idlist": ["123"]}})
            return httpx.Response(200, json={
                "result": {
                    "uids": ["123"],
                    "123": {
                        "uid": "123",
                        "scientificname": scientific_name,
                        "rank": rank,
                    },
                },
            })

        return TaxonomyResolver(
            terms_path=empty_terms_path,
            transport=httpx.MockTransport(handler),
            retry_backoff_seconds=0,
        )

    name_mismatch = asyncio.run(
        resolver_for_summary("Different organism", "species").resolve(["Example pathogen"])
    )["example pathogen"]
    assert name_mismatch["ncbi_taxonomy_id"] is None
    assert name_mismatch["taxonomy_resolution_reason_code"] == (
        "ncbi_taxonomy_summary_scientific_name_mismatch"
    )

    rank_mismatch = asyncio.run(
        resolver_for_summary("Example pathogen", "genus").resolve(["Example pathogen"])
    )["example pathogen"]
    assert rank_mismatch["ncbi_taxonomy_id"] is None
    assert rank_mismatch["taxonomy_resolution_reason_code"] == (
        "ncbi_taxonomy_summary_non_concrete_rank"
    )


def test_taxonomy_resolver_requires_ncbi_virus_division_for_no_rank(
    tmp_path: Path,
) -> None:
    empty_terms_path = tmp_path / "empty-terms.json"
    empty_terms_path.write_text('{"pathogens": []}', encoding="utf-8")

    def resolve_with_division(division: str, rank: str = "no rank") -> dict[str, Any]:
        def handler(request: httpx.Request) -> httpx.Response:
            if "esearch.fcgi" in str(request.url):
                return httpx.Response(200, json={"esearchresult": {"idlist": ["999"]}})
            return httpx.Response(200, json={
                "result": {
                    "uids": ["999"],
                    "999": {
                        "uid": "999",
                        "scientificname": "Example virus",
                        "rank": rank,
                        "division": division,
                    },
                },
            })

        resolver = TaxonomyResolver(
            terms_path=empty_terms_path,
            transport=httpx.MockTransport(handler),
            retry_backoff_seconds=0,
        )
        return asyncio.run(resolver.resolve(["Example virus"]))["example virus"]

    non_viral = resolve_with_division("Bacteria")
    assert non_viral["taxonomy_resolution_status"] == "unresolved"
    assert non_viral["taxonomy_resolution_reason_code"] == (
        "ncbi_taxonomy_summary_non_concrete_rank"
    )

    viral = resolve_with_division("Viruses")
    assert viral["taxonomy_resolution_status"] == "resolved"
    assert viral["product_taxonomic_rank"] == "virus_type"
    assert viral["ncbi_taxonomy_rank"] == "no rank"
    assert viral["taxonomy_lineage_anchor"] == "NCBITaxon:10239"
    assert viral["taxonomy_resolution_reason_code"] == (
        "ncbi_name_rank_lineage_verified"
    )

    # ESummary sometimes serializes the displayed "no rank" as an empty
    # field.  It is normalized only alongside the explicit Viruses division.
    blank_rank_viral = resolve_with_division("Viruses", rank="")
    assert blank_rank_viral["taxonomy_resolution_status"] == "resolved"
    assert blank_rank_viral["ncbi_taxonomy_rank"] == "no rank"


def test_taxonomy_resolver_retries_429_and_5xx_before_verified_success(tmp_path: Path) -> None:
    empty_terms_path = tmp_path / "empty-terms.json"
    empty_terms_path.write_text('{"pathogens": []}', encoding="utf-8")
    search_calls = 0
    summary_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal search_calls, summary_calls
        if "esearch.fcgi" in str(request.url):
            search_calls += 1
            if search_calls == 1:
                return httpx.Response(429)
            if search_calls == 2:
                return httpx.Response(503)
            return httpx.Response(200, json={"esearchresult": {"idlist": ["644"]}})
        summary_calls += 1
        return httpx.Response(200, json={
            "result": {
                "uids": ["644"],
                "644": {
                    "uid": "644",
                    "scientificname": "Aeromonas hydrophila",
                    "rank": "species",
                },
            },
        })

    resolver = TaxonomyResolver(
        terms_path=empty_terms_path,
        transport=httpx.MockTransport(handler),
        max_attempts=3,
        retry_backoff_seconds=0,
    )
    result = asyncio.run(resolver.resolve(["Aeromonas hydrophila"]))

    assert search_calls == 3
    assert summary_calls == 1
    assert result["aeromonas hydrophila"]["taxonomy_resolution_status"] == "resolved"


def test_taxonomy_resolver_retry_exhaustion_has_safe_reason_code(tmp_path: Path) -> None:
    empty_terms_path = tmp_path / "empty-terms.json"
    empty_terms_path.write_text('{"pathogens": []}', encoding="utf-8")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, text="provider body must not enter the reason code")

    resolver = TaxonomyResolver(
        terms_path=empty_terms_path,
        transport=httpx.MockTransport(handler),
        max_attempts=2,
        retry_backoff_seconds=0,
    )
    record = asyncio.run(resolver.resolve(["Example pathogen"]))["example pathogen"]

    assert calls == 2
    assert record["taxonomy_resolution_status"] == "unresolved"
    assert record["taxonomy_resolution_reason_code"] == (
        "ncbi_taxonomy_search_retryable_http_exhausted"
    )
    assert "provider body" not in json.dumps(record)


def test_taxonomy_resolver_retries_network_error_before_success(tmp_path: Path) -> None:
    empty_terms_path = tmp_path / "empty-terms.json"
    empty_terms_path.write_text('{"pathogens": []}', encoding="utf-8")
    search_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal search_calls
        if "esearch.fcgi" in str(request.url):
            search_calls += 1
            if search_calls == 1:
                raise httpx.ConnectError("synthetic network failure", request=request)
            return httpx.Response(200, json={"esearchresult": {"idlist": ["644"]}})
        return httpx.Response(200, json={
            "result": {
                "uids": ["644"],
                "644": {
                    "uid": "644",
                    "scientificname": "Aeromonas hydrophila",
                    "rank": "species",
                },
            },
        })

    resolver = TaxonomyResolver(
        terms_path=empty_terms_path,
        transport=httpx.MockTransport(handler),
        max_attempts=2,
        retry_backoff_seconds=0,
    )
    record = asyncio.run(resolver.resolve(["Aeromonas hydrophila"]))[
        "aeromonas hydrophila"
    ]

    assert search_calls == 2
    assert record["taxonomy_resolution_status"] == "resolved"
    assert record["taxonomy_resolution_reason_code"] == "ncbi_name_and_rank_verified"


def test_verified_taxonomy_positive_cache_is_atomic_and_reused(tmp_path: Path) -> None:
    empty_terms_path = tmp_path / "empty-terms.json"
    empty_terms_path.write_text('{"pathogens": []}', encoding="utf-8")
    cache_path = tmp_path / "data" / "taxonomy-cache.json"
    calls = 0

    def first_handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if "esearch.fcgi" in str(request.url):
            return httpx.Response(200, json={"esearchresult": {"idlist": ["644"]}})
        return httpx.Response(200, json={
            "result": {
                "uids": ["644"],
                "644": {
                    "uid": "644",
                    "scientificname": "Aeromonas hydrophila",
                    "rank": "species",
                },
            },
        })

    first = TaxonomyResolver(
        terms_path=empty_terms_path,
        cache_path=cache_path,
        transport=httpx.MockTransport(first_handler),
        retry_backoff_seconds=0,
    )
    first_record = asyncio.run(first.resolve(["Aeromonas hydrophila"]))[
        "aeromonas hydrophila"
    ]
    assert first_record["taxonomy_resolution_reason_code"] == "ncbi_name_and_rank_verified"
    assert calls == 2
    persisted = json.loads(cache_path.read_text(encoding="utf-8"))
    assert persisted["schema_version"] == "owlpath.taxonomy-positive-cache.v2"
    assert persisted["entries"]["aeromonas hydrophila"] == {
        "canonical_latin_name": "Aeromonas hydrophila",
        "ncbi_taxonomy_id": 644,
        "ncbi_taxonomy_rank": "species",
        "product_taxonomic_rank": "species",
        "taxonomy_rank_mapping_rule": "ncbi_species_to_product_species_v1",
        "taxonomy_lineage_anchor": None,
    }
    assert not list(cache_path.parent.glob("*.tmp"))

    def no_network(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Verified positive cache unexpectedly used HTTP: %s" % request.url)

    second = TaxonomyResolver(
        terms_path=empty_terms_path,
        cache_path=cache_path,
        transport=httpx.MockTransport(no_network),
    )
    second_record = asyncio.run(second.resolve(["Aeromonas hydrophila"]))[
        "aeromonas hydrophila"
    ]
    assert second_record["taxonomy_resolution_status"] == "cache_resolved"
    assert second_record["taxonomy_resolution_reason_code"] == (
        "persistent_verified_cache_match"
    )


def test_injected_terms_path_does_not_use_real_data_cache_by_default(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    empty_terms_path = tmp_path / "empty-terms.json"
    empty_terms_path.write_text('{"pathogens": []}', encoding="utf-8")
    configured_data_dir = tmp_path / "real-data"
    monkeypatch.setenv("OWLPATH_DATA_DIR", str(configured_data_dir))

    def handler(request: httpx.Request) -> httpx.Response:
        if "esearch.fcgi" in str(request.url):
            return httpx.Response(200, json={"esearchresult": {"idlist": ["644"]}})
        return httpx.Response(200, json={
            "result": {
                "uids": ["644"],
                "644": {
                    "uid": "644",
                    "scientificname": "Aeromonas hydrophila",
                    "rank": "species",
                },
            },
        })

    resolver = TaxonomyResolver(
        terms_path=empty_terms_path,
        transport=httpx.MockTransport(handler),
        retry_backoff_seconds=0,
    )
    record = asyncio.run(resolver.resolve(["Aeromonas hydrophila"]))[
        "aeromonas hydrophila"
    ]

    assert record["taxonomy_resolution_status"] == "resolved"
    assert resolver.cache_path is None
    assert not configured_data_dir.exists()
