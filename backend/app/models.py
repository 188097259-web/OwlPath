from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Sequence, Set, Union
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return "%s_%s" % (prefix, uuid4().hex)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LocalizedText(StrictModel):
    """A bounded bilingual string with an explicit translation status.

    New v2 contracts use the object form.  Accepting a plain string here is a
    deliberate read-compatibility bridge for v1/provider payloads; it is
    classified as Chinese when it contains a CJK ideograph and English
    otherwise.  The generated provider schema still advertises the object
    form, so new model calls produce both languages in the same response.
    """

    zh_cn: Optional[str] = Field(default=None, min_length=1, max_length=3000)
    en: Optional[str] = Field(default=None, min_length=1, max_length=3000)
    status: Literal["complete", "partial"]

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_string(cls, value: Any) -> Any:
        if isinstance(value, str):
            cleaned = value.strip()
            language = "zh_cn" if any("\u3400" <= char <= "\u9fff" for char in cleaned) else "en"
            return {language: cleaned, "status": "partial"}
        if isinstance(value, dict):
            normalized = dict(value)
            for language in ("zh_cn", "en"):
                text = normalized.get(language)
                if isinstance(text, str):
                    normalized[language] = text.strip() or None
            if "status" not in normalized:
                normalized["status"] = (
                    "complete" if normalized.get("zh_cn") and normalized.get("en") else "partial"
                )
            return normalized
        return value

    @model_validator(mode="after")
    def require_at_least_one_language(self) -> "LocalizedText":
        if not self.zh_cn and not self.en:
            raise ValueError("at least one of zh_cn or en is required")
        if self.status == "complete" and not (self.zh_cn and self.en):
            raise ValueError("status complete requires both zh_cn and en")
        return self


def _preferred_localized_text(value: Any) -> Optional[str]:
    """Return a stable legacy-string view of a localized value."""
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, LocalizedText):
        return value.zh_cn or value.en
    if isinstance(value, dict):
        for language in ("zh_cn", "en"):
            text = value.get(language)
            if isinstance(text, str) and text.strip():
                return text.strip()
    return None


SENSITIVE_HEADER_NAMES = {
    "authorization", "proxy-authorization", "x-api-key", "api-key", "cookie",
    "set-cookie", "x-auth-token", "x-access-token", "x-goog-api-key",
    "ocp-apim-subscription-key", "host", "content-length", "transfer-encoding",
    "connection", "forwarded", "x-forwarded-host", "x-forwarded-for",
}
ALLOWED_EXTRA_HEADER_NAMES = {
    "anthropic-beta", "openai-organization", "openai-project",
}


def reject_sensitive_headers(value: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
    if value is None:
        return value
    sensitive_fragments = ("auth", "token", "secret", "password", "credential", "cookie", "api-key", "apikey")
    if any(
        key.strip().lower() in SENSITIVE_HEADER_NAMES
        or any(fragment in key.strip().lower() for fragment in sensitive_fragments)
        for key in value
    ):
        raise ValueError("secret-bearing headers must use the encrypted api_key field")
    disallowed = [key for key in value if key.strip().lower() not in ALLOWED_EXTRA_HEADER_NAMES]
    if disallowed:
        raise ValueError("extra_headers contains a header outside the non-secret allowlist")
    if any(str(item).strip().lower().startswith(("bearer ", "basic ")) for item in value.values()):
        raise ValueError("authorization values must use the encrypted api_key field")
    return value


def validate_http_url(value: Optional[str]) -> Optional[str]:
    if value is None or value == "":
        return value
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base_url must be an absolute http or https URL")
    if parsed.username or parsed.password:
        raise ValueError("base_url must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("base_url must not contain query parameters or fragments; use encrypted api_key for credentials")
    return value


SECRET_CONFIG_KEYS = {
    "api_key", "apikey", "authorization", "password", "secret", "token",
    "access_token", "refresh_token", "client_secret", "cookie",
}


def reject_secret_config_keys(value: Any, path: str = "options") -> Any:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).strip().lower() in SECRET_CONFIG_KEYS:
                raise ValueError("%s contains a secret-like field; use encrypted api_key" % path)
            reject_secret_config_keys(item, "%s.%s" % (path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            reject_secret_config_keys(item, "%s[%s]" % (path, index))
    return value


class ProviderKind(str, Enum):
    OPENAI_RESPONSES = "openai_responses"
    ANTHROPIC_MESSAGES = "anthropic_messages"
    GEMINI_GENERATE_CONTENT = "gemini_generate_content"
    OPENAI_COMPATIBLE = "openai_compatible"
    OLLAMA = "ollama"


class DataBoundary(str, Enum):
    EXTERNAL = "external"
    LOCAL = "local"


class ProviderCreate(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    kind: ProviderKind
    model: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
    base_url: Optional[str] = Field(default=None, max_length=500)
    api_key: Optional[SecretStr] = None
    enabled: bool = False
    data_boundary: Optional[DataBoundary] = None
    weight: float = Field(default=1.0, gt=0, le=10)
    extra_headers: Dict[str, str] = Field(default_factory=dict)
    options: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def http_url_only(cls, value: Optional[str]) -> Optional[str]:
        return validate_http_url(value)

    @field_validator("api_key")
    @classmethod
    def nonblank_api_key(cls, value: Optional[SecretStr]) -> Optional[SecretStr]:
        if value is None:
            return value
        cleaned = value.get_secret_value().strip()
        if not cleaned:
            raise ValueError("api_key must not be blank")
        return SecretStr(cleaned)

    @field_validator("extra_headers")
    @classmethod
    def no_secret_headers(cls, value: Dict[str, str]) -> Dict[str, str]:
        return reject_sensitive_headers(value) or {}

    @field_validator("options")
    @classmethod
    def no_secrets_in_options(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        return reject_secret_config_keys(value)

    @model_validator(mode="after")
    def default_boundary(self) -> "ProviderCreate":
        if self.data_boundary is None:
            self.data_boundary = DataBoundary.LOCAL if self.kind == ProviderKind.OLLAMA else DataBoundary.EXTERNAL
        return self


class ProviderUpdate(StrictModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    model: Optional[str] = Field(
        default=None, min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"
    )
    base_url: Optional[str] = Field(default=None, max_length=500)
    api_key: Optional[SecretStr] = None
    clear_api_key: bool = False
    enabled: Optional[bool] = None
    data_boundary: Optional[DataBoundary] = None
    weight: Optional[float] = Field(default=None, gt=0, le=10)
    extra_headers: Optional[Dict[str, str]] = None
    options: Optional[Dict[str, Any]] = None

    @field_validator("base_url")
    @classmethod
    def http_url_only(cls, value: Optional[str]) -> Optional[str]:
        return validate_http_url(value)

    @field_validator("api_key")
    @classmethod
    def nonblank_api_key(cls, value: Optional[SecretStr]) -> Optional[SecretStr]:
        if value is None:
            return value
        cleaned = value.get_secret_value().strip()
        if not cleaned:
            raise ValueError("api_key must not be blank")
        return SecretStr(cleaned)

    @field_validator("extra_headers")
    @classmethod
    def no_secret_headers(cls, value: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
        return reject_sensitive_headers(value)

    @field_validator("options")
    @classmethod
    def no_secrets_in_options(cls, value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        return reject_secret_config_keys(value) if value is not None else value

    @model_validator(mode="after")
    def unambiguous_key_update(self) -> "ProviderUpdate":
        if self.clear_api_key and self.api_key is not None:
            raise ValueError("clear_api_key and api_key are mutually exclusive")
        return self


class ProviderPublic(StrictModel):
    id: str
    name: str
    kind: ProviderKind
    model: str
    base_url: Optional[str]
    enabled: bool
    data_boundary: DataBoundary
    weight: float
    has_api_key: bool
    extra_header_names: List[str]
    options: Dict[str, Any]
    last_test_ok: Optional[bool] = None
    last_tested_at: Optional[datetime] = None
    last_test_latency_ms: Optional[int] = None
    last_test_error_code: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ProviderTestRequest(StrictModel):
    confirm_possible_cost: bool = False


class Sex(str, Enum):
    FEMALE = "female"
    MALE = "male"
    INTERSEX = "intersex"
    UNKNOWN = "unknown"


class CareSetting(str, Enum):
    OUTPATIENT = "outpatient"
    EMERGENCY = "emergency"
    WARD = "ward"
    ICU = "icu"
    OTHER = "other"


class CaseDemographics(StrictModel):
    age_years: Optional[float] = Field(default=None, ge=0, le=130)
    sex: Sex = Sex.UNKNOWN
    pregnant: Optional[bool] = None
    immunocompromised: Optional[bool] = None
    region_code: Optional[str] = Field(default=None, max_length=80, pattern=r"^[A-Za-z0-9._:-]+$")
    care_setting: CareSetting = CareSetting.OTHER


class CaseContext(StrictModel):
    primary_syndrome: Optional[Literal[
        "respiratory", "bloodstream", "urinary", "central_nervous_system", "other"
    ]] = None
    acquisition_context: Optional[Literal["community", "healthcare_associated", "hospital_acquired", "unknown"]] = "unknown"
    institution_code: Optional[str] = Field(default=None, max_length=80, pattern=r"^[A-Za-z0-9._:-]+$")
    unit_code: Optional[str] = Field(default=None, max_length=80, pattern=r"^[A-Za-z0-9._:-]+$")
    notes_deidentified: Optional[str] = Field(default=None, max_length=4000)


class CaseDataOrigin(str, Enum):
    CLINICAL = "clinical"
    SYNTHETIC = "synthetic"


class CaseCreate(StrictModel):
    # A case alias is a pseudonymous local handle, never a patient name.  Keep
    # the alphabet intentionally small so common direct identifiers cannot be
    # smuggled into model input through this metadata field.
    case_alias: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    demographics: CaseDemographics = Field(default_factory=CaseDemographics)
    context: CaseContext = Field(default_factory=CaseContext)
    external_data_consent: bool = False
    data_origin: CaseDataOrigin = CaseDataOrigin.CLINICAL


class CaseUpdate(StrictModel):
    case_alias: Optional[str] = Field(
        default=None, min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
    )
    demographics: Optional[CaseDemographics] = None
    context: Optional[CaseContext] = None
    external_data_consent: Optional[bool] = None
    status: Optional[Literal["active", "closed"]] = None


class CaseRead(StrictModel):
    id: str
    case_alias: str
    demographics: CaseDemographics
    context: CaseContext
    external_data_consent: bool
    data_origin: CaseDataOrigin = CaseDataOrigin.CLINICAL
    status: str
    created_at: datetime
    updated_at: datetime


FORBIDDEN_PERSONAL_KEYS = {
    "name", "patient_name", "full_name", "id_number", "identity_number",
    "national_id", "phone", "telephone", "mobile", "email", "address",
    "medical_record_number", "mrn", "patient_id",
}


def reject_personal_keys(value: Any, path: str = "data") -> Any:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_PERSONAL_KEYS:
                raise ValueError("%s contains forbidden direct identifier field: %s" % (path, key))
            reject_personal_keys(item, "%s.%s" % (path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            reject_personal_keys(item, "%s[%s]" % (path, index))
    return value


class EventKind(str, Enum):
    HISTORY = "history"
    SYMPTOM = "symptom"
    EXPOSURE = "exposure"
    VITAL = "vital"
    LAB = "lab"
    IMAGING_REPORT = "imaging_report"
    MICROBIOLOGY = "microbiology"
    MEDICATION = "medication"
    PROCEDURE = "procedure"
    LOCAL_PRIOR = "local_prior"
    OTHER = "other"


class EventStatus(str, Enum):
    PRELIMINARY = "preliminary"
    FINAL = "final"
    AMENDED = "amended"
    CORRECTED = "corrected"
    ENTERED_IN_ERROR = "entered_in_error"
    UNKNOWN = "unknown"


class ClinicalEventCreate(StrictModel):
    kind: EventKind
    occurred_at: datetime
    visible_at: datetime
    collected_at: Optional[datetime] = None
    issued_at: Optional[datetime] = None
    source: str = Field(min_length=1, max_length=120)
    status: EventStatus = EventStatus.UNKNOWN
    data: Dict[str, Any]
    quality: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("occurred_at", "visible_at", "collected_at", "issued_at")
    @classmethod
    def normalize_event_time(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return value
        if value.tzinfo is None:
            raise ValueError("clinical timestamps must include an explicit timezone offset or Z")
        return value.astimezone(timezone.utc)

    @field_validator("data", "quality")
    @classmethod
    def no_direct_identifiers(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        reject_personal_keys(value)
        return value

    @model_validator(mode="after")
    def time_consistency(self) -> "ClinicalEventCreate":
        if self.visible_at < self.occurred_at:
            raise ValueError("visible_at cannot precede occurred_at")
        if self.collected_at and self.collected_at < self.occurred_at:
            raise ValueError("collected_at cannot precede occurred_at")
        if self.issued_at and self.collected_at and self.issued_at < self.collected_at:
            raise ValueError("issued_at cannot precede collected_at")
        if self.issued_at and not self.collected_at and self.issued_at < self.occurred_at:
            raise ValueError("issued_at cannot precede occurred_at")
        if self.issued_at and self.visible_at < self.issued_at:
            raise ValueError("visible_at cannot precede issued_at")
        return self


class ClinicalEventRead(ClinicalEventCreate):
    id: str
    case_id: str
    sequence: int
    created_at: datetime


class RankLevel(str, Enum):
    CATEGORY = "category"
    GENUS = "genus"
    SPECIES = "species"
    UNKNOWN = "unknown"


class PathogenCandidate(StrictModel):
    canonical_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=240)
    display_name_i18n: LocalizedText
    rank_level: RankLevel
    category: Optional[Literal[
        "bacteria", "virus", "fungus", "parasite", "other", "unknown"
    ]] = None
    genus: Optional[str] = Field(default=None, max_length=160)
    species: Optional[str] = Field(default=None, max_length=200)
    probability: float = Field(ge=0, le=1)
    calibration_status: Literal["uncalibrated_model_score", "calibrated", "heuristic_unvalidated"] = "uncalibrated_model_score"
    evidence_for: List[str] = Field(default_factory=list, max_length=20)
    evidence_against: List[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="before")
    @classmethod
    def bridge_legacy_name(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        if normalized.get("display_name_i18n") is None and normalized.get("name") is not None:
            normalized["display_name_i18n"] = normalized["name"]
        if normalized.get("name") is None:
            preferred = _preferred_localized_text(normalized.get("display_name_i18n"))
            if preferred:
                normalized["name"] = preferred
        return normalized


class CoinfectionPair(StrictModel):
    pathogen_ids: List[str] = Field(min_length=2, max_length=4)
    probability: float = Field(ge=0, le=1)
    rationale: Optional[str] = Field(default=None, max_length=1000)


class NextTestSuggestion(StrictModel):
    test_code: str = Field(min_length=1, max_length=160)
    test_name: str = Field(min_length=1, max_length=240)
    test_name_i18n: LocalizedText
    specimen: Optional[str] = Field(default=None, max_length=160)
    rationale: str = Field(min_length=1, max_length=1600)
    rationale_i18n: LocalizedText
    expected_information_gain: float = Field(default=0.0, ge=0, le=1)
    estimated_turnaround: Optional[str] = Field(default=None, max_length=120)
    burden: Literal["low", "moderate", "high", "unknown"] = "unknown"
    requires_clinician_order: bool = True

    @model_validator(mode="before")
    @classmethod
    def bridge_legacy_text(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        pairs = (
            ("test_name", "test_name_i18n"),
            ("rationale", "rationale_i18n"),
        )
        for legacy_field, localized_field in pairs:
            if normalized.get(localized_field) is None and normalized.get(legacy_field) is not None:
                normalized[localized_field] = normalized[legacy_field]
            if normalized.get(legacy_field) is None:
                preferred = _preferred_localized_text(normalized.get(localized_field))
                if preferred:
                    normalized[legacy_field] = preferred
        return normalized


class ModelPrediction(StrictModel):
    summary: str = Field(min_length=1, max_length=3000)
    summary_i18n: LocalizedText
    infection_probability: float = Field(ge=0, le=1)
    syndrome_probabilities: Dict[str, float] = Field(default_factory=dict)
    candidates: List[PathogenCandidate] = Field(default_factory=list, max_length=20)
    coinfection_probability: float = Field(default=0.0, ge=0, le=1)
    coinfection_pairs: List[CoinfectionPair] = Field(default_factory=list, max_length=10)
    unknown_probability: float = Field(default=0.0, ge=0, le=1)
    next_tests: List[NextTestSuggestion] = Field(default_factory=list, max_length=10)
    data_quality_warnings: List[str] = Field(default_factory=list, max_length=20)
    distribution_shift_warning: bool = False
    abstain: bool = False
    abstain_reason: Optional[str] = Field(default=None, max_length=1200)

    @model_validator(mode="before")
    @classmethod
    def bridge_legacy_summary(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        if normalized.get("summary_i18n") is None and normalized.get("summary") is not None:
            normalized["summary_i18n"] = normalized["summary"]
        if normalized.get("summary") is None:
            preferred = _preferred_localized_text(normalized.get("summary_i18n"))
            if preferred:
                normalized["summary"] = preferred
        return normalized

    @field_validator("syndrome_probabilities")
    @classmethod
    def valid_syndrome_probabilities(cls, value: Dict[str, float]) -> Dict[str, float]:
        allowed = {
            "respiratory", "bloodstream", "urinary", "central_nervous_system",
            "other", "non_infectious", "unknown",
        }
        safe: Dict[str, float] = {}
        for key, score in value.items():
            if score < 0 or score > 1:
                raise ValueError("syndrome probability for %s must be in [0, 1]" % key)
            normalized = str(key).strip().lower()
            if normalized in allowed:
                safe[normalized] = score
        return safe


class ClinicalReviewRecord(StrictModel):
    accepted: bool
    confirmed_at: datetime
    statement_version: str = Field(min_length=1, max_length=120)
    parser_version: Optional[str] = Field(default=None, max_length=160)
    source_text_sha256: Optional[str] = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")
    input_snapshot_sha256: Optional[str] = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")

    @field_validator("confirmed_at")
    @classmethod
    def normalize_confirmed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("confirmed_at must include an explicit timezone offset or Z")
        return value.astimezone(timezone.utc)


class TransferProviderTarget(StrictModel):
    provider_id: str
    kind: ProviderKind
    model: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
    base_url_origin: str = Field(min_length=1, max_length=500)
    endpoint_url: str = Field(min_length=1, max_length=800)
    data_boundary: DataBoundary


class DataTransferConsentRecord(StrictModel):
    accepted: bool
    confirmed_at: datetime
    statement_version: str = Field(min_length=1, max_length=120)
    external_provider_ids: List[str] = Field(default_factory=list, max_length=12)
    provider_targets: List[TransferProviderTarget] = Field(default_factory=list, max_length=12)
    input_snapshot_sha256: Optional[str] = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")

    @field_validator("confirmed_at")
    @classmethod
    def normalize_confirmed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("confirmed_at must include an explicit timezone offset or Z")
        return value.astimezone(timezone.utc)


class RunMode(str, Enum):
    LIVE = "live"
    RETROSPECTIVE = "retrospective"
    DEVELOPMENT_DEMO = "development_demo"


class RunCreate(StrictModel):
    case_id: str
    decision_time: Optional[datetime] = None
    run_mode: RunMode = RunMode.LIVE
    retrospective_anchor_id: Optional[str] = Field(
        default=None, min_length=8, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
    )
    # None preserves the API convenience of "all enabled providers".  An
    # explicit empty array means exactly zero external/configured providers,
    # which is required for an unambiguous baseline-only run.
    provider_ids: Optional[List[str]] = Field(default=None, max_length=12)
    include_baseline: bool = True
    clinical_review: Optional[ClinicalReviewRecord] = None
    data_transfer_consent: Optional[DataTransferConsentRecord] = None

    @field_validator("decision_time")
    @classmethod
    def decision_time_has_zone(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return value
        if value.tzinfo is None:
            raise ValueError("decision_time must include an explicit timezone offset or Z")
        return value.astimezone(timezone.utc)


class DevelopmentDemoRunCreate(StrictModel):
    """One-shot synthetic input for a real-provider development run.

    This deliberately has no clinical-review or patient-transfer-consent fields:
    the endpoint is a separate synthetic-only execution surface, not a relaxed
    form of the clinical ``RunCreate`` contract.
    """

    text: str = Field(min_length=1, max_length=30000)
    provider_ids: List[str] = Field(min_length=1, max_length=12)

    @field_validator("text")
    @classmethod
    def text_not_only_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must contain non-whitespace content")
        return value

    @field_validator("provider_ids")
    @classmethod
    def provider_ids_are_nonblank(cls, value: List[str]) -> List[str]:
        if any(not str(item).strip() for item in value):
            raise ValueError("provider_ids must not contain blank values")
        return value


class DevelopmentRunCreate(DevelopmentDemoRunCreate):
    """Development-first multi-agent run input.

    The full text is intentionally the primary LLM input on this explicitly
    synthetic/de-identified surface.  ``specialist_config_version`` is frozen
    into the execution manifest by the engine so a later prompt/config change
    cannot silently alter an existing run.
    """

    specialist_config_version: str = Field(
        default="owlpath.development-agents.v3",
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )


class DevelopmentSpecialistRole(str, Enum):
    """Versioned specialist roles used by development execution graphs.

    The first twenty-five values are the v3 core consultation team and dynamic
    specialty registry.  Older v2/v1 wire values are retained solely so
    persisted runs and provider fixtures remain readable; new execution
    manifests must not schedule those legacy roles.
    """

    # v3 core consultation team (always scheduled for a new run).
    INFECTIOUS_DISEASES = "infectious_diseases"
    CRITICAL_CARE_EMERGENCY = "critical_care_emergency"
    CLINICAL_EPIDEMIOLOGY = "clinical_epidemiology"
    LABORATORY_MEDICINE = "laboratory_medicine"
    CLINICAL_MICROBIOLOGY_CULTURE = "clinical_microbiology_culture"

    # v3 dynamic specialty registry (selected from source-fragment cues).
    RADIOLOGY = "radiology"
    PULMONOLOGY = "pulmonology"
    GASTROENTEROLOGY = "gastroenterology"
    HEPATOBILIARY_PANCREATIC = "hepatobiliary_pancreatic"
    UROLOGY = "urology"
    NEPHROLOGY = "nephrology"
    NEUROLOGY_NEUROINFECTION = "neurology_neuroinfection"
    CARDIOLOGY_ENDOCARDITIS = "cardiology_endocarditis"
    HEMATOLOGY_IMMUNOLOGY = "hematology_immunology"
    TRANSPLANT_INFECTIOUS_DISEASES = "transplant_infectious_diseases"
    SURGERY_SOURCE_CONTROL = "surgery_source_control"
    ORTHOPEDICS_BONE_JOINT = "orthopedics_bone_joint"
    DERMATOLOGY_SOFT_TISSUE = "dermatology_soft_tissue"
    OBSTETRICS_GYNECOLOGY = "obstetrics_gynecology"
    PEDIATRICS_NEONATOLOGY = "pediatrics_neonatology"
    TROPICAL_MEDICINE_PARASITOLOGY = "tropical_medicine_parasitology"
    MEDICAL_MYCOLOGY = "medical_mycology"
    CLINICAL_VIROLOGY_MOLECULAR = "clinical_virology_molecular"
    ANTIMICROBIAL_STEWARDSHIP = "antimicrobial_stewardship"
    HEALTHCARE_DEVICE_INFECTION = "healthcare_device_infection"

    # Legacy v2 wire values. Keep for read compatibility only.
    TIMELINE_COURSE = "timeline_course"
    HOST_SUSCEPTIBILITY = "host_susceptibility"
    SYNDROME_LOCALIZATION = "syndrome_localization"
    EXPOSURE_ONE_HEALTH = "exposure_one_health"
    LAB_PATHOPHYSIOLOGY = "lab_pathophysiology"
    ORGAN_SEVERITY = "organ_severity"
    IMAGING_DISSEMINATION = "imaging_dissemination"
    MICROBIOLOGY_TREATMENT = "microbiology_treatment"
    NEUROINFECTION = "neuroinfection"
    IMMUNOCOMPROMISED_OPPORTUNISTIC = "immunocompromised_opportunistic"
    TRAVEL_ZOONOTIC = "travel_zoonotic"
    HEALTHCARE_DEVICE_AMR = "healthcare_device_amr"

    # Deprecated v1 wire values. Keep for read compatibility only.
    TIMELINE_HOST = "timeline_host"
    SYNDROME_SITE = "syndrome_site"
    EXPOSURE_EPIDEMIOLOGY = "exposure_epidemiology"
    LABORATORY_ORGAN_INJURY = "laboratory_organ_injury"
    IMAGING_MICROBIOLOGY_TREATMENT = "imaging_microbiology_treatment"


class DevelopmentAgentRole(str, Enum):
    # v3 core consultation team.
    INFECTIOUS_DISEASES = "infectious_diseases"
    CRITICAL_CARE_EMERGENCY = "critical_care_emergency"
    CLINICAL_EPIDEMIOLOGY = "clinical_epidemiology"
    LABORATORY_MEDICINE = "laboratory_medicine"
    CLINICAL_MICROBIOLOGY_CULTURE = "clinical_microbiology_culture"

    # v3 dynamic specialty registry.
    RADIOLOGY = "radiology"
    PULMONOLOGY = "pulmonology"
    GASTROENTEROLOGY = "gastroenterology"
    HEPATOBILIARY_PANCREATIC = "hepatobiliary_pancreatic"
    UROLOGY = "urology"
    NEPHROLOGY = "nephrology"
    NEUROLOGY_NEUROINFECTION = "neurology_neuroinfection"
    CARDIOLOGY_ENDOCARDITIS = "cardiology_endocarditis"
    HEMATOLOGY_IMMUNOLOGY = "hematology_immunology"
    TRANSPLANT_INFECTIOUS_DISEASES = "transplant_infectious_diseases"
    SURGERY_SOURCE_CONTROL = "surgery_source_control"
    ORTHOPEDICS_BONE_JOINT = "orthopedics_bone_joint"
    DERMATOLOGY_SOFT_TISSUE = "dermatology_soft_tissue"
    OBSTETRICS_GYNECOLOGY = "obstetrics_gynecology"
    PEDIATRICS_NEONATOLOGY = "pediatrics_neonatology"
    TROPICAL_MEDICINE_PARASITOLOGY = "tropical_medicine_parasitology"
    MEDICAL_MYCOLOGY = "medical_mycology"
    CLINICAL_VIROLOGY_MOLECULAR = "clinical_virology_molecular"
    ANTIMICROBIAL_STEWARDSHIP = "antimicrobial_stewardship"
    HEALTHCARE_DEVICE_INFECTION = "healthcare_device_infection"

    # Legacy v2 wire values. Keep for read compatibility only.
    TIMELINE_COURSE = "timeline_course"
    HOST_SUSCEPTIBILITY = "host_susceptibility"
    SYNDROME_LOCALIZATION = "syndrome_localization"
    EXPOSURE_ONE_HEALTH = "exposure_one_health"
    LAB_PATHOPHYSIOLOGY = "lab_pathophysiology"
    ORGAN_SEVERITY = "organ_severity"
    IMAGING_DISSEMINATION = "imaging_dissemination"
    MICROBIOLOGY_TREATMENT = "microbiology_treatment"
    NEUROINFECTION = "neuroinfection"
    IMMUNOCOMPROMISED_OPPORTUNISTIC = "immunocompromised_opportunistic"
    TRAVEL_ZOONOTIC = "travel_zoonotic"
    HEALTHCARE_DEVICE_AMR = "healthcare_device_amr"

    # Deprecated v1 wire values. Keep for read compatibility only.
    TIMELINE_HOST = "timeline_host"
    SYNDROME_SITE = "syndrome_site"
    EXPOSURE_EPIDEMIOLOGY = "exposure_epidemiology"
    LABORATORY_ORGAN_INJURY = "laboratory_organ_injury"
    IMAGING_MICROBIOLOGY_TREATMENT = "imaging_microbiology_treatment"
    EVIDENCE_RETRIEVAL = "evidence_retrieval"
    PATHOGEN_SYNTHESIS = "pathogen_synthesis"
    INDEPENDENT_CRITIC = "independent_critic"


class DevelopmentSourceFragment(StrictModel):
    source_fragment_id: str = Field(
        min_length=4,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    order: int = Field(ge=0)
    section: Optional[str] = Field(default=None, max_length=120)
    text: str = Field(min_length=1, max_length=5000)


class DevelopmentObservationKind(str, Enum):
    KEY_FACT = "key_fact"
    CONTRADICTION = "contradiction"
    MISSING_INFORMATION = "missing_information"
    SUPPORTING_PATTERN = "supporting_pattern"
    OPPOSING_PATTERN = "opposing_pattern"


class DevelopmentSpecialistObservation(StrictModel):
    observation_id: str = Field(min_length=1, max_length=160)
    kind: DevelopmentObservationKind
    statement_i18n: LocalizedText
    source_fragment_ids: List[str] = Field(default_factory=list, max_length=40)
    importance: Literal["low", "moderate", "high", "critical"] = "moderate"


class DevelopmentRetrievalConceptKind(str, Enum):
    SYNDROME = "syndrome"
    EXPOSURE = "exposure"
    HOST_FACTOR = "host_factor"
    ANATOMY = "anatomy"
    TEST_CONTEXT = "test_context"
    ACQUISITION = "acquisition"
    PATHOGEN = "pathogen"
    GEO_SEASON = "geo_season"


class DevelopmentRetrievalConcept(StrictModel):
    """A de-identified, source-grounded concept safe for retrieval planning."""

    kind: DevelopmentRetrievalConceptKind
    term_en: str = Field(min_length=1, max_length=240)
    source_fragment_ids: List[str] = Field(default_factory=list, max_length=40)
    negated: bool = False

    @field_validator("term_en")
    @classmethod
    def normalize_term_en(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("term_en must contain non-whitespace content")
        return normalized


class DevelopmentTaxonomicRank(str, Enum):
    """Draft ranks deliberately include invalid final-output levels.

    Category/genus/unknown are accepted in a *draft* so the deterministic
    validator and critic can report a precise semantic error and request one
    correction.  They can never be converted to a final concrete Top-5.
    """

    SPECIES = "species"
    SPECIES_COMPLEX = "species_complex"
    VIRUS_TYPE = "virus_type"
    GENUS = "genus"
    CATEGORY = "category"
    UNKNOWN = "unknown"


class DevelopmentPathogenCategory(str, Enum):
    BACTERIA = "bacteria"
    VIRUS = "virus"
    FUNGUS = "fungus"
    PARASITE = "parasite"
    OTHER = "other"


class DevelopmentTaxonomyResolutionStatus(str, Enum):
    NOT_CHECKED = "not_checked"
    RESOLVED = "resolved"
    CACHE_RESOLVED = "cache_resolved"
    UNRESOLVED = "unresolved"
    INVALID = "invalid"


class DevelopmentPathogenProposal(StrictModel):
    canonical_latin_name: str = Field(min_length=1, max_length=240)
    name_i18n: LocalizedText
    taxonomic_rank: DevelopmentTaxonomicRank
    category: DevelopmentPathogenCategory
    model_score: float = Field(ge=0, le=1)
    rationale_i18n: LocalizedText
    counterevidence_i18n: Optional[LocalizedText] = None
    source_fragment_ids: List[str] = Field(default_factory=list, max_length=40)


class DevelopmentSpecialistResult(StrictModel):
    schema_version: Literal[
        "owlpath.specialist.v1", "owlpath.specialist.v2"
    ] = "owlpath.specialist.v2"
    role: DevelopmentSpecialistRole
    summary_i18n: LocalizedText
    observations: List[DevelopmentSpecialistObservation] = Field(default_factory=list, max_length=40)
    candidate_pool: List[DevelopmentPathogenProposal] = Field(default_factory=list, max_length=15)
    retrieval_concepts: List[DevelopmentRetrievalConcept] = Field(
        default_factory=list,
        max_length=30,
    )
    warnings: List[str] = Field(default_factory=list, max_length=30)


class DevelopmentEvidenceSource(StrictModel):
    evidence_source_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=500)
    url: str = Field(min_length=1, max_length=1200)
    source_kind: Literal[
        "europe_pmc", "pubmed", "who", "cdc", "ecdc", "professional_society", "journal", "other"
    ]
    citation: Optional[str] = Field(default=None, max_length=1200)
    relevance_i18n: LocalizedText


class DevelopmentEvidenceLink(StrictModel):
    statement_i18n: LocalizedText
    source_fragment_ids: List[str] = Field(default_factory=list, max_length=40)
    evidence_source_ids: List[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def require_a_traceable_source(self) -> "DevelopmentEvidenceLink":
        if not self.source_fragment_ids and not self.evidence_source_ids:
            raise ValueError("evidence must reference a case fragment or retrieved evidence source")
        return self


class DevelopmentDraftPathogen(StrictModel):
    """A synthesis draft that remains parseable when the LLM breaks semantics."""

    rank: int = Field(ge=1, le=10)
    canonical_latin_name: str = Field(min_length=1, max_length=240)
    name_i18n: LocalizedText
    taxonomic_rank: DevelopmentTaxonomicRank
    category: DevelopmentPathogenCategory
    ncbi_taxonomy_id: Optional[int] = Field(default=None, gt=0)
    taxonomy_resolution_status: DevelopmentTaxonomyResolutionStatus = (
        DevelopmentTaxonomyResolutionStatus.NOT_CHECKED
    )
    taxonomy_resolution_reason_code: str = Field(
        default="not_checked",
        min_length=1,
        max_length=160,
        pattern=r"^[a-z0-9_]+$",
    )
    ncbi_taxonomy_rank: Optional[str] = Field(default=None, max_length=80)
    model_score: float = Field(ge=0, le=1)
    supporting_evidence: List[DevelopmentEvidenceLink] = Field(default_factory=list, max_length=20)
    opposing_evidence: List[DevelopmentEvidenceLink] = Field(default_factory=list, max_length=20)
    why_ranked_i18n: LocalizedText
    main_uncertainty_i18n: LocalizedText
    proposed_by_agent_roles: List[DevelopmentSpecialistRole] = Field(default_factory=list, max_length=12)


class DevelopmentCategoryOverview(StrictModel):
    category: DevelopmentPathogenCategory
    model_score: float = Field(ge=0, le=1)
    rationale_i18n: LocalizedText


class DevelopmentCoinfectionHypothesis(StrictModel):
    pathogen_latin_names: List[str] = Field(min_length=2, max_length=4)
    model_score: float = Field(ge=0, le=1)
    rationale_i18n: LocalizedText


class DevelopmentNextTest(StrictModel):
    test_code: str = Field(min_length=1, max_length=160)
    test_name_i18n: LocalizedText
    rationale_i18n: LocalizedText
    model_score: float = Field(ge=0, le=1)
    target_pathogen_latin_names: List[str] = Field(default_factory=list, max_length=20)
    source_fragment_ids: List[str] = Field(default_factory=list, max_length=40)


class DevelopmentSynthesisDraft(StrictModel):
    schema_version: Literal["owlpath.synthesis-draft.v1"] = "owlpath.synthesis-draft.v1"
    summary_i18n: LocalizedText
    concrete_pathogens: List[DevelopmentDraftPathogen] = Field(default_factory=list, max_length=10)
    category_overview: List[DevelopmentCategoryOverview] = Field(default_factory=list, max_length=5)
    unknown_score: float = Field(ge=0, le=1)
    coinfection_hypotheses: List[DevelopmentCoinfectionHypothesis] = Field(default_factory=list, max_length=10)
    next_tests: List[DevelopmentNextTest] = Field(default_factory=list, max_length=10)
    warnings: List[str] = Field(default_factory=list, max_length=30)


class DevelopmentContractIssue(StrictModel):
    code: str = Field(min_length=1, max_length=160)
    message: str = Field(min_length=1, max_length=1000)
    candidate_rank: Optional[int] = Field(default=None, ge=1, le=10)
    field: Optional[str] = Field(default=None, max_length=160)


class DevelopmentTop5Validation(StrictModel):
    valid: bool
    issues: List[DevelopmentContractIssue] = Field(default_factory=list)
    attempt_origin: Literal[
        "unspecified",
        "pipeline_technical_failure",
        "synthesis_draft",
        "revision_draft",
        "synthesis_failure_agent_pool_fallback",
        "post_revision_agent_pool_fallback",
    ] = "unspecified"


_GENERIC_PATHOGEN_NAMES = {
    "bacteria", "bacterium", "bacterial pathogen", "virus", "viral pathogen",
    "fungus", "fungi", "fungal pathogen", "parasite", "pathogen", "unknown",
    "unknown pathogen", "unspecified pathogen", "other pathogen", "细菌", "病毒",
    "真菌", "寄生虫", "病原体", "未知病原体", "其他病原体",
}


def _normalized_pathogen_name(value: str) -> str:
    return " ".join(value.strip().casefold().replace("_", " ").split())


def validate_development_top5(
    draft: Union[DevelopmentSynthesisDraft, Sequence[DevelopmentDraftPathogen]],
    *,
    valid_fragment_ids: Optional[Set[str]] = None,
    require_taxonomy_resolution: bool = True,
) -> DevelopmentTop5Validation:
    """Deterministically validate a development Top-5 without guessing names.

    The function reports all actionable violations in one pass.  It does not
    silently drop, rename, translate, re-rank, or backfill candidates; those
    changes must come from the critic/revision loop or the transparent agent
    pool fallback.
    """

    candidates = list(draft.concrete_pathogens if isinstance(draft, DevelopmentSynthesisDraft) else draft)
    issues: List[DevelopmentContractIssue] = []

    def add(code: str, message: str, candidate: Optional[DevelopmentDraftPathogen] = None, field: Optional[str] = None) -> None:
        issues.append(DevelopmentContractIssue(
            code=code,
            message=message,
            candidate_rank=candidate.rank if candidate else None,
            field=field,
        ))

    if len(candidates) != 5:
        add("top5_count", "concrete_pathogens must contain exactly five candidates")
    ranks = [item.rank for item in candidates]
    if sorted(ranks) != list(range(1, len(candidates) + 1)) or len(set(ranks)) != len(ranks):
        add("rank_sequence", "candidate ranks must be unique and contiguous starting at 1", field="rank")

    allowed_ranks = {
        DevelopmentTaxonomicRank.SPECIES,
        DevelopmentTaxonomicRank.SPECIES_COMPLEX,
        DevelopmentTaxonomicRank.VIRUS_TYPE,
    }
    names_seen: Set[str] = set()
    prior_score: Optional[float] = None
    for candidate in sorted(candidates, key=lambda item: item.rank):
        normalized_name = _normalized_pathogen_name(candidate.canonical_latin_name)
        localized_names = {
            _normalized_pathogen_name(text)
            for text in (candidate.name_i18n.zh_cn, candidate.name_i18n.en)
            if text
        }
        if candidate.taxonomic_rank not in allowed_ranks:
            add(
                "non_concrete_taxonomic_rank",
                "Top-5 entries must be a species, species complex, or explicit virus type",
                candidate,
                "taxonomic_rank",
            )
        if normalized_name in _GENERIC_PATHOGEN_NAMES or localized_names.intersection(_GENERIC_PATHOGEN_NAMES):
            add("generic_pathogen_name", "A pathogen category or unknown label cannot occupy Top-5", candidate, "canonical_latin_name")
        if any(token in normalized_name for token in ("unspecified", "unknown pathogen", "other pathogen")):
            add("unspecified_pathogen_name", "Top-5 names must identify a concrete pathogen", candidate, "canonical_latin_name")
        if normalized_name in names_seen:
            add("duplicate_pathogen", "Top-5 pathogen names must be unique", candidate, "canonical_latin_name")
        names_seen.add(normalized_name)
        if prior_score is not None and candidate.model_score > prior_score:
            add("score_order", "model_score must be non-increasing by rank", candidate, "model_score")
        prior_score = candidate.model_score
        if not candidate.supporting_evidence:
            add("missing_supporting_evidence", "Each candidate requires supporting evidence", candidate, "supporting_evidence")
        patient_fragment_ids = {
            fragment_id
            for evidence in candidate.supporting_evidence
            for fragment_id in evidence.source_fragment_ids
        }
        if not patient_fragment_ids:
            add("missing_case_evidence", "Each candidate must cite at least one case source fragment", candidate, "supporting_evidence")
        if valid_fragment_ids is not None:
            referenced_ids = {
                fragment_id
                for evidence in candidate.supporting_evidence + candidate.opposing_evidence
                for fragment_id in evidence.source_fragment_ids
            }
            unknown_ids = sorted(referenced_ids.difference(valid_fragment_ids))
            if unknown_ids:
                add(
                    "unknown_source_fragment",
                    "Candidate references unknown source fragments: %s" % ", ".join(unknown_ids[:5]),
                    candidate,
                    "supporting_evidence",
                )
        if not candidate.proposed_by_agent_roles:
            add("missing_agent_provenance", "Each candidate must identify proposing specialist agents", candidate, "proposed_by_agent_roles")
        if require_taxonomy_resolution and (
            candidate.ncbi_taxonomy_id is None
            or candidate.taxonomy_resolution_status not in {
                DevelopmentTaxonomyResolutionStatus.RESOLVED,
                DevelopmentTaxonomyResolutionStatus.CACHE_RESOLVED,
            }
        ):
            add(
                "taxonomy_unresolved",
                "A final Top-5 candidate requires a resolved NCBI Taxonomy ID",
                candidate,
                "ncbi_taxonomy_id",
            )
    return DevelopmentTop5Validation(valid=not issues, issues=issues)


class DevelopmentCriticIssue(StrictModel):
    code: str = Field(min_length=1, max_length=160)
    severity: Literal["warning", "error"]
    message_i18n: LocalizedText
    candidate_ranks: List[int] = Field(default_factory=list, max_length=10)
    source_fragment_ids: List[str] = Field(default_factory=list, max_length=40)


class DevelopmentCriticResult(StrictModel):
    schema_version: Literal["owlpath.critic.v1"] = "owlpath.critic.v1"
    accepted: bool
    revision_required: bool
    review_summary_i18n: LocalizedText
    issues: List[DevelopmentCriticIssue] = Field(default_factory=list, max_length=40)
    required_changes_i18n: List[LocalizedText] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def consistent_decision(self) -> "DevelopmentCriticResult":
        has_error = any(issue.severity == "error" for issue in self.issues)
        if self.accepted and (self.revision_required or has_error):
            raise ValueError("an accepted review cannot require revision or contain error issues")
        if not self.accepted and not self.revision_required:
            raise ValueError("a rejected review must require revision")
        return self


class DevelopmentSpecialistRequest(StrictModel):
    role: DevelopmentSpecialistRole
    source_text: str = Field(min_length=1, max_length=30000)
    source_fragments: List[DevelopmentSourceFragment] = Field(min_length=1, max_length=500)
    supplementary_structured_context: Dict[str, Any] = Field(default_factory=dict)


class DevelopmentRevisionContext(StrictModel):
    prior_draft: DevelopmentSynthesisDraft
    deterministic_issues: List[DevelopmentContractIssue] = Field(default_factory=list, max_length=100)
    critic_result: DevelopmentCriticResult


class DevelopmentSynthesisRequest(StrictModel):
    source_text: str = Field(min_length=1, max_length=30000)
    source_fragments: List[DevelopmentSourceFragment] = Field(min_length=1, max_length=500)
    specialist_results: List[DevelopmentSpecialistResult] = Field(min_length=1, max_length=12)
    evidence_sources: List[DevelopmentEvidenceSource] = Field(default_factory=list, max_length=100)
    evidence_board: Dict[str, Any] = Field(default_factory=dict)
    revision_context: Optional[DevelopmentRevisionContext] = None


class DevelopmentCriticRequest(StrictModel):
    source_text: str = Field(min_length=1, max_length=30000)
    source_fragments: List[DevelopmentSourceFragment] = Field(min_length=1, max_length=500)
    specialist_results: List[DevelopmentSpecialistResult] = Field(min_length=1, max_length=12)
    evidence_sources: List[DevelopmentEvidenceSource] = Field(default_factory=list, max_length=100)
    evidence_board: Dict[str, Any] = Field(default_factory=dict)
    draft: DevelopmentSynthesisDraft
    deterministic_issues: List[DevelopmentContractIssue] = Field(default_factory=list, max_length=100)


class DevelopmentConcretePathogen(StrictModel):
    rank: int = Field(ge=1, le=5)
    canonical_latin_name: str = Field(min_length=1, max_length=240)
    name_i18n: LocalizedText
    taxonomic_rank: Literal["species", "species_complex", "virus_type"]
    category: DevelopmentPathogenCategory
    ncbi_taxonomy_id: int = Field(gt=0)
    taxonomy_resolution_status: Literal["resolved", "cache_resolved"]
    taxonomy_resolution_reason_code: str = Field(
        default="legacy_reason_unavailable",
        min_length=1,
        max_length=160,
        pattern=r"^[a-z0-9_]+$",
    )
    ncbi_taxonomy_rank: Optional[str] = Field(default=None, max_length=80)
    model_score: float = Field(ge=0, le=1)
    supporting_evidence: List[DevelopmentEvidenceLink] = Field(min_length=1, max_length=20)
    opposing_evidence: List[DevelopmentEvidenceLink] = Field(default_factory=list, max_length=20)
    why_ranked_i18n: LocalizedText
    main_uncertainty_i18n: LocalizedText
    proposed_by_agent_roles: List[DevelopmentSpecialistRole] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def require_patient_level_support(self) -> "DevelopmentConcretePathogen":
        if not any(item.source_fragment_ids for item in self.supporting_evidence):
            raise ValueError("a final candidate must cite at least one case source fragment")
        return self


class DevelopmentAgentObservationSummary(StrictModel):
    role: DevelopmentAgentRole
    status: Literal["completed", "completed_with_warnings", "failed", "skipped"]
    summary_i18n: LocalizedText
    warning_codes: List[str] = Field(default_factory=list, max_length=30)


class DevelopmentReviewSummary(StrictModel):
    # `accepted` intentionally means only that the final published draft passed
    # the deterministic Top-5 contract.  It does *not* mean the independent
    # critic approved that exact final draft; see `status` for that distinction.
    accepted: bool
    # Default keeps persisted owlpath.result.v3 records created before this
    # field was introduced readable without rewriting historical results.
    status: Literal[
        "critic_accepted",
        "revision_completed_not_re_reviewed",
        "critic_changes_not_closed",
        "critic_unavailable",
        "technical_failure",
        "not_reviewed",
    ] = "not_reviewed"
    revision_count: int = Field(default=0, ge=0, le=1)
    deterministic_validation: DevelopmentTop5Validation
    critic: Optional[DevelopmentCriticResult] = None


class DevelopmentResultV3(StrictModel):
    schema_version: Literal["owlpath.result.v3"] = "owlpath.result.v3"
    status: Literal["completed", "completed_with_warnings", "technical_failure"]
    summary_i18n: LocalizedText
    concrete_pathogens: List[DevelopmentConcretePathogen] = Field(default_factory=list, max_length=5)
    category_overview: List[DevelopmentCategoryOverview] = Field(default_factory=list, max_length=5)
    unknown_score: float = Field(ge=0, le=1)
    coinfection_hypotheses: List[DevelopmentCoinfectionHypothesis] = Field(default_factory=list, max_length=10)
    next_tests: List[DevelopmentNextTest] = Field(default_factory=list, max_length=10)
    evidence_sources: List[DevelopmentEvidenceSource] = Field(default_factory=list, max_length=100)
    agent_observations: List[DevelopmentAgentObservationSummary] = Field(default_factory=list, max_length=20)
    warnings: List[str] = Field(default_factory=list, max_length=100)
    review: DevelopmentReviewSummary
    fallback_mode: Literal[
        "none",
        "agent_pool_fallback",
        "revision_rejected_retained_prior_valid_draft",
    ] = "none"
    result_sha256: Optional[str] = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")

    @model_validator(mode="before")
    @classmethod
    def infer_legacy_review_status(cls, value: Any) -> Any:
        """Bridge stored v3 results created before review.status existed.

        This runs only while reading an old payload with no explicit status.
        New execution paths always write an authoritative status, which must
        never be overwritten during deserialization.
        """
        if not isinstance(value, dict):
            return value
        review = value.get("review")
        if not isinstance(review, dict) or "status" in review:
            return value

        normalized = dict(value)
        normalized_review = dict(review)
        critic = normalized_review.get("critic")
        critic_accepted = (
            isinstance(critic, dict)
            and critic.get("accepted") is True
        )
        try:
            revision_count = int(normalized_review.get("revision_count") or 0)
        except (TypeError, ValueError):
            revision_count = 0

        if normalized.get("status") == "technical_failure":
            inferred_status = "technical_failure"
        elif critic_accepted:
            inferred_status = "critic_accepted"
        elif revision_count > 0:
            inferred_status = "revision_completed_not_re_reviewed"
        elif isinstance(critic, dict):
            inferred_status = "critic_changes_not_closed"
        else:
            inferred_status = "critic_unavailable"

        normalized_review["status"] = inferred_status
        normalized["review"] = normalized_review
        return normalized

    @model_validator(mode="after")
    def completed_runs_require_exact_concrete_top5(self) -> "DevelopmentResultV3":
        if self.status in {"completed", "completed_with_warnings"}:
            if len(self.concrete_pathogens) != 5:
                raise ValueError("a completed development result requires exactly five concrete pathogens")
            ranks = [candidate.rank for candidate in self.concrete_pathogens]
            if ranks != [1, 2, 3, 4, 5]:
                raise ValueError("completed concrete_pathogens must be ordered by ranks 1 through 5")
            if any(
                self.concrete_pathogens[index].model_score
                < self.concrete_pathogens[index + 1].model_score
                for index in range(4)
            ):
                raise ValueError("completed concrete_pathogens must be ordered by non-increasing model_score")
            normalized_names = [
                _normalized_pathogen_name(candidate.canonical_latin_name)
                for candidate in self.concrete_pathogens
            ]
            taxonomy_ids = [candidate.ncbi_taxonomy_id for candidate in self.concrete_pathogens]
            if len(set(normalized_names)) != 5 or len(set(taxonomy_ids)) != 5:
                raise ValueError("completed concrete_pathogens must be taxonomically unique")
            if any(name in _GENERIC_PATHOGEN_NAMES for name in normalized_names):
                raise ValueError("pathogen categories and unknown labels cannot occupy final Top-5")
        elif self.concrete_pathogens:
            raise ValueError("technical_failure must not publish a partial pathogen ranking")
        return self


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class SafetyAction(str, Enum):
    NON_INFECTION = "non_infection"
    SPECIES_SET = "species_set"
    CATEGORY_ONLY = "category_only"
    NEXT_TEST = "next_test"
    ABSTAIN = "abstain"


class ModelContribution(StrictModel):
    provider_id: str
    provider_name: str
    status: str
    provider_kind: Optional[str] = None
    model: Optional[str] = None
    base_url_origin: Optional[str] = None
    weight: Optional[float] = None
    data_boundary: Optional[DataBoundary] = None
    model_fingerprint: Optional[str] = None
    latency_ms: Optional[int] = None
    error_code: Optional[str] = None


class DevelopmentDemoProjection(StrictModel):
    """Uncalibrated, sanitized Top-K view for synthetic development demos only."""

    development_demo: Literal[True] = True
    synthetic_only: Literal[True] = True
    uncalibrated: Literal[True] = True
    not_for_clinical_use: Literal[True] = True
    infection_probability: float = Field(ge=0, le=1)
    unknown_probability: float = Field(ge=0, le=1)
    candidates: List[PathogenCandidate]
    coinfection_pairs: List[CoinfectionPair] = Field(default_factory=list)
    bypassed_controls: List[str] = Field(default_factory=list)
    successful_model_count: int = Field(ge=0)
    applicability_warnings: List[str] = Field(default_factory=list)
    input_quality_warnings: List[str] = Field(default_factory=list)


class AggregatedResult(StrictModel):
    schema_version: str = "owlpath.result.v2"
    engine_version: str = "0.1.0-research"
    governance_version: str
    generated_at: datetime
    input_snapshot_sha256: str
    result_sha256: Optional[str] = None
    run_id: str
    decision_time: datetime
    infection_probability: float = Field(ge=0, le=1)
    syndrome_probabilities: Dict[str, float]
    candidates: List[PathogenCandidate]
    coinfection_probability: float = Field(ge=0, le=1)
    coinfection_pairs: List[CoinfectionPair]
    unknown_probability: float = Field(ge=0, le=1)
    disagreement_score: float = Field(ge=0, le=1)
    disagreement_notes: List[str]
    safety_action: SafetyAction
    safety_reasons: List[str]
    human_summary_i18n: LocalizedText
    safety_conclusion_i18n: LocalizedText
    next_tests: List[NextTestSuggestion]
    model_contributions: List[ModelContribution]
    limitations: List[str]
    development_demo: bool = False
    demo_projection: Optional[DevelopmentDemoProjection] = None
    research_only: bool = True

    @model_validator(mode="before")
    @classmethod
    def bridge_v1_result(cls, value: Any) -> Any:
        """Populate v2 presentation text while continuing to read v1 rows."""
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        action_value = normalized.get("safety_action", SafetyAction.ABSTAIN)
        action = action_value.value if isinstance(action_value, SafetyAction) else str(action_value)
        action_labels = {
            SafetyAction.NON_INFECTION.value: ("当前结果不支持报告感染性病原候选。", "The current result does not support reporting an infectious pathogen candidate."),
            SafetyAction.SPECIES_SET.value: ("候选可在物种集层级展示，仍需临床与微生物学复核。", "Candidates may be displayed at species-set level and still require clinical and microbiological review."),
            SafetyAction.CATEGORY_ONLY.value: ("安全裁决仅允许展示病原大类。", "The safety adjudication permits pathogen-category display only."),
            SafetyAction.NEXT_TEST.value: ("当前证据不足，建议先补充诊断信息。", "Current evidence is insufficient; additional diagnostic information is suggested first."),
            SafetyAction.ABSTAIN.value: ("当前输入不支持可靠结论，系统已弃答。", "The current input does not support a reliable conclusion, so the system abstained."),
        }
        safety_zh, safety_en = action_labels.get(action, action_labels[SafetyAction.ABSTAIN.value])
        try:
            infection_score = float(normalized.get("infection_probability", 0.0))
            unknown_score = float(normalized.get("unknown_probability", 0.0))
        except (TypeError, ValueError):
            infection_score = 0.0
            unknown_score = 0.0
        if normalized.get("human_summary_i18n") is None:
            normalized["human_summary_i18n"] = {
                "zh_cn": "融合结果：感染评分 %.2f，未知病因评分 %.2f。%s" % (
                    infection_score, unknown_score, safety_zh,
                ),
                "en": "Ensemble result: infection score %.2f and unknown-cause score %.2f. %s" % (
                    infection_score, unknown_score, safety_en,
                ),
                "status": "complete",
            }
        if normalized.get("safety_conclusion_i18n") is None:
            normalized["safety_conclusion_i18n"] = {
                "zh_cn": safety_zh,
                "en": safety_en,
                "status": "complete",
            }
        return normalized


class RunRead(StrictModel):
    id: str
    case_id: str
    decision_time: datetime
    requested_at: datetime
    run_mode: RunMode = RunMode.LIVE
    retrospective_anchor_id: Optional[str] = None
    status: RunStatus
    provider_ids: List[str]
    include_baseline: bool
    governance_version: str
    schema_version: str = "owlpath.result.v2"
    engine_version: str = "0.1.0-research"
    input_snapshot_sha256: Optional[str] = None
    execution_graph_version: Optional[str] = None
    execution_manifest_sha256: Optional[str] = None
    trace_version: Optional[str] = None
    result_sha256: Optional[str] = None
    result: Optional[Union[AggregatedResult, DevelopmentResultV3]] = None
    error: Optional[Dict[str, Any]] = None
    completed_at: Optional[datetime] = None
    clinical_review: Optional[ClinicalReviewRecord] = None
    data_transfer_consent: Optional[DataTransferConsentRecord] = None


class GovernanceConfig(StrictModel):
    version: str = "0.2.0-research"
    run_enabled: bool = True
    intended_use: str = "Research-only adult community-onset lower-respiratory pathogen hypothesis support"
    decision_support_only: bool = True
    minimum_age_years: float = Field(default=18.0, ge=0, le=130)
    allowed_syndromes: List[str] = Field(default_factory=lambda: ["respiratory"])
    excluded_populations: List[str] = Field(default_factory=lambda: ["immunocompromised", "pregnancy"])
    non_infection_max_probability: float = Field(default=0.35, ge=0, le=1)
    exact_species_min_probability: float = Field(default=0.55, ge=0, le=1)
    category_min_probability: float = Field(default=0.25, ge=0, le=1)
    unknown_abstain_threshold: float = Field(default=0.55, ge=0, le=1)
    max_disagreement: float = Field(default=0.45, ge=0, le=1)
    min_independent_nonbaseline_models_for_species: int = Field(default=2, ge=1, le=10)
    # A model claiming that its own score is calibrated is not sufficient.
    # Species-level output is unlocked only when governance names a separately
    # validated, frozen calibrator artifact.  The research default is locked.
    species_calibrator_version: Optional[str] = Field(default=None, min_length=1, max_length=160)
    max_candidates: int = Field(default=5, ge=1, le=20)
    disclaimer: str = "OwlPath 0.1.0-research is not clinically validated and must not independently direct diagnosis or treatment."


class CausalPathogenLabel(StrictModel):
    canonical_id: str
    name: str
    certainty: Literal["confirmed", "probable", "possible", "uncertain"]


class EvaluationLabel(StrictModel):
    infection_status: Literal["infectious", "non_infectious", "uncertain"]
    causal_pathogens: List[CausalPathogenLabel] = Field(default_factory=list)
    colonizers: List[str] = Field(default_factory=list)
    contaminants: List[str] = Field(default_factory=list)
    coinfection: Literal["yes", "no", "possible", "unknown"] = "unknown"
    adjudication_status: Literal["single_reviewer", "independent_consensus", "panel_consensus", "not_adjudicated"] = "not_adjudicated"
    label_version: str = "1"
    notes: Optional[str] = Field(default=None, max_length=3000)


class EvaluationCreate(StrictModel):
    run_id: str
    label: EvaluationLabel


class EvaluationRead(StrictModel):
    id: str
    run_id: str
    case_id: str
    label: EvaluationLabel
    metrics: Dict[str, Optional[float]]
    created_at: datetime
    updated_at: datetime


class ErrorBody(StrictModel):
    code: str
    message: str
    details: Any = None
    request_id: str


class ErrorResponse(StrictModel):
    error: ErrorBody


class ClinicalTextOrganizeRequest(StrictModel):
    text: str = Field(min_length=1, max_length=30000)
    decision_time: datetime
    source: str = Field(min_length=1, max_length=120)

    @field_validator("text")
    @classmethod
    def text_not_only_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must contain non-whitespace content")
        return value

    @field_validator("decision_time")
    @classmethod
    def decision_time_has_zone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("decision_time must include an explicit timezone offset or Z")
        return value


class CompilerWarning(StrictModel):
    code: str
    message: str
    severity: Literal["info", "warning"] = "warning"


class ClinicalTextDemographics(StrictModel):
    age: Optional[float] = Field(default=None, ge=0, le=130)
    sex: Literal["male", "female", "other", "unknown"] = "unknown"
    pregnant: Optional[bool] = None
    immunocompromised: Optional[bool] = None
    department: Optional[str] = None
    encounter_type: Optional[Literal["emergency", "inpatient", "outpatient", "icu"]] = Field(
        default=None, alias="encounterType"
    )

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ClinicalTextHistory(StrictModel):
    chief_complaint: str = Field(default="", alias="chiefComplaint")
    present_illness: str = Field(default="", alias="presentIllness")
    exposure_history: str = Field(default="", alias="exposureHistory")
    epidemiology: str = ""
    prior_antimicrobials: str = Field(default="", alias="priorAntimicrobials")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ClinicalTextHost(StrictModel):
    comorbidities: str = ""
    immune_status: str = Field(default="", alias="immuneStatus")
    devices_and_procedures: str = Field(default="", alias="devicesAndProcedures")
    allergies: str = ""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ClinicalTextVital(StrictModel):
    id: str
    measured_at: datetime = Field(alias="measuredAt")
    name: str
    value: str
    unit: str
    source: str
    time_certainty: Literal["explicit", "assumed_decision_time"] = Field(alias="timeCertainty")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ClinicalTextLab(StrictModel):
    id: str
    sampled_at: datetime = Field(alias="sampledAt")
    available_at: datetime = Field(alias="availableAt")
    name: str
    value: str
    unit: str
    reference_range: Optional[str] = Field(default=None, alias="referenceRange")
    abnormal: Literal["high", "low", "normal", "unknown"] = "unknown"
    source: str
    sampled_time_certainty: Literal["explicit", "assumed_decision_time"] = Field(alias="sampledTimeCertainty")
    available_time_certainty: Literal["explicit", "uncertain_assumed_decision_time"] = Field(alias="availableTimeCertainty")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ClinicalTextImaging(StrictModel):
    modality: Optional[str] = None
    performed_at: Optional[datetime] = Field(default=None, alias="performedAt")
    available_at: Optional[datetime] = Field(default=None, alias="availableAt")
    report: str = ""
    quality_note: Optional[str] = Field(default=None, alias="qualityNote")
    performed_time_certainty: Optional[Literal["explicit", "assumed_decision_time"]] = Field(
        default=None, alias="performedTimeCertainty"
    )
    available_time_certainty: Optional[Literal["explicit", "uncertain_assumed_decision_time"]] = Field(
        default=None, alias="availableTimeCertainty"
    )

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ClinicalTextCaseDraft(StrictModel):
    decision_time: datetime = Field(alias="decisionTime")
    scenario: Literal["lower_respiratory", "bloodstream", "urinary", "cns", "abdominal", "undifferentiated"]
    acquisition_context: Literal["community", "healthcare_associated", "hospital_acquired", "unknown"] = Field(
        default="unknown", alias="acquisitionContext"
    )
    demographics: ClinicalTextDemographics
    history: ClinicalTextHistory
    host: ClinicalTextHost
    vitals: List[ClinicalTextVital]
    labs: List[ClinicalTextLab]
    imaging: ClinicalTextImaging
    timeline: List[Dict[str, Any]] = Field(default_factory=list)
    selected_providers: List[str] = Field(default_factory=list, alias="selectedProviders")
    deidentified_note: str = Field(max_length=30000)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class OrganizedClinicalEvent(StrictModel):
    kind: EventKind
    occurred_at: datetime
    visible_at: datetime
    collected_at: Optional[datetime] = None
    issued_at: Optional[datetime] = None
    source: str
    status: EventStatus = EventStatus.FINAL
    data: Dict[str, Any]
    quality: Dict[str, Any] = Field(default_factory=dict)
    time_certainty: str


class ClinicalTextOrganizeResponse(StrictModel):
    case_draft: ClinicalTextCaseDraft
    recognized_sections: Dict[str, str]
    unrecognized_segments: List[str]
    events: List[OrganizedClinicalEvent]
    warnings: List[CompilerWarning]
    parser_version: str
    source_text_sha256: str
    persistence: Literal["none"] = "none"
    model_fact_preview: List["ClinicalFactPreviewItem"] = Field(default_factory=list)


class ClinicalFactPreviewItem(StrictModel):
    event_index: int = Field(ge=0)
    kind: EventKind
    occurred_at: datetime
    visible_at: datetime
    collected_at: Optional[datetime] = None
    issued_at: Optional[datetime] = None
    source: Literal["clinician_reviewed_structured_event"] = "clinician_reviewed_structured_event"
    status: EventStatus
    data: Dict[str, Any]
    quality: Dict[str, Any]


class ClinicalFactsPreviewRequest(StrictModel):
    events: List[ClinicalEventCreate] = Field(default_factory=list, max_length=200)


class ClinicalFactsPreviewResponse(StrictModel):
    facts: List[ClinicalFactPreviewItem]
    excluded_event_indexes: List[int]
    boundary: Literal["atomic_controlled_facts_only"] = "atomic_controlled_facts_only"
