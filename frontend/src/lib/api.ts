/**
 * OwlPath HTTP contract.
 *
 * All network paths and wire types live in this file so the UI can be adapted
 * to the backend without spreading transport assumptions across components.
 * API keys are write-only: this client never requests or renders their value.
 */

export type ConnectionState = 'checking' | 'online' | 'offline'
export type ProviderKind =
  | 'baseline'
  | 'openai_responses'
  | 'anthropic_messages'
  | 'gemini_generate_content'
  | 'deepseek'
  | 'qwen'
  | 'openai_compatible'
  | 'ollama'

/** Provider instances are backend-created UUIDs; several instances may share one kind. */
export type ProviderId = string
export type DataBoundary = 'local' | 'external'

export type ProviderHealth = 'ready' | 'missing_key' | 'error' | 'unknown'
export type RunStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
export type RunMode = 'live' | 'retrospective' | 'development_demo'
export type StageStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped'
export type SafetyDisposition =
  | 'non_infection'
  | 'species_supported'
  | 'category_only'
  | 'more_information_needed'
  | 'abstain'

export interface HealthResponse {
  status: 'ok' | 'degraded'
  service?: string
  version?: string
  baselineAvailable?: boolean
  time?: string
}

export interface ProviderSummary {
  id: ProviderId
  kind: ProviderKind
  name: string
  enabled: boolean
  configured: boolean
  hasApiKey: boolean
  model?: string
  baseUrl?: string
  weight?: number
  dataBoundary: DataBoundary
  health: ProviderHealth
  lastCheckedAt?: string
  lastTestLatencyMs?: number
  message?: string
}

export interface ProviderConfigInput {
  id?: ProviderId
  kind: ProviderKind
  name: string
  enabled: boolean
  model?: string
  baseUrl?: string
  weight?: number
  dataBoundary: DataBoundary
  apiKey?: string
  clearApiKey?: boolean
}

export interface ProviderTestResponse {
  ok: boolean
  latencyMs?: number
  model?: string
  message?: string
  schemaValid?: boolean
  errorCode?: string
  retryable?: boolean
}

export interface VitalEntry {
  id: string
  measuredAt: string
  name: string
  value: string
  unit: string
  source?: string
  timeUncertain?: boolean
  sourceText?: string
}

export interface LabEntry {
  id: string
  sampledAt: string
  availableAt: string
  name: string
  value: string
  unit: string
  referenceRange?: string
  abnormal?: 'high' | 'low' | 'normal' | 'unknown'
  source?: string
  timeUncertain?: boolean
  sourceText?: string
}

export interface TimelineEntry {
  id: string
  occurredAt: string
  availableAt: string
  kind: 'symptom' | 'exam' | 'lab' | 'imaging' | 'treatment' | 'microbiology' | 'other'
  title: string
  detail?: string
  source?: string
}

export interface CaseDraft {
  caseId?: string
  deidentifiedNote?: string
  decisionTime: string
  scenario: 'lower_respiratory' | 'bloodstream' | 'urinary' | 'cns' | 'abdominal' | 'undifferentiated'
  acquisitionContext: 'community' | 'healthcare_associated' | 'hospital_acquired' | 'unknown'
  demographics: {
    age?: number
    sex?: 'male' | 'female' | 'other' | 'unknown'
    pregnant?: boolean
    immunocompromised?: boolean
    department?: string
    encounterType?: 'emergency' | 'inpatient' | 'outpatient' | 'icu'
  }
  history: {
    chiefComplaint: string
    presentIllness: string
    exposureHistory: string
    epidemiology: string
    priorAntimicrobials: string
  }
  host: {
    comorbidities: string
    immuneStatus: string
    devicesAndProcedures: string
    allergies: string
  }
  vitals: VitalEntry[]
  labs: LabEntry[]
  imaging: {
    modality?: string
    performedAt?: string
    availableAt?: string
    report: string
    qualityNote?: string
  }
  timeline: TimelineEntry[]
  selectedProviders: ProviderId[]
}

export interface ClinicalTextOrganization {
  deidentifiedNote: string
  demographics: CaseDraft['demographics']
  scenario: CaseDraft['scenario']
  acquisitionContext: CaseDraft['acquisitionContext']
  history: CaseDraft['history']
  host: CaseDraft['host']
  vitals: VitalEntry[]
  labs: LabEntry[]
  imaging: CaseDraft['imaging']
  recognizedSections: Record<string, string>
  unrecognized: string[]
  warnings: Array<{ code: string; message: string; severity: 'info' | 'warning' }>
  parserVersion: string
  sourceTextSha256: string
  modelFactPreview: Array<{
    eventIndex: number
    kind: string
    occurredAt: string
    visibleAt: string
    collectedAt?: string
    issuedAt?: string
    source: string
    status: string
    data: Record<string, unknown>
    quality: Record<string, unknown>
  }>
}

export interface CreateRunRequest {
  case: CaseDraft
  clinicalReview: {
    accepted: boolean
    confirmedAt: string
    statementVersion: string
    parserVersion?: string
    sourceTextSha256?: string
  }
  dataTransferConsent?: {
    accepted: boolean
    confirmedAt: string
    statementVersion: string
    externalProviderIds: ProviderId[]
    providerTargets: Array<{
      providerId: ProviderId
      kind: ProviderKind
      model: string
      baseUrl?: string
      dataBoundary: DataBoundary
    }>
  }
}

export interface CreateRunResponse {
  runId: string
  status: RunStatus
  createdAt?: string
}

export interface LocalizedText {
  zhCn?: string
  en?: string
  status?: 'complete' | 'partial'
}

export type AgentPlane = 'governance' | 'online' | 'offline'
export type AgentNodeStatus = StageStatus | 'bypassed' | 'not_started'

export interface AgentEdge {
  source: string
  target: string
  kind: string
  label?: LocalizedText
}

export interface AgentArtifactSummary {
  key: string
  label: LocalizedText
  summary?: LocalizedText
  value?: string | number | boolean
  direction?: string
  contentSha256?: string
  integrityOk?: boolean
  visibility?: string
  json?: unknown
}

export interface AgentNode {
  id: string
  nodeKey: string
  name: LocalizedText
  kind: string
  role?: LocalizedText
  description?: LocalizedText
  plane: AgentPlane
  dependsOn: string[]
  sequence?: number
  attempt?: number
  parentId?: string
  status?: AgentNodeStatus
  outcome?: string
  maturity?: string
  version?: string
  provider?: string
  model?: string
  startedAt?: string
  completedAt?: string
  latencyMs?: number
  inputSha256?: string
  outputSha256?: string
  error?: { code?: string; message?: LocalizedText; retryable?: boolean }
  artifacts?: AgentArtifactSummary[]
  safetyJson?: unknown
  metadata?: Record<string, unknown>
}

export interface AgentGraph {
  version?: string
  name?: LocalizedText
  description?: LocalizedText
  nodes: AgentNode[]
  edges: AgentEdge[]
  maturity?: string
}

export interface ArchitectureResponse {
  version?: string
  current: AgentGraph
  target: AgentGraph
  planes: Array<{ id: AgentPlane; name: LocalizedText; description?: LocalizedText }>
  edgeTypes: Array<{ id: string; name: LocalizedText; description?: LocalizedText }>
}

export interface RunTrace extends AgentGraph {
  runId: string
  traceVersion?: string
  runMode?: RunMode
}

export interface RunStage {
  id: string
  name: string
  description?: string
  status: StageStatus
  startedAt?: string
  completedAt?: string
  durationMs?: number
  message?: string
}

export interface ModelRunState {
  providerId: ProviderId
  providerKind?: ProviderKind
  providerName?: string
  model?: string
  status: StageStatus
  latencyMs?: number
  message?: string
}

export interface CandidatePathogen {
  canonicalId?: string
  rank: number
  name: string
  displayNameI18n?: LocalizedText
  taxonomyLevel: 'category' | 'family' | 'genus' | 'species'
  category?: string
  genus?: string
  species?: string
  calibrationStatus?: 'uncalibrated_model_score' | 'calibrated' | 'heuristic_unvalidated'
  probability?: number
  inPredictionSet?: boolean
  modelAgreement?: number
  evidenceFor?: string[]
  evidenceAgainst?: string[]
}

export interface EvidenceItem {
  id: string
  direction: 'support' | 'against' | 'uncertain'
  statement: string
  sourceType: 'patient' | 'model' | 'knowledge' | 'epidemiology'
  source?: string
  observedAt?: string
  quality?: 'high' | 'medium' | 'low' | 'unknown'
}

export interface NextTestRecommendation {
  code?: string
  name: string
  nameI18n?: LocalizedText
  rationale: string
  rationaleI18n?: LocalizedText
  expectedInformationGain?: number
  turnaround?: string
  specimen?: string
  availability?: string
  invasiveness?: 'none' | 'low' | 'moderate' | 'high'
  cautions?: string[]
  requiresClinicianOrder?: boolean
}

export type DevelopmentResultStatus = 'completed' | 'completed_with_warnings' | 'technical_failure'

export interface DevelopmentPathogen {
  rank: number
  canonicalName: string
  displayNameI18n?: LocalizedText
  taxonomyId?: string
  taxonomyStatus?: string
  modelScore?: number
  evidenceFor: string[]
  evidenceAgainst: string[]
  sourceFragmentIds: string[]
  citationIds: string[]
  specificityRationale?: LocalizedText
  uncertaintyReason?: LocalizedText
  agentSources: string[]
}

export interface DevelopmentCategory {
  name: string
  nameI18n?: LocalizedText
  modelScore?: number
}

export interface DevelopmentEvidenceSource {
  id: string
  title?: string
  url?: string
  source?: string
  publishedAt?: string
  summaryI18n?: LocalizedText
}

export interface DevelopmentAgentObservation {
  id: string
  nameI18n?: LocalizedText
  role?: string
  status?: string
  provider?: string
  model?: string
  summaryI18n?: LocalizedText
  keyFacts: string[]
  contradictions: string[]
  missingInformation: string[]
  candidatePool: string[]
  structuredOutput?: unknown
}

export interface DevelopmentReview {
  status?: string
  passed?: boolean
  issues: string[]
  revisionAttempted?: boolean
  fallbackUsed?: boolean
  raw?: unknown
}

export interface DevelopmentRunResult {
  status: DevelopmentResultStatus
  summaryI18n?: LocalizedText
  concretePathogens: DevelopmentPathogen[]
  categoryOverview: DevelopmentCategory[]
  unknownScore?: number
  coinfectionHypotheses: Array<{ pathogens: string[]; modelScore?: number; rationaleI18n?: LocalizedText }>
  nextTests: NextTestRecommendation[]
  evidenceSources: DevelopmentEvidenceSource[]
  agentObservations: DevelopmentAgentObservation[]
  warnings: string[]
  review: DevelopmentReview
}

export interface SafetyAssessment {
  disposition: SafetyDisposition
  title: string
  explanation: string
  applicability: 'applicable' | 'partially_applicable' | 'not_applicable'
  dataQuality: 'high' | 'medium' | 'low'
  calibrationState?: 'reliable' | 'uncertain' | 'unavailable'
  outOfDistribution?: boolean
  conflicts?: string[]
  missingCriticalInformation?: string[]
  conclusionI18n?: LocalizedText
}

export interface ModelNormalizedOutput {
  summary: string
  summaryI18n?: LocalizedText
  infectionProbability?: number
  syndromeProbabilities: Array<{ name: string; score: number }>
  candidates: CandidatePathogen[]
  coinfectionProbability?: number
  coinfectionPairs: Array<{ pathogenIds: string[]; probability: number; rationale?: string }>
  unknownProbability?: number
  nextTests: NextTestRecommendation[]
  dataQualityWarnings: string[]
  distributionShiftWarning: boolean
  abstain: boolean
  abstainReason?: string
}

export interface ModelComparisonRow {
  outputId?: string
  providerId: ProviderId
  providerKind?: ProviderKind
  providerName?: string
  model?: string
  status: 'completed' | 'failed' | 'skipped'
  latencyMs?: number
  topCandidate?: string
  topProbability?: number
  predictionSet?: string[]
  unknownProbability?: number
  category?: string
  notes?: string
  normalized?: ModelNormalizedOutput
  createdAt?: string
  completedAt?: string
  error?: { code?: string; message?: string }
}

export interface RunResult {
  schemaVersion?: string
  mode?: 'development' | 'clinical' | string
  infectionProbability?: number
  syndrome?: string
  syndromeI18n?: LocalizedText
  categoryProbabilities?: Array<{ name: string; probability: number }>
  candidates: CandidatePathogen[]
  unknownProbability?: number
  coinfectionProbability?: number
  coinfectionCandidates?: string[]
  evidence: EvidenceItem[]
  nextTest?: NextTestRecommendation
  nextTests?: NextTestRecommendation[]
  safety: SafetyAssessment
  narrative?: string
  humanSummaryI18n?: LocalizedText
  comparison: ModelComparisonRow[]
  generatedAt: string
  modelVersion?: string
  calibrationVersion?: string
  knowledgeVersion?: string
  developmentDemo?: boolean
  demoSyntheticOnly?: boolean
  demoUncalibrated?: boolean
  demoNotForClinicalUse?: boolean
  demoBypassedControls?: string[]
  developmentResult?: DevelopmentRunResult
}

export interface RunDetail {
  runId: string
  caseId?: string
  caseSummary?: string
  decisionTime?: string
  runMode: RunMode
  retrospectiveAnchorId?: string
  status: RunStatus
  createdAt: string
  updatedAt?: string
  progress: number
  currentStage?: string
  stages: RunStage[]
  models: ModelRunState[]
  /**
   * A safe, structured run-level failure. The transport mapper deliberately
   * does not expose a provider/backend's raw error text to the browser.
   */
  error?: { code: string; message: string; retryable: boolean }
  result?: RunResult
  traceVersion?: string
  executionGraphVersion?: string
  resultSchemaVersion?: string
}

export interface RunHistoryItem {
  runId: string
  caseId?: string
  caseSummary?: string
  scenario?: string
  decisionTime?: string
  runMode: RunMode
  retrospectiveAnchorId?: string
  createdAt: string
  status: RunStatus
  disposition?: SafetyDisposition
  topCandidate?: string
  providers?: Array<{ id: ProviderId; name?: string; kind?: ProviderKind }>
  traceVersion?: string
}

export interface HistoryResponse {
  items: RunHistoryItem[]
  total: number
}

export interface MetricValue {
  key: string
  label: string
  value?: number
  unit?: string
  target?: number
  lowerIsBetter?: boolean
  description?: string
}

export interface EvaluationSlice {
  name: string
  sampleSize: number
  metrics: MetricValue[]
  warning?: string
}

export interface EvaluationResponse {
  evaluatedAt?: string
  datasetVersion?: string
  labelPolicy?: string
  decisionTimePolicy?: string
  sampleSize?: number
  metrics: MetricValue[]
  slices: EvaluationSlice[]
  labelDistribution?: Array<{ label: string; count: number; color?: string }>
  notes?: string[]
}

export type InfectionStatus = 'infectious' | 'non_infectious' | 'uncertain'
export type PathogenCertainty = 'confirmed' | 'probable' | 'possible' | 'uncertain'
export type CoinfectionLabel = 'yes' | 'no' | 'possible' | 'unknown'
export type AdjudicationStatus = 'single_reviewer' | 'independent_consensus' | 'panel_consensus' | 'not_adjudicated'

export interface CausalPathogenLabelInput {
  canonicalId: string
  name: string
  certainty: PathogenCertainty
}

export interface EvaluationLabelInput {
  infectionStatus: InfectionStatus
  causalPathogens: CausalPathogenLabelInput[]
  colonizers: string[]
  contaminants: string[]
  coinfection: CoinfectionLabel
  adjudicationStatus: AdjudicationStatus
  notes?: string
}

export interface CreateEvaluationInput {
  runId: string
  label: EvaluationLabelInput
}

export interface EvaluationRecord {
  id: string
  runId: string
  caseId: string
  metrics: Record<string, number | undefined>
  createdAt: string
  updatedAt: string
}

export interface VersionRecord {
  component: string
  version: string
  status: 'active' | 'candidate' | 'retired' | 'blocked'
  releasedAt?: string
  approvedBy?: string
  checksum?: string
  notes?: string
}

export interface AuditRecord {
  id: string
  time: string
  actor: string
  action: string
  target?: string
  result: 'success' | 'denied' | 'failed'
  detail?: string
}

export interface GovernanceResponse {
  scopeContract?: {
    population: string
    scenario: string
    decisionTimeRule: string
    intendedUse: string
    exclusions: string[]
  }
  versions: VersionRecord[]
  audits: AuditRecord[]
  monitoring?: Array<{ label: string; value?: number; unit?: string; state: 'ok' | 'warning' | 'critical' | 'unknown' }>
}

export class ApiError extends Error {
  status?: number
  detail?: string
  code?: string

  constructor(message: string, status?: number, detail?: string, code?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.code = code
  }
}

const API_BASE = ((import.meta as ImportMeta & { env?: { VITE_API_BASE_URL?: string } }).env?.VITE_API_BASE_URL || '').replace(/\/$/, '')
const BASELINE_ID = 'local_baseline'

type WireProviderKind = 'openai_responses' | 'anthropic_messages' | 'gemini_generate_content' | 'openai_compatible' | 'ollama'

interface WireProvider {
  id: string
  name: string
  kind: WireProviderKind
  model: string
  base_url?: string | null
  enabled: boolean
  data_boundary: 'external' | 'local'
  weight: number
  has_api_key: boolean
  last_test_ok?: boolean | null
  last_tested_at?: string | null
  last_test_latency_ms?: number | null
  last_test_error_code?: string | null
  created_at: string
  updated_at: string
}

interface WireLocalizedText {
  zh_cn?: string | null
  en?: string | null
  status?: 'complete' | 'partial'
}

interface WireCandidate {
  canonical_id: string
  name: string
  rank_level: 'category' | 'genus' | 'species' | 'unknown'
  category?: string | null
  genus?: string | null
  species?: string | null
  probability: number
  calibration_status: 'uncalibrated_model_score' | 'calibrated' | 'heuristic_unvalidated'
  evidence_for: string[]
  evidence_against: string[]
  display_name_i18n?: WireLocalizedText | null
}

interface WireNextTest {
  test_code: string
  test_name: string
  specimen?: string | null
  rationale: string
  expected_information_gain: number
  estimated_turnaround?: string | null
  burden: 'low' | 'moderate' | 'high' | 'unknown'
  requires_clinician_order: boolean
  test_name_i18n?: WireLocalizedText | null
  rationale_i18n?: WireLocalizedText | null
}

interface WirePrediction {
  summary: string
  summary_i18n?: WireLocalizedText | null
  infection_probability: number
  syndrome_probabilities: Record<string, number>
  candidates: WireCandidate[]
  coinfection_probability: number
  coinfection_pairs: Array<{ pathogen_ids: string[]; probability: number; rationale?: string | null }>
  unknown_probability: number
  next_tests: WireNextTest[]
  data_quality_warnings: string[]
  distribution_shift_warning: boolean
  abstain: boolean
  abstain_reason?: string | null
}

interface WireContribution {
  provider_id: string
  provider_name: string
  status: string
  provider_kind?: string | null
  model?: string | null
  latency_ms?: number | null
  error_code?: string | null
}

interface WireAggregatedResult {
  schema_version?: string
  engine_version: string
  governance_version: string
  generated_at: string
  infection_probability: number
  syndrome_probabilities: Record<string, number>
  candidates: WireCandidate[]
  coinfection_probability: number
  coinfection_pairs: Array<{ pathogen_ids: string[]; probability: number; rationale?: string | null }>
  unknown_probability: number
  disagreement_score: number
  disagreement_notes: string[]
  safety_action: 'non_infection' | 'species_set' | 'category_only' | 'next_test' | 'abstain'
  safety_reasons: string[]
  next_tests: WireNextTest[]
  model_contributions: WireContribution[]
  limitations: string[]
  research_only: boolean
  human_summary_i18n?: WireLocalizedText | null
  safety_conclusion_i18n?: WireLocalizedText | null
  development_demo?: boolean
  demo_projection?: {
    synthetic_only: boolean
    uncalibrated: boolean
    not_for_clinical_use: boolean
    bypassed_controls: string[]
    candidates: WireCandidate[]
    coinfection_pairs: Array<{ pathogen_ids: string[]; probability: number; rationale?: string | null }>
  } | null
}

interface WireDevelopmentPathogen {
  rank?: number
  canonical_name?: string
  canonicalName?: string
  canonical_latin_name?: string
  name?: string
  display_name_i18n?: WireLocalizedText | null
  displayNameI18n?: WireLocalizedText | null
  name_i18n?: WireLocalizedText | null
  taxonomy_id?: string | number | null
  taxonomyId?: string | number | null
  ncbi_taxonomy_id?: string | number | null
  taxonomy_status?: string | null
  taxonomyStatus?: string | null
  taxonomy_resolution_status?: string | null
  model_score?: number | null
  modelScore?: number | null
  evidence_for?: unknown[]
  evidenceFor?: unknown[]
  evidence_against?: unknown[]
  evidenceAgainst?: unknown[]
  supporting_evidence?: unknown[]
  opposing_evidence?: unknown[]
  source_fragment_ids?: string[]
  sourceFragmentIds?: string[]
  citation_ids?: string[]
  citationIds?: string[]
  specificity_rationale?: WireLocalizedText | string | null
  specificityRationale?: WireLocalizedText | string | null
  why_ranked_i18n?: WireLocalizedText | string | null
  uncertainty_reason?: WireLocalizedText | string | null
  uncertaintyReason?: WireLocalizedText | string | null
  main_uncertainty_i18n?: WireLocalizedText | string | null
  agent_sources?: string[]
  agentSources?: string[]
  proposed_by_agent_roles?: string[]
}

interface WireDevelopmentResult {
  status?: 'completed' | 'completed_with_warnings' | 'technical_failure'
  summary_i18n?: WireLocalizedText | null
  summaryI18n?: WireLocalizedText | null
  concrete_pathogens?: WireDevelopmentPathogen[]
  concretePathogens?: WireDevelopmentPathogen[]
  category_overview?: unknown[]
  categoryOverview?: unknown[]
  unknown_score?: number | null
  unknownScore?: number | null
  coinfection_hypotheses?: unknown[]
  coinfectionHypotheses?: unknown[]
  next_tests?: unknown[]
  nextTests?: unknown[]
  evidence_sources?: unknown[]
  evidenceSources?: unknown[]
  agent_observations?: unknown[]
  agentObservations?: unknown[]
  warnings?: unknown[]
  review?: unknown
}

interface WireResultV3 extends WireDevelopmentResult {
  schema_version?: string
  schemaVersion?: string
  mode?: string
  generated_at?: string
  generatedAt?: string
  engine_version?: string
  engineVersion?: string
  governance_version?: string
  governanceVersion?: string
  development_result?: WireDevelopmentResult | null
  developmentResult?: WireDevelopmentResult | null
  fallback_mode?: string
}

interface WireRun {
  id: string
  case_id: string
  decision_time: string
  requested_at: string
  run_mode?: RunMode
  retrospective_anchor_id?: string | null
  status: RunStatus
  provider_ids: string[]
  include_baseline: boolean
  governance_version: string
  schema_version?: string
  engine_version?: string
  input_snapshot_sha256?: string | null
  result_sha256?: string | null
  result?: WireAggregatedResult | WireResultV3 | null
  error?: { code?: string; message?: string; retryable?: boolean } | null
  completed_at?: string | null
  trace_version?: string | null
  execution_graph_version?: string | null
}

interface WireModelOutput {
  id: string
  provider_id: string
  provider_name: string
  status: string
  // Legacy clinical runs return WirePrediction. Development v3 persists one
  // normalized object per specialist/synthesis/critic Agent instead. Keep the
  // wire boundary honest and narrow the shape inside the mapper.
  normalized?: WirePrediction | Record<string, unknown> | null
  provider_kind?: string | null
  model?: string | null
  error?: { code?: string; message?: string; retryable?: boolean } | null
  latency_ms?: number | null
  created_at: string
  completed_at?: string | null
}

interface WireArchitectureNode {
  id: string
  name: WireLocalizedText
  description?: WireLocalizedText | null
  kind: string
  maturity: string
  plane: string
  order: number
}

interface WireArchitectureView {
  title: WireLocalizedText
  description: WireLocalizedText
  nodes: WireArchitectureNode[]
  edges: Array<{ source: string; target: string; kind: string; condition?: string | null }>
}

interface WireArchitecture {
  schema_version?: string
  architecture_version?: string
  generated_from?: string[]
  edge_legend?: Array<{ kind: string; zh_cn?: string | null; en?: string | null }>
  views: { current: WireArchitectureView; target: WireArchitectureView }
}

interface WireTraceArtifact {
  id: string
  direction?: string
  artifact_type?: string
  schema_version?: string | null
  content_sha256?: string | null
  content?: unknown
  created_at?: string
  integrity_ok?: boolean
  visibility?: string
  label_i18n?: WireLocalizedText | null
  summary_i18n?: WireLocalizedText | null
}

interface WireTraceNode {
  id: string
  node_key?: string
  node_kind?: string
  kind?: string
  display_name_i18n?: WireLocalizedText | null
  name?: WireLocalizedText | string
  role?: WireLocalizedText | string | null
  plane?: string
  depends_on?: string[]
  status?: string
  outcome?: string | null
  sequence?: number
  attempt?: number
  parent_node_id?: string | null
  provider_id?: string | null
  provider?: string | null
  provider_model?: string | null
  model?: string | null
  version?: string | null
  started_at?: string | null
  completed_at?: string | null
  latency_ms?: number | null
  input_artifact_id?: string | null
  output_artifact_id?: string | null
  input_sha256?: string | null
  output_sha256?: string | null
  error?: { code?: string; message?: WireLocalizedText | string; retryable?: boolean } | null
  metadata?: Record<string, unknown> | null
  artifacts?: WireTraceArtifact[]
}

interface WireRunTrace {
  run_id: string
  run_status?: RunStatus
  status?: RunStatus
  trace_version?: string
  execution_graph_version?: string
  execution_manifest_sha256?: string
  run_mode?: RunMode
  manifest?: Record<string, unknown>
  nodes?: WireTraceNode[]
  edges?: Array<{ from: string; to: string; relation?: string }>
}

interface WireTraceNodeDetail {
  node: WireTraceNode
  artifacts?: WireTraceArtifact[]
  trace_privacy?: Record<string, unknown>
}

interface WireGovernance {
  version: string
  run_enabled: boolean
  intended_use: string
  decision_support_only: boolean
  allowed_syndromes: string[]
  excluded_populations: string[]
  non_infection_max_probability: number
  exact_species_min_probability: number
  category_min_probability: number
  unknown_abstain_threshold: number
  max_disagreement: number
  min_independent_nonbaseline_models_for_species: number
  max_candidates: number
  disclaimer: string
}

interface WireAudit {
  id: string | number
  actor: string
  action: string
  entity_type: string
  entity_id: string
  details?: Record<string, unknown>
  created_at: string
}

interface WireEvaluationRead {
  id: string
  run_id: string
  case_id: string
  label: {
    infection_status: InfectionStatus
    causal_pathogens: Array<{ canonical_id: string; name: string; certainty: PathogenCertainty }>
    colonizers: string[]
    contaminants: string[]
    coinfection: CoinfectionLabel
    adjudication_status: AdjudicationStatus
    label_version: string
    notes?: string | null
  }
  metrics: Record<string, number | null>
  created_at: string
  updated_at: string
}

interface WireClinicalTextOrganization {
  case_draft: {
    decisionTime?: string
    decision_time?: string
    scenario: CaseDraft['scenario']
    acquisitionContext?: CaseDraft['acquisitionContext']
    acquisition_context?: CaseDraft['acquisitionContext']
    demographics: {
      age?: number | null
      sex?: CaseDraft['demographics']['sex']
      pregnant?: boolean | null
      immunocompromised?: boolean | null
      department?: string | null
      encounterType?: CaseDraft['demographics']['encounterType'] | null
      encounter_type?: CaseDraft['demographics']['encounterType'] | null
    }
    history: {
      chiefComplaint?: string
      chief_complaint?: string
      presentIllness?: string
      present_illness?: string
      exposureHistory?: string
      exposure_history?: string
      epidemiology?: string
      priorAntimicrobials?: string
      prior_antimicrobials?: string
    }
    host: {
      comorbidities?: string
      immuneStatus?: string
      immune_status?: string
      devicesAndProcedures?: string
      devices_and_procedures?: string
      allergies?: string
    }
    vitals: Array<{
      id: string
      measuredAt?: string
      measured_at?: string
      name: string
      value: string
      unit: string
      source?: string
      timeCertainty?: 'explicit' | 'assumed_decision_time'
      time_certainty?: 'explicit' | 'assumed_decision_time'
    }>
    labs: Array<{
      id: string
      sampledAt?: string
      sampled_at?: string
      availableAt?: string
      available_at?: string
      name: string
      value: string
      unit: string
      referenceRange?: string | null
      reference_range?: string | null
      abnormal?: LabEntry['abnormal']
      source?: string
      sampledTimeCertainty?: 'explicit' | 'assumed_decision_time'
      sampled_time_certainty?: 'explicit' | 'assumed_decision_time'
      availableTimeCertainty?: 'explicit' | 'uncertain_assumed_decision_time'
      available_time_certainty?: 'explicit' | 'uncertain_assumed_decision_time'
    }>
    imaging: {
      modality?: string | null
      performedAt?: string | null
      performed_at?: string | null
      availableAt?: string | null
      available_at?: string | null
      report?: string
      qualityNote?: string | null
      quality_note?: string | null
      performedTimeCertainty?: 'explicit' | 'assumed_decision_time' | null
      performed_time_certainty?: 'explicit' | 'assumed_decision_time' | null
      availableTimeCertainty?: 'explicit' | 'uncertain_assumed_decision_time' | null
      available_time_certainty?: 'explicit' | 'uncertain_assumed_decision_time' | null
    }
    deidentified_note: string
  }
  recognized_sections: Record<string, string>
  unrecognized_segments: string[]
  warnings: Array<{ code: string; message: string; severity: 'info' | 'warning' }>
  parser_version: string
  source_text_sha256: string
  model_fact_preview: Array<{
    event_index: number
    kind: string
    occurred_at: string
    visible_at: string
    collected_at?: string | null
    issued_at?: string | null
    source: string
    status: string
    data: Record<string, unknown>
    quality: Record<string, unknown>
  }>
  persistence: 'none'
}

async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  headers.set('Accept', 'application/json')

  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers,
      credentials: 'same-origin',
      signal: init.signal ?? AbortSignal.timeout(15_000),
    })
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error)
    if (error instanceof DOMException && error.name === 'TimeoutError') throw new ApiError('请求超时', undefined, detail)
    if (error instanceof DOMException && error.name === 'AbortError') throw new ApiError('请求已取消', undefined, detail)
    throw new ApiError('后端未连接', undefined, detail)
  }

  if (!response.ok) {
    let detail = ''
    let code = ''
    try {
      const body = (await response.json()) as { detail?: string; message?: string; error?: { code?: string; message?: string } }
      detail = body.error?.message || body.detail || body.message || ''
      code = body.error?.code || ''
    } catch {
      detail = await response.text().catch(() => '')
    }
    throw new ApiError(detail || `请求失败（${response.status}）`, response.status, detail, code)
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

function jsonBody(value: unknown): string {
  return JSON.stringify(value)
}

function toWireKind(kind: ProviderKind): WireProviderKind {
  if (kind === 'deepseek' || kind === 'qwen') return 'openai_compatible'
  if (kind === 'baseline') throw new ApiError('基线模型由后端内置，不能新建或编辑')
  return kind
}

function providerOrigin(kind: ProviderKind, baseUrl?: string): string {
  const fallback = {
    openai_responses: 'https://api.openai.com/v1',
    anthropic_messages: 'https://api.anthropic.com/v1',
    gemini_generate_content: 'https://generativelanguage.googleapis.com/v1beta',
    deepseek: '',
    qwen: '',
    openai_compatible: '',
    ollama: 'http://127.0.0.1:11434',
    baseline: '',
  }[kind]
  const value = baseUrl || fallback
  if (!value) return 'unknown://unspecified'
  try { return new URL(value).origin } catch { return 'unknown://unspecified' }
}

function appendEndpoint(base: string, suffix: string): string {
  const normalized = base.replace(/\/$/, '')
  return normalized.endsWith(suffix) ? normalized : `${normalized}${suffix}`
}

export function providerEndpoint(kind: ProviderKind, model: string, baseUrl?: string): string {
  if (kind === 'openai_responses') return appendEndpoint(baseUrl || 'https://api.openai.com/v1', '/responses')
  if (kind === 'anthropic_messages') return appendEndpoint(baseUrl || 'https://api.anthropic.com/v1', '/messages')
  if (kind === 'gemini_generate_content') {
    const root = (baseUrl || 'https://generativelanguage.googleapis.com/v1beta').replace(/\/$/, '')
    return root.endsWith(':generateContent') ? root : `${root}/models/${model}:generateContent`
  }
  if (kind === 'ollama') return appendEndpoint(baseUrl || 'http://127.0.0.1:11434', '/api/chat')
  if (kind === 'deepseek' || kind === 'qwen' || kind === 'openai_compatible') {
    return baseUrl ? appendEndpoint(baseUrl, '/chat/completions') : 'unknown://unspecified'
  }
  return 'unknown://unspecified'
}

function fromWireKind(provider: Pick<WireProvider, 'kind' | 'name' | 'model'>): ProviderKind {
  if (provider.kind !== 'openai_compatible') return provider.kind
  const label = `${provider.name} ${provider.model}`.toLowerCase()
  if (label.includes('deepseek')) return 'deepseek'
  if (label.includes('qwen') || label.includes('通义')) return 'qwen'
  return 'openai_compatible'
}

function mapProvider(provider: WireProvider): ProviderSummary {
  const configured = provider.data_boundary === 'local' || provider.has_api_key
  return {
    id: provider.id,
    kind: fromWireKind(provider),
    name: provider.name,
    enabled: provider.enabled,
    configured,
    hasApiKey: provider.has_api_key,
    model: provider.model,
    baseUrl: provider.base_url || undefined,
    weight: provider.weight,
    dataBoundary: provider.data_boundary,
    health: provider.last_test_ok === true
      ? 'ready'
      : provider.last_test_ok === false
        ? 'error'
        : configured
          ? 'unknown'
          : 'missing_key',
    lastCheckedAt: provider.last_tested_at || undefined,
    lastTestLatencyMs: provider.last_test_latency_ms ?? undefined,
    message: provider.last_test_error_code || undefined,
  }
}

function baselineProvider(): ProviderSummary {
  return {
    id: BASELINE_ID,
    kind: 'baseline',
    name: '本地透明基线',
    enabled: true,
    configured: true,
    hasApiKey: false,
    model: 'owlpath-baseline-v1',
    weight: 0.5,
    dataBoundary: 'local',
    health: 'ready',
  }
}

function asIso(value?: string, fallback?: string): string {
  const date = new Date(value || fallback || Date.now())
  if (Number.isNaN(date.getTime())) throw new ApiError('病例中包含无效时间')
  return date.toISOString()
}

function scenarioToWire(value: CaseDraft['scenario']): string {
  return {
    lower_respiratory: 'respiratory',
    bloodstream: 'bloodstream',
    urinary: 'urinary',
    cns: 'central_nervous_system',
    abdominal: 'other',
    undifferentiated: 'other',
  }[value]
}

function eventKindToWire(value: TimelineEntry['kind']): string {
  return {
    symptom: 'symptom',
    exam: 'other',
    lab: 'lab',
    imaging: 'imaging_report',
    treatment: 'medication',
    microbiology: 'microbiology',
    other: 'other',
  }[value]
}

interface WireEventCreate {
  kind: string
  occurred_at: string
  visible_at: string
  collected_at?: string
  issued_at?: string
  source: string
  status: string
  data: Record<string, unknown>
  quality: Record<string, unknown>
}

function buildWireEvents(caseDraft: CaseDraft): WireEventCreate[] {
  const decisionTime = asIso(caseDraft.decisionTime)
  const events: WireEventCreate[] = []
  if (caseDraft.deidentifiedNote?.trim() || caseDraft.history.chiefComplaint.trim() || caseDraft.history.presentIllness.trim() || caseDraft.history.priorAntimicrobials.trim()) {
    events.push({
      kind: 'history', occurred_at: decisionTime, visible_at: decisionTime,
      source: 'clinician-ui', status: 'final', quality: { entered_by: 'clinician', clinician_reviewed: true },
      data: {
        chief_complaint: caseDraft.history.chiefComplaint,
        present_illness: caseDraft.history.presentIllness,
        prior_antimicrobials: caseDraft.history.priorAntimicrobials,
        deidentified_note: caseDraft.deidentifiedNote && caseDraft.deidentifiedNote.trim()
          ? caseDraft.deidentifiedNote
          : undefined,
      },
    })
  }
  if (caseDraft.history.exposureHistory.trim() || caseDraft.history.epidemiology.trim()) {
    events.push({
      kind: 'exposure', occurred_at: decisionTime, visible_at: decisionTime,
      source: 'clinician-ui', status: 'final', quality: { entered_by: 'clinician', clinician_reviewed: true },
      data: { exposure_history: caseDraft.history.exposureHistory, epidemiology: caseDraft.history.epidemiology },
    })
  }
  if (Object.values(caseDraft.host).some((value) => value.trim())) {
    events.push({
      kind: 'history', occurred_at: decisionTime, visible_at: decisionTime,
      source: 'clinician-ui', status: 'final', quality: { entered_by: 'clinician', clinician_reviewed: true },
      data: {
        comorbidities: caseDraft.host.comorbidities,
        immune_status: caseDraft.host.immuneStatus,
        devices_and_procedures: caseDraft.host.devicesAndProcedures,
        allergies: caseDraft.host.allergies,
      },
    })
  }
  caseDraft.vitals.filter((item) => item.name.trim() && item.value.trim()).forEach((item) => events.push({
    kind: 'vital', occurred_at: asIso(item.measuredAt, decisionTime), visible_at: asIso(item.measuredAt, decisionTime),
    source: item.source || 'clinician-ui', status: 'final', quality: { time_uncertain: Boolean(item.timeUncertain), needs_clinician_confirmation: Boolean(item.timeUncertain), source_text: item.sourceText || undefined, clinician_reviewed: true },
    data: { observation: item.name, value: item.value, unit: item.unit },
  }))
  caseDraft.labs.filter((item) => item.name.trim() && item.value.trim()).forEach((item) => {
    const sampled = asIso(item.sampledAt, decisionTime)
    const available = asIso(item.availableAt, decisionTime)
    events.push({
      kind: 'lab', occurred_at: sampled, collected_at: sampled, issued_at: available, visible_at: available,
      source: item.source || 'clinician-ui', status: 'final', quality: { time_uncertain: Boolean(item.timeUncertain), needs_clinician_confirmation: Boolean(item.timeUncertain), source_text: item.sourceText || undefined, clinician_reviewed: true },
      data: { test_name: item.name, value: item.value, unit: item.unit, reference_range: item.referenceRange || null, abnormal: item.abnormal || 'unknown' },
    })
  })
  if (caseDraft.imaging.report.trim()) {
    const performed = asIso(caseDraft.imaging.performedAt, decisionTime)
    const available = asIso(caseDraft.imaging.availableAt, decisionTime)
    events.push({
      kind: 'imaging_report', occurred_at: performed, issued_at: available, visible_at: available,
      source: 'clinician-ui', status: 'final', quality: { note: caseDraft.imaging.qualityNote || null, clinician_reviewed: true },
      data: { modality: caseDraft.imaging.modality || 'unspecified', report: caseDraft.imaging.report },
    })
  }
  caseDraft.timeline.filter((item) => item.title.trim()).forEach((item) => events.push({
    kind: eventKindToWire(item.kind),
    occurred_at: asIso(item.occurredAt, decisionTime), visible_at: asIso(item.availableAt, decisionTime),
    source: item.source || 'clinician-ui', status: 'final', quality: { clinician_reviewed: true },
    data: { event_title: item.title, event_detail: item.detail || null },
  }))
  return events
}

function mapSafetyAction(value?: WireAggregatedResult['safety_action']): SafetyDisposition | undefined {
  if (!value) return undefined
  return {
    non_infection: 'non_infection',
    species_set: 'species_supported',
    category_only: 'category_only',
    next_test: 'more_information_needed',
    abstain: 'abstain',
  }[value] as SafetyDisposition
}

function mapModelKind(value?: string | null, name = '', model = ''): ProviderKind | undefined {
  if (!value) return undefined
  if (value === 'transparent_rule') return 'baseline'
  if (value === 'openai_compatible') return fromWireKind({ kind: 'openai_compatible', name, model })
  if (['openai_responses', 'anthropic_messages', 'gemini_generate_content', 'ollama'].includes(value)) return value as ProviderKind
  return undefined
}

function mapStageStatus(value: string): StageStatus {
  if (value === 'completed') return 'completed'
  if (value === 'failed') return 'failed'
  if (value === 'running') return 'running'
  if (value === 'skipped' || value === 'cancelled' || value === 'interrupted' || value === 'not_started') return 'skipped'
  return 'pending'
}

function isTerminalRunStatus(status: RunStatus): boolean {
  return status === 'completed' || status === 'failed' || status === 'cancelled'
}

/**
 * Child records can be left as `running` when the process is interrupted.
 * Once the parent run is terminal the UI must not present those stale rows as
 * active work. A failed parent turns work that had started into failed and
 * work that never started into skipped; a cancelled/completed parent treats
 * any non-terminal residue as skipped.
 */
function mapChildStageStatus(value: string, runStatus: RunStatus): StageStatus {
  const mapped = mapStageStatus(value)
  if (!isTerminalRunStatus(runStatus) || mapped === 'completed' || mapped === 'failed' || mapped === 'skipped') return mapped
  if (runStatus === 'failed' && mapped === 'running') return 'failed'
  return 'skipped'
}

type WireTechnicalError = { code?: string; message?: string; retryable?: boolean } | null | undefined

function normalizedSafeErrorCode(value?: string): string | undefined {
  const candidate = value?.trim()
  if (!candidate || !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(candidate)) return undefined
  if (/(?:^|[_:.-])(?:sk|token|bearer)[_:.-]?[A-Za-z0-9_-]{8,}/i.test(candidate)) return undefined
  return candidate
}

/**
 * Preserve machine-actionable error metadata while keeping raw upstream text
 * out of the frontend contract. Raw provider messages can contain API keys,
 * request fragments or vendor internals and remain available only in backend
 * logs/audit storage.
 */
function mapSafeTechnicalError(error: WireTechnicalError): NonNullable<RunDetail['error']> | undefined {
  if (!error) return undefined
  const classifier = `${error.code || ''} ${error.message || ''}`.toLowerCase()
  let code = normalizedSafeErrorCode(error.code)

  if (!code) {
    if (/server.*restart|service.*restart|interrupted by server restart/.test(classifier)) code = 'server_restarted'
    else if (/cancel|aborted|interrupted/.test(classifier)) code = 'run_interrupted'
    else if (/timeout|timed.out|deadline/.test(classifier)) code = 'run_timeout'
    else code = 'unclassified_technical_error'
  }

  const normalized = code.toLowerCase()
  let message = '本次运行未能完成；原始技术错误仅保留在服务端日志中。'
  let defaultRetryable = true
  if (/server_restarted|service.*restart/.test(normalized)) {
    message = '分析服务在运行中重启，本次运行已中断。'
  } else if (/cancel|aborted|interrupted/.test(normalized)) {
    message = '本次运行已中断，未完成的步骤不会继续执行。'
  } else if (/timeout|timed_out|deadline/.test(normalized)) {
    message = '本次运行超过了允许的等待时间。'
  } else if (/401|403|auth|forbidden|permission|missing_api_key|secret_decryption/.test(normalized)) {
    message = '模型密钥或访问权限不可用。'
    defaultRetryable = false
  } else if (/429|rate|quota|budget/.test(normalized)) {
    message = '模型账户额度或请求频率受限。'
  } else if (/schema|invalid.*json|structured_output|output_truncated/.test(normalized)) {
    message = '模型已返回内容，但不符合本次结构化结果要求。'
  } else if (/network|dns|connection|transport|http_5|server_error|service_unavailable/.test(normalized)) {
    message = '模型或分析服务暂时不可用。'
  } else if (/integrity|manifest.*mismatch|config.*hash|unsafe|ssrf|egress_policy/.test(normalized)) {
    message = '本次运行的完整性或网络保护检查未通过。'
    defaultRetryable = false
  }

  return { code, message, retryable: error.retryable ?? defaultRetryable }
}

function localizedClinicalCode(value?: string | null): string | undefined {
  if (!value) return undefined
  const normalized = value.trim().toLowerCase()
  const labels: Record<string, string> = {
    bacteria: '细菌', bacterial: '细菌',
    virus: '病毒', viral: '病毒',
    fungus: '真菌', fungi: '真菌', fungal: '真菌',
    parasite: '寄生虫', parasitic: '寄生虫',
    respiratory: '呼吸道感染综合征',
    bloodstream: '血流感染综合征',
    urinary: '尿路感染综合征',
    central_nervous_system: '中枢神经系统感染综合征',
    other: '其他感染综合征',
    unknown: '未知',
  }
  return labels[normalized] || value
}

function localizedCalibration(value: WireCandidate['calibration_status']): string {
  return {
    calibrated: '已校准',
    uncalibrated_model_score: '未校准的模型分数',
    heuristic_unvalidated: '未验证的启发式分数',
  }[value]
}

function localizedDevelopmentAgentRole(value?: string): LocalizedText | undefined {
  if (!value) return undefined
  const roleKey = value.startsWith('specialist:') ? value.slice('specialist:'.length) : value
  const labels: Record<string, string> = {
    timeline_course: '病程时间线 Agent',
    host_susceptibility: '宿主易感性 Agent',
    syndrome_localization: '综合征与解剖定位 Agent',
    exposure_one_health: '暴露与 One Health Agent',
    lab_pathophysiology: '实验室病理生理 Agent',
    organ_severity: '器官损伤与严重度 Agent',
    imaging_dissemination: '影像与播散路径 Agent',
    microbiology_treatment: '微生物证据与治疗干扰 Agent',
    neuroinfection: '神经感染专病 Agent（动态）',
    immunocompromised_opportunistic: '免疫抑制与机会感染 Agent（动态）',
    travel_zoonotic: '旅行、人畜共患与环境感染 Agent（动态）',
    healthcare_device_amr: '医疗相关、器械与耐药 Agent（动态）',
    complexity_router: '复杂度与专病路由器',
    evidence_board: '证据委员会与候选板',
    retrieval_planner: '医学证据检索规划器',
    literature_retrieval: '文献与类似病例检索',
    public_health_retrieval: '公共卫生、指南与疫情信号检索',
    evidence_verifier: '外部证据核验与重排',
    timeline_host: '临床时间线与宿主 Agent',
    syndrome_site: '感染部位与综合征 Agent',
    exposure_epidemiology: '暴露与流行病学 Agent',
    laboratory_organ_injury: '实验室与器官损伤 Agent',
    imaging_microbiology_treatment: '影像、微生物与治疗背景 Agent',
    evidence_retrieval: '医学证据检索 Agent',
    pathogen_synthesis: '病原体总诊 Agent',
    independent_critic: '独立审稿 Agent',
  }
  return { zhCn: labels[roleKey] || value, en: roleKey.replaceAll('_', ' '), status: labels[roleKey] ? 'complete' : 'partial' }
}

function localizedCandidateName(candidate?: WireCandidate): string | undefined {
  if (!candidate) return undefined
  return candidate.rank_level === 'category' ? localizedClinicalCode(candidate.name) || candidate.name : candidate.name
}

function mapLocalizedText(
  value?: WireLocalizedText | LocalizedText | string | null,
  fallbackZh?: string,
  fallbackEn?: string,
): LocalizedText | undefined {
  if (!value && !fallbackZh && !fallbackEn) return undefined
  if (typeof value === 'string') {
    const hasChinese = /[\u3400-\u9fff]/.test(value)
    return {
      zhCn: hasChinese ? value : fallbackZh,
      en: hasChinese ? fallbackEn : value,
      status: fallbackZh && fallbackEn ? 'complete' : 'partial',
    }
  }
  const wire = value as WireLocalizedText | LocalizedText | undefined
  const zhCn = (wire as WireLocalizedText | undefined)?.zh_cn || (wire as LocalizedText | undefined)?.zhCn || fallbackZh || undefined
  const en = wire?.en || fallbackEn || undefined
  return { zhCn, en, status: wire?.status || (zhCn && en ? 'complete' : 'partial') }
}

const NODE_KIND_LABELS: Record<string, [string, string]> = {
  orchestrator: ['运行编排器', 'Run orchestrator'],
  input_snapshot: ['输入快照', 'Input snapshot'],
  provider_invocation: ['模型 Provider 推理', 'Provider inference'],
  model: ['模型推理', 'Model inference'],
  schema_validation: ['Schema 校验', 'Schema validation'],
  evidence_sanitizer: ['证据核验', 'Evidence validation'],
  aggregation: ['多模型融合', 'Multi-model aggregation'],
  safety_adjudication: ['安全裁决', 'Safety adjudication'],
  demo_projection: ['开发投影', 'Development projection'],
  result_persistence: ['结果固化', 'Result persistence'],
  bilingual_rendering: ['双语呈现', 'Bilingual rendering'],
  source_compiler: ['原文片段编译', 'Source fragment compiler'],
  specialist_agent: ['专科 Agent', 'Specialist agent'],
  medical_retrieval: ['医学证据检索', 'Medical evidence retrieval'],
  pathogen_synthesis: ['病原体总诊', 'Pathogen synthesis'],
  contract_validation: ['输出合同检查', 'Output contract validation'],
  critic_agent: ['独立审稿 Agent', 'Independent critic agent'],
  revision: ['总诊修订', 'Synthesis revision'],
  result_compiler: ['开发结果编译', 'Development result compiler'],
}

function nodeKindName(kind: string, value?: WireLocalizedText | LocalizedText | string | null): LocalizedText {
  const label = NODE_KIND_LABELS[kind] || [kind, kind]
  return mapLocalizedText(value, label[0], label[1]) || { zhCn: label[0], en: label[1], status: 'complete' }
}

function normalizedPlane(value?: string | null, kind = ''): AgentPlane {
  if (value === 'governance' || value === 'online' || value === 'offline') return value
  if (/governance|audit|scope|registry|approval/i.test(kind)) return 'governance'
  if (/offline|evaluation|monitor|drift|calibrat/i.test(kind)) return 'offline'
  return 'online'
}

function normalizedNodeStatus(value?: string | null): AgentNodeStatus | undefined {
  if (!value) return undefined
  if (value === 'bypassed') return 'bypassed'
  if (value === 'not_started') return 'not_started'
  if (['pending', 'running', 'completed', 'failed', 'skipped'].includes(value)) return value as StageStatus
  return 'pending'
}

function mapWireCandidate(candidate: WireCandidate, index: number): CandidatePathogen {
  const primaryName = localizedCandidateName(candidate) || candidate.name
  const fallbackEn = candidate.rank_level === 'category' ? candidate.name : candidate.name
  return {
    canonicalId: candidate.canonical_id,
    rank: index + 1,
    name: primaryName,
    displayNameI18n: mapLocalizedText(candidate.display_name_i18n, primaryName, fallbackEn),
    taxonomyLevel: candidate.rank_level === 'unknown' ? 'category' : candidate.rank_level,
    category: candidate.category || undefined,
    genus: candidate.genus || undefined,
    species: candidate.species || undefined,
    calibrationStatus: candidate.calibration_status,
    probability: candidate.probability,
    evidenceFor: candidate.evidence_for,
    evidenceAgainst: candidate.evidence_against,
  }
}

function mapWireNextTest(item: WireNextTest): NextTestRecommendation {
  return {
    code: item.test_code,
    name: item.test_name,
    nameI18n: mapLocalizedText(item.test_name_i18n, item.test_name, item.test_name),
    rationale: item.rationale,
    rationaleI18n: mapLocalizedText(item.rationale_i18n, item.rationale, item.rationale),
    expectedInformationGain: item.expected_information_gain,
    turnaround: item.estimated_turnaround || undefined,
    specimen: item.specimen || undefined,
    availability: item.specimen ? `标本：${item.specimen}` : undefined,
    invasiveness: item.burden === 'unknown' ? undefined : item.burden,
    cautions: item.requires_clinician_order ? ['需由临床医生下达检查'] : undefined,
    requiresClinicianOrder: item.requires_clinician_order,
  }
}

function mapTraceArtifact(item: WireTraceArtifact): AgentArtifactSummary {
  const key = item.artifact_type || item.id
  const content = item.content as Record<string, unknown> | undefined
  const embeddedSummary = content && typeof content === 'object'
    ? (content.summary_i18n as WireLocalizedText | undefined)
    : undefined
  return {
    key,
    label: mapLocalizedText(item.label_i18n, key, key) || { zhCn: key, en: key },
    summary: mapLocalizedText(item.summary_i18n || embeddedSummary),
    value: item.schema_version || undefined,
    direction: item.direction,
    contentSha256: item.content_sha256 || undefined,
    integrityOk: item.integrity_ok,
    visibility: item.visibility,
    json: item.content,
  }
}

function mapTraceNode(item: WireTraceNode): AgentNode {
  const kind = item.node_kind || item.kind || 'unknown'
  const metadata = item.metadata || undefined
  const artifacts = (item.artifacts || []).map(mapTraceArtifact)
  const safetyArtifact = (item.artifacts || []).find((artifact) => /safety|adjudicat/i.test(artifact.artifact_type || ''))
  const inputArtifact = (item.artifacts || []).find((artifact) => artifact.direction === 'input')
  const outputArtifact = (item.artifacts || []).find((artifact) => artifact.direction === 'output')
  const dependsOn = [...new Set([...(item.depends_on || []), ...(item.parent_node_id ? [item.parent_node_id] : [])])]
  const rawTraceErrorMessage = typeof item.error?.message === 'string'
    ? item.error.message
    : item.error?.message?.zh_cn || item.error?.message?.en || undefined
  const safeTraceError = mapSafeTechnicalError(item.error ? {
    code: item.error.code,
    message: rawTraceErrorMessage,
    retryable: item.error.retryable,
  } : undefined)
  return {
    id: item.id,
    nodeKey: item.node_key || item.id,
    name: nodeKindName(kind, item.display_name_i18n || item.name),
    kind,
    role: mapLocalizedText(item.role || (metadata?.role as string | undefined)),
    plane: normalizedPlane(item.plane || (metadata?.plane as string | undefined), kind),
    dependsOn,
    sequence: item.sequence,
    attempt: item.attempt,
    parentId: item.parent_node_id || undefined,
    status: normalizedNodeStatus(item.status),
    outcome: item.outcome || (metadata?.outcome as string | undefined),
    maturity: metadata?.maturity as string | undefined,
    version: item.version || (metadata?.version as string | undefined),
    provider: item.provider_id || item.provider || undefined,
    model: item.provider_model || item.model || undefined,
    startedAt: item.started_at || undefined,
    completedAt: item.completed_at || undefined,
    latencyMs: item.latency_ms ?? undefined,
    inputSha256: item.input_sha256 || inputArtifact?.content_sha256 || (metadata?.input_sha256 as string | undefined),
    outputSha256: item.output_sha256 || outputArtifact?.content_sha256 || (metadata?.output_sha256 as string | undefined),
    error: safeTraceError ? {
      code: safeTraceError.code,
      message: mapLocalizedText(undefined, safeTraceError.message),
      retryable: safeTraceError.retryable,
    } : undefined,
    artifacts,
    safetyJson: safetyArtifact?.content,
    metadata,
  }
}

function mapArchitecture(wire: WireArchitecture): ArchitectureResponse {
  const planes: ArchitectureResponse['planes'] = [
    { id: 'governance', name: { zhCn: '治理平面', en: 'Governance plane', status: 'complete' }, description: { zhCn: '适用范围、版本、安全和审计约束。', en: 'Scope, version, safety and audit controls.', status: 'complete' } },
    { id: 'online', name: { zhCn: '在线运行平面', en: 'Online runtime plane', status: 'complete' }, description: { zhCn: '本次请求的编排、模型调用、融合与呈现。', en: 'Per-request orchestration, model calls, aggregation and rendering.', status: 'complete' } },
    { id: 'offline', name: { zhCn: '离线验证平面', en: 'Offline validation plane', status: 'complete' }, description: { zhCn: '脱敏评价、监测、校准和版本候选。', en: 'De-identified evaluation, monitoring, calibration and version candidates.', status: 'complete' } },
  ]
  const mapView = (view: WireArchitectureView, maturity: string): AgentGraph => {
    const edges: AgentEdge[] = (view.edges || []).map((edge) => ({
      source: edge.source,
      target: edge.target,
      kind: edge.kind,
      label: mapLocalizedText(edge.condition || undefined, edge.condition || edge.kind, edge.condition || edge.kind),
    }))
    const nodes = [...(view.nodes || [])]
      .sort((a, b) => a.order - b.order)
      .map((item): AgentNode => ({
        id: item.id,
        nodeKey: item.id,
        name: mapLocalizedText(item.name, item.id, item.id) || { zhCn: item.id, en: item.id },
        description: mapLocalizedText(item.description),
        kind: item.kind,
        plane: normalizedPlane(item.plane, item.kind),
        dependsOn: edges.filter((edge) => edge.target === item.id).map((edge) => edge.source),
        sequence: item.order,
        maturity: item.maturity,
      }))
    return {
      version: wire.architecture_version,
      name: mapLocalizedText(view.title),
      description: mapLocalizedText(view.description),
      nodes,
      edges,
      maturity,
    }
  }
  return {
    version: wire.architecture_version,
    planes,
    edgeTypes: (wire.edge_legend || []).map((edge) => ({
      id: edge.kind,
      name: mapLocalizedText({ zh_cn: edge.zh_cn, en: edge.en }, edge.zh_cn || edge.kind, edge.en || edge.kind) || { zhCn: edge.kind, en: edge.kind },
    })),
    current: mapView(wire.views.current, 'current'),
    target: mapView(wire.views.target, 'target'),
  }
}

function terminalNodeStatus(status: AgentNodeStatus | undefined, runStatus?: RunStatus): AgentNodeStatus | undefined {
  if (!runStatus || !isTerminalRunStatus(runStatus) || !status) return status
  if (status === 'completed' || status === 'failed' || status === 'skipped' || status === 'bypassed') return status
  if (runStatus === 'failed' && status === 'running') return 'failed'
  return 'skipped'
}

/** Keep a detail response from reviving stale work after its parent run ended. */
export function clampTraceNodeForRunStatus(node: AgentNode, runStatus?: RunStatus): AgentNode {
  return { ...node, status: terminalNodeStatus(node.status, runStatus) }
}

export function mapRunTrace(wire: WireRunTrace, runStatus?: RunStatus): RunTrace {
  const authoritativeRunStatus = runStatus || wire.run_status || wire.status
  const mappedNodes = (wire.nodes || []).map(mapTraceNode).sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0))
  const suppliedEdges = (wire.edges || []).map((edge) => ({ source: edge.from, target: edge.to, kind: edge.relation || 'flow' }))
  const nodes = mappedNodes.map((rawNode) => {
    const node = clampTraceNodeForRunStatus(rawNode, authoritativeRunStatus)
    return {
    ...node,
    dependsOn: [...new Set([
      ...node.dependsOn,
      ...suppliedEdges.filter((edge) => edge.target === node.id).map((edge) => edge.source),
    ])],
  }})
  const inferredEdges = nodes.flatMap((node) => node.dependsOn.map((source) => ({ source, target: node.id, kind: 'depends_on' })))
  return {
    runId: wire.run_id,
    traceVersion: wire.trace_version,
    runMode: wire.run_mode,
    version: wire.execution_graph_version,
    name: { zhCn: '本次运行轨迹', en: 'Run execution trace', status: 'complete' },
    description: wire.execution_manifest_sha256
      ? { zhCn: `执行清单 ${wire.execution_manifest_sha256.slice(0, 12)}`, en: `Execution manifest ${wire.execution_manifest_sha256.slice(0, 12)}`, status: 'complete' }
      : undefined,
    nodes,
    edges: suppliedEdges.length ? suppliedEdges : inferredEdges,
    maturity: 'executed',
  }
}

function toLocalDateTimeInput(value?: string | null): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16)
}

function mapClinicalTextOrganization(wire: WireClinicalTextOrganization): ClinicalTextOrganization {
  const draft = wire.case_draft
  const history = draft.history
  const host = draft.host
  const imaging = draft.imaging
  const imagingTimeUncertain = (imaging.performedTimeCertainty || imaging.performed_time_certainty) === 'assumed_decision_time'
    || (imaging.availableTimeCertainty || imaging.available_time_certainty) === 'uncertain_assumed_decision_time'
  return {
    deidentifiedNote: draft.deidentified_note,
    demographics: {
      age: draft.demographics.age ?? undefined,
      sex: draft.demographics.sex || 'unknown',
      pregnant: draft.demographics.pregnant ?? undefined,
      immunocompromised: draft.demographics.immunocompromised ?? undefined,
      department: draft.demographics.department || undefined,
      encounterType: draft.demographics.encounterType || draft.demographics.encounter_type || undefined,
    },
    scenario: draft.scenario,
    acquisitionContext: draft.acquisitionContext || draft.acquisition_context || 'unknown',
    history: {
      chiefComplaint: history.chiefComplaint || history.chief_complaint || '',
      presentIllness: history.presentIllness || history.present_illness || '',
      exposureHistory: history.exposureHistory || history.exposure_history || '',
      epidemiology: history.epidemiology || '',
      priorAntimicrobials: history.priorAntimicrobials || history.prior_antimicrobials || '',
    },
    host: {
      comorbidities: host.comorbidities || '',
      immuneStatus: host.immuneStatus || host.immune_status || '',
      devicesAndProcedures: host.devicesAndProcedures || host.devices_and_procedures || '',
      allergies: host.allergies || '',
    },
    vitals: draft.vitals.map((item) => {
      const timeCertainty = item.timeCertainty || item.time_certainty
      return {
        id: item.id,
        measuredAt: toLocalDateTimeInput(item.measuredAt || item.measured_at),
        name: item.name,
        value: item.value,
        unit: item.unit,
        source: item.source,
        timeUncertain: timeCertainty === 'assumed_decision_time',
      }
    }),
    labs: draft.labs.map((item) => {
      const sampledCertainty = item.sampledTimeCertainty || item.sampled_time_certainty
      const availableCertainty = item.availableTimeCertainty || item.available_time_certainty
      return {
        id: item.id,
        sampledAt: toLocalDateTimeInput(item.sampledAt || item.sampled_at),
        availableAt: toLocalDateTimeInput(item.availableAt || item.available_at),
        name: item.name,
        value: item.value,
        unit: item.unit,
        referenceRange: item.referenceRange || item.reference_range || undefined,
        abnormal: item.abnormal || 'unknown',
        source: item.source,
        timeUncertain: sampledCertainty === 'assumed_decision_time' || availableCertainty === 'uncertain_assumed_decision_time',
      }
    }),
    imaging: {
      modality: imaging.modality || undefined,
      performedAt: toLocalDateTimeInput(imaging.performedAt || imaging.performed_at) || undefined,
      availableAt: toLocalDateTimeInput(imaging.availableAt || imaging.available_at) || undefined,
      report: imaging.report || '',
      qualityNote: [imaging.qualityNote || imaging.quality_note, imagingTimeUncertain ? '时间不确定：解析器暂以当前决策时点作为可见时间上界，需医生核对。' : ''].filter(Boolean).join('；') || undefined,
    },
    recognizedSections: wire.recognized_sections,
    unrecognized: wire.unrecognized_segments,
    warnings: wire.warnings,
    parserVersion: wire.parser_version,
    sourceTextSha256: wire.source_text_sha256,
    modelFactPreview: (wire.model_fact_preview || []).map((item) => ({
      eventIndex: item.event_index,
      kind: item.kind,
      occurredAt: item.occurred_at,
      visibleAt: item.visible_at,
      collectedAt: item.collected_at || undefined,
      issuedAt: item.issued_at || undefined,
      source: item.source,
      status: item.status,
      data: item.data,
      quality: item.quality,
    })),
  }
}

function mapComparison(output: WireModelOutput, contribution?: WireContribution): ModelComparisonRow {
  const safeOutputError = mapSafeTechnicalError(output.error)
  const rawNormalized = unknownRecord(output.normalized)
  const normalizedSchema = firstString(rawNormalized, 'schema_version', 'schemaVersion')
  const isDevelopmentAgentOutput = Boolean(normalizedSchema?.startsWith('owlpath.') && normalizedSchema !== 'owlpath.model-prediction.v1')
    && (!Array.isArray(rawNormalized.candidates) || !rawNormalized.syndrome_probabilities)

  if (isDevelopmentAgentOutput) {
    const rawCandidates = Array.isArray(rawNormalized.candidate_pool)
      ? rawNormalized.candidate_pool
      : Array.isArray(rawNormalized.concrete_pathogens) ? rawNormalized.concrete_pathogens : []
    const developmentCandidates: CandidatePathogen[] = rawCandidates.map((value, index) => {
      const item = unknownRecord(value)
      const canonicalName = firstString(item, 'canonical_latin_name', 'canonical_name', 'canonicalName', 'name') || `Unnamed pathogen ${index + 1}`
      const displayName = localizedFromRecord(item, 'name_i18n', 'nameI18n', 'display_name_i18n', 'displayNameI18n')
      const taxonomyLevel = firstString(item, 'taxonomic_rank', 'taxonomy_level', 'taxonomyLevel')
      return {
        rank: firstNumber(item, 'rank') || index + 1,
        name: canonicalName,
        displayNameI18n: displayName || mapLocalizedText(undefined, canonicalName, canonicalName),
        taxonomyLevel: taxonomyLevel === 'genus' || taxonomyLevel === 'family' || taxonomyLevel === 'category' ? taxonomyLevel : 'species',
        category: firstString(item, 'category'),
        calibrationStatus: 'uncalibrated_model_score',
        probability: firstNumber(item, 'model_score', 'modelScore', 'score'),
      }
    })
    const summaryI18n = localizedFromRecord(rawNormalized, 'summary_i18n', 'summaryI18n', 'review_summary_i18n', 'reviewSummaryI18n')
    const warnings = [
      ...readableList(rawNormalized.warnings),
      ...readableList(rawNormalized.issues),
    ]
    return {
      outputId: output.id,
      providerId: output.provider_id,
      providerName: output.provider_name,
      providerKind: mapModelKind(contribution?.provider_kind || output.provider_kind, output.provider_name, contribution?.model || output.model || ''),
      model: contribution?.model || output.model || undefined,
      status: output.status === 'completed' ? 'completed' : output.status === 'failed' ? 'failed' : 'skipped',
      latencyMs: output.latency_ms ?? undefined,
      topCandidate: developmentCandidates[0]?.displayNameI18n?.zhCn || developmentCandidates[0]?.name,
      topProbability: developmentCandidates[0]?.probability,
      predictionSet: developmentCandidates.map((candidate) => candidate.displayNameI18n?.zhCn || candidate.name),
      unknownProbability: firstNumber(rawNormalized, 'unknown_score', 'unknownScore'),
      category: firstString(rawNormalized, 'role') || normalizedSchema,
      notes: summaryI18n?.zhCn || summaryI18n?.en || warnings[0] || safeOutputError?.message,
      normalized: {
        summary: summaryI18n?.zhCn || summaryI18n?.en || normalizedSchema || '开发 Agent 结构化输出',
        summaryI18n,
        syndromeProbabilities: [],
        candidates: developmentCandidates,
        coinfectionPairs: [],
        unknownProbability: firstNumber(rawNormalized, 'unknown_score', 'unknownScore'),
        nextTests: (Array.isArray(rawNormalized.next_tests) ? rawNormalized.next_tests : []).map(mapDevelopmentNextTest),
        dataQualityWarnings: warnings,
        distributionShiftWarning: false,
        abstain: false,
      },
      createdAt: output.created_at,
      completedAt: output.completed_at || undefined,
      error: safeOutputError ? { code: safeOutputError.code, message: safeOutputError.message } : undefined,
    }
  }

  const prediction = output.normalized as WirePrediction | null | undefined
  const top = prediction?.candidates?.[0]
  const topSyndrome = prediction?.syndrome_probabilities ? Object.entries(prediction.syndrome_probabilities).sort((a, b) => b[1] - a[1])[0]?.[0] : undefined
  const candidates = prediction?.candidates?.map(mapWireCandidate) || []
  return {
    outputId: output.id,
    providerId: output.provider_id,
    providerName: output.provider_name,
    providerKind: mapModelKind(contribution?.provider_kind || output.provider_kind, output.provider_name, contribution?.model || output.model || ''),
    model: contribution?.model || output.model || undefined,
    status: output.status === 'completed' ? 'completed' : output.status === 'failed' ? 'failed' : 'skipped',
    latencyMs: output.latency_ms ?? undefined,
    topCandidate: localizedCandidateName(top),
    topProbability: top?.probability,
    predictionSet: prediction?.candidates?.map((item) => localizedCandidateName(item) || item.name),
    unknownProbability: prediction?.unknown_probability,
    category: localizedClinicalCode(top?.category || topSyndrome),
    notes: prediction?.summary || safeOutputError?.message,
    normalized: prediction ? {
      summary: prediction.summary,
      summaryI18n: mapLocalizedText(prediction.summary_i18n, prediction.summary, prediction.summary),
      infectionProbability: prediction.infection_probability,
      syndromeProbabilities: Object.entries(prediction.syndrome_probabilities || {})
        .sort((a, b) => b[1] - a[1])
        .map(([name, score]) => ({ name: localizedClinicalCode(name) || name, score })),
      candidates,
      coinfectionProbability: prediction.coinfection_probability,
      coinfectionPairs: (prediction.coinfection_pairs || []).map((pair) => ({
        pathogenIds: pair.pathogen_ids,
        probability: pair.probability,
        rationale: pair.rationale || undefined,
      })),
      unknownProbability: prediction.unknown_probability,
      nextTests: (prediction.next_tests || []).map(mapWireNextTest),
      dataQualityWarnings: prediction.data_quality_warnings || [],
      distributionShiftWarning: prediction.distribution_shift_warning || false,
      abstain: prediction.abstain || false,
      abstainReason: prediction.abstain_reason || undefined,
    } : undefined,
    createdAt: output.created_at,
    completedAt: output.completed_at || undefined,
    error: safeOutputError ? { code: safeOutputError.code, message: safeOutputError.message } : undefined,
  }
}

function unknownRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function firstString(record: Record<string, unknown>, ...keys: string[]): string | undefined {
  for (const key of keys) {
    const value = record[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
    if (typeof value === 'number') return String(value)
  }
  return undefined
}

function firstNumber(record: Record<string, unknown>, ...keys: string[]): number | undefined {
  for (const key of keys) {
    const value = record[key]
    if (typeof value === 'number' && Number.isFinite(value)) return value
  }
  return undefined
}

function readableText(value: unknown): string | undefined {
  if (typeof value === 'string' && value.trim()) return value.trim()
  const record = unknownRecord(value)
  const direct = firstString(record, 'statement', 'text', 'summary', 'name', 'canonical_name', 'canonicalName', 'canonical_latin_name', 'message', 'issue')
  if (direct) return direct
  const localized = unknownRecord(record.statement_i18n || record.statementI18n || record.rationale_i18n || record.rationaleI18n)
  const zh = firstString(localized, 'zh_cn', 'zhCn')
  const en = firstString(localized, 'en')
  return [zh, en && en !== zh ? en : undefined].filter(Boolean).join(' / ') || undefined
}

function readableList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.map(readableText).filter((item): item is string => Boolean(item))
}

const DEVELOPMENT_ISSUE_LABELS: Record<string, string> = {
  taxonomy_unresolved: '病原体术语未解析到 NCBI Taxonomy ID',
  unknown_source_fragment: '证据引用了不存在的病例片段',
  missing_case_evidence: '缺少可追溯的病例原文证据',
  missing_supporting_evidence: '缺少支持证据',
  missing_agent_provenance: '缺少真实提出该候选的专科 Agent',
  score_order: '候选分数与排名顺序不一致',
  top5_count: '具体病原体候选不是恰好 5 个',
  duplicate_pathogen: 'Top-5 中出现重复病原体',
  non_concrete_taxonomic_rank: 'Top-5 中出现了病原大类而非具体病原体',
  generic_pathogen_name: 'Top-5 中出现了细菌、病毒等通用名称',
}

function readableDevelopmentIssue(value: unknown): string | undefined {
  if (typeof value === 'string') return value.trim() || undefined
  const record = unknownRecord(value)
  const code = firstString(record, 'code')
  const rank = firstNumber(record, 'candidate_rank', 'candidateRank', 'rank')
  const rawRanks = record.candidate_ranks ?? record.candidateRanks
  const ranks = Array.isArray(rawRanks)
    ? rawRanks.filter((item): item is number => typeof item === 'number')
    : []
  const localized = localizedFromRecord(record, 'message_i18n', 'messageI18n')
  const detail = localized?.zhCn || localized?.en || firstString(record, 'message')
  const label = code ? DEVELOPMENT_ISSUE_LABELS[code] : undefined
  const rankLabel = rank !== undefined ? `第 ${rank} 名` : ranks.length ? `第 ${ranks.join('、')} 名` : undefined
  const statement = label || detail || code
  if (!statement) return undefined
  return [rankLabel, statement, label && detail && detail !== label ? detail : undefined].filter(Boolean).join('：')
}

function readableDevelopmentIssueList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.map(readableDevelopmentIssue).filter((item): item is string => Boolean(item))
}

function localizedFromRecord(record: Record<string, unknown>, ...keys: string[]): LocalizedText | undefined {
  for (const key of keys) {
    const value = record[key]
    if (value !== undefined && value !== null) return mapLocalizedText(value as WireLocalizedText | string)
  }
  return undefined
}

function isWireResultV3(value: WireAggregatedResult | WireResultV3): value is WireResultV3 {
  const candidate = value as WireResultV3
  return 'development_result' in value || 'developmentResult' in value || candidate.schema_version === 'owlpath.result.v3' || candidate.schemaVersion === 'owlpath.result.v3'
}

function mapDevelopmentNextTest(value: unknown): NextTestRecommendation {
  const item = unknownRecord(value)
  const nameI18n = localizedFromRecord(item, 'test_name_i18n', 'testNameI18n', 'name_i18n', 'nameI18n')
  const rationaleI18n = localizedFromRecord(item, 'rationale_i18n', 'rationaleI18n')
  const name = firstString(item, 'test_name', 'testName', 'name') || nameI18n?.zhCn || nameI18n?.en || '未命名检查'
  const rationale = firstString(item, 'rationale', 'reason') || rationaleI18n?.zhCn || rationaleI18n?.en || '后端未返回检查理由。'
  const burden = firstString(item, 'burden', 'invasiveness')
  return {
    code: firstString(item, 'test_code', 'testCode', 'code'),
    name,
    nameI18n: nameI18n || mapLocalizedText(undefined, name, name),
    rationale,
    rationaleI18n: rationaleI18n || mapLocalizedText(undefined, rationale, rationale),
    expectedInformationGain: firstNumber(item, 'expected_information_gain', 'expectedInformationGain', 'model_score', 'modelScore', 'score'),
    turnaround: firstString(item, 'estimated_turnaround', 'estimatedTurnaround', 'turnaround'),
    specimen: firstString(item, 'specimen'),
    availability: firstString(item, 'availability'),
    invasiveness: burden && ['none', 'low', 'moderate', 'high'].includes(burden) ? burden as NextTestRecommendation['invasiveness'] : undefined,
    cautions: readableList(item.cautions),
    requiresClinicianOrder: typeof item.requires_clinician_order === 'boolean'
      ? item.requires_clinician_order
      : typeof item.requiresClinicianOrder === 'boolean' ? item.requiresClinicianOrder : undefined,
  }
}

function mapDevelopmentResult(result: WireResultV3, modelOutputs: WireModelOutput[], fallbackGeneratedAt?: string): RunResult {
  const wire = result.development_result || result.developmentResult || result
  const pathogenWire = wire.concrete_pathogens || wire.concretePathogens || []
  const concretePathogens = pathogenWire.map((item, index) => {
    const record = item as WireDevelopmentPathogen
    const canonicalName = record.canonical_latin_name || record.canonical_name || record.canonicalName || record.name || `Unnamed pathogen ${index + 1}`
    return {
      rank: record.rank || index + 1,
      canonicalName,
      displayNameI18n: mapLocalizedText(record.name_i18n || record.display_name_i18n || record.displayNameI18n, canonicalName, canonicalName),
      taxonomyId: record.ncbi_taxonomy_id !== undefined && record.ncbi_taxonomy_id !== null
        ? String(record.ncbi_taxonomy_id)
        : record.taxonomy_id !== undefined && record.taxonomy_id !== null
        ? String(record.taxonomy_id)
        : record.taxonomyId !== undefined && record.taxonomyId !== null ? String(record.taxonomyId) : undefined,
      taxonomyStatus: record.taxonomy_resolution_status || record.taxonomy_status || record.taxonomyStatus || undefined,
      modelScore: record.model_score ?? record.modelScore ?? undefined,
      evidenceFor: readableList(record.supporting_evidence || record.evidence_for || record.evidenceFor),
      evidenceAgainst: readableList(record.opposing_evidence || record.evidence_against || record.evidenceAgainst),
      sourceFragmentIds: record.source_fragment_ids || record.sourceFragmentIds || [...new Set((record.supporting_evidence || []).flatMap((value) => {
        const evidence = unknownRecord(value)
        return Array.isArray(evidence.source_fragment_ids) ? evidence.source_fragment_ids.filter((item): item is string => typeof item === 'string') : []
      }))],
      citationIds: record.citation_ids || record.citationIds || [...new Set((record.supporting_evidence || []).flatMap((value) => {
        const evidence = unknownRecord(value)
        return Array.isArray(evidence.evidence_source_ids) ? evidence.evidence_source_ids.filter((item): item is string => typeof item === 'string') : []
      }))],
      specificityRationale: mapLocalizedText(record.why_ranked_i18n || record.specificity_rationale || record.specificityRationale),
      uncertaintyReason: mapLocalizedText(record.main_uncertainty_i18n || record.uncertainty_reason || record.uncertaintyReason),
      agentSources: record.proposed_by_agent_roles || record.agent_sources || record.agentSources || [],
    }
  }).sort((a, b) => a.rank - b.rank)

  const categoryOverview = (wire.category_overview || wire.categoryOverview || []).map((value) => {
    const item = unknownRecord(value)
    const name = firstString(item, 'category', 'name', 'canonical_name', 'canonicalName') || '未分类'
    return {
      name,
      nameI18n: localizedFromRecord(item, 'display_name_i18n', 'displayNameI18n', 'name_i18n', 'nameI18n') || mapLocalizedText(undefined, localizedClinicalCode(name), name),
      modelScore: firstNumber(item, 'model_score', 'modelScore', 'score'),
    }
  })
  const nextTests = (wire.next_tests || wire.nextTests || []).map(mapDevelopmentNextTest)
  const evidenceSources = (wire.evidence_sources || wire.evidenceSources || []).map((value, index) => {
    const item = unknownRecord(value)
    return {
      id: firstString(item, 'id', 'citation_id', 'citationId', 'evidence_source_id') || `source-${index + 1}`,
      title: firstString(item, 'title', 'name'),
      url: firstString(item, 'url', 'link'),
      source: firstString(item, 'source', 'publisher', 'journal', 'source_kind', 'citation'),
      publishedAt: firstString(item, 'published_at', 'publishedAt', 'year'),
      summaryI18n: localizedFromRecord(item, 'summary_i18n', 'summaryI18n', 'relevance_i18n'),
    }
  })
  const agentObservations = (wire.agent_observations || wire.agentObservations || []).map((value, index) => {
    const item = unknownRecord(value)
    const role = firstString(item, 'role', 'agent_role', 'agentRole')
    return {
      id: firstString(item, 'id', 'agent_id', 'agentId', 'agent_key', 'agentKey', 'role') || `agent-${index + 1}`,
      nameI18n: localizedFromRecord(item, 'name_i18n', 'nameI18n', 'display_name_i18n', 'displayNameI18n', 'agent_name_i18n', 'agentNameI18n')
        || mapLocalizedText(firstString(item, 'name', 'agent_name', 'agentName')) || localizedDevelopmentAgentRole(role),
      role,
      status: firstString(item, 'status', 'outcome'),
      provider: firstString(item, 'provider', 'provider_name', 'providerName'),
      model: firstString(item, 'model'),
      summaryI18n: localizedFromRecord(item, 'summary_i18n', 'summaryI18n') || mapLocalizedText(firstString(item, 'summary')),
      keyFacts: readableList(item.key_facts || item.keyFacts || item.facts || item.observations),
      contradictions: readableList(item.contradictions || item.conflicts),
      missingInformation: readableList(item.missing_information || item.missingInformation || item.missing_info),
      candidatePool: readableList(item.candidate_pool || item.candidatePool || item.candidates),
      structuredOutput: item.structured_output ?? item.structuredOutput ?? item.output ?? (item.warning_codes ? { warning_codes: item.warning_codes } : undefined),
    }
  })
  const coinfectionHypotheses = (wire.coinfection_hypotheses || wire.coinfectionHypotheses || []).map((value) => {
    const item = unknownRecord(value)
    return {
      pathogens: readableList(item.pathogens || item.pathogen_latin_names || item.pathogen_names || item.pathogenNames || item.pathogen_ids || item.pathogenIds),
      modelScore: firstNumber(item, 'model_score', 'modelScore', 'score'),
      rationaleI18n: localizedFromRecord(item, 'rationale_i18n', 'rationaleI18n') || mapLocalizedText(firstString(item, 'rationale')),
    }
  })
  const reviewWire = unknownRecord(wire.review)
  const deterministicValidation = unknownRecord(reviewWire.deterministic_validation || reviewWire.deterministicValidation)
  const critic = unknownRecord(reviewWire.critic)
  const reviewAccepted = typeof reviewWire.accepted === 'boolean' ? reviewWire.accepted : typeof reviewWire.passed === 'boolean' ? reviewWire.passed : undefined
  const revisionCount = firstNumber(reviewWire, 'revision_count', 'revisionCount')
  const review = {
    status: firstString(reviewWire, 'status', 'outcome') || (reviewAccepted === true ? 'accepted' : reviewAccepted === false ? 'revision_required' : undefined),
    passed: reviewAccepted,
    issues: [
      ...readableDevelopmentIssueList(reviewWire.issues || reviewWire.errors || reviewWire.findings),
      ...readableDevelopmentIssueList(deterministicValidation.issues),
      ...readableDevelopmentIssueList(critic.issues),
    ].filter((item, index, items) => items.indexOf(item) === index),
    revisionAttempted: revisionCount !== undefined ? revisionCount > 0 : typeof reviewWire.revision_attempted === 'boolean'
      ? reviewWire.revision_attempted
      : typeof reviewWire.revisionAttempted === 'boolean' ? reviewWire.revisionAttempted : undefined,
    fallbackUsed: result.fallback_mode === 'agent_pool_fallback' || (typeof reviewWire.fallback_used === 'boolean'
      ? reviewWire.fallback_used
      : typeof reviewWire.fallbackUsed === 'boolean' ? reviewWire.fallbackUsed : false),
  }
  const warnings = readableList(wire.warnings)
  const summaryI18n = mapLocalizedText(wire.summary_i18n || wire.summaryI18n)
  const status = wire.status || (warnings.length ? 'completed_with_warnings' : 'completed')
  const technicalFailure = status === 'technical_failure'
  const developmentResult: DevelopmentRunResult = {
    status,
    summaryI18n,
    concretePathogens,
    categoryOverview,
    unknownScore: wire.unknown_score ?? wire.unknownScore ?? undefined,
    coinfectionHypotheses,
    nextTests,
    evidenceSources,
    agentObservations,
    warnings,
    review,
  }
  const publishablePathogens = technicalFailure ? [] : concretePathogens
  const evidence: EvidenceItem[] = publishablePathogens.flatMap((pathogen) => [
    ...pathogen.evidenceFor.map((statement, index) => ({ id: `${pathogen.rank}-for-${index}`, direction: 'support' as const, statement, sourceType: 'model' as const, source: pathogen.canonicalName, quality: 'unknown' as const })),
    ...pathogen.evidenceAgainst.map((statement, index) => ({ id: `${pathogen.rank}-against-${index}`, direction: 'against' as const, statement, sourceType: 'model' as const, source: pathogen.canonicalName, quality: 'unknown' as const })),
  ])
  const candidates: CandidatePathogen[] = publishablePathogens.map((pathogen) => ({
    canonicalId: pathogen.taxonomyId,
    rank: pathogen.rank,
    name: pathogen.canonicalName,
    displayNameI18n: pathogen.displayNameI18n,
    taxonomyLevel: 'species',
    calibrationStatus: 'uncalibrated_model_score',
    probability: pathogen.modelScore,
    evidenceFor: pathogen.evidenceFor,
    evidenceAgainst: pathogen.evidenceAgainst,
  }))
  return {
    schemaVersion: result.schema_version || result.schemaVersion || 'owlpath.result.v3',
    mode: result.mode || 'development',
    candidates,
    categoryProbabilities: technicalFailure ? [] : categoryOverview.map((item) => ({ name: item.nameI18n?.zhCn || item.name, probability: item.modelScore || 0 })),
    unknownProbability: technicalFailure ? undefined : developmentResult.unknownScore,
    coinfectionCandidates: technicalFailure ? [] : coinfectionHypotheses.map((item) => item.pathogens.join(' + ')).filter(Boolean),
    evidence,
    nextTest: technicalFailure ? undefined : nextTests[0],
    nextTests: technicalFailure ? [] : nextTests,
    safety: {
      disposition: technicalFailure ? 'more_information_needed' : 'species_supported',
      title: technicalFailure ? '开发结果未生成' : '开发推演已完成',
      explanation: technicalFailure ? '技术流程未生成完整、可解读的具体病原体结果。' : warnings.join('；') || '开发告警仅记录，不阻断推演。',
      applicability: 'partially_applicable',
      dataQuality: technicalFailure ? 'low' : warnings.length ? 'medium' : 'high',
      calibrationState: 'unavailable',
    },
    humanSummaryI18n: summaryI18n,
    comparison: modelOutputs.map((output) => mapComparison(output)),
    generatedAt: result.generated_at || result.generatedAt || fallbackGeneratedAt || new Date().toISOString(),
    modelVersion: result.engine_version || result.engineVersion || undefined,
    calibrationVersion: '开发推演：未校准模型分数',
    knowledgeVersion: result.governance_version || result.governanceVersion || undefined,
    developmentDemo: true,
    demoUncalibrated: true,
    demoNotForClinicalUse: true,
    demoBypassedControls: warnings,
    developmentResult,
  }
}

function safetyTitle(action: SafetyDisposition): string {
  return {
    non_infection: '当前更支持非感染性方向',
    species_supported: '可报告物种级预测集合',
    category_only: '仅报告病原大类',
    more_information_needed: '需要更多关键信息',
    abstain: '弃答并转人工复核',
  }[action]
}

function mapResult(result: WireAggregatedResult, modelOutputs: WireModelOutput[]): RunResult {
  const developmentDemo = result.development_demo === true || Boolean(result.demo_projection)
  const demoProjection = developmentDemo ? result.demo_projection : undefined
  const displayedCandidates = developmentDemo ? (demoProjection?.candidates || []) : result.candidates
  const displayedCoinfectionPairs = developmentDemo ? (demoProjection?.coinfection_pairs || []) : result.coinfection_pairs
  const disposition = mapSafetyAction(result.safety_action) || 'abstain'
  const allWarnings = [...result.safety_reasons, ...result.limitations]
  const textWarnings = allWarnings.join(' ').toLowerCase()
  const outOfDistribution = /distribution|out.?of.?scope|unsupported|ood|分布外|超出适用|不支持亚组/.test(textWarnings)
  const missing = allWarnings.filter((item) => /missing|insufficient|incomplete|information|data quality|缺失|不足|不完整|信息不全/i.test(item))
  const categoryMap = new Map<string, number>()
  displayedCandidates.forEach((item) => {
    const category = localizedClinicalCode(item.category || (item.rank_level === 'category' ? item.name : '未分类')) || '未分类'
    categoryMap.set(category, Math.max(categoryMap.get(category) || 0, item.probability))
  })
  const evidence: EvidenceItem[] = []
  displayedCandidates.forEach((candidate, index) => {
    const source = localizedCandidateName(candidate)
    candidate.evidence_for.forEach((statement, evidenceIndex) => evidence.push({ id: `for-${index}-${evidenceIndex}`, direction: 'support', statement, sourceType: 'model', source, quality: 'unknown' }))
    candidate.evidence_against.forEach((statement, evidenceIndex) => evidence.push({ id: `against-${index}-${evidenceIndex}`, direction: 'against', statement, sourceType: 'model', source, quality: 'unknown' }))
  })
  const substantiveDisagreement = result.disagreement_score > 0.05
    ? result.disagreement_notes.filter((item) => !/未发现.*冲突|no (meaningful|material|significant) .*conflict/i.test(item))
    : []
  substantiveDisagreement.forEach((statement, index) => evidence.push({ id: `uncertain-${index}`, direction: 'uncertain', statement, sourceType: 'model', source: '模型不一致性', quality: 'unknown' }))
  const contributionMap = new Map(result.model_contributions.map((item) => [item.provider_id, item]))
  const calibrations = new Set(displayedCandidates.map((item) => item.calibration_status))
  const topSyndrome = Object.entries(result.syndrome_probabilities).sort((a, b) => b[1] - a[1])[0]?.[0]
  return {
    schemaVersion: 'owlpath.result.v2',
    mode: developmentDemo ? 'development' : 'clinical',
    infectionProbability: result.infection_probability,
    syndrome: localizedClinicalCode(topSyndrome),
    syndromeI18n: topSyndrome ? mapLocalizedText(undefined, localizedClinicalCode(topSyndrome), topSyndrome) : undefined,
    categoryProbabilities: [...categoryMap.entries()].sort((a, b) => b[1] - a[1]).map(([name, probability]) => ({ name, probability })),
    candidates: displayedCandidates.map((candidate, index) => ({
      ...mapWireCandidate(candidate, index),
      inPredictionSet: !developmentDemo && (disposition === 'species_supported' || disposition === 'category_only'),
    })),
    unknownProbability: result.unknown_probability,
    coinfectionProbability: result.coinfection_probability,
    coinfectionCandidates: displayedCoinfectionPairs.map((pair) => pair.pathogen_ids.join(' + ')),
    evidence,
    nextTest: result.next_tests[0] ? mapWireNextTest(result.next_tests[0]) : undefined,
    nextTests: result.next_tests.map(mapWireNextTest),
    safety: {
      disposition,
      title: safetyTitle(disposition),
      explanation: result.safety_reasons.join('；') || '后端未返回额外安全理由。',
      applicability: outOfDistribution ? 'not_applicable' : disposition === 'abstain' ? 'partially_applicable' : 'applicable',
      dataQuality: result.limitations.length >= 3 ? 'low' : result.limitations.length ? 'medium' : 'high',
      calibrationState: developmentDemo ? 'unavailable' : calibrations.size === 1 && calibrations.has('calibrated') ? 'reliable' : calibrations.size ? 'uncertain' : 'unavailable',
      outOfDistribution,
      conflicts: substantiveDisagreement,
      missingCriticalInformation: missing,
      conclusionI18n: mapLocalizedText(result.safety_conclusion_i18n, safetyTitle(disposition)),
    },
    humanSummaryI18n: mapLocalizedText(result.human_summary_i18n),
    comparison: modelOutputs.map((output) => mapComparison(output, contributionMap.get(output.provider_id))),
    generatedAt: result.generated_at,
    modelVersion: result.engine_version,
    calibrationVersion: developmentDemo ? '开发演示：未校准模型分数' : [...calibrations].map(localizedCalibration).join('、') || undefined,
    knowledgeVersion: result.governance_version,
    developmentDemo,
    demoSyntheticOnly: demoProjection?.synthetic_only,
    demoUncalibrated: demoProjection?.uncalibrated,
    demoNotForClinicalUse: demoProjection?.not_for_clinical_use,
    demoBypassedControls: demoProjection?.bypassed_controls || [],
  }
}

export function deriveRunDetail(run: WireRun, modelOutputs: WireModelOutput[]): RunDetail {
  const developmentDemo = run.run_mode === 'development_demo'
  const developmentV3 = run.schema_version === 'owlpath.result.v3' || Boolean(run.result && isWireResultV3(run.result))
  const expectedModels = run.provider_ids.length + (run.include_baseline ? 1 : 0)
  const safeRunError = mapSafeTechnicalError(run.error)
  const modelStatuses = modelOutputs.map((item) => mapChildStageStatus(item.status, run.status))
  const terminalModels = modelStatuses.filter((status) => status === 'completed' || status === 'failed' || status === 'skipped').length
  const modelProgress = expectedModels
    ? Math.min(1, terminalModels / expectedModels)
    : modelOutputs.length || run.result ? 1 : 0
  const executionHasStarted = modelOutputs.length > 0 || Boolean(run.result)
  const progress = run.status === 'completed'
    ? 100
    : run.status === 'queued'
      ? 10
      : run.status === 'running'
        ? Math.round(25 + modelProgress * 55)
        : executionHasStarted
          ? Math.min(95, Math.round(25 + modelProgress * 55))
          : 10
  const allRecordedModelsCompleted = modelOutputs.length > 0
    && (expectedModels === 0 || modelOutputs.length >= expectedModels)
    && modelStatuses.every((status) => status === 'completed')
  const modelStageStatus: StageStatus = run.status === 'completed'
    ? 'completed'
    : run.status === 'failed'
      ? allRecordedModelsCompleted ? 'completed' : 'failed'
      : run.status === 'cancelled'
        ? allRecordedModelsCompleted ? 'completed' : 'skipped'
        : run.status === 'running'
          ? expectedModels > 0 && terminalModels >= expectedModels ? 'completed' : 'running'
          : 'pending'
  const sourceStageStatus: StageStatus = run.status === 'queued'
    ? 'pending'
    : run.status === 'running' || run.status === 'completed' || executionHasStarted
      ? 'completed'
      : run.status === 'failed' ? 'failed' : 'skipped'
  const failedOrSkipped: StageStatus = run.status === 'failed' ? 'failed' : 'skipped'
  const downstreamSkipped: StageStatus = run.status === 'failed' || run.status === 'cancelled' ? 'skipped' : 'pending'
  const stages: RunStage[] = developmentV3 ? [
    { id: 'source-fragments', name: '构建原文片段与证据索引', description: '原始病例全文是推理主依据；结构化信息用于索引和矛盾检查。', status: sourceStageStatus },
    { id: 'specialists', name: '核心会诊组与动态顶尖专科专家', description: '5个核心专家固定会诊；路由器再从20个动态专科中最多选择6个，未选角色明确记为 not_applicable。', status: modelStageStatus },
    { id: 'retrieval-chief', name: '证据板、多来源检索与病原体总诊', description: '汇合候选和反证，分别检索文献/类似病例与公共卫生信号，核验后生成具体病原体 Top-5。', status: run.status === 'completed' ? 'completed' : run.status === 'failed' || run.status === 'cancelled' ? failedOrSkipped : 'pending' },
    { id: 'review', name: '输出合同、独立反证与有限修订', description: '检查具体命名、证据追溯和固定五项合同；只在发现明确问题时修订一次。', status: run.status === 'completed' ? 'completed' : downstreamSkipped },
    { id: 'persist', name: '开发结果固化', description: '保存 v3 结果、执行图和完整性哈希。', status: run.status === 'completed' ? 'completed' : downstreamSkipped },
  ] : [
    developmentDemo
      ? { id: 'snapshot', name: '接收纯虚构开发文本', description: '本次绕过临床整理、医生复核和时间闸门，仅用于调通所选模型 Provider。', status: sourceStageStatus }
      : { id: 'snapshot', name: '保存当前证据快照', description: '只纳入 visible_at 不晚于当前决策时点的事件。', status: sourceStageStatus },
    { id: 'models', name: developmentDemo ? '调用所选模型 Provider' : '并行运行所选模型', description: `${Math.min(terminalModels, expectedModels)}/${expectedModels} 个模型已返回终态。`, status: modelStageStatus },
    { id: 'safety', name: developmentDemo ? '生成未校准开发投影' : '独立安全融合与降级', status: run.status === 'completed' ? 'completed' : run.status === 'failed' || run.status === 'cancelled' ? 'skipped' : 'pending' },
    { id: 'signed-result', name: developmentDemo ? '记录非临床演示结果' : '生成可追溯结果', status: run.status === 'completed' ? 'completed' : run.status === 'failed' || run.status === 'cancelled' ? failedOrSkipped : 'pending' },
  ]
  const legacyResult = run.result && !isWireResultV3(run.result) ? run.result : undefined
  const contributions = new Map((legacyResult?.model_contributions || []).map((item) => [item.provider_id, item]))
  const models: ModelRunState[] = modelOutputs.map((item, index) => {
    const contribution = contributions.get(item.provider_id)
    const safeModelError = mapSafeTechnicalError(item.error)
    const status = modelStatuses[index]
    return {
      providerId: item.provider_id,
      providerName: item.provider_name,
      providerKind: mapModelKind(contribution?.provider_kind || item.provider_kind, item.provider_name, contribution?.model || item.model || ''),
      model: contribution?.model || item.model || undefined,
      status,
      latencyMs: item.latency_ms ?? undefined,
      message: safeModelError?.message || (isTerminalRunStatus(run.status) && mapStageStatus(item.status) === 'running'
        ? safeRunError?.message || '本次运行结束时，该模型尚未返回终态。'
        : undefined),
    }
  })
  if (modelOutputs.length === 0) {
    const missingOutputStatus: StageStatus = run.status === 'queued' ? 'pending' : run.status === 'running' ? 'running' : run.status === 'failed' ? 'failed' : 'skipped'
    const missingOutputMessage = isTerminalRunStatus(run.status)
      ? safeRunError?.message || '本次运行结束时，模型未留下可用的返回记录。'
      : undefined
    if (run.include_baseline) models.push({ providerId: BASELINE_ID, providerKind: 'baseline', providerName: '本地透明基线', model: 'owlpath-baseline-v1', status: missingOutputStatus, message: missingOutputMessage })
    run.provider_ids.forEach((providerId) => models.push({ providerId, providerName: providerId, status: missingOutputStatus, message: missingOutputMessage }))
  }
  return {
    runId: run.id,
    caseId: run.case_id,
    decisionTime: run.decision_time,
    runMode: run.run_mode || 'live',
    retrospectiveAnchorId: run.retrospective_anchor_id || undefined,
    status: run.status,
    createdAt: run.requested_at,
    updatedAt: run.completed_at || undefined,
    progress,
    currentStage: run.status === 'queued' ? '排队中' : run.status === 'running' ? developmentV3 ? '多专科 Agent 推演中' : developmentDemo ? '模型 Provider 调用中' : '模型推演与安全融合' : run.status === 'completed' ? '已完成' : run.status === 'cancelled' ? '已取消' : '运行失败',
    stages,
    models,
    error: safeRunError,
    result: run.result ? (isWireResultV3(run.result) ? mapDevelopmentResult(run.result, modelOutputs, run.completed_at || run.requested_at) : mapResult(run.result, modelOutputs)) : undefined,
    traceVersion: run.trace_version || undefined,
    executionGraphVersion: run.execution_graph_version || undefined,
    resultSchemaVersion: run.schema_version || run.result?.schema_version || undefined,
  }
}

function mapHistoryRun(run: WireRun, providers: ProviderSummary[]): RunHistoryItem {
  const providerItems = run.provider_ids.map((id) => {
    const provider = providers.find((item) => item.id === id)
    return { id, name: provider?.name, kind: provider?.kind }
  })
  if (run.include_baseline) providerItems.unshift({ id: BASELINE_ID, name: '本地透明基线', kind: 'baseline' })
  const v3Result = run.result && isWireResultV3(run.result) ? run.result : undefined
  const legacyResult = run.result && !isWireResultV3(run.result) ? run.result : undefined
  const development = v3Result?.development_result || v3Result?.developmentResult || v3Result
  const developmentFailed = development?.status === 'technical_failure'
  const topDevelopment = developmentFailed ? undefined : (development?.concrete_pathogens || development?.concretePathogens || [])[0]
  return {
    runId: run.id,
    caseId: run.case_id,
    decisionTime: run.decision_time,
    runMode: run.run_mode || 'live',
    retrospectiveAnchorId: run.retrospective_anchor_id || undefined,
    createdAt: run.requested_at,
    status: run.status,
    disposition: legacyResult ? mapSafetyAction(legacyResult.safety_action) : undefined,
    topCandidate: topDevelopment
      ? topDevelopment.name_i18n?.zh_cn || topDevelopment.display_name_i18n?.zh_cn || topDevelopment.displayNameI18n?.zh_cn || topDevelopment.canonical_latin_name || topDevelopment.canonical_name || topDevelopment.canonicalName || topDevelopment.name
      : localizedCandidateName(
      run.run_mode === 'development_demo'
        ? legacyResult?.demo_projection?.candidates?.[0]
        : legacyResult?.candidates?.[0],
      ),
    providers: providerItems,
    traceVersion: run.trace_version || undefined,
  }
}

export const api = {
  async health(signal?: AbortSignal): Promise<HealthResponse> {
    const response = await apiRequest<{ status: 'ok' | 'degraded'; service?: string; version?: string }>('/api/health', { signal })
    return { ...response, baselineAvailable: true }
  },

  async providers(signal?: AbortSignal): Promise<ProviderSummary[]> {
    const providers = await apiRequest<WireProvider[]>('/api/providers', { signal })
    return [baselineProvider(), ...providers.map(mapProvider)]
  },

  async architecture(signal?: AbortSignal): Promise<ArchitectureResponse> {
    return mapArchitecture(await apiRequest<WireArchitecture>('/api/architecture', { signal }))
  },

  async organizeClinicalText(text: string, decisionTime: string): Promise<ClinicalTextOrganization> {
    const response = await apiRequest<WireClinicalTextOrganization>('/api/clinical-text/organize', {
      method: 'POST',
      body: jsonBody({ text, decision_time: asIso(decisionTime), source: 'clinician-ui' }),
    })
    return mapClinicalTextOrganization(response)
  },

  async previewClinicalFacts(caseDraft: CaseDraft): Promise<ClinicalTextOrganization['modelFactPreview']> {
    const response = await apiRequest<{
      facts: Array<{
        event_index: number
        kind: string
        occurred_at: string
        visible_at: string
        collected_at?: string | null
        issued_at?: string | null
        source: string
        status: string
        data: Record<string, unknown>
        quality: Record<string, unknown>
      }>
    }>('/api/clinical-facts/preview', {
      method: 'POST',
      body: jsonBody({ events: buildWireEvents(caseDraft) }),
    })
    return response.facts.map((item) => ({
      eventIndex: item.event_index,
      kind: item.kind,
      occurredAt: item.occurred_at,
      visibleAt: item.visible_at,
      collectedAt: item.collected_at || undefined,
      issuedAt: item.issued_at || undefined,
      source: item.source,
      status: item.status,
      data: item.data,
      quality: item.quality,
    }))
  },

  async saveProvider(input: ProviderConfigInput): Promise<ProviderSummary> {
    if (input.kind === 'baseline' || input.id === BASELINE_ID) throw new ApiError('基线模型由后端内置，不能修改')
    const createPayload = {
      name: input.name,
      kind: toWireKind(input.kind),
      model: input.model,
      base_url: input.baseUrl || null,
      api_key: input.apiKey || undefined,
      enabled: input.enabled,
      data_boundary: input.dataBoundary,
      weight: input.weight ?? 1,
    }
    const updatePayload = {
      name: input.name,
      model: input.model,
      base_url: input.baseUrl || null,
      api_key: input.apiKey || undefined,
      clear_api_key: Boolean(input.clearApiKey),
      enabled: input.enabled,
      data_boundary: input.dataBoundary,
      weight: input.weight ?? 1,
    }
    const provider = await apiRequest<WireProvider>(input.id ? `/api/providers/${encodeURIComponent(input.id)}` : '/api/providers', {
      method: input.id ? 'PATCH' : 'POST',
      body: jsonBody(input.id ? updatePayload : createPayload),
    })
    return mapProvider(provider)
  },

  async testProvider(provider: ProviderId): Promise<ProviderTestResponse> {
    const response = await apiRequest<{ ok: boolean; latency_ms?: number; schema_valid?: boolean; model?: string; error?: { code?: string; message?: string; retryable?: boolean } }>(`/api/providers/${encodeURIComponent(provider)}/test`, {
      method: 'POST',
      body: jsonBody({ confirm_possible_cost: true }),
      signal: AbortSignal.timeout(90_000),
    })
    return {
      ok: response.ok,
      latencyMs: response.latency_ms,
      model: response.model,
      message: response.error?.message,
      schemaValid: response.schema_valid,
      errorCode: response.error?.code,
      retryable: response.error?.retryable,
    }
  },

  async setProviderEnabled(provider: ProviderId, enabled: boolean): Promise<ProviderSummary> {
    const response = await apiRequest<WireProvider>(`/api/providers/${encodeURIComponent(provider)}`, {
      method: 'PATCH',
      body: jsonBody({ enabled }),
    })
    return mapProvider(response)
  },

  deleteProvider(provider: ProviderId) {
    return apiRequest<void>(`/api/providers/${encodeURIComponent(provider)}`, { method: 'DELETE' })
  },

  async createDevelopmentDemoRun(text: string, providerIds: ProviderId[]): Promise<CreateRunResponse> {
    if (!text.trim()) throw new ApiError('请先粘贴一段纯虚构或已脱敏的开发测试文本')
    const selected = providerIds.filter((id) => id && id !== BASELINE_ID)
    if (!selected.length) throw new ApiError('没有可用的已验证模型')
    const payload = { text, provider_ids: selected }
    let run: WireRun
    try {
      run = await apiRequest<WireRun>('/api/development/runs', {
        method: 'POST',
        body: jsonBody(payload),
      })
    } catch (error) {
      if (!(error instanceof ApiError) || (error.status !== 404 && error.status !== 405)) throw error
      run = await apiRequest<WireRun>('/api/development-demo/runs', {
        method: 'POST',
        body: jsonBody(payload),
      })
    }
    return { runId: run.id, status: run.status, createdAt: run.requested_at }
  },

  async createRun(input: CreateRunRequest): Promise<CreateRunResponse> {
    const externalConsent = Boolean(input.dataTransferConsent?.accepted && input.dataTransferConsent.externalProviderIds.length)
    const requestedAlias = input.case.caseId?.trim() || ''
    const alias = /^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(requestedAlias) ? requestedAlias : `OWL-${Date.now().toString(36).toUpperCase()}`
    const caseResponse = await apiRequest<{ id: string }>('/api/cases', {
      method: 'POST',
      body: jsonBody({
        case_alias: alias,
        demographics: {
          age_years: input.case.demographics.age,
          sex: input.case.demographics.sex === 'male' || input.case.demographics.sex === 'female' ? input.case.demographics.sex : 'unknown',
          pregnant: input.case.demographics.pregnant,
          immunocompromised: input.case.demographics.immunocompromised,
          care_setting: input.case.demographics.encounterType === 'inpatient' ? 'ward' : input.case.demographics.encounterType || 'other',
        },
        context: {
          primary_syndrome: scenarioToWire(input.case.scenario),
          acquisition_context: input.case.acquisitionContext,
          unit_code: input.case.demographics.department && /^[A-Za-z0-9._:-]+$/.test(input.case.demographics.department)
            ? input.case.demographics.department
            : undefined,
          notes_deidentified: '本病例由临床工作台建立；不包含直接身份字段。',
        },
        external_data_consent: externalConsent,
      }),
    })
    try {
      const events = buildWireEvents(input.case)
      for (const event of events) {
        await apiRequest(`/api/cases/${encodeURIComponent(caseResponse.id)}/events`, { method: 'POST', body: jsonBody(event) })
      }
      const includeBaseline = input.case.selectedProviders.includes(BASELINE_ID)
      const providerIds = input.case.selectedProviders.filter((id) => id !== BASELINE_ID)
      const snapshotBinding = await apiRequest<{ input_snapshot_sha256: string }>(
        `/api/cases/${encodeURIComponent(caseResponse.id)}/snapshot-hash?decision_time=${encodeURIComponent(asIso(input.case.decisionTime))}`,
      )
      const run = await apiRequest<WireRun>('/api/runs', {
        method: 'POST',
        body: jsonBody({
          case_id: caseResponse.id,
          decision_time: asIso(input.case.decisionTime),
          run_mode: 'live',
          provider_ids: providerIds,
          include_baseline: includeBaseline,
          clinical_review: {
            accepted: input.clinicalReview.accepted,
            confirmed_at: input.clinicalReview.confirmedAt,
            statement_version: input.clinicalReview.statementVersion,
            parser_version: input.clinicalReview.parserVersion,
            source_text_sha256: input.clinicalReview.sourceTextSha256,
            input_snapshot_sha256: snapshotBinding.input_snapshot_sha256,
          },
          data_transfer_consent: input.dataTransferConsent ? {
            accepted: input.dataTransferConsent.accepted,
            confirmed_at: input.dataTransferConsent.confirmedAt,
            statement_version: input.dataTransferConsent.statementVersion,
            external_provider_ids: input.dataTransferConsent.externalProviderIds,
            input_snapshot_sha256: snapshotBinding.input_snapshot_sha256,
            provider_targets: input.dataTransferConsent.providerTargets.map((target) => ({
              provider_id: target.providerId,
              kind: toWireKind(target.kind),
              model: target.model,
              base_url_origin: providerOrigin(target.kind, target.baseUrl),
              endpoint_url: providerEndpoint(target.kind, target.model, target.baseUrl),
              data_boundary: target.dataBoundary,
            })),
          } : undefined,
        }),
      })
      return { runId: run.id, status: run.status, createdAt: run.requested_at }
    } catch (error) {
      // Roll back only an unstarted case. If the run was actually created, the
      // backend refuses deletion and preserves the immutable audit trail.
      await apiRequest<void>(`/api/cases/${encodeURIComponent(caseResponse.id)}`, { method: 'DELETE' }).catch(() => undefined)
      throw error
    }
  },

  async run(runId: string, signal?: AbortSignal): Promise<RunDetail> {
    const modelRequest = apiRequest<WireModelOutput[]>(`/api/runs/${encodeURIComponent(runId)}/models`, { signal })
      .catch((error) => {
        if (error instanceof ApiError && error.status === 404) return []
        throw error
      })
    const [run, models] = await Promise.all([
      apiRequest<WireRun>(`/api/runs/${encodeURIComponent(runId)}`, { signal }),
      modelRequest,
    ])
    return deriveRunDetail(run, models)
  },

  async runTrace(runId: string, signal?: AbortSignal): Promise<RunTrace> {
    const encodedRunId = encodeURIComponent(runId)
    const [trace, runState] = await Promise.all([
      apiRequest<WireRunTrace>(`/api/runs/${encodedRunId}/trace`, { signal }),
      apiRequest<Pick<WireRun, 'status'>>(`/api/runs/${encodedRunId}`, { signal }),
    ])
    return mapRunTrace(trace, runState.status)
  },

  async traceNode(runId: string, nodeId: string, signal?: AbortSignal, runStatus?: RunStatus): Promise<AgentNode> {
    const scopedPath = `/api/runs/${encodeURIComponent(runId)}/trace/nodes/${encodeURIComponent(nodeId)}`
    const legacyPath = `/api/trace/nodes/${encodeURIComponent(nodeId)}`
    let response: WireTraceNode | WireTraceNodeDetail
    try {
      response = await apiRequest<WireTraceNode | WireTraceNodeDetail>(scopedPath, { signal })
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 404) throw error
      response = await apiRequest<WireTraceNode | WireTraceNodeDetail>(legacyPath, { signal })
    }
    if ('node' in response) {
      return clampTraceNodeForRunStatus(mapTraceNode({
        ...response.node,
        artifacts: response.artifacts || response.node.artifacts,
        metadata: {
          ...(response.node.metadata || {}),
          ...(response.trace_privacy ? { trace_privacy: response.trace_privacy } : {}),
        },
      }), runStatus)
    }
    return clampTraceNodeForRunStatus(mapTraceNode(response), runStatus)
  },

  async history(limit = 100, signal?: AbortSignal): Promise<HistoryResponse> {
    const [runs, providers] = await Promise.all([
      apiRequest<WireRun[]>('/api/runs', { signal }),
      api.providers(signal),
    ])
    const traceableRuns = runs.filter((run) => run.trace_version === 'owlpath.trace.v2')
    const items = traceableRuns.slice(0, limit).map((run) => mapHistoryRun(run, providers))
    return { items, total: traceableRuns.length }
  },

  async evaluation(signal?: AbortSignal): Promise<EvaluationResponse> {
    const summary = await apiRequest<Record<string, number | null>>('/api/evaluations/summary', { signal })
    const metricMeta: Record<string, { label: string; unit?: string; lowerIsBetter?: boolean; description: string }> = {
      top1: { label: 'Top-1', unit: '%', description: '正确因果病原体位于首位的比例。' },
      top3: { label: 'Top-3', unit: '%', description: '正确因果病原体进入前三的比例。' },
      top5: { label: 'Top-5', unit: '%', description: '正确因果病原体进入前五的比例。' },
      mrr: { label: 'MRR', description: '正确病原体排名倒数的均值。' },
      pathogen_brier: { label: '病原体 Brier', lowerIsBetter: true, description: '研究版多标签 Brier 误差，越低越好。' },
      infection_brier: { label: '感染 Brier', lowerIsBetter: true, description: '感染/非感染概率误差，越低越好。' },
    }
    return {
      sampleSize: summary.n_evaluations ?? 0,
      metrics: Object.entries(metricMeta).map(([key, meta]) => ({ key, ...meta, value: summary[key] ?? undefined })),
      slices: [],
      notes: ['当前后端只汇总已录入的研究标签，不替代预注册的多中心外部验证。'],
    }
  },

  async createEvaluation(input: CreateEvaluationInput): Promise<EvaluationRecord> {
    const response = await apiRequest<WireEvaluationRead>('/api/evaluations', {
      method: 'POST',
      body: jsonBody({
        run_id: input.runId,
        label: {
          infection_status: input.label.infectionStatus,
          causal_pathogens: input.label.causalPathogens.map((item) => ({
            canonical_id: item.canonicalId.trim(),
            name: item.name.trim(),
            certainty: item.certainty,
          })),
          colonizers: input.label.colonizers,
          contaminants: input.label.contaminants,
          coinfection: input.label.coinfection,
          adjudication_status: input.label.adjudicationStatus,
          label_version: '1',
          notes: input.label.notes?.trim() || null,
        },
      }),
    })
    return {
      id: response.id,
      runId: response.run_id,
      caseId: response.case_id,
      metrics: Object.fromEntries(Object.entries(response.metrics).map(([key, value]) => [key, value ?? undefined])),
      createdAt: response.created_at,
      updatedAt: response.updated_at,
    }
  },

  async governance(signal?: AbortSignal): Promise<GovernanceResponse> {
    const [governance, audits, providers] = await Promise.all([
      apiRequest<WireGovernance>('/api/governance', { signal }),
      apiRequest<WireAudit[]>('/api/audit?limit=200', { signal }),
      api.providers(signal),
    ])
    return {
      scopeContract: {
        population: '研究性临床决策支持人群；排除人群见下方条件',
        scenario: governance.allowed_syndromes.join('、'),
        decisionTimeRule: '每次运行以 decision_time 保存不可变快照，仅包含当时已可见事件',
        intendedUse: governance.intended_use,
        exclusions: governance.excluded_populations,
      },
      versions: [
        { component: '治理配置', version: governance.version, status: governance.run_enabled ? 'active' : 'blocked', notes: governance.disclaimer },
        ...providers.map((provider) => ({ component: `模型实例：${provider.name}`, version: provider.model || provider.id, status: provider.enabled ? 'active' as const : 'retired' as const, releasedAt: provider.lastCheckedAt, notes: `${kindMetaForNote(provider.kind)} · ${provider.dataBoundary}` })),
      ],
      audits: audits.map((item) => ({ id: String(item.id), time: item.created_at, actor: item.actor, action: item.action, target: `${item.entity_type}:${item.entity_id}`, result: item.action.includes('blocked') || item.action.includes('denied') ? 'denied' : 'success', detail: item.details ? JSON.stringify(item.details) : undefined })),
      monitoring: [
        { label: '新运行开关', value: governance.run_enabled ? 1 : 0, state: governance.run_enabled ? 'ok' : 'critical' },
        { label: '已启用模型实例', value: providers.filter((item) => item.enabled).length, state: providers.some((item) => item.enabled) ? 'ok' : 'warning' },
        { label: 'Unknown弃答阈值', value: governance.unknown_abstain_threshold * 100, unit: '%', state: 'ok' },
        { label: '最大模型分歧', value: governance.max_disagreement * 100, unit: '%', state: 'ok' },
      ],
    }
  },
}

function kindMetaForNote(kind: ProviderKind): string {
  return {
    baseline: '本地透明基线',
    openai_responses: 'OpenAI Responses',
    anthropic_messages: 'Anthropic Messages',
    gemini_generate_content: 'Gemini GenerateContent',
    deepseek: 'OpenAI-compatible / DeepSeek',
    qwen: 'OpenAI-compatible / Qwen',
    openai_compatible: 'OpenAI-compatible',
    ollama: 'Ollama',
  }[kind]
}

export function apiErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'provider_test_superseded') return '测试期间模型配置已被修改，旧测试结果已自动丢弃。请按当前配置重新测试。'
    if (error.code === 'internal_error') return '本地服务发生未预期错误，请重试；如果再次出现，请保留当前页面并反馈。'
    if (error.status !== undefined) {
      if (error.status === 400 || error.status === 422) return '提交内容未通过检查，请核对必填项、格式和当前运行状态。'
      if (error.status === 401 || error.status === 403) return '访问权限或模型鉴权未通过，请检查账户与密钥配置。'
      if (error.status === 404) return '未找到请求的运行记录或服务接口。'
      if (error.status === 409) return '当前配置或运行状态已变化，请刷新页面后重试。'
      if (error.status === 429) return '请求过于频繁或账户额度受限，请稍后重试。'
      if (error.status >= 500) return '分析服务本次未正常完成请求，请稍后重试。'
      return `请求未完成（HTTP ${error.status}），请刷新页面后重试。`
    }
    return error.message
  }
  if (error instanceof Error) return '页面操作未完成，请重试；技术详细信息仅保留在开发日志中。'
  return '发生未知错误'
}
