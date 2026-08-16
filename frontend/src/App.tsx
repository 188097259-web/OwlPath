import {
  Activity,
  AlertCircle,
  AlertTriangle,
  Archive,
  ArrowRight,
  BarChart3,
  BookOpenCheck,
  BrainCircuit,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  ClipboardCheck,
  Clock3,
  CloudOff,
  Code2,
  Database,
  FileClock,
  FileSearch,
  FlaskConical,
  Gauge,
  History,
  KeyRound,
  Laptop,
  LoaderCircle,
  Menu,
  Microscope,
  Moon,
  Network,
  OctagonX,
  PanelLeftClose,
  PanelLeftOpen,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  SearchCheck,
  ServerCog,
  Settings2,
  ShieldCheck,
  Sparkles,
  Stethoscope,
  Sun,
  TestTube2,
  Trash2,
  TriangleAlert,
  UserRoundCheck,
  Wifi,
  X,
  XCircle,
  type LucideIcon,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react'
import {
  api,
  apiErrorMessage,
  clampTraceNodeForRunStatus,
  providerEndpoint,
  type AuditRecord,
  type AdjudicationStatus,
  type AgentGraph,
  type AgentNode,
  type ArchitectureResponse,
  type CaseDraft,
  type CausalPathogenLabelInput,
  type ClinicalTextOrganization,
  type CoinfectionLabel,
  type ConnectionState,
  type DevelopmentAgentObservation,
  type EvaluationResponse,
  type GovernanceResponse,
  type InfectionStatus,
  type LabEntry,
  type LocalizedText,
  type MetricValue,
  type ProviderConfigInput,
  type ProviderId,
  type ProviderKind,
  type ProviderSummary,
  type ProviderTestResponse,
  type RunDetail,
  type RunHistoryItem,
  type RunTrace,
  type SafetyDisposition,
  type StageStatus,
  type TimelineEntry,
  type VitalEntry,
} from './lib/api'
import {
  appRouteHash,
  parseAppRoute,
  resultTabForLoadedContract,
  type AppRoute,
  type ArchitectureView,
  type PageId,
  type ResultTab,
} from './lib/route'
import {
  EXPERT_CONSULT_REGISTRY,
  LATEST_WORKFLOW_DATE,
  LATEST_WORKFLOW_LABEL,
  LATEST_WORKFLOW_NODES,
  LATEST_WORKFLOW_STAGES,
  WORKFLOW_DESIGN_EVIDENCE,
  WORKFLOW_GOVERNANCE,
  WORKFLOW_MEMORY_LAYERS,
  type WorkflowMaturity,
  type WorkflowNodeDefinition,
} from './lib/latest-workflow'
import {
  CORE_CLINICAL_EXPERT_ROLE_IDS,
  DYNAMIC_CLINICAL_EXPERT_ROLE_IDS,
  clinicalAgentRole,
  clinicalComparisonMode,
  clinicalDevelopmentResultPresentation,
  clinicalDevelopmentAgentStatus,
  clinicalDevelopmentRunStatus,
  clinicalNodeResponsibility,
  clinicalRunPhase,
  clinicalTechnicalError,
  developmentAgentGroup,
} from './lib/clinical-copy'

type Theme = 'light' | 'dark'
type WorkbenchMode = 'development_demo' | 'strict_clinical'
// The strict workflow remains implemented for regression and future clinical
// validation, but the ordinary development UI intentionally does not expose it.
const ENABLE_STRICT_CLINICAL_UI = false

const NAV_ITEMS: Array<{ id: PageId; label: string; caption: string; icon: LucideIcon }> = [
  { id: 'case', label: '新建病原体分析', caption: '粘贴测试病例，生成候选病原体', icon: Stethoscope },
  { id: 'models', label: '模型设置', caption: '连接、测试和切换模型服务', icon: BrainCircuit },
  { id: 'architecture', label: '系统 Workflow', caption: '当前运行、最新闭环与长期目标', icon: Network },
  { id: 'run', label: '分析进度', caption: '查看当前步骤和运行记录', icon: Activity },
  { id: 'result', label: '本次分析结果', caption: '前5位候选、依据和下一步检查', icon: ClipboardCheck },
  { id: 'compare', label: '分析视角对照', caption: '比较各视角提出的候选', icon: BarChart3 },
  { id: 'history', label: '历史分析', caption: '查看可追溯的既往记录', icon: History },
  { id: 'evaluation', label: '验证与评价', caption: '标签、指标和离线验证', icon: Gauge },
  { id: 'governance', label: '安全与版本', caption: '查看边界、版本和审计记录', icon: ShieldCheck },
]

const SIDEBAR_GROUPS: Array<{ label: string; items: PageId[] }> = [
  { label: '病例分析', items: ['case', 'result', 'history', 'architecture'] },
  { label: '模型与研究工具', items: ['models', 'evaluation', 'governance'] },
]

function sidebarItemIsActive(page: PageId, item: PageId): boolean {
  if (item === 'result') return page === 'result' || page === 'run' || page === 'compare'
  return page === item
}

const PROVIDER_KIND_META: Array<{
  kind: ProviderKind
  name: string
  description: string
  baseUrlPlaceholder?: string
  modelPlaceholder?: string
  defaultModel?: string
  modelSuggestions?: Array<{ id: string; label: string }>
  dataBoundary: 'local' | 'external'
}> = [
  { kind: 'baseline', name: '本地基线模型', description: '无 Key 也可由后端真实运行的结构化基线。', dataBoundary: 'local' },
  { kind: 'openai_responses', name: 'OpenAI', description: '通用推理与结构化输出。', modelPlaceholder: '例：gpt-5.6（以账户可用模型为准）', dataBoundary: 'external' },
  { kind: 'anthropic_messages', name: 'Anthropic', description: '长上下文与证据综合。', modelPlaceholder: '输入账户可用的精确模型ID', dataBoundary: 'external' },
  { kind: 'gemini_generate_content', name: 'Gemini', description: '多模态理解与长文本。', modelPlaceholder: '输入账户可用的精确模型ID', dataBoundary: 'external' },
  {
    kind: 'deepseek',
    name: 'DeepSeek（兼容接口）',
    description: '通过OpenAI兼容协议接入。',
    modelPlaceholder: '例：deepseek-v4-flash',
    defaultModel: 'deepseek-v4-flash',
    modelSuggestions: [
      { id: 'deepseek-v4-flash', label: 'V4 Flash（更快、更省）' },
      { id: 'deepseek-v4-pro', label: 'V4 Pro（能力更强）' },
    ],
    baseUrlPlaceholder: 'https://api.deepseek.com',
    dataBoundary: 'external',
  },
  { kind: 'qwen', name: '通义千问（兼容接口）', description: '通过OpenAI兼容协议接入。', modelPlaceholder: '输入账户可用的精确模型ID', baseUrlPlaceholder: 'https://dashscope.aliyuncs.com/compatible-mode/v1', dataBoundary: 'external' },
  { kind: 'openai_compatible', name: 'OpenAI 兼容接口', description: '私有网关或其他兼容服务。', modelPlaceholder: '输入接口提供的模型ID', baseUrlPlaceholder: 'https://your-gateway.example/v1', dataBoundary: 'external' },
  { kind: 'ollama', name: 'Ollama', description: '本机或院内私有化模型。', modelPlaceholder: '输入本地已拉取的模型名', baseUrlPlaceholder: 'http://127.0.0.1:11434', dataBoundary: 'local' },
]

const PROVIDER_QUICK_PRESETS: Array<{
  kind: Exclude<ProviderKind, 'baseline' | 'openai_compatible'>
  label: string
  description: string
  icon: LucideIcon
}> = [
  { kind: 'openai_responses', label: 'OpenAI', description: '只需模型ID和API Key', icon: Sparkles },
  { kind: 'anthropic_messages', label: 'Anthropic', description: '只需模型ID和API Key', icon: BrainCircuit },
  { kind: 'gemini_generate_content', label: 'Gemini', description: '只需模型ID和API Key', icon: Sparkles },
  { kind: 'deepseek', label: 'DeepSeek', description: 'V4 Flash与接口地址已预填', icon: BrainCircuit },
  { kind: 'qwen', label: '通义千问', description: '兼容地址已自动填写', icon: Sparkles },
  { kind: 'ollama', label: 'Ollama', description: '本机运行，不需要Key', icon: Laptop },
]

function kindMeta(kind: ProviderKind) {
  return PROVIDER_KIND_META.find((item) => item.kind === kind) || PROVIDER_KIND_META[0]
}

function requiresCustomBaseUrl(kind: ProviderKind): boolean {
  return kind === 'deepseek' || kind === 'qwen' || kind === 'openai_compatible'
}

function requiresCommercialApiKey(kind: ProviderKind): boolean {
  return kind !== 'baseline' && kind !== 'ollama' && kind !== 'openai_compatible'
}

function presetBaseUrl(kind: ProviderKind): string {
  return requiresCustomBaseUrl(kind) || kind === 'ollama' ? kindMeta(kind).baseUrlPlaceholder || '' : ''
}

function providerTestFailureText(result: ProviderTestResponse): string {
  const code = result.errorCode || ''
  if (code === 'missing_api_key' || code === 'provider_http_401' || code === 'provider_http_403') return '请检查API Key、账户权限以及该模型是否已开通。'
  if (code === 'provider_http_404') return '请检查模型ID和服务地址；该模型可能不在当前账户或区域中。'
  if (code === 'provider_http_400') return '服务端不接受当前请求。请优先检查“模型ID”是否为厂商公布的精确名称。'
  if (code === 'provider_http_429') return '接口已限流或额度不足，请检查配额后稍后重试。'
  if (['provider_timeout', 'provider_network_error', 'provider_dns_error'].includes(code)) return '网络或服务地址暂时不可达，请检查地址、代理和本地模型是否已启动。'
  if (code === 'provider_output_truncated') return '接口与鉴权已通过，但结构化输出被截断。这不是Key错误；请先重试，持续发生时需提高输出上限。'
  if (code.startsWith('invalid_provider') || code === 'empty_provider_response' || code === 'provider_schema_mismatch') return '接口与鉴权已通过，但本次结构化医学输出未通过。这不是Key错误；可先重试，或核对协议类型。'
  return result.message || '请检查Key、模型ID和服务地址。'
}

function storedProviderErrorText(code: string): string {
  return providerTestFailureText({ ok: false, errorCode: code })
}

function providerName(providerId: ProviderId, providers: ProviderSummary[] = []): string {
  return providers.find((item) => item.id === providerId)?.name || providerId
}

function providerTargetLabel(provider: ProviderSummary): string {
  const endpoint = providerEndpoint(provider.kind, provider.model || '', provider.baseUrl)
  return `${provider.name} · ${provider.model || kindMeta(provider.kind).name} · ${endpoint}`
}

function providerClinicalLabel(provider: ProviderSummary): string {
  return `${provider.name} · ${provider.model || kindMeta(provider.kind).name}`
}

function toLocalInputValue(date = new Date()): string {
  const offset = date.getTimezoneOffset()
  return new Date(date.getTime() - offset * 60_000).toISOString().slice(0, 16)
}

function newId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
}

function createEmptyCase(): CaseDraft {
  return {
    decisionTime: toLocalInputValue(),
    scenario: 'undifferentiated',
    acquisitionContext: 'unknown',
    demographics: { encounterType: 'emergency' },
    history: {
      chiefComplaint: '',
      presentIllness: '',
      exposureHistory: '',
      epidemiology: '',
      priorAntimicrobials: '',
    },
    host: { comorbidities: '', immuneStatus: '', devicesAndProcedures: '', allergies: '' },
    vitals: [],
    labs: [],
    imaging: { report: '' },
    timeline: [],
    selectedProviders: [],
  }
}

function clinicalDraftFingerprint(draft: CaseDraft): string {
  const { selectedProviders: _providers, caseId: _caseId, ...clinicalContent } = draft
  return JSON.stringify(clinicalContent)
}

function formatDate(value?: string): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function formatDuration(ms?: number): string {
  if (ms === undefined) return '—'
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`
}

function formatPercent(value?: number, digits = 0): string {
  if (value === undefined || Number.isNaN(value)) return '—'
  return `${(value * 100).toFixed(digits)}%`
}

function formatModelScore(value?: number): string {
  if (value === undefined || Number.isNaN(value)) return '—'
  return Math.max(0, Math.min(1, value)).toFixed(2)
}

function BilingualCopy({
  text,
  zh,
  en,
  className,
}: {
  text?: LocalizedText
  zh?: string
  en?: string
  className?: string
}) {
  const primary = text?.zhCn || zh || text?.en || en || '—'
  const secondary = text?.en || en
  return (
    <span className={cx('bilingual-copy', className)}>
      <span lang="zh-CN">{primary}</span>
      {secondary && secondary !== primary && <small lang="en">{secondary}</small>}
    </span>
  )
}

function plainCitationTitle(value?: string): string {
  if (!value) return ''
  return value
    .replace(/&lt;\/?(?:i|b|em|strong|sup|sub)&gt;/gi, '')
    .replace(/<\/?(?:i|b|em|strong|sup|sub)>/gi, '')
    .replace(/&amp;/gi, '&')
    .replace(/&quot;/gi, '"')
    .replace(/&#(?:39|x27);/gi, "'")
    .replace(/\s+/g, ' ')
    .trim()
}

function taxonomyLevelLabel(value: NonNullable<RunDetail['result']>['candidates'][number]['taxonomyLevel']): string {
  return { category: '病原大类', family: '科', genus: '属', species: '种' }[value]
}

function invasivenessLabel(value?: NonNullable<RunDetail['result']>['nextTest'] extends infer T ? T extends { invasiveness?: infer V } ? V : never : never): string {
  if (!value) return '—'
  return { none: '无创', low: '低', moderate: '中等', high: '高' }[value]
}

function runStatusLabel(value: RunDetail['status']): string {
  return { queued: '排队中', running: '运行中', completed: '已完成', failed: '失败', cancelled: '已取消' }[value]
}

function scenarioLabel(value: CaseDraft['scenario']): string {
  return {
    lower_respiratory: '疑似下呼吸道感染',
    bloodstream: '疑似血流感染',
    urinary: '疑似尿路感染',
    cns: '疑似中枢神经系统感染',
    abdominal: '疑似腹腔感染',
    undifferentiated: '未分化感染综合征',
  }[value]
}

function triStateLabel(value?: boolean): string {
  return value === true ? '是' : value === false ? '否' : '未知'
}

const ATOMIC_FACT_LABELS: Record<string, string> = {
  fever: '发热', cough: '咳嗽', sputum: '咳痰', purulent_sputum: '脓痰', dyspnea: '呼吸困难',
  chest_pain: '胸痛', chills: '寒战', fatigue: '乏力', myalgia: '肌痛', diarrhea: '腹泻',
  altered_mental_status: '意识状态改变', sick_contact: '患病接触', bird_exposure: '禽鸟暴露',
  animal_exposure: '动物暴露', recent_travel: '近期旅行', aspiration_risk: '误吸风险', copd: '慢阻肺',
  diabetes: '糖尿病', chronic_kidney_disease: '慢性肾病', heart_failure: '心衰', malignancy: '恶性肿瘤',
  prior_antimicrobial_exposure: '既往抗微生物药暴露', consolidation: '肺实变',
  ground_glass_opacity: '磨玻璃影', infiltrate: '浸润影', pleural_effusion: '胸腔积液',
  cavitation: '空洞', multilobar: '多肺叶受累', bilateral: '双侧受累', wbc: '白细胞',
  neutrophil_percent: '中性粒细胞比例', lymphocyte_percent: '淋巴细胞比例', hemoglobin: '血红蛋白',
  platelet: '血小板', crp: 'C反应蛋白', procalcitonin: '降钙素原', lactate: '乳酸',
  creatinine: '肌酐', temperature: '体温', heart_rate: '心率', respiratory_rate: '呼吸频率',
  blood_pressure: '血压', oxygen_saturation: '血氧饱和度',
}

function atomicFactSummary(item: ClinicalTextOrganization['modelFactPreview'][number]): string {
  const clinicalFacts = (item.data.clinical_facts || item.data.imaging_facts) as Array<{ code?: string; status?: string; temporality?: string }> | undefined
  if (Array.isArray(clinicalFacts)) {
    const temporality = { current: '当前', historical: '既往', mixed: '时相混合' }
    return clinicalFacts.map((fact) => {
      const status = fact.status === 'absent' ? '否认/未见' : fact.status === 'unknown' ? '不确定' : '存在'
      return `${ATOMIC_FACT_LABELS[fact.code || ''] || fact.code || '未知事实'}：${status}（${temporality[fact.temporality as keyof typeof temporality] || fact.temporality || '时相未知'}）`
    }).join('；')
  }
  const code = String(item.data.test_code || item.data.observation_code || '')
  if (code) {
    const abnormal = { high: '偏高', low: '偏低', normal: '正常', unknown: '异常标记未知' }
    const suffix = item.data.abnormal ? `；${abnormal[item.data.abnormal as keyof typeof abnormal] || String(item.data.abnormal)}` : ''
    return `${ATOMIC_FACT_LABELS[code] || code}：${String(item.data.value ?? '')} ${String(item.data.unit ?? '')}${suffix}`.trim()
  }
  return '该事件未产生可发送的受控事实。'
}

const BLOCKING_ORGANIZATION_WARNING_CODES = new Set([
  'future_timestamp_in_text',
  'possible_pathogen_label_leakage',
  'lab_after_decision_time',
  'imaging_after_decision_time',
])

function isBlockingOrganizationWarning(code: string): boolean {
  return code.startsWith('possible_direct_identifier') || BLOCKING_ORGANIZATION_WARNING_CODES.has(code)
}

function cx(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(' ')
}

function handleTabListKeyDown(event: ReactKeyboardEvent<HTMLDivElement>): void {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  const tabs = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]'))
  const currentIndex = tabs.findIndex((tab) => tab === document.activeElement)
  if (currentIndex < 0 || !tabs.length) return
  event.preventDefault()
  const nextIndex = event.key === 'Home'
    ? 0
    : event.key === 'End'
      ? tabs.length - 1
      : event.key === 'ArrowRight'
        ? (currentIndex + 1) % tabs.length
        : (currentIndex - 1 + tabs.length) % tabs.length
  tabs[nextIndex]?.focus()
  tabs[nextIndex]?.click()
}

function Card({ children, className, tone }: { children: ReactNode; className?: string; tone?: 'soft' | 'accent' }) {
  return <section className={cx('card', tone && `card-${tone}`, className)}>{children}</section>
}

function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow: string
  title: string
  description: string
  actions?: ReactNode
}) {
  return (
    <div className="page-header">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </div>
  )
}

function StatusPill({
  tone = 'neutral',
  children,
  icon: Icon,
}: {
  tone?: 'neutral' | 'success' | 'warning' | 'danger' | 'info' | 'violet'
  children: ReactNode
  icon?: LucideIcon
}) {
  return (
    <span className={cx('status-pill', `status-${tone}`)}>
      {Icon && <Icon size={13} />}
      {children}
    </span>
  )
}

function InlineNotice({
  tone,
  title,
  children,
}: {
  tone: 'info' | 'warning' | 'danger' | 'success'
  title?: string
  children: ReactNode
}) {
  const Icon = tone === 'danger' ? XCircle : tone === 'warning' ? TriangleAlert : tone === 'success' ? CheckCircle2 : CircleHelp
  return (
    <div className={cx('inline-notice', `notice-${tone}`)} role={tone === 'danger' ? 'alert' : undefined}>
      <Icon size={18} />
      <div>
        {title && <strong>{title}</strong>}
        <div>{children}</div>
      </div>
    </div>
  )
}

function EmptyState({
  icon: Icon = FileSearch,
  title,
  description,
  action,
}: {
  icon?: LucideIcon
  title: string
  description: string
  action?: ReactNode
}) {
  return (
    <div className="empty-state">
      <div className="empty-icon"><Icon size={25} /></div>
      <h3>{title}</h3>
      <p>{description}</p>
      {action && <div className="empty-action">{action}</div>}
    </div>
  )
}

function LoadingState({ label = '正在读取…' }: { label?: string }) {
  return (
    <div className="loading-state">
      <LoaderCircle className="spin" size={19} />
      <span>{label}</span>
    </div>
  )
}

function Field({ label, hint, children, wide }: { label: string; hint?: string; children: ReactNode; wide?: boolean }) {
  return (
    <label className={cx('field', wide && 'field-wide')}>
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  )
}

function SectionHeading({ icon: Icon, title, description, action }: { icon: LucideIcon; title: string; description?: string; action?: ReactNode }) {
  return (
    <div className="section-heading">
      <div className="section-icon"><Icon size={18} /></div>
      <div className="section-copy">
        <h2>{title}</h2>
        {description && <p>{description}</p>}
      </div>
      {action && <div className="section-action">{action}</div>}
    </div>
  )
}

function ConnectionBanner({ state, message, onRetry }: { state: ConnectionState; message?: string; onRetry: () => void }) {
  if (state !== 'offline') return null
  return (
    <div className="connection-banner" role="alert">
      <CloudOff size={17} />
      <span><strong>分析服务未连接。</strong> 暂时无法开始分析或读取结果；系统不会用模拟数据代替真实运行。{message ? ` ${message}` : ''}</span>
      <button className="button button-small button-ghost" onClick={onRetry}><RefreshCw size={14} />重试</button>
    </div>
  )
}

function DevelopmentDemoBanner({ bypassedControls = [] }: { bypassedControls?: string[] }) {
  return (
    <div className="development-demo-watermark" role="status">
      <Code2 size={20} />
      <div>
        <strong><BilingualCopy zh="研发测试 · 仅限虚构或已脱敏病例 · 不用于临床" en="Development test · Synthetic or de-identified cases only · Not for clinical use" /></strong>
        <span><BilingualCopy zh="本次调用用户选择的模型 Provider；若选择外部 Provider，可能传输文本并产生费用。候选分数只用于排序，不代表实际患病概率。" en="This run uses the selected model provider. External providers may receive text and incur charges. Candidate scores are for ranking only, not calibrated disease probabilities." /></span>
        {bypassedControls.length > 0 && <details><summary>本次记录了 {bypassedControls.length} 项研发提示</summary><small>仅记录、未作为停止条件的工程检查：{bypassedControls.join('、')}</small></details>}
      </div>
    </div>
  )
}

function Sidebar({
  page,
  collapsed,
  mobileOpen,
  connection,
  onNavigate,
  onCollapse,
  onCloseMobile,
}: {
  page: PageId
  collapsed: boolean
  mobileOpen: boolean
  connection: ConnectionState
  onNavigate: (page: PageId) => void
  onCollapse: () => void
  onCloseMobile: () => void
}) {
  return (
    <>
      <div className={cx('mobile-scrim', mobileOpen && 'show')} onClick={onCloseMobile} />
      <aside className={cx('sidebar', collapsed && 'collapsed', mobileOpen && 'mobile-open')}>
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">
            <div className="owl-eye left" />
            <div className="owl-eye right" />
            <div className="owl-beak" />
          </div>
          {!collapsed && (
            <div className="brand-copy">
              <strong>OwlPath<span>｜鸮径</span></strong>
              <small>病原体鉴别分析与模型评估</small>
            </div>
          )}
          <button className="icon-button sidebar-close-mobile" onClick={onCloseMobile} aria-label="关闭导航"><X size={18} /></button>
        </div>

        <nav className="nav-list" aria-label="主导航">
          {SIDEBAR_GROUPS.map((group) => <div className="nav-group" key={group.label}>
            {!collapsed && <span className="nav-group-label">{group.label}</span>}
            {group.items.map((id) => {
              const item = NAV_ITEMS.find((candidate) => candidate.id === id)!
              const Icon = item.icon
              const active = sidebarItemIsActive(page, item.id)
              return (
                <button
                  key={item.id}
                  className={cx('nav-item', active && 'active')}
                  onClick={() => onNavigate(item.id)}
                  title={collapsed ? item.label : undefined}
                  aria-current={active ? 'page' : undefined}
                >
                  <Icon size={19} />
                  {!collapsed && <span><strong>{item.label}</strong><small>{item.caption}</small></span>}
                  {!collapsed && active && <ChevronRight size={15} />}
                </button>
              )
            })}
          </div>)}
        </nav>

        <div className="sidebar-footer">
          {!collapsed && (
            <div className="connection-card">
              <span className={cx('connection-dot', connection)} />
              <div>
                <strong>{connection === 'online' ? '分析服务正常' : connection === 'checking' ? '正在检查服务' : '分析服务未连接'}</strong>
                <small>{connection === 'online' ? '可以启动并查看分析' : '暂时无法启动或读取结果'}</small>
              </div>
            </div>
          )}
          <button className="collapse-button" onClick={onCollapse} aria-label={collapsed ? '展开侧边栏' : '收起侧边栏'}>
            {collapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
            {!collapsed && <span>收起导航</span>}
          </button>
        </div>
      </aside>
    </>
  )
}

function Topbar({
  page,
  theme,
  connection,
  activeRun,
  onTheme,
  onMenu,
  onNavigate,
}: {
  page: PageId
  theme: Theme
  connection: ConnectionState
  activeRun?: RunDetail
  onTheme: () => void
  onMenu: () => void
  onNavigate: (page: PageId) => void
}) {
  const current = NAV_ITEMS.find((item) => item.id === page)
  const activeRunPhase = activeRun ? clinicalRunPhase(activeRun.currentStage) : undefined
  return (
    <header className="topbar">
      <div className="topbar-left">
        <button className="icon-button mobile-menu" onClick={onMenu} aria-label="打开导航"><Menu size={20} /></button>
        <div>
          <span className="topbar-kicker">鸮径 OwlPath · 病原体分析工作台</span>
          <strong>{current?.label}</strong>
        </div>
      </div>
      <div className="topbar-actions">
        {activeRun && (activeRun.status === 'queued' || activeRun.status === 'running') && (
          <button className={cx('run-chip', activeRun.runMode === 'development_demo' && 'demo-run-chip')} onClick={() => onNavigate('run')}>
            <LoaderCircle className="spin" size={15} />
            <span>{activeRun.runMode === 'development_demo' ? '研发测试 · ' : ''}{activeRun.progress}% · {activeRunPhase?.primary || '正在分析'}</span>
          </button>
        )}
        <StatusPill
          tone={connection === 'online' ? 'success' : connection === 'offline' ? 'danger' : 'neutral'}
          icon={connection === 'online' ? Wifi : connection === 'offline' ? CloudOff : LoaderCircle}
        >
          {connection === 'online' ? '已连接' : connection === 'offline' ? '未连接' : '检查中'}
        </StatusPill>
        <button className="icon-button" onClick={onTheme} aria-label="切换深浅色">
          {theme === 'light' ? <Moon size={18} /> : <Sun size={18} />}
        </button>
      </div>
    </header>
  )
}

function CaseWorkbench({
  providers,
  connection,
  onStart,
  onStartDevelopmentDemo,
  onManageModels,
}: {
  providers: ProviderSummary[]
  connection: ConnectionState
  onManageModels: () => void
  onStart: (
    draft: CaseDraft,
    externalProviders: ProviderSummary[],
    review: {
      parserVersion: string
      sourceTextSha256: string
      confirmedAt: string
      transferConfirmedAt?: string
    },
  ) => Promise<void>
  onStartDevelopmentDemo: (text: string, providers: ProviderSummary[]) => Promise<void>
}) {
  const [workbenchMode, setWorkbenchMode] = useState<WorkbenchMode>('development_demo')
  const [draft, setDraft] = useState<CaseDraft>(() => createEmptyCase())
  const [clinicalText, setClinicalText] = useState('')
  const [organizedSource, setOrganizedSource] = useState('')
  const [organizedDecisionTime, setOrganizedDecisionTime] = useState('')
  const [organization, setOrganization] = useState<ClinicalTextOrganization>()
  const [modelFactPreview, setModelFactPreview] = useState<ClinicalTextOrganization['modelFactPreview']>([])
  const [factPreviewDraftFingerprint, setFactPreviewDraftFingerprint] = useState('')
  const [refreshingFactPreview, setRefreshingFactPreview] = useState(false)
  const [organizationMessage, setOrganizationMessage] = useState<{ tone: 'success' | 'warning' | 'danger'; text: string }>()
  const [organizationReviewed, setOrganizationReviewed] = useState(false)
  const [reviewedDraftFingerprint, setReviewedDraftFingerprint] = useState('')
  const [reviewConfirmedAt, setReviewConfirmedAt] = useState('')
  const [organizing, setOrganizing] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [demoSubmitting, setDemoSubmitting] = useState(false)
  const [demoError, setDemoError] = useState('')
  const [error, setError] = useState('')
  const [cloudConsent, setCloudConsent] = useState(false)
  const [cloudConsentFingerprint, setCloudConsentFingerprint] = useState('')
  const [cloudConsentProviderBinding, setCloudConsentProviderBinding] = useState('')
  const [cloudConsentConfirmedAt, setCloudConsentConfirmedAt] = useState('')
  const previewIsStale = Boolean(organization && (organizedSource !== clinicalText || organizedDecisionTime !== draft.decisionTime))
  const currentDraftFingerprint = useMemo(() => clinicalDraftFingerprint(draft), [draft])
  const factPreviewIsCurrent = Boolean(
    modelFactPreview.length > 0
    && factPreviewDraftFingerprint
    && factPreviewDraftFingerprint === currentDraftFingerprint,
  )
  const reviewValid = Boolean(
    organizationReviewed
    && reviewConfirmedAt
    && reviewedDraftFingerprint === currentDraftFingerprint
    && !previewIsStale
    && factPreviewIsCurrent,
  )
  const blockingOrganizationWarnings = useMemo(
    () => organization?.warnings.filter((item) => isBlockingOrganizationWarning(item.code)) ?? [],
    [organization],
  )

  const readyProviders = useMemo(() => {
    return providers.filter((provider) => provider.enabled && (provider.kind === 'baseline' || provider.health === 'ready'))
  }, [providers])

  const demoCloudProviders = useMemo(() => {
    const modelProviders = readyProviders.filter((provider) => provider.kind !== 'baseline')
    const explicitlySelected = modelProviders.filter((provider) => draft.selectedProviders.includes(provider.id))
    return explicitlySelected.length ? explicitlySelected : modelProviders
  }, [draft.selectedProviders, readyProviders])
  const demoCloudProvider = demoCloudProviders[0]
  const demoUsesExternalProvider = demoCloudProviders.some((provider) => provider.dataBoundary === 'external')

  const selectedCloudProviders = useMemo(
    () => readyProviders.filter((provider) => draft.selectedProviders.includes(provider.id) && provider.dataBoundary === 'external'),
    [draft.selectedProviders, readyProviders],
  )
  const selectedCloudProviderBinding = useMemo(
    () => JSON.stringify(selectedCloudProviders.map((provider) => ({
      id: provider.id,
      kind: provider.kind,
      model: provider.model || '',
      baseUrl: provider.baseUrl || '',
      dataBoundary: provider.dataBoundary,
    })).sort((a, b) => a.id.localeCompare(b.id))),
    [selectedCloudProviders],
  )
  const cloudConsentValid = Boolean(
    cloudConsent
    && cloudConsentConfirmedAt
    && cloudConsentFingerprint === currentDraftFingerprint
    && cloudConsentProviderBinding === selectedCloudProviderBinding,
  )

  useEffect(() => {
    setDraft((current) => ({
      ...current,
      selectedProviders: current.selectedProviders.filter((item) => readyProviders.some((provider) => provider.id === item)).length
        ? current.selectedProviders.filter((item) => readyProviders.some((provider) => provider.id === item))
        : readyProviders.filter((provider) => provider.kind === 'baseline').slice(0, 1).map((provider) => provider.id),
    }))
  }, [readyProviders])

  useEffect(() => {
    setCloudConsent(false)
    setCloudConsentFingerprint('')
    setCloudConsentProviderBinding('')
    setCloudConsentConfirmedAt('')
  }, [selectedCloudProviderBinding])

  const excludedCount = useMemo(() => {
    const cutoff = new Date(draft.decisionTime).getTime()
    if (Number.isNaN(cutoff)) return 0
    const labCount = draft.labs.filter((item) => item.availableAt && new Date(item.availableAt).getTime() > cutoff).length
    const timelineCount = draft.timeline.filter((item) => item.availableAt && new Date(item.availableAt).getTime() > cutoff).length
    const imagingCount = draft.imaging.availableAt && new Date(draft.imaging.availableAt).getTime() > cutoff ? 1 : 0
    return labCount + timelineCount + imagingCount
  }, [draft])

  const updateDecisionTime = (value: string) => {
    setDraft((current) => ({ ...current, decisionTime: value }))
    if (organization && value !== organizedDecisionTime) {
      setOrganizationReviewed(false)
      setOrganizationMessage({ tone: 'warning', text: '决策时点已修改，时间相关的结构化预览与医生核对确认均已失效；请重新整理。' })
    }
  }

  const updateVital = (id: string, patch: Partial<VitalEntry>) => {
    setDraft((current) => ({ ...current, vitals: current.vitals.map((item) => item.id === id ? { ...item, ...patch, ...('measuredAt' in patch ? { timeUncertain: false } : {}) } : item) }))
  }
  const updateLab = (id: string, patch: Partial<LabEntry>) => {
    setDraft((current) => ({ ...current, labs: current.labs.map((item) => item.id === id ? { ...item, ...patch, ...(('sampledAt' in patch || 'availableAt' in patch) ? { timeUncertain: false } : {}) } : item) }))
  }
  const updateTimeline = (id: string, patch: Partial<TimelineEntry>) => {
    setDraft((current) => ({ ...current, timeline: current.timeline.map((item) => item.id === id ? { ...item, ...patch } : item) }))
  }

  const rawOnlyDraft = useCallback((source: string): CaseDraft => ({
    ...createEmptyCase(),
    deidentifiedNote: source,
    decisionTime: draft.decisionTime,
    selectedProviders: draft.selectedProviders,
    history: { ...createEmptyCase().history, presentIllness: '' },
  }), [draft.decisionTime, draft.selectedProviders])

  const applyOrganization = useCallback((result: ClinicalTextOrganization): CaseDraft => ({
    ...draft,
    deidentifiedNote: result.deidentifiedNote,
    scenario: result.scenario,
    acquisitionContext: result.acquisitionContext,
    demographics: { ...draft.demographics, ...result.demographics },
    history: result.history,
    host: result.host,
    vitals: result.vitals,
    labs: result.labs,
    imaging: result.imaging,
  }), [draft])

  const organizeClinicalText = async (): Promise<void> => {
    const source = clinicalText
    if (!source.trim()) {
      setOrganizationMessage({ tone: 'danger', text: '请先粘贴病史和检查结果。' })
      return
    }
    setOrganizing(true); setOrganizationMessage(undefined); setOrganizationReviewed(false)
    setReviewedDraftFingerprint(''); setReviewConfirmedAt(''); setError('')
    try {
      const result = await api.organizeClinicalText(source, draft.decisionTime)
      const next = applyOrganization(result)
      setDraft(next)
      setOrganization(result)
      setModelFactPreview(result.modelFactPreview)
      setFactPreviewDraftFingerprint(clinicalDraftFingerprint(next))
      setOrganizedSource(clinicalText)
      setOrganizedDecisionTime(draft.decisionTime)
      const blockers = result.warnings.filter((item) => isBlockingOrganizationWarning(item.code))
      setOrganizationMessage(blockers.length
        ? { tone: 'danger', text: `整理已完成，但发现 ${blockers.length} 项阻断性风险。请按下方提示修正原文或时间后重新整理；当前禁止启动推演。` }
        : { tone: 'success', text: `已用本地确定性解析器整理；请核对预览${result.warnings.length ? `，有 ${result.warnings.length} 项需注意` : ''}，然后显式确认“我已核对整理结果”。` })
    } catch (err) {
      const next = rawOnlyDraft(source)
      setDraft(next)
      setOrganization(undefined)
      setModelFactPreview([])
      setFactPreviewDraftFingerprint('')
      setOrganizedSource(clinicalText)
      setOrganizedDecisionTime(draft.decisionTime)
      setOrganizationMessage({ tone: 'danger', text: `自动整理失败：${apiErrorMessage(err)}。完整原文已保留，但在成功整理并由医生核对前不能启动推演。` })
    } finally {
      setOrganizing(false)
    }
  }

  const refreshModelFactPreview = async () => {
    setRefreshingFactPreview(true); setError('')
    try {
      const preview = await api.previewClinicalFacts(draft)
      setModelFactPreview(preview)
      setFactPreviewDraftFingerprint(currentDraftFingerprint)
      setOrganizationReviewed(false)
      setReviewedDraftFingerprint('')
      setReviewConfirmedAt('')
    } catch (err) {
      setError(`无法刷新模型事实预览：${apiErrorMessage(err)}`)
    } finally {
      setRefreshingFactPreview(false)
    }
  }

  const resetCase = () => {
    setDraft(createEmptyCase())
    setClinicalText('')
    setOrganizedSource('')
    setOrganizedDecisionTime('')
    setOrganization(undefined)
    setModelFactPreview([])
    setFactPreviewDraftFingerprint('')
    setOrganizationMessage(undefined)
    setOrganizationReviewed(false)
    setReviewedDraftFingerprint('')
    setReviewConfirmedAt('')
    setCloudConsent(false)
    setCloudConsentFingerprint('')
    setCloudConsentProviderBinding('')
    setCloudConsentConfirmedAt('')
    setError('')
  }

  const start = async () => {
    setError('')
    if (!draft.decisionTime) return setError('请设置当前决策时点 t。')
    const decisionMillis = new Date(draft.decisionTime).getTime()
    const driftMillis = Date.now() - decisionMillis
    if (Number.isNaN(decisionMillis) || driftMillis > 15 * 60_000 || driftMillis < -60_000) {
      return setError('实时临床运行的决策时点必须接近当前服务器时间（过去15分钟至未来1分钟）。请点击“设为现在”，重新整理并核对。')
    }
    if (!clinicalText.trim() && !draft.history.chiefComplaint.trim() && !draft.history.presentIllness.trim()) return setError('请粘贴病史和检查结果。')
    if (!organization) return setError('请先点击“自动整理并生成预览”。整理与启动是两个独立步骤，系统不会在启动时自动整理。')
    if (previewIsStale) return setError('原文或决策时点已变化，整理结果已失效。请重新整理并再次核对。')
    if (blockingOrganizationWarnings.length > 0) return setError(`存在阻断性安全风险，无法启动：${blockingOrganizationWarnings.map((item) => item.message).join('；')}`)
    if (!reviewValid) return setError('结构化内容在上次确认后发生变化。请重新核对并再次勾选“我已核对整理结果”。')
    if (!draft.selectedProviders.length) return setError('请至少选择一个模型。')
    if (selectedCloudProviders.length > 0 && !cloudConsentValid) return setError('病例内容或云模型选择在授权后发生变化。请重新确认本次外部传输。')
    if (connection !== 'online') return setError('后端未连接，无法启动真实推演。')
    setSubmitting(true)
    try {
      await onStart(
        draft,
        selectedCloudProviders,
        {
          parserVersion: organization.parserVersion,
          sourceTextSha256: organization.sourceTextSha256,
          confirmedAt: reviewConfirmedAt,
          transferConfirmedAt: cloudConsentConfirmedAt || undefined,
        },
      )
    } catch (err) {
      setError(apiErrorMessage(err))
    } finally {
      setSubmitting(false)
    }
  }

  const startDevelopmentDemo = async () => {
    setDemoError('')
    if (!clinicalText.trim()) return setDemoError('请先粘贴一段纯虚构或已脱敏的开发测试文本。')
    if (!demoCloudProvider) return setDemoError('尚无已启用且连接测试通过的模型。请先到“模型设置”完成连接测试并启用。')
    if (connection !== 'online') return setDemoError('分析服务未连接，暂时无法开始。请检查服务后重试。')
    setDemoSubmitting(true)
    try {
      await onStartDevelopmentDemo(clinicalText, demoCloudProviders)
    } catch (err) {
      setDemoError(apiErrorMessage(err))
    } finally {
      setDemoSubmitting(false)
    }
  }

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow={workbenchMode === 'development_demo' ? '病例输入 · 研发测试' : '病例资料快照'}
        title={workbenchMode === 'development_demo' ? '病原体鉴别分析' : '病例工作台'}
        description={workbenchMode === 'development_demo'
          ? '系统固定召集感染科、重症/急诊、临床流行病学、检验医学和临床微生物/培养实验室五个核心专家，再根据病例最多选择六个动态专科。全部已运行意见经证据核验、总诊和独立反证后生成前5位具体病原体。'
          : '为本次运行保存一个不可变的当前证据快照；每当有新证据返回，都可在新的决策时点 t 再次运行。'}
        actions={workbenchMode === 'strict_clinical' ? (
          <div className="header-button-row">
            <button className="button button-secondary" onClick={resetCase}><RotateCcw size={16} />新建空白病例</button>
            <button className="button button-primary" onClick={start} disabled={submitting || connection !== 'online'}>
              {submitting ? <LoaderCircle className="spin" size={16} /> : <Play size={16} />}
              {submitting ? '正在提交' : '启动推演'}
            </button>
          </div>
        ) : undefined}
      />

      <Card className="workflow-mode-card">
        <div>
          <span className="eyebrow">工作模式</span>
          <strong>{workbenchMode === 'development_demo' ? '研发测试模式（默认，不用于临床）' : '受控临床验证模式'}</strong>
          <small>{workbenchMode === 'development_demo'
            ? '系统会完整读取粘贴的病例；若使用云端模型，全文将发送给该模型服务。本地整理只用于辅助索引。'
            : '保留自动整理、医生复核、时间闸门和云传输确认。'}</small>
        </div>
        <div className="workflow-mode-switch" role="group" aria-label="工作模式">
          <button
            type="button"
            className={cx('mode-option', workbenchMode === 'development_demo' && 'active')}
            aria-pressed={workbenchMode === 'development_demo'}
            onClick={() => { setWorkbenchMode('development_demo'); setError('') }}
          >
            <Code2 size={16} />研发测试
          </button>
          {ENABLE_STRICT_CLINICAL_UI && <button
            type="button"
            className={cx('mode-option', workbenchMode === 'strict_clinical' && 'active')}
            aria-pressed={workbenchMode === 'strict_clinical'}
            onClick={() => { setWorkbenchMode('strict_clinical'); setDemoError('') }}
          >
            <ShieldCheck size={16} />严格临床
          </button>}
        </div>
      </Card>

      {workbenchMode === 'development_demo' ? (
        <InlineNotice tone="info" title="开始前请确认数据用途">候选分数只表示模型排序强弱，不是患病概率。病例全文会发送到所选模型（包括外部云模型）；请仅使用纯虚构或已脱敏测试文本。系统会记录资料问题和模型异常，但在流程仍可继续时不会中断。</InlineNotice>
      ) : (
        <InlineNotice tone="info" title="去标识与用途提醒">
          请勿输入姓名、证件号、电话等直接身份信息。当前工具用于病原体推演和模型比较，不直接产生用药医嘱。
        </InlineNotice>
      )}

      {workbenchMode === 'strict_clinical' && <Card className="cutoff-card" tone="accent">
        <div className="cutoff-main">
          <div className="cutoff-icon"><Clock3 size={22} /></div>
          <div>
            <span className="eyebrow">时间闸门</span>
            <h2>当前决策时点 t · 实时临床模式</h2>
            <p>检查的采样时间可以早于 t，但只有在 t 前已经对医生可见的结果才能进入本次快照。历史回放不能在此伪装成实时运行。</p>
          </div>
        </div>
        <div className="cutoff-control">
          <input type="datetime-local" value={draft.decisionTime} onChange={(event) => updateDecisionTime(event.target.value)} />
          <button className="button button-small button-secondary" type="button" onClick={() => updateDecisionTime(toLocalInputValue())}><Clock3 size={14} />设为现在</button>
          <StatusPill tone={excludedCount ? 'warning' : 'success'} icon={excludedCount ? AlertTriangle : CheckCircle2}>
            {excludedCount ? `${excludedCount} 条信息在当前时点后才可见` : '未发现未来信息'}
          </StatusPill>
        </div>
      </Card>}

      <Card className="free-text-card">
        <SectionHeading
          icon={Sparkles}
          title={workbenchMode === 'development_demo' ? '粘贴病例资料，开始病原体分析' : '一次粘贴全部早期信息'}
          description={workbenchMode === 'development_demo'
            ? '系统会完整阅读原文，不会因自动整理遗漏而丢弃病例信息。本地结构化仅用于索引、时间标记和矛盾检查。'
            : '病史、查体、生命体征、血常规、生化、影像报告等可原样粘贴；无需先拆成多个表单。'}
          action={workbenchMode === 'development_demo'
            ? demoUsesExternalProvider
              ? <StatusPill tone="warning" icon={Wifi}>包含外部模型 Provider（可能传输文本并产生费用）</StatusPill>
              : <StatusPill tone="success" icon={Laptop}>仅调用本地/院内模型 Provider</StatusPill>
            : <StatusPill tone="success" icon={Laptop}>本地确定性整理 · 不需要模型Key</StatusPill>}
        />
        <textarea
          className={cx('clinical-text-input', workbenchMode === 'development_demo' && 'demo-text-input')}
          rows={14}
          value={clinicalText}
          onChange={(event) => {
            setClinicalText(event.target.value)
            if (organizedSource && event.target.value !== organizedSource) {
              setOrganizationReviewed(false)
              setOrganizationMessage({ tone: 'warning', text: '原文已修改，当前结构化预览与医生核对确认均已失效；请重新整理后再核对。' })
            }
          }}
          placeholder={workbenchMode === 'development_demo'
            ? '【仅限纯虚构或已脱敏测试病例】\n建议包括：现病史与病程、基础情况、流行病学与暴露、生命体征、实验室检查、影像、微生物结果（含待回报项目）及当前抗感染治疗。\n\n例：虚构成年人，发热、咳嗽2天，体温 38.7℃，SpO₂ 92%；WBC 14.2×10^9/L，CRP 112 mg/L；胸部CT提示右下肺实变。'
            : '直接粘贴去标识化的早期临床资料，例如：\n68岁男性，院外起病，发热、咳嗽2天，急诊就诊。无免疫抑制，无禽类接触史。\n体温 38.7℃，SpO₂ 92%。\n血常规：WBC 14.2×10^9/L，中性粒细胞 86%；CRP 112 mg/L。\n胸部CT：右下肺实变。\n已使用头孢曲松1次。\n\n如知道采样时间、报告可见时间，请一并粘贴；不知道就明确写“时间不详”。'}
          aria-label={workbenchMode === 'development_demo' ? '纯虚构或已脱敏开发测试文本' : '去标识化临床资料'}
        />
        <div className="clinical-text-actions">
          {workbenchMode === 'development_demo' ? (
            <>
              <div><strong>病例全文将发送给下方所选的模型 Provider</strong><span>{demoUsesExternalProvider ? '其中包含外部 Provider，可能发生文本外传和费用；仅粘贴纯虚构或已脱敏测试文本。' : '当前均为本地/院内 Provider；仍仅限纯虚构或已脱敏测试文本。'}</span></div>
              <button className="button button-primary demo-run-button" onClick={() => void startDevelopmentDemo()} disabled={demoSubmitting || !clinicalText.trim() || connection !== 'online' || !demoCloudProvider}>
                {demoSubmitting ? <LoaderCircle className="spin" size={16} /> : <Play size={16} />}
                {connection !== 'online' ? '等待分析服务连接' : demoSubmitting ? '正在启动分析' : '开始病原体分析'}
              </button>
            </>
          ) : (
            <>
              <div><strong>原文不会被摘要替代</strong><span>未识别内容仍完整保存在病史事件中；解析结果只用于辅助结构化。</span></div>
              <button className="button button-primary" onClick={() => void organizeClinicalText()} disabled={organizing || !clinicalText.trim() || connection !== 'online'}>
                {organizing ? <LoaderCircle className="spin" size={16} /> : <SearchCheck size={16} />}
                {organizing ? '正在整理' : '自动整理并生成预览'}
              </button>
            </>
          )}
        </div>
        {workbenchMode === 'development_demo' && (
          <div className="demo-provider-strip">
            <div>
              <Wifi size={17} />
              <span>
                <strong>{demoCloudProvider ? `本次将调用 ${demoCloudProviders.length} 个已通过连接测试的模型` : '没有可用的模型'}</strong>
                <small>{demoCloudProvider ? demoCloudProviders.map(providerClinicalLabel).join('；') : '请先完成模型配置、连接测试并启用；本地基线不会被选中。'}</small>
              </span>
            </div>
            <button className="button button-small button-secondary" type="button" onClick={onManageModels}><Settings2 size={14} />选择和管理模型</button>
          </div>
        )}
        {workbenchMode === 'development_demo' && demoError && <InlineNotice tone="danger">{demoError}</InlineNotice>}
        {workbenchMode === 'strict_clinical' && organizationMessage && <InlineNotice tone={organizationMessage.tone}>{organizationMessage.text}</InlineNotice>}
      </Card>

      {workbenchMode === 'strict_clinical' && organization && (
        <Card className={cx('organization-preview', previewIsStale && 'preview-stale')} tone="accent">
          <SectionHeading
            icon={ClipboardCheck}
            title="结构化预览 · 请由医生核对"
            description={`解析器 ${organization.parserVersion} · 原文指纹 ${organization.sourceTextSha256.slice(0, 12)}`}
            action={<StatusPill tone={previewIsStale ? 'warning' : 'success'}>{previewIsStale ? '预览已过期' : '与当前原文和时点一致'}</StatusPill>}
          />
          <div className="preview-summary-grid">
            <div><span>综合征</span><strong>{scenarioLabel(organization.scenario)}</strong></div>
            <div><span>起病场景</span><strong>{{ community: '社区/院外起病', healthcare_associated: '医疗相关', hospital_acquired: '医院获得', unknown: '未确认' }[organization.acquisitionContext]}</strong></div>
            <div><span>基本信息</span><strong>{organization.demographics.age !== undefined ? `${organization.demographics.age}岁` : '年龄未识别'} · {{ male: '男', female: '女', other: '其他', unknown: '性别未知' }[organization.demographics.sex || 'unknown']}</strong></div>
            <div className={cx((organization.demographics.pregnant === true || organization.demographics.immunocompromised === true) && 'preview-exclusion')}><span>排除人群核查</span><strong>妊娠：{triStateLabel(organization.demographics.pregnant)} · 免疫抑制：{triStateLabel(organization.demographics.immunocompromised)}</strong></div>
            <div><span>生命体征</span><strong>{organization.vitals.length} 项</strong></div>
            <div><span>检验结果</span><strong>{organization.labs.length} 项</strong></div>
            <div><span>影像</span><strong>{organization.imaging.report ? organization.imaging.modality || '已识别' : '未识别'}</strong></div>
          </div>

          <div className="preview-content-grid">
            <div className="preview-block"><strong>病史与暴露</strong><p>{[organization.history.chiefComplaint, organization.history.presentIllness, organization.history.exposureHistory, organization.history.epidemiology].filter(Boolean).join('；') || '未识别明确段落；完整原文仍保留。'}</p></div>
            <div className="preview-block"><strong>宿主与治疗信息</strong><p>{[organization.host.comorbidities, organization.host.immuneStatus, organization.host.devicesAndProcedures, organization.history.priorAntimicrobials].filter(Boolean).join('；') || '未识别明确内容。'}</p></div>
          </div>

          {(organization.vitals.length > 0 || organization.labs.length > 0) && (
            <div className="preview-observations">
              {[...organization.vitals.map((item) => ({ id: item.id, kind: '生命体征', name: item.name, value: `${item.value} ${item.unit}`.trim(), uncertain: item.timeUncertain })), ...organization.labs.map((item) => ({ id: item.id, kind: '检验', name: item.name, value: `${item.value} ${item.unit}`.trim(), uncertain: item.timeUncertain }))].map((item) => (
                <div key={`${item.kind}-${item.id}`}><span>{item.kind}</span><strong>{item.name}</strong><b>{item.value}</b><StatusPill tone={item.uncertain ? 'warning' : 'success'}>{item.uncertain ? '时间待核对' : '时间已识别'}</StatusPill></div>
              ))}
            </div>
          )}

          <div className="atomic-fact-review">
            <SectionHeading
              icon={ShieldCheck}
              title="最终进入模型的原子事实"
              description="云端与本地模型只会收到下列受控代码和数值；完整原文、姓名样式文本、影像自由文本均不跨越模型边界。"
              action={(
                <button className="button button-small button-secondary" onClick={() => void refreshModelFactPreview()} disabled={refreshingFactPreview}>
                  {refreshingFactPreview ? <LoaderCircle className="spin" size={14} /> : <RotateCcw size={14} />}
                  {refreshingFactPreview ? '刷新中' : '按当前校对刷新'}
                </button>
              )}
            />
            {!factPreviewIsCurrent && <InlineNotice tone="warning" title="原子事实预览已失效">结构化字段已发生变化。请刷新本区域并重新核对，之后才能再次确认。</InlineNotice>}
            {modelFactPreview.length ? (
              <div className="atomic-fact-list">
                {modelFactPreview.map((item, index) => (
                  <div key={`${item.eventIndex}-${item.kind}-${index}`}>
                    <span>事件 {item.eventIndex + 1} · {item.kind}</span>
                    <strong>{atomicFactSummary(item)}</strong>
                    <small>发生 {formatDate(item.occurredAt)} · 可见 {formatDate(item.visibleAt)} · 状态 {item.status}</small>
                    <details className="atomic-json-details">
                      <summary>展开核对规范化临床字段（事件ID与序号在提交后生成）</summary>
                      <pre>{JSON.stringify({
                        kind: item.kind,
                        occurred_at: item.occurredAt,
                        collected_at: item.collectedAt || null,
                        issued_at: item.issuedAt || null,
                        visible_at: item.visibleAt,
                        source: item.source,
                        status: item.status,
                        data: item.data,
                        quality: item.quality,
                      }, null, 2)}</pre>
                    </details>
                  </div>
                ))}
              </div>
            ) : <InlineNotice tone="danger">当前没有可安全发送的原子事实；请补充或校对早期信息。</InlineNotice>}
          </div>

          {organization.warnings.length > 0 && <InlineNotice tone={blockingOrganizationWarnings.length ? 'danger' : 'warning'} title={blockingOrganizationWarnings.length ? '安全风险 · 已阻止运行' : '需要人工核对'}>{organization.warnings.map((item) => item.message).join('；')}</InlineNotice>}
          {(organization.demographics.pregnant === true || organization.demographics.immunocompromised === true) && <InlineNotice tone="danger" title="超出当前验证范围">当前默认治理范围为成人呼吸道，妊娠与免疫抑制人群均属于排除人群。该标记将随病例提交，由后端安全裁决器按治理规则降级或弃答。</InlineNotice>}
          {organization.unrecognized.length > 0 && <div className="unrecognized-box"><div><AlertTriangle size={16} /><strong>未结构化识别，但原文已保留</strong></div><p>{organization.unrecognized.join('；')}</p></div>}
          <label className={cx('organization-review', reviewValid && 'confirmed', (previewIsStale || !factPreviewIsCurrent || blockingOrganizationWarnings.length > 0) && 'disabled')}>
            <input
              type="checkbox"
              checked={reviewValid}
              disabled={previewIsStale || !factPreviewIsCurrent || blockingOrganizationWarnings.length > 0}
              onChange={(event) => {
                setOrganizationReviewed(event.target.checked)
                setReviewedDraftFingerprint(event.target.checked ? currentDraftFingerprint : '')
                setReviewConfirmedAt(event.target.checked ? new Date().toISOString() : '')
                setError('')
              }}
            />
            <span className="check-mark">{reviewValid && <Check size={13} />}</span>
            <span>
              <strong>我已核对整理结果</strong>
              <small>{blockingOrganizationWarnings.length
                ? '请先处理上方阻断性风险并重新整理。'
                : previewIsStale
                  ? '原文或决策时点已变化，请重新整理后再确认。'
                  : reviewValid
                    ? '已绑定当前结构化内容；任何字段或决策时点变化都会自动使本确认失效。'
                    : '请核对当前结构化字段、未识别内容和所有提醒；确认会绑定当前内容指纹与时间。'}</small>
            </span>
          </label>
        </Card>
      )}

      {workbenchMode === 'strict_clinical' && <div className="content-grid two-one">
        <details className="advanced-case-details">
          <summary><span><Settings2 size={17} /><strong>展开结构化校对（可选）</strong><small>自动整理后如需修正字段或补充精确时间，可在这里人工校对。</small></span><ChevronDown size={18} /></summary>
          <div className="page-stack compact advanced-case-body">
          <Card>
            <SectionHeading icon={Stethoscope} title="就诊场景与基本信息" description="仅填写影响适用范围的去标识特征。" />
            <div className="form-grid four">
              <Field label="感染综合征">
                <select value={draft.scenario} onChange={(event) => setDraft({ ...draft, scenario: event.target.value as CaseDraft['scenario'] })}>
                  <option value="lower_respiratory">疑似下呼吸道感染</option>
                  <option value="bloodstream">疑似血流感染</option>
                  <option value="urinary">疑似尿路感染</option>
                  <option value="cns">疑似中枢神经系统感染</option>
                  <option value="abdominal">疑似腹腔感染</option>
                  <option value="undifferentiated">未分化感染综合征</option>
                </select>
              </Field>
              <Field label="起病场景">
                <select value={draft.acquisitionContext} onChange={(event) => setDraft({ ...draft, acquisitionContext: event.target.value as CaseDraft['acquisitionContext'] })}>
                  <option value="unknown">未确认</option><option value="community">社区/院外起病</option><option value="healthcare_associated">医疗相关</option><option value="hospital_acquired">医院获得</option>
                </select>
              </Field>
              <Field label="年龄">
                <input type="number" min="0" max="120" value={draft.demographics.age ?? ''} onChange={(event) => setDraft({ ...draft, demographics: { ...draft.demographics, age: event.target.value ? Number(event.target.value) : undefined } })} placeholder="岁" />
              </Field>
              <Field label="生理性别">
                <select value={draft.demographics.sex ?? 'unknown'} onChange={(event) => setDraft({ ...draft, demographics: { ...draft.demographics, sex: event.target.value as NonNullable<CaseDraft['demographics']['sex']> } })}>
                  <option value="unknown">未知</option><option value="male">男</option><option value="female">女</option><option value="other">其他</option>
                </select>
              </Field>
              <Field label="是否妊娠">
                <select value={draft.demographics.pregnant === undefined ? 'unknown' : String(draft.demographics.pregnant)} onChange={(event) => setDraft({ ...draft, demographics: { ...draft.demographics, pregnant: event.target.value === 'unknown' ? undefined : event.target.value === 'true' } })}>
                  <option value="unknown">未知</option><option value="false">否</option><option value="true">是（排除人群）</option>
                </select>
              </Field>
              <Field label="是否免疫抑制">
                <select value={draft.demographics.immunocompromised === undefined ? 'unknown' : String(draft.demographics.immunocompromised)} onChange={(event) => setDraft({ ...draft, demographics: { ...draft.demographics, immunocompromised: event.target.value === 'unknown' ? undefined : event.target.value === 'true' } })}>
                  <option value="unknown">未知</option><option value="false">否</option><option value="true">是（排除人群）</option>
                </select>
              </Field>
              <Field label="就诊类型">
                <select value={draft.demographics.encounterType ?? 'emergency'} onChange={(event) => setDraft({ ...draft, demographics: { ...draft.demographics, encounterType: event.target.value as NonNullable<CaseDraft['demographics']['encounterType']> } })}>
                  <option value="emergency">急诊</option><option value="inpatient">住院</option><option value="icu">ICU</option><option value="outpatient">门诊</option>
                </select>
              </Field>
              <Field label="科室" wide><input value={draft.demographics.department ?? ''} onChange={(event) => setDraft({ ...draft, demographics: { ...draft.demographics, department: event.target.value } })} placeholder="例：急诊内科" /></Field>
            </div>
          </Card>

          <Card>
            <SectionHeading icon={BookOpenCheck} title="病史与暴露" description="保留时间、否定和不确定性；“未询问”不等于“无暴露”。" />
            <div className="form-grid two">
              <Field label="主诉"><textarea rows={3} value={draft.history.chiefComplaint} onChange={(event) => setDraft({ ...draft, history: { ...draft.history, chiefComplaint: event.target.value } })} placeholder="主要症状、持续时间" /></Field>
              <Field label="现病史"><textarea rows={3} value={draft.history.presentIllness} onChange={(event) => setDraft({ ...draft, history: { ...draft.history, presentIllness: event.target.value } })} placeholder="起病方式、进展、相关症状" /></Field>
              <Field label="暴露史"><textarea rows={3} value={draft.history.exposureHistory} onChange={(event) => setDraft({ ...draft, history: { ...draft.history, exposureHistory: event.target.value } })} placeholder="旅行、动物、食物、职业、聚集性接触" /></Field>
              <Field label="流行病学背景"><textarea rows={3} value={draft.history.epidemiology} onChange={(event) => setDraft({ ...draft, history: { ...draft.history, epidemiology: event.target.value } })} placeholder="地区、季节、院内/社区获得" /></Field>
              <Field label="当前决策时点前的抗微生物药暴露" wide><textarea rows={2} value={draft.history.priorAntimicrobials} onChange={(event) => setDraft({ ...draft, history: { ...draft.history, priorAntimicrobials: event.target.value } })} placeholder="药物、剂量、首次用药时间；如不清楚请明确写未知" /></Field>
            </div>
          </Card>

          <Card>
            <SectionHeading
              icon={Activity}
              title="生命体征"
              description="按测量时间记录，可保留多个时点。"
              action={<button className="button button-small button-secondary" onClick={() => setDraft({ ...draft, vitals: [...draft.vitals, { id: newId('vital'), measuredAt: draft.decisionTime, name: '', value: '', unit: '' }] })}><Plus size={14} />添加</button>}
            />
            {draft.vitals.length === 0 ? (
              <EmptyState icon={Activity} title="尚未添加生命体征" description="可添加体温、心率、呼吸、血压、SpO₂等。" />
            ) : (
              <div className="editable-table">
                <div className="editable-row editable-head"><span>测量时间</span><span>指标</span><span>数值</span><span>单位</span><span /></div>
                {draft.vitals.map((item) => (
                  <div className={cx('editable-row', item.timeUncertain && 'time-uncertain')} key={item.id}>
                    <div className="input-with-flag"><input type="datetime-local" value={item.measuredAt} onChange={(event) => updateVital(item.id, { measuredAt: event.target.value })} />{item.timeUncertain && <span title="原文未提供精确时间；当前值仅是可见时间上界，请人工核对"><Clock3 size={14} /></span>}</div>
                    <input value={item.name} onChange={(event) => updateVital(item.id, { name: event.target.value })} placeholder="体温 / SpO₂" />
                    <input value={item.value} onChange={(event) => updateVital(item.id, { value: event.target.value })} placeholder="数值" />
                    <input value={item.unit} onChange={(event) => updateVital(item.id, { unit: event.target.value })} placeholder="℃ / %" />
                    <button className="icon-button danger" onClick={() => setDraft({ ...draft, vitals: draft.vitals.filter((row) => row.id !== item.id) })} aria-label="删除"><Trash2 size={15} /></button>
                  </div>
                ))}
              </div>
            )}
          </Card>

          <Card>
            <SectionHeading
              icon={TestTube2}
              title="血液检查"
              description="分别记录采样时间和结果可见时间。"
              action={<button className="button button-small button-secondary" onClick={() => setDraft({ ...draft, labs: [...draft.labs, { id: newId('lab'), sampledAt: draft.decisionTime, availableAt: draft.decisionTime, name: '', value: '', unit: '', abnormal: 'unknown' }] })}><Plus size={14} />添加</button>}
            />
            {draft.labs.length === 0 ? (
              <EmptyState icon={TestTube2} title="尚未添加血液结果" description="可添加血常规、CRP、PCT、肝肾功、凝血等当前已可见结果。" />
            ) : (
              <div className="editable-table labs-table">
                <div className="editable-row editable-head"><span>采样时间</span><span>可见时间</span><span>项目</span><span>结果</span><span>单位</span><span /></div>
                {draft.labs.map((item) => {
                  const afterDecisionTime = item.availableAt && new Date(item.availableAt).getTime() > new Date(draft.decisionTime).getTime()
                  return (
                    <div className={cx('editable-row', afterDecisionTime && 'after-cutoff', item.timeUncertain && 'time-uncertain')} key={item.id}>
                      <input type="datetime-local" value={item.sampledAt} onChange={(event) => updateLab(item.id, { sampledAt: event.target.value })} />
                      <div className="input-with-flag"><input type="datetime-local" value={item.availableAt} onChange={(event) => updateLab(item.id, { availableAt: event.target.value })} />{(afterDecisionTime || item.timeUncertain) && <span title={afterDecisionTime ? '当前决策时点后才可见，本次快照将排除' : '原文未提供精确时间；当前值仅是可见时间上界，请人工核对'}><Clock3 size={14} /></span>}</div>
                      <input value={item.name} onChange={(event) => updateLab(item.id, { name: event.target.value })} placeholder="WBC / CRP" />
                      <input value={item.value} onChange={(event) => updateLab(item.id, { value: event.target.value })} placeholder="数值" />
                      <input value={item.unit} onChange={(event) => updateLab(item.id, { unit: event.target.value })} placeholder="单位" />
                      <button className="icon-button danger" onClick={() => setDraft({ ...draft, labs: draft.labs.filter((row) => row.id !== item.id) })} aria-label="删除"><Trash2 size={15} /></button>
                    </div>
                  )
                })}
              </div>
            )}
          </Card>

          <Card>
            <SectionHeading icon={FileSearch} title="影像学" description="竞赛版优先使用已结构化的影像报告。" />
            <div className="form-grid three">
              <Field label="检查类型"><input value={draft.imaging.modality ?? ''} onChange={(event) => setDraft({ ...draft, imaging: { ...draft.imaging, modality: event.target.value } })} placeholder="胸部CT / 胸片" /></Field>
              <Field label="检查时间"><input type="datetime-local" value={draft.imaging.performedAt ?? ''} onChange={(event) => setDraft({ ...draft, imaging: { ...draft.imaging, performedAt: event.target.value } })} /></Field>
              <Field label="报告可见时间"><input type="datetime-local" value={draft.imaging.availableAt ?? ''} onChange={(event) => setDraft({ ...draft, imaging: { ...draft.imaging, availableAt: event.target.value } })} /></Field>
              <Field label="影像报告" wide><textarea rows={5} value={draft.imaging.report} onChange={(event) => setDraft({ ...draft, imaging: { ...draft.imaging, report: event.target.value } })} placeholder="所见、分布、影像学印象" /></Field>
              {draft.imaging.qualityNote && <Field label="影像时间与质量提醒" wide><textarea rows={2} value={draft.imaging.qualityNote} onChange={(event) => setDraft({ ...draft, imaging: { ...draft.imaging, qualityNote: event.target.value } })} /></Field>}
            </div>
          </Card>

          <Card>
            <SectionHeading
              icon={FileClock}
              title="临床时间事件"
              description="用于复原患者在当前决策时点前真正可见的证据链。"
              action={<button className="button button-small button-secondary" onClick={() => setDraft({ ...draft, timeline: [...draft.timeline, { id: newId('event'), occurredAt: draft.decisionTime, availableAt: draft.decisionTime, kind: 'other', title: '' }] })}><Plus size={14} />添加事件</button>}
            />
            {draft.timeline.length === 0 ? (
              <EmptyState icon={FileClock} title="尚未建立时间轴" description="建议记录起病、就诊、采样、报告返回和用药时间。" />
            ) : (
              <div className="timeline-editor">
                {draft.timeline.map((item) => {
                  const afterDecisionTime = item.availableAt && new Date(item.availableAt).getTime() > new Date(draft.decisionTime).getTime()
                  return (
                    <div className={cx('timeline-edit-item', afterDecisionTime && 'after-cutoff')} key={item.id}>
                      <div className="timeline-dot" />
                      <div className="timeline-fields">
                        <select value={item.kind} onChange={(event) => updateTimeline(item.id, { kind: event.target.value as TimelineEntry['kind'] })}>
                          <option value="symptom">症状</option><option value="exam">查体</option><option value="lab">检验</option><option value="imaging">影像</option><option value="treatment">治疗</option><option value="microbiology">病原学</option><option value="other">其他</option>
                        </select>
                        <input type="datetime-local" value={item.occurredAt} onChange={(event) => updateTimeline(item.id, { occurredAt: event.target.value })} title="发生时间" />
                        <input type="datetime-local" value={item.availableAt} onChange={(event) => updateTimeline(item.id, { availableAt: event.target.value })} title="可见时间" />
                        <input value={item.title} onChange={(event) => updateTimeline(item.id, { title: event.target.value })} placeholder="事件摘要" />
                        <button className="icon-button danger" onClick={() => setDraft({ ...draft, timeline: draft.timeline.filter((row) => row.id !== item.id) })} aria-label="删除"><Trash2 size={15} /></button>
                      </div>
                      {afterDecisionTime && <span className="cutoff-label">当前决策时点后才可见，本次快照排除</span>}
                    </div>
                  )
                })}
              </div>
            )}
          </Card>
          </div>
        </details>

        <aside className="page-stack compact sticky-column">
          <details className="advanced-side-details">
            <summary><UserRoundCheck size={16} /><span><strong>宿主因素校对</strong><small>可选高级字段</small></span><ChevronDown size={16} /></summary>
            <Card>
              <div className="form-grid one">
                <Field label="基础疾病"><textarea rows={3} value={draft.host.comorbidities} onChange={(event) => setDraft({ ...draft, host: { ...draft.host, comorbidities: event.target.value } })} placeholder="慢性肺病、糖尿病、肝肾功能等" /></Field>
                <Field label="免疫状态"><textarea rows={3} value={draft.host.immuneStatus} onChange={(event) => setDraft({ ...draft, host: { ...draft.host, immuneStatus: event.target.value } })} placeholder="移植、粒缺、激素、免疫制剂；无则明确填写" /></Field>
                <Field label="器械与近期操作"><textarea rows={3} value={draft.host.devicesAndProcedures} onChange={(event) => setDraft({ ...draft, host: { ...draft.host, devicesAndProcedures: event.target.value } })} placeholder="中心静脉导管、气管插管、手术等" /></Field>
                <Field label="过敏史"><textarea rows={2} value={draft.host.allergies} onChange={(event) => setDraft({ ...draft, host: { ...draft.host, allergies: event.target.value } })} placeholder="用于报告完整性，不自动生成医嘱" /></Field>
              </div>
            </Card>
          </details>

          <Card>
            <SectionHeading icon={Network} title="本次运行模型" description="启用表示模型可供选择；勾选才表示本次实际调用。" action={<button className="button button-small button-secondary" type="button" onClick={onManageModels}><Settings2 size={14} />管理API</button>} />
            <div className="provider-selection-toolbar">
              <span>已选 {draft.selectedProviders.length} / {readyProviders.length}</span>
              <div>
                <button className="button button-tiny button-secondary" type="button" onClick={() => setDraft({ ...draft, selectedProviders: readyProviders.map((provider) => provider.id) })} disabled={readyProviders.length === 0}>全部比较</button>
                <button className="button button-tiny button-secondary" type="button" onClick={() => setDraft({ ...draft, selectedProviders: readyProviders.filter((provider) => provider.dataBoundary === 'local').map((provider) => provider.id) })} disabled={!readyProviders.some((provider) => provider.dataBoundary === 'local')}>仅本地</button>
                <button className="button button-tiny button-secondary" type="button" onClick={() => setDraft({ ...draft, selectedProviders: readyProviders.filter((provider) => provider.dataBoundary === 'external').map((provider) => provider.id) })} disabled={!readyProviders.some((provider) => provider.dataBoundary === 'external')}>仅云模型</button>
              </div>
            </div>
            <div className="provider-check-list">
              {readyProviders.map((provider) => {
                const checked = draft.selectedProviders.includes(provider.id)
                return (
                  <label className={cx('provider-check', checked && 'checked')} key={provider.id}>
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => setDraft({ ...draft, selectedProviders: checked ? draft.selectedProviders.filter((item) => item !== provider.id) : [...draft.selectedProviders, provider.id] })}
                    />
                    <span className="check-mark">{checked && <Check size={13} />}</span>
                    <span><strong>{provider.name}</strong><small>{provider.model || kindMeta(provider.kind).name} · {provider.dataBoundary === 'external' ? '外部/云端' : '本地'}{provider.health === 'ready' ? ' · 已测试' : ' · 待测试'}</small></span>
                  </label>
                )
              })}
            </div>
            {readyProviders.length === 0 && <p className="empty-inline">后端尚未返回可用模型实例。无Key时请确认后端基线模型已启用。</p>}
            {selectedCloudProviders.length > 0 && (
              <label className={cx('cloud-consent', cloudConsentValid && 'confirmed')}>
                <input
                  type="checkbox"
                  checked={cloudConsentValid}
                  onChange={(event) => {
                    const checked = event.target.checked
                    setCloudConsent(checked)
                    setCloudConsentFingerprint(checked ? currentDraftFingerprint : '')
                    setCloudConsentProviderBinding(checked ? selectedCloudProviderBinding : '')
                    setCloudConsentConfirmedAt(checked ? new Date().toISOString() : '')
                  }}
                />
                <span className="check-mark">{cloudConsentValid && <Check size={13} />}</span>
                <span>
                  <strong>请先去标识化；所选云模型将接收本次病例内容</strong>
                  <small>我已完成去标识化并确认本次内容传输至：{selectedCloudProviders.map(providerTargetLabel).join('；')}。目标或内容变化后必须重新确认。</small>
                </span>
              </label>
            )}
          </Card>

          <Card className="summary-card">
            <SectionHeading icon={SearchCheck} title="当前证据快照摘要" />
            <dl className="summary-list">
              <div><dt>决策时间</dt><dd>{formatDate(draft.decisionTime)}</dd></div>
              <div><dt>生命体征</dt><dd>{draft.vitals.length} 项</dd></div>
              <div><dt>血液检查</dt><dd>{draft.labs.length} 项</dd></div>
              <div><dt>影像报告</dt><dd>{draft.imaging.report.trim() ? '已填写' : '未填写'}</dd></div>
              <div><dt>时点 t 后才可见</dt><dd className={excludedCount ? 'text-warning' : ''}>{excludedCount} 条</dd></div>
              <div><dt>整理结果复核</dt><dd className={reviewValid ? '' : 'text-warning'}>{reviewValid ? '医生已确认并绑定当前内容' : '待确认或已失效'}</dd></div>
              <div><dt>模型</dt><dd>{draft.selectedProviders.length} 个</dd></div>
            </dl>
            {error && <InlineNotice tone="danger">{error}</InlineNotice>}
            <button className="button button-primary button-block" onClick={start} disabled={submitting || connection !== 'online'}>
              {submitting ? <LoaderCircle className="spin" size={16} /> : <Play size={16} />}
              {connection !== 'online' ? '等待后端连接' : submitting ? '正在创建运行' : '启动真实推演'}
            </button>
          </Card>
        </aside>
      </div>}
    </div>
  )
}

function ModelSettings({
  providers,
  loading,
  error,
  connection,
  onRefresh,
  onChanged,
  onDeleted,
}: {
  providers: ProviderSummary[]
  loading: boolean
  error?: string
  connection: ConnectionState
  onRefresh: () => Promise<void>
  onChanged: (summary: ProviderSummary) => void
  onDeleted: (id: ProviderId) => void
}) {
  const [editing, setEditing] = useState<ProviderConfigInput>()
  const [editingDirty, setEditingDirty] = useState(false)
  const [busy, setBusy] = useState<'save' | 'save-test' | 'test' | 'toggle' | 'delete'>()
  const [busyProviderId, setBusyProviderId] = useState<ProviderId>()
  const [message, setMessage] = useState<{ tone: 'success' | 'danger'; text: string }>()

  const beginCreate = (kind: ProviderKind = 'openai_responses') => {
    const meta = kindMeta(kind)
    const count = providers.filter((provider) => provider.kind === kind).length
    setMessage(undefined)
    setEditingDirty(true)
    setEditing({
      kind,
      name: `${meta.name}${count ? ` ${count + 1}` : ''}`,
      enabled: false,
      model: meta.defaultModel || '',
      baseUrl: presetBaseUrl(kind),
      weight: 1,
      dataBoundary: meta.dataBoundary,
      apiKey: '',
      clearApiKey: false,
    })
  }

  const beginEdit = (provider: ProviderSummary) => {
    setMessage(undefined)
    setEditingDirty(false)
    setEditing({
      id: provider.id,
      kind: provider.kind,
      name: provider.name,
      enabled: provider.enabled,
      model: provider.model || '',
      baseUrl: provider.baseUrl || '',
      weight: provider.weight ?? 1,
      dataBoundary: provider.dataBoundary,
      apiKey: '',
      clearApiKey: false,
    })
  }

  const patchEditing = (patch: Partial<ProviderConfigInput>) => {
    setEditingDirty(true)
    const changesConnection = ['model', 'baseUrl', 'dataBoundary', 'apiKey', 'clearApiKey'].some((key) => key in patch)
    setEditing((current) => current ? { ...current, ...patch, ...(changesConnection ? { enabled: false } : {}) } : current)
  }

  const editingValidationError = (): string | undefined => {
    if (!editing) return '没有可保存的配置。'
    if (!editing.name.trim()) return '请填写配置名称。'
    if (editing.kind !== 'baseline' && !editing.model?.trim()) return '请填写账户或本地服务实际可用的模型ID。'
    if (requiresCustomBaseUrl(editing.kind) && !editing.baseUrl?.trim()) return '兼容接口必须填写厂商或院内服务提供的完整 Base URL。'
    if (editing.dataBoundary === 'external' && editing.baseUrl && !editing.baseUrl.toLowerCase().startsWith('https://')) return '外部/云端模型的 Base URL 必须使用 HTTPS；HTTP 仅允许本机或院内私网模型。'
    const saved = editing.id ? providers.find((provider) => provider.id === editing.id) : undefined
    if (requiresCommercialApiKey(editing.kind) && !editing.apiKey?.trim() && !saved?.hasApiKey) return '请输入该厂商的 API Key。Key 只会加密写入本机后端。'
    return undefined
  }

  const save = async () => {
    const validationError = editingValidationError()
    if (validationError || !editing) return setMessage({ tone: 'danger', text: validationError || '没有可保存的配置。' })
    setBusy('save'); setMessage(undefined)
    try {
      const summary = await api.saveProvider({ ...editing, apiKey: editing.apiKey || undefined })
      onChanged(summary)
      setEditingDirty(false)
      setEditing(undefined)
      setMessage({ tone: 'success', text: `“${summary.name}”已保存但尚未验证。可直接点击卡片上的“测试连接”。` })
    } catch (err) {
      setMessage({ tone: 'danger', text: apiErrorMessage(err) })
    } finally {
      setBusy(undefined)
    }
  }

  const testSavedProvider = async (provider: Pick<ProviderSummary, 'id' | 'name'>) => {
    if (!window.confirm('连接测试只发送系统生成的合成数据，不会读取病例工作台；但可能产生一次模型调用费用。是否继续？')) return
    setBusy('test'); setBusyProviderId(provider.id); setMessage(undefined)
    try {
      const result = await api.testProvider(provider.id)
      await onRefresh()
      setMessage(result.ok
        ? { tone: 'success', text: `“${provider.name}”的接口、鉴权与结构化输出测试通过${result.latencyMs !== undefined ? ` · ${result.latencyMs} ms` : ''}。这不代表医学准确性。` }
        : { tone: 'danger', text: `“${provider.name}”测试未通过：${providerTestFailureText(result)}` })
    } catch (err) {
      setMessage({ tone: 'danger', text: apiErrorMessage(err) })
    } finally {
      setBusy(undefined); setBusyProviderId(undefined)
    }
  }

  const saveAndTest = async () => {
    const validationError = editingValidationError()
    if (validationError || !editing) return setMessage({ tone: 'danger', text: validationError || '没有可保存的配置。' })
    if (!window.confirm('将保存配置，并向所选模型发送一次纯合成连接测试；不会发送病例工作台内容，但可能产生一次调用费用。是否继续？')) return
    setBusy('save-test'); setMessage(undefined)
    try {
      const summary = await api.saveProvider({ ...editing, apiKey: editing.apiKey || undefined })
      onChanged(summary)
      setEditingDirty(false)
      setBusyProviderId(summary.id)
      const result = await api.testProvider(summary.id)
      await onRefresh()
      setEditing(undefined)
      setMessage(result.ok
        ? { tone: 'success', text: `“${summary.name}”连接成功${result.latencyMs !== undefined ? ` · ${result.latencyMs} ms` : ''}。现在可用右侧开关把它加入“可供选择”的模型。` }
        : { tone: 'danger', text: `配置已保存，但连接测试未通过：${providerTestFailureText(result)}` })
    } catch (err) {
      setMessage({ tone: 'danger', text: apiErrorMessage(err) })
    } finally {
      setBusy(undefined); setBusyProviderId(undefined)
    }
  }

  const toggleProvider = async (provider: ProviderSummary) => {
    setBusy('toggle'); setBusyProviderId(provider.id); setMessage(undefined)
    try {
      const summary = await api.setProviderEnabled(provider.id, !provider.enabled)
      onChanged(summary)
      setMessage({ tone: 'success', text: `“${provider.name}”已${summary.enabled ? '启用，可在病例页选择' : '停用，不再出现在病例页'}。` })
    } catch (err) {
      setMessage({ tone: 'danger', text: apiErrorMessage(err) })
    } finally {
      setBusy(undefined); setBusyProviderId(undefined)
    }
  }

  const remove = async (provider: ProviderSummary) => {
    if (provider.kind === 'baseline') return
    if (!window.confirm(`删除模型配置“${provider.name}”？此操作不删除历史运行。`)) return
    setBusy('delete'); setMessage(undefined)
    try {
      await api.deleteProvider(provider.id)
      onDeleted(provider.id)
      if (editing?.id === provider.id) setEditing(undefined)
    } catch (err) {
      setMessage({ tone: 'danger', text: apiErrorMessage(err) })
    } finally {
      setBusy(undefined)
    }
  }

  const savedEditingProvider = editing?.id ? providers.find((provider) => provider.id === editing.id) : undefined
  const editingEndpoint = editing ? providerEndpoint(editing.kind, editing.model || '', editing.baseUrl) : ''

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow="模型服务设置 · Provider registry"
        title="设置本次分析使用的模型"
        description="选择模型服务，填写模型 ID 和 API Key，再用系统生成的合成内容测试连接。"
        actions={<div className="header-button-row"><button className="button button-secondary" onClick={onRefresh} disabled={loading}><RefreshCw className={loading ? 'spin' : ''} size={16} />刷新</button><button className="button button-secondary" onClick={() => beginCreate('openai_compatible')}><ServerCog size={16} />自定义兼容接口</button></div>}
      />

      <InlineNotice tone="warning" title="患者数据边界">
        外部商业API是否可以处理患者数据，必须由医院的隐私、信息安全和合规流程决定。仅“填入Key”不等于已获得授权。
      </InlineNotice>
      <InlineNotice tone="success" title="连接测试不使用病例">
        本页的“测试连接”只发送系统生成的合成内容，不会读取病例工作台。测试通过只代表网络、鉴权和结构化输出可用，不代表医学准确性。
      </InlineNotice>

      <Card className="provider-quick-connect">
        <SectionHeading icon={Wifi} title="快速连接" description="选择厂商后，常用地址和数据边界会自动填写。模型ID仍以你的账户实际可用名称为准。" />
        <div className="provider-preset-grid">
          {PROVIDER_QUICK_PRESETS.map((preset) => {
            const PresetIcon = preset.icon
            const count = providers.filter((provider) => provider.kind === preset.kind).length
            return (
              <button className="provider-preset" type="button" key={preset.kind} onClick={() => beginCreate(preset.kind)}>
                <span className="provider-preset-icon"><PresetIcon size={19} /></span>
                <span><strong>{preset.label}</strong><small>{preset.description}</small></span>
                <span className="provider-preset-count">{count ? `已配置 ${count}` : '添加'}</span>
              </button>
            )
          })}
        </div>
      </Card>

      {loading && providers.length === 0 ? <Card><LoadingState label="正在读取可用模型…" /></Card> : null}
      {error && <InlineNotice tone="danger" title="无法读取配置">{error}</InlineNotice>}
      {message?.text && <InlineNotice tone={message.tone}>{message.text}</InlineNotice>}

      {editing && (
        <Card className="provider-editor" tone="accent">
          <SectionHeading icon={editing.id ? Settings2 : Plus} title={editing.id ? `编辑“${editing.name}”` : `连接 ${kindMeta(editing.kind).name}`} description="常用项放在上方；地址、权重和数据边界收在高级设置中。" action={<button className="icon-button" onClick={() => { setEditingDirty(false); setEditing(undefined) }} aria-label="关闭配置"><X size={16} /></button>} />
          <div className="provider-editor-banner">
            <span className="provider-logo">{editing.kind === 'ollama' ? <Laptop size={20} /> : editing.kind === 'openai_compatible' ? <ServerCog size={20} /> : <Sparkles size={20} />}</span>
            <div><strong>{kindMeta(editing.kind).name}</strong><small>{kindMeta(editing.kind).description}</small></div>
            <StatusPill tone={editing.dataBoundary === 'local' ? 'success' : 'warning'}>{editing.dataBoundary === 'local' ? '本地/院内' : '外部/云端'}</StatusPill>
          </div>
          <div className="form-grid three provider-primary-fields">
            <Field label="模型ID" hint="必须使用厂商公布的精确名称，不是自己给模型起的名字。">
              <input autoFocus value={editing.model || ''} onChange={(event) => patchEditing({ model: event.target.value })} placeholder={kindMeta(editing.kind).modelPlaceholder} />
              {kindMeta(editing.kind).modelSuggestions && (
                <div className="provider-model-suggestions" aria-label="常用模型ID">
                  <span>当前官方选项：</span>
                  {kindMeta(editing.kind).modelSuggestions!.map((suggestion) => (
                    <button
                      type="button"
                      key={suggestion.id}
                      className={editing.model === suggestion.id ? 'active' : undefined}
                      onClick={() => patchEditing({ model: suggestion.id })}
                    >
                      {suggestion.label}
                    </button>
                  ))}
                </div>
              )}
            </Field>
            {(requiresCustomBaseUrl(editing.kind) || editing.kind === 'ollama') && <Field label="服务地址" hint={editing.dataBoundary === 'external' ? '已填常用地址；如厂商控制台给出的地址不同，请以控制台为准。' : '默认连接本机Ollama，也可改为院内私网地址。'}><input required value={editing.baseUrl || ''} onChange={(event) => patchEditing({ baseUrl: event.target.value })} placeholder={kindMeta(editing.kind).baseUrlPlaceholder} /></Field>}
            {editing.kind !== 'ollama' && <Field label="API Key" hint={editing.id ? '留空会保留已有Key；页面永不回显旧Key。' : 'Key只会加密写入本机后端。'}><div className="secret-input"><KeyRound size={16} /><input type="password" autoComplete="new-password" value={editing.apiKey || ''} onChange={(event) => patchEditing({ apiKey: event.target.value, clearApiKey: event.target.value ? false : editing.clearApiKey })} placeholder={savedEditingProvider?.hasApiKey ? '已配置；留空保持不变' : '粘贴 API Key'} /></div>{editing.id && savedEditingProvider?.hasApiKey && <label className="inline-check"><input type="checkbox" checked={Boolean(editing.clearApiKey)} onChange={(event) => patchEditing({ clearApiKey: event.target.checked, apiKey: event.target.checked ? '' : editing.apiKey })} /><span>保存时清除已有 API Key，并停用此模型</span></label>}</Field>}
          </div>
          <div className="endpoint-preview"><span>最终请求地址</span><code>{(editing.kind === 'gemini_generate_content' && !editing.model?.trim()) || editingEndpoint === 'unknown://unspecified' ? '填写模型ID和服务地址后显示' : editingEndpoint}</code></div>
          <details className="provider-advanced-options">
            <summary><Settings2 size={15} />高级设置</summary>
            <div className="form-grid three">
              <Field label="提供商类型" hint={editing.id ? '已保存实例不能更换协议；请另建一个实例。' : undefined}><select value={editing.kind} disabled={Boolean(editing.id)} onChange={(event) => { const kind = event.target.value as ProviderKind; const meta = kindMeta(kind); patchEditing({ kind, name: meta.name, dataBoundary: meta.dataBoundary, baseUrl: presetBaseUrl(kind), model: meta.defaultModel || '', apiKey: '', enabled: false }) }}>{PROVIDER_KIND_META.filter((item) => item.kind !== 'baseline').map((item) => <option key={item.kind} value={item.kind}>{item.name}</option>)}</select></Field>
              <Field label="配置名称"><input value={editing.name} onChange={(event) => patchEditing({ name: event.target.value })} placeholder="例：OpenAI 主模型 / 院内 Qwen" /></Field>
              {!requiresCustomBaseUrl(editing.kind) && editing.kind !== 'ollama' && <Field label="自定义 Base URL" hint="留空使用厂商标准地址；只有网关或代理场景才需要修改。"><input value={editing.baseUrl || ''} onChange={(event) => patchEditing({ baseUrl: event.target.value })} placeholder="留空使用标准地址" /></Field>}
              <Field label="模型权重" hint="仅用于多模型融合，不代表医学可靠性。"><input type="number" min="0.1" max="10" step="0.1" value={editing.weight ?? 1} onChange={(event) => patchEditing({ weight: Number(event.target.value) })} /></Field>
              <Field label="数据边界"><select value={editing.dataBoundary} disabled={editing.kind === 'ollama'} onChange={(event) => patchEditing({ dataBoundary: event.target.value as 'local' | 'external' })}><option value="external">外部/云端</option><option value="local">本地/院内</option></select></Field>
              {editing.id && <Field label="病例页可选"><label className="inline-switch"><span>{editing.enabled ? '已启用' : '已停用'}</span><span className="switch"><input type="checkbox" checked={editing.enabled} disabled={editingDirty && !editing.enabled} onChange={(event) => patchEditing({ enabled: event.target.checked })} /><span /></span></label></Field>}
            </div>
          </details>
          <div className="provider-editor-actions">
            {editing.id && editingDirty && <span className="muted">当前有未保存修改；保存后才能测试新配置。</span>}
            {editing.id && <button className="button button-secondary" onClick={() => void testSavedProvider({ id: editing.id!, name: editing.name })} disabled={connection !== 'online' || busy !== undefined || editingDirty} title={editingDirty ? '请先保存当前修改' : '使用后端已保存配置测试'}>{busy === 'test' && busyProviderId === editing.id ? <LoaderCircle className="spin" size={15} /> : <Wifi size={15} />}测试已保存配置</button>}
            <button className="button button-secondary" onClick={() => void save()} disabled={connection !== 'online' || busy !== undefined}>{busy === 'save' ? <LoaderCircle className="spin" size={15} /> : <Save size={15} />}仅保存</button>
            <button className="button button-primary" onClick={() => void saveAndTest()} disabled={connection !== 'online' || busy !== undefined}>{busy === 'save-test' ? <LoaderCircle className="spin" size={15} /> : <Wifi size={15} />}保存并用合成数据测试</button>
          </div>
        </Card>
      )}

      {!loading && providers.length === 0 ? <Card><EmptyState icon={BrainCircuit} title="尚无模型实例" description="后端尚未发布基线模型，也没有自定义模型配置。" action={<button className="button button-primary" onClick={() => beginCreate()}><Plus size={16} />新增模型实例</button>} /></Card> : (
        <div className="provider-grid">
          {providers.map((provider) => {
            const meta = kindMeta(provider.kind)
            return (
              <Card className={cx('provider-card', provider.enabled && 'selected')} key={provider.id}>
                <div className="provider-card-head">
                  <div className="provider-logo">{provider.kind === 'baseline' ? <Gauge size={20} /> : provider.kind === 'ollama' ? <Laptop size={20} /> : <Sparkles size={20} />}</div>
                  <div><h2>{provider.name}</h2><p>{meta.name} · {provider.model || '后端内置'}</p></div>
                  <StatusPill tone={provider.dataBoundary === 'local' ? 'success' : 'warning'}>{provider.dataBoundary === 'local' ? '本地' : '外部/云端'}</StatusPill>
                </div>
                <div className="provider-status-row"><StatusPill tone={provider.health === 'ready' ? 'success' : provider.health === 'error' ? 'danger' : provider.configured ? 'info' : 'neutral'} icon={provider.health === 'ready' ? CheckCircle2 : provider.health === 'error' ? XCircle : CircleHelp}>{provider.health === 'ready' ? '连接测试通过' : provider.health === 'error' ? '测试未通过' : provider.configured ? '已保存 · 待测试' : provider.kind === 'baseline' ? '服务内置' : '缺少Key'}</StatusPill><span className="muted">{provider.lastCheckedAt ? `${formatDate(provider.lastCheckedAt)}${provider.lastTestLatencyMs !== undefined ? ` · ${provider.lastTestLatencyMs} ms` : ''}` : '尚未测试'}</span></div>
                {provider.message && <div className="provider-error-hint">最近错误：{storedProviderErrorText(provider.message)}</div>}
                {provider.kind === 'baseline' ? <div className="baseline-explain"><Database size={18} /><p>基线模型不调用外部LLM。其状态与真实结果均由后端管理。</p></div> : <dl className="provider-instance-meta"><div><dt>Base URL</dt><dd>{provider.baseUrl || '后端默认'}</dd></div><div><dt>权重</dt><dd>{provider.weight ?? 1}</dd></div><div><dt>API Key</dt><dd>{provider.kind === 'ollama' ? '不需要 / 未使用' : provider.hasApiKey ? '已配置 · 不回显' : '未配置'}</dd></div></dl>}
                {provider.kind === 'baseline' ? <div className="provider-availability-static"><CheckCircle2 size={15} />始终可在病例页选择</div> : <label className="provider-availability-control"><span><strong>病例页可选</strong><small>{provider.enabled ? '已启用' : provider.health === 'ready' ? '连接测试通过，可随时启用' : '请先测试连接'}</small></span><span className="switch"><input type="checkbox" checked={provider.enabled} disabled={busy !== undefined || (!provider.enabled && provider.health !== 'ready')} onChange={() => void toggleProvider(provider)} /><span /></span></label>}
                <div className="provider-actions">
                  {provider.kind !== 'baseline' && <button className="button button-secondary" onClick={() => void testSavedProvider(provider)} disabled={busy !== undefined}>{busy === 'test' && busyProviderId === provider.id ? <LoaderCircle className="spin" size={15} /> : <Wifi size={15} />}测试连接</button>}
                  <button className="button button-secondary" onClick={() => beginEdit(provider)} disabled={provider.kind === 'baseline' || busy !== undefined}><Settings2 size={15} />编辑</button>
                  {provider.kind !== 'baseline' && <button className="button button-danger-soft" onClick={() => void remove(provider)} disabled={busy !== undefined}><Trash2 size={15} />删除</button>}
                </div>
              </Card>
            )
          })}
        </div>
      )}
    </div>
  )
}

function stageIcon(status: StageStatus): ReactNode {
  if (status === 'completed') return <Check size={14} />
  if (status === 'running') return <LoaderCircle className="spin" size={14} />
  if (status === 'failed') return <X size={14} />
  if (status === 'skipped') return <ChevronRight size={14} />
  return <span className="pending-dot" />
}

const FALLBACK_AGENT_PLANES: ArchitectureResponse['planes'] = [
  { id: 'governance', name: { zhCn: '治理平面', en: 'Governance plane' } },
  { id: 'online', name: { zhCn: '在线运行平面', en: 'Online runtime plane' } },
  { id: 'offline', name: { zhCn: '离线验证平面', en: 'Offline validation plane' } },
]

function shortGraphLabel(value?: string, max = 13): string {
  if (!value) return '未命名'
  return value.length > max ? `${value.slice(0, max - 1)}…` : value
}

function isDevelopmentBypassNode(node: AgentNode, developmentDemo: boolean): boolean {
  if (!developmentDemo) return false
  if (node.status === 'failed' || node.outcome === 'blocked') return false
  if (node.status === 'bypassed' || node.outcome === 'demo_bypassed') return true
  const metadata = node.metadata || {}
  if (metadata.bypassed === true || metadata.bypass === true || metadata.bypassed_controls || metadata.observation_only === true) return true
  return /demo_projection|clinical_review|cloud_consent|time_gate|organizer|governance|safety|quality|applicability|ood/i.test(`${node.kind} ${node.nodeKey}`)
}

function agentNodeStateLabel(node: AgentNode, developmentDemo: boolean): string {
  if (node.status === 'failed') return '未完成（技术故障）'
  if (node.outcome === 'blocked') return '未继续执行'
  if (isDevelopmentBypassNode(node, developmentDemo)) return '已记录研发提示'
  if (node.status === 'running') return '正在分析'
  if (node.status === 'pending') return '等待开始'
  if (node.status === 'skipped') return '本次未执行'
  if (node.outcome === 'not_applicable') return '本次不适用'
  if (node.outcome === 'passed') return '已完成'
  if (node.outcome === 'warning') return '已完成（有研发提示）'
  if (node.outcome) return '状态待确认'
  if (node.status === 'completed') return '已完成'
  if (node.maturity === 'implemented') return '已实现'
  if (/partial|research/i.test(node.maturity || '')) return '部分实现'
  if (/planned|target/i.test(node.maturity || '')) return '规划中'
  return node.status || node.maturity || '未报告'
}

function maturityLabel(value: string): string {
  if (value === 'implemented') return '已实现'
  if (/partial|research/i.test(value)) return '部分实现'
  if (/planned|target/i.test(value)) return '规划中'
  return value
}

function AgentNodeInspector({ node, loading, error, developmentDemo, nodeLabels }: { node?: AgentNode; loading?: boolean; error?: string; developmentDemo: boolean; nodeLabels?: Map<string, string> }) {
  if (!node) return <aside className="agent-inspector"><EmptyState icon={Network} title="选择一个分析步骤" description="点击流程中的步骤，查看它做了什么、是否完成，以及可展开的工程记录。" /></aside>
  const bypass = isDevelopmentBypassNode(node, developmentDemo)
  const responsibility = clinicalNodeResponsibility(node.nodeKey)
  const nodeErrorCopy = node.error ? clinicalTechnicalError(node.error) : undefined
  const runtimeNode = Boolean(node.status || node.startedAt || node.completedAt || node.provider || node.model || node.artifacts?.length)
  const nodeTone = bypass ? 'warning' : node.outcome === 'blocked' || node.status === 'failed' ? 'danger' : node.outcome === 'warning' ? 'warning' : node.status === 'running' ? 'info' : node.outcome === 'not_applicable' ? 'neutral' : node.status === 'completed' ? 'success' : 'neutral'
  return (
    <aside className={cx('agent-inspector', bypass && 'inspector-demo-bypass')} aria-live="polite">
      {loading && <LoadingState label="正在读取这一步的结构化结果…" />}
      {error && <InlineNotice tone="danger" title="无法读取这一步的详细记录">{error}</InlineNotice>}
      <div className="agent-inspector-head">
        <div>
          <h2>{responsibility.primary}</h2>
        </div>
        <StatusPill tone={nodeTone}>
          {agentNodeStateLabel(node, developmentDemo)}
        </StatusPill>
      </div>
      <p className="agent-description">{responsibility.detail}</p>
      {runtimeNode && <div className="agent-clinical-runtime-summary"><div><span>完成情况</span><strong>{agentNodeStateLabel(node, developmentDemo)}</strong></div><div><span>耗时</span><strong>{formatDuration(node.latencyMs)}</strong></div><div><span>是否有提示</span><strong>{bypass || node.outcome === 'warning' || node.error ? '有，点击工程信息查看' : '未记录额外提示'}</strong></div></div>}
      {nodeErrorCopy && <InlineNotice tone="danger" title={nodeErrorCopy.title}><p>{nodeErrorCopy.reason}</p><p>{nodeErrorCopy.action}</p></InlineNotice>}
      {node.artifacts && node.artifacts.length > 0 && <div className="agent-artifacts">
        <strong>本步骤保存的结构化结果</strong>
        <small>脱敏运行制品（Artifact）；点击展开查看摘要。</small>
        {node.artifacts.map((artifact) => <details key={artifact.key}>
          <summary><BilingualCopy text={artifact.label} />{artifact.value && <code>{String(artifact.value)}</code>}</summary>
          {artifact.summary && <p><BilingualCopy text={artifact.summary} /></p>}
          {(artifact.direction || artifact.contentSha256 || artifact.integrityOk !== undefined || artifact.visibility || artifact.json !== undefined) && <details className="artifact-engineering-details"><summary>展开脱敏技术记录（JSON 与完整性信息）</summary><dl className="agent-artifact-meta">
            {artifact.direction && <div><dt>数据方向</dt><dd>{artifact.direction}</dd></div>}
            {artifact.visibility && <div><dt>可见性</dt><dd>{artifact.visibility}</dd></div>}
            {artifact.integrityOk !== undefined && <div><dt>完整性</dt><dd>{artifact.integrityOk ? '通过' : '未通过'}</dd></div>}
            {artifact.contentSha256 && <div><dt>SHA-256</dt><dd><code>{artifact.contentSha256}</code></dd></div>}
          </dl>{artifact.json !== undefined && <pre>{JSON.stringify(artifact.json, null, 2)}</pre>}</details>}
        </details>)}
      </div>}
      <details className="agent-engineering-details">
        <summary>工程信息：版本、模型服务、依赖、原始状态与完整性记录</summary>
        <dl className="agent-inspector-grid">
          <div><dt>技术 ID</dt><dd>{node.nodeKey}</dd></div>
          {nodeErrorCopy && <div><dt>安全错误码</dt><dd><code>{node.error?.code || nodeErrorCopy.engineeringCode}</code></dd></div>}
          <div><dt>所属流程</dt><dd>{node.plane}</dd></div>
          <div><dt>系统原始名称</dt><dd><BilingualCopy text={node.name} /></dd></div>
          <div><dt>组件类型</dt><dd>{agentKindLabel(node.kind)}</dd></div>
          <div><dt>实现状态</dt><dd>{node.maturity ? maturityLabel(node.maturity) : '—'}</dd></div>
          <div><dt>版本</dt><dd>{node.version || '—'}</dd></div>
          {runtimeNode ? <>
            <div><dt>模型服务（Provider）</dt><dd>{node.provider || '—'}</dd></div>
            <div><dt>模型</dt><dd>{node.model || '—'}</dd></div>
            <div><dt>原始执行状态</dt><dd>{node.status || '—'}</dd></div>
            <div><dt>原始业务结果</dt><dd>{node.outcome || '—'}</dd></div>
            <div><dt>执行顺序 / 调用次数</dt><dd>{node.sequence ?? '—'} / {node.attempt ?? '—'}</dd></div>
            <div><dt>耗时</dt><dd>{formatDuration(node.latencyMs)}</dd></div>
            <div><dt>开始</dt><dd>{formatDate(node.startedAt)}</dd></div>
            <div><dt>完成</dt><dd>{formatDate(node.completedAt)}</dd></div>
          </> : <><div><dt>流程顺序</dt><dd>{node.sequence ?? '—'}</dd></div><div><dt>技术 ID</dt><dd>{node.nodeKey}</dd></div></>}
        </dl>
        {node.dependsOn.length > 0 && <div className="agent-dependencies"><strong>依赖步骤</strong><div>{node.dependsOn.map((item) => <code key={item} title={item}>{nodeLabels?.get(item) || item}</code>)}</div></div>}
        {node.outcome && <div className="agent-outcome"><strong>原始业务结果</strong><p>{node.outcome}</p></div>}
        {(node.inputSha256 || node.outputSha256) && <dl className="agent-hashes">{node.inputSha256 && <div><dt>输入 SHA-256</dt><dd><code>{node.inputSha256}</code></dd></div>}{node.outputSha256 && <div><dt>输出 SHA-256</dt><dd><code>{node.outputSha256}</code></dd></div>}</dl>}
        {node.safetyJson !== undefined && <details className="safety-json"><summary>展开脱敏技术记录（JSON）</summary><pre>{JSON.stringify(node.safetyJson, null, 2)}</pre></details>}
        {node.metadata && Object.keys(node.metadata).length > 0 && <details className="safety-json"><summary>运行追踪元数据 / Trace metadata</summary><pre>{JSON.stringify(node.metadata, null, 2)}</pre></details>}
      </details>
    </aside>
  )
}

type AgentFlowStageId = 'intake' | 'routing' | 'core_agents' | 'dynamic_specialists' | 'models' | 'evidence' | 'synthesis' | 'review' | 'output' | 'process'

const AGENT_FLOW_STAGE_META: Record<AgentFlowStageId, { zh: string; en: string; description: string }> = {
  intake: { zh: '整理病例与当前可见证据', en: 'Case intake & evidence preparation', description: '保留输入原文、检查完整性，并生成可追溯的证据片段。' },
  routing: { zh: '判断感染部位与临床综合征', en: 'Infection & syndrome routing', description: '先判断本病例的主要感染部位和综合征方向。' },
  core_agents: { zh: '五个核心临床专家并行会诊', en: 'Five core clinical experts in parallel', description: '感染科、重症/急诊、临床流行病学、检验医学和临床微生物/培养实验室固定召集，交付职责分开的结构化意见。' },
  dynamic_specialists: { zh: '按证据动态召集顶尖专科专家', en: 'Evidence-routed specialist consultation', description: '路由器从二十个专科角色中最多选择六个；未选角色明确显示 not_applicable，不生成伪会诊意见。' },
  models: { zh: '从不同方法估计病原体', en: 'Multi-route pathogen modelling', description: '多种建模方法从不同机制形成病原体候选。' },
  evidence: { zh: '建立证据板并检索多类外部证据', en: 'Evidence board and federated retrieval', description: '先汇总支持、反对、矛盾和候选，再使用去标识化查询检索文献、类似病例和 WHO 疫情信号；当前以题名相关性核对为主，摘要/全文核验仍在建设。' },
  synthesis: { zh: '综合各视角并形成候选', en: 'Synthesis & fusion', description: '汇总各视角意见和文献，排序出具体病原体。' },
  review: { zh: '合同检查、独立反证与有限修订', en: 'Output contract, counter-evidence review & bounded revision', description: '核对病原体名称、前5位候选和证据引用，再从遗漏、替代解释和反对证据挑战结果；必要时只修订一次。' },
  output: { zh: '生成中英文结果并保存记录', en: 'Bilingual result & trace persistence', description: '生成医生可读结果，并保存版本、完整运行轨迹和完整性哈希。' },
  process: { zh: '其他支持步骤', en: 'Additional processing', description: '当前流程中的其他执行或支持组件。' },
}

function agentFlowStageId(node: AgentNode): AgentFlowStageId {
  const value = `${node.nodeKey} ${node.id} ${node.kind} ${node.name.zhCn || ''} ${node.name.en || ''}`.toLowerCase()
  if (DYNAMIC_CLINICAL_EXPERT_ROLE_IDS.some((role) => value.includes(role))) return 'dynamic_specialists'
  if (CORE_CLINICAL_EXPERT_ROLE_IDS.some((role) => value.includes(role))) return 'core_agents'
  if (/specialist:(neuroinfection|immunocompromised_opportunistic|travel_zoonotic|healthcare_device_amr)|dynamic_llm_agent|\b(neuroinfection|immunocompromised_opportunistic|travel_zoonotic|healthcare_device_amr)\b/.test(value)) return 'dynamic_specialists'
  if (/specialist:|timeline_host|syndrome_site|exposure_epidemiology|laboratory_organ_injury|imaging_microbiology_treatment|timeline_course|host_susceptibility|syndrome_localization|exposure_one_health|lab_pathophysiology|organ_severity|imaging_dissemination|microbiology_treatment/.test(value)) return 'core_agents'
  if (/complexity_router|target_router|syndrome router|综合征路由|专病路由/.test(value)) return 'routing'
  if (/target_discriminative|target_world_model|target_bayesian_prior|multimodal|world model|bayesian|判别式|世界模型|贝叶斯/.test(value)) return 'models'
  if (/candidate_evidence_enrichment|文献补强|contract|critic|revision|safety|adjudicat|conformal|ood|five-state|五态|审稿|修订|裁决/.test(value)) return 'review'
  if (/evidence_board|retrieval|medical_retrieval|evidence_verifier|evidence_enrichment|医学证据检索|外部证据|证据委员会/.test(value)) return 'evidence'
  if (/synthesis|fusion|aggregat|总诊|融合/.test(value)) return 'synthesis'
  if (/result|persistence|bilingual|report_agent|target_human|renderer|固化|呈现|医生独立复核/.test(value)) return 'output'
  if (/source|input|snapshot|preflight|applicability|quality|ledger|compiler|observer|病例全文|证据编译|事件账本|适用性/.test(value)) return 'intake'
  return 'process'
}

function agentKindLabel(kind: string): string {
  if (/llm_agent|specialist_agent|critic_agent|pathogen_synthesis|revision/.test(kind)) return '大模型分析单元（LLM Agent）'
  if (/tool_agent|medical_retrieval/.test(kind)) return '证据工具（Tool Agent）'
  if (/human/.test(kind)) return '医生'
  if (/input/.test(kind)) return '病例输入'
  if (/policy|guard|safety/.test(kind)) return '规则与安全'
  if (/validator/.test(kind)) return '确定性检查'
  if (/deterministic|compiler|renderer|aggregator/.test(kind)) return '确定性处理'
  if (/infrastructure/.test(kind)) return '基础设施'
  if (/clinical_model|model/.test(kind)) return '临床模型'
  return kind.replaceAll('_', ' ')
}

function agentNodeVisualTone(node: AgentNode, developmentDemo: boolean): string {
  if (node.status === 'failed' || node.outcome === 'blocked') return 'failed'
  if (isDevelopmentBypassNode(node, developmentDemo)) return 'observe'
  if (node.status === 'running') return 'running'
  if (node.status === 'skipped' || node.outcome === 'not_applicable') return 'neutral'
  if (node.outcome === 'warning' || /partial|research/i.test(node.maturity || '')) return 'warning'
  if (/planned|target/i.test(node.maturity || '')) return 'planned'
  if (node.status === 'completed' || node.outcome === 'passed' || node.maturity === 'implemented') return 'complete'
  return 'neutral'
}

function AgentGraphExplorer({
  graph,
  planes = FALLBACK_AGENT_PLANES,
  edgeTypes = [],
  developmentDemo = false,
  loadNodeDetail,
}: {
  graph: AgentGraph
  planes?: ArchitectureResponse['planes']
  edgeTypes?: ArchitectureResponse['edgeTypes']
  developmentDemo?: boolean
  loadNodeDetail?: (nodeId: string) => Promise<AgentNode>
}) {
  const [selectedId, setSelectedId] = useState(graph.nodes[0]?.id || '')
  const [detail, setDetail] = useState<AgentNode | undefined>(graph.nodes[0])
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState('')
  const selectedIdRef = useRef(selectedId)
  const detailRequestId = useRef(0)

  useEffect(() => {
    detailRequestId.current += 1
    const selected = graph.nodes.find((node) => node.id === selectedIdRef.current) || graph.nodes[0]
    selectedIdRef.current = selected?.id || ''
    setSelectedId(selectedIdRef.current)
    setDetail(selected)
    setDetailLoading(false)
    setDetailError('')
    return () => { detailRequestId.current += 1 }
  }, [graph])

  const story = useMemo(() => {
    const sortNodes = (nodes: AgentNode[]) => [...nodes].sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0))
    const governance = sortNodes(graph.nodes.filter((node) => node.plane === 'governance'))
    const offline = sortNodes(graph.nodes.filter((node) => node.plane === 'offline'))
    const grouped = new Map<AgentFlowStageId, AgentNode[]>()
    sortNodes(graph.nodes.filter((node) => node.plane === 'online')).forEach((node) => {
      const stage = agentFlowStageId(node)
      grouped.set(stage, [...(grouped.get(stage) || []), node])
    })
    const order: AgentFlowStageId[] = ['intake', 'routing', 'core_agents', 'dynamic_specialists', 'models', 'evidence', 'synthesis', 'review', 'output', 'process']
    const stages = order.filter((stage) => grouped.has(stage)).map((stage) => ({ id: stage, ...AGENT_FLOW_STAGE_META[stage], nodes: grouped.get(stage)! }))
    return { governance, stages, offline }
  }, [graph.nodes])
  const nodeLabels = useMemo(() => new Map(graph.nodes.map((node) => [node.id, clinicalNodeResponsibility(node.nodeKey).primary])), [graph.nodes])

  const selectNode = async (node: AgentNode) => {
    const requestId = ++detailRequestId.current
    selectedIdRef.current = node.id
    setSelectedId(node.id)
    setDetail(node)
    setDetailError('')
    if (!loadNodeDetail) {
      setDetailLoading(false)
      return
    }
    setDetailLoading(true)
    try {
      const loaded = await loadNodeDetail(node.id)
      if (requestId !== detailRequestId.current) return
      setDetail({
        ...loaded,
        dependsOn: [...new Set([...node.dependsOn, ...loaded.dependsOn])],
      })
    } catch (error) {
      if (requestId === detailRequestId.current) setDetailError(apiErrorMessage(error))
    } finally {
      if (requestId === detailRequestId.current) setDetailLoading(false)
    }
  }

  const nodeButton = (node: AgentNode, compact = false) => {
    const tone = agentNodeVisualTone(node, developmentDemo)
    const responsibility = clinicalNodeResponsibility(node.nodeKey)
    return <button
      type="button"
      key={node.id}
      className={cx('agent-story-node', compact && 'is-compact', `tone-${tone}`, selectedId === node.id && 'is-selected')}
      onClick={() => void selectNode(node)}
      aria-pressed={selectedId === node.id}
      aria-label={`${responsibility.primary}，${agentNodeStateLabel(node, developmentDemo)}`}
    >
      <span className="agent-story-node-state" aria-hidden="true">{tone === 'failed' ? <X size={13} /> : tone === 'running' ? <LoaderCircle className="spin" size={13} /> : tone === 'planned' ? <Clock3 size={13} /> : node.status === 'skipped' || node.outcome === 'not_applicable' ? <ChevronRight size={13} /> : <Check size={13} />}</span>
      <span className="agent-story-node-copy"><strong>{responsibility.primary}</strong><small>{agentKindLabel(node.kind)}</small></span>
      {!compact && <span className="agent-story-node-meta">{agentNodeStateLabel(node, developmentDemo)}</span>}
      <ChevronRight size={15} aria-hidden="true" />
    </button>
  }

  return (
    <div className={cx('agent-explorer agent-story-explorer', developmentDemo && 'agent-explorer-demo')}>
      <div className="agent-story-layout">
        <div className="agent-story-map" aria-label={graph.name?.zhCn || 'Agent 执行流程'}>
          {story.governance.length > 0 && <section className="agent-plane-rail governance-rail">
            <div className="agent-plane-rail-title"><ShieldCheck size={18} /><div><strong>贯穿全程的治理控制</strong><small>Governance controls across every stage</small></div></div>
            <div>{story.governance.map((node) => nodeButton(node, true))}</div>
          </section>}

          <ol className="agent-flow-stages">
            {story.stages.map((stage, index) => <li className={cx('agent-flow-stage', (stage.id === 'core_agents' || stage.id === 'dynamic_specialists') && 'is-parallel', stage.id === 'dynamic_specialists' && 'is-dynamic')} key={stage.id}>
              <header>
                <span>{String(index + 1).padStart(2, '0')}</span>
                <div><strong>{stage.zh}</strong><small>{stage.en}</small><p>{stage.description}</p></div>
                <em>{stage.nodes.length} 个节点</em>
              </header>
              <div className="agent-flow-node-grid">{stage.nodes.map((node) => nodeButton(node))}</div>
              {index < story.stages.length - 1 && <div className="agent-flow-connector" aria-hidden="true"><ArrowRight size={18} /><span>进入下一步</span></div>}
            </li>)}
          </ol>

          {story.offline.length > 0 && <section className="agent-plane-rail offline-rail">
            <div className="agent-plane-rail-title"><Gauge size={18} /><div><strong>结果回流到离线验证</strong><small>Offline evaluation and release feedback</small></div></div>
            <div>{story.offline.map((node) => nodeButton(node, true))}</div>
          </section>}

          {edgeTypes.length > 0 && <details className="agent-edge-details">
            <summary>查看数据流和连线说明</summary>
            <div className="agent-edge-legend" aria-label="边类型图例">
              {edgeTypes.map((edge) => <span key={edge.id} className={`edge-legend-${edge.id}`}><i /><BilingualCopy text={edge.name} /></span>)}
            </div>
          </details>}
        </div>
        <div className="agent-story-inspector-wrap">
          <AgentNodeInspector node={detail} loading={detailLoading} error={detailError} developmentDemo={developmentDemo} nodeLabels={nodeLabels} />
        </div>
      </div>
      <details className="agent-text-fallback">
        <summary>工程信息：依赖关系与技术清单 / Technical dependency list</summary>
        <div className="agent-text-grid">{planes.map((plane) => <section key={plane.id}>
          <h3><BilingualCopy text={plane.name} /></h3>
          <ol>{graph.nodes.filter((node) => node.plane === plane.id).sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0)).map((node) => <li key={node.id}>
            <button type="button" onClick={() => void selectNode(node)}>{clinicalNodeResponsibility(node.nodeKey).primary}</button>
            <small>{agentKindLabel(node.kind)} · {node.name.zhCn || node.name.en || node.nodeKey} · {node.status || node.maturity || '未报告'}{node.dependsOn.length ? ` · 依赖 ${node.dependsOn.join(', ')}` : ''}</small>
          </li>)}</ol>
        </section>)}</div>
      </details>
    </div>
  )
}

function workflowMaturityLabel(value: WorkflowMaturity): string {
  if (value === 'implemented') return '已有基础'
  if (value === 'partial') return '正在升级'
  return '规划建设'
}

function workflowMaturityTone(value: WorkflowMaturity): 'success' | 'warning' | 'violet' {
  if (value === 'implemented') return 'success'
  if (value === 'partial') return 'warning'
  return 'violet'
}

function workflowKindLabel(value: WorkflowNodeDefinition['kind']): string {
  const labels: Record<WorkflowNodeDefinition['kind'], string> = {
    deterministic: '确定性处理',
    llm_agent: 'LLM分析单元',
    clinical_model: '临床模型',
    retrieval: '证据检索工具',
    validator: '独立核验器',
    governance: '治理控制',
    offline: '离线科学验证',
  }
  return labels[value]
}

function workflowPriorityLabel(value: WorkflowNodeDefinition['priority']): string {
  if (value === 'foundation') return '现有底座'
  return value.toUpperCase()
}

function LatestWorkflowBlueprint() {
  const nodeById = useMemo(() => new Map(LATEST_WORKFLOW_NODES.map((node) => [node.id, node])), [])
  const [selectedId, setSelectedId] = useState(LATEST_WORKFLOW_NODES[0]?.id || '')
  const [filter, setFilter] = useState<'all' | 'implemented' | 'upgrade'>('all')
  const visibleNodes = useMemo(() => LATEST_WORKFLOW_NODES.filter((node) => {
    if (filter === 'implemented') return node.maturity === 'implemented'
    if (filter === 'upgrade') return node.maturity !== 'implemented'
    return true
  }), [filter])
  const visibleIds = useMemo(() => new Set(visibleNodes.map((node) => node.id)), [visibleNodes])
  const selected = nodeById.get(selectedId) || visibleNodes[0]
  const maturityCounts = useMemo(() => LATEST_WORKFLOW_NODES.reduce<Record<WorkflowMaturity, number>>((counts, node) => {
    counts[node.maturity] += 1
    return counts
  }, { implemented: 0, partial: 0, planned: 0 }), [])
  const coreExperts = useMemo(() => EXPERT_CONSULT_REGISTRY.filter((expert) => expert.group === 'core'), [])
  const dynamicExperts = useMemo(() => EXPERT_CONSULT_REGISTRY.filter((expert) => expert.group === 'dynamic'), [])

  useEffect(() => {
    if (selectedId && visibleIds.has(selectedId)) return
    setSelectedId(visibleNodes[0]?.id || '')
  }, [selectedId, visibleIds, visibleNodes])

  const scrollToStage = (stageId: string) => {
    document.getElementById(`workflow-stage-${stageId}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  const selectWorkflowNode = (nodeId: string) => {
    setSelectedId(nodeId)
    if (window.matchMedia('(max-width: 1240px)').matches) {
      window.requestAnimationFrame(() => document.getElementById('workflow-node-inspector')?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
    }
  }

  return <div className="latest-workflow-page">
    <Card className="workflow-hero" tone="accent">
      <div className="workflow-hero-copy">
        <span className="eyebrow">最新研发蓝图 · {LATEST_WORKFLOW_DATE}</span>
        <h2>{LATEST_WORKFLOW_LABEL}</h2>
        <p>系统回答的是：在选定时间点 T，根据当时已经可见的信息，最可能的具体病原体是什么？在回顾性验证中，T可以设为ICU入科后0、6、12或24小时。</p>
        <div className="workflow-hero-formula"><span>多路提出候选</span><ArrowRight size={16} /><span>逐候选支持与反证</span><ArrowRight size={16} /><span>证据更新后确定Top-5</span></div>
      </div>
      <div className="workflow-hero-status" aria-label="最新Workflow成熟度">
        <StatusPill tone="success">已有基础 {maturityCounts.implemented}</StatusPill>
        <StatusPill tone="warning">正在升级 {maturityCounts.partial}</StatusPill>
        <StatusPill tone="violet">规划建设 {maturityCounts.planned}</StatusPill>
        <small>工程状态不等于临床有效性；全部节点仍需真实数据验证。</small>
      </div>
    </Card>

    <InlineNotice tone="warning" title="这是最新目标Workflow，不是当前每次运行已经具备的全部能力">
      紫色规划节点不会出现在真实运行轨迹中，也不会生成伪造结果。当前系统的真实执行链请切换到“当前真实运行”。
    </InlineNotice>

    <Card className="workflow-change-card">
      <div className="section-heading"><div><span className="eyebrow">本次最核心的结构变化</span><h2>从“先定答案再补文献”，改成“证据回来后再定答案”</h2></div></div>
      <div className="workflow-change-grid">
        <article className="is-before"><span>当前链路的限制</span><strong>总诊先生成Top‑5</strong><p>候选特异文献补强发生得较晚，更多用于给既定结果挂引用，难以反向纠正排名。</p></article>
        <ArrowRight size={22} aria-hidden="true" />
        <article className="is-after"><span>最新Workflow</span><strong>先保留Top‑10至15，再逐个查证</strong><p>支持、反证、相似病例差异和治疗检出率共同更新候选后，才形成最终Top‑5。</p></article>
        <article className="is-bound"><span>受控纠错</span><strong>独立找错，最多修订一次</strong><p>只有明确问题才能触发修订；不使用无限反思，也不让同一个模型简单批准自己。</p></article>
      </div>
    </Card>

    <Card className="workflow-expert-registry-card">
      <div className="section-heading workflow-expert-heading">
        <div><span className="eyebrow">owlpath.development-agents.v3</span><h2>核心会诊组 + 动态顶尖临床专科专家库</h2><p>“已实现”只表示软件已能调度该角色，不表示已证明达到真实顶尖专家的临床水平。临床等效性与实际增益仍需DR.ECC与MIMIC验证。</p></div>
        <StatusPill tone="warning">临床效能未验证</StatusPill>
      </div>
      <div className="workflow-expert-routing-rule" role="note">
        <div><UserRoundCheck size={18} /><span><strong>核心组</strong>每次固定召集 {coreExperts.length} 位，轨迹状态为 <code>selected</code>。</span></div>
        <ArrowRight size={18} aria-hidden="true" />
        <div><Network size={18} /><span><strong>动态库</strong>从 {dynamicExperts.length} 位中最多召集 6 位，其余为 <code>not_applicable</code>。</span></div>
        <ArrowRight size={18} aria-hidden="true" />
        <div><ClipboardCheck size={18} /><span><strong>单次上限</strong>最多 11 个临床专家逻辑角色，实际状态以运行轨迹为准。</span></div>
      </div>
      <section className="workflow-expert-group is-core" aria-labelledby="workflow-core-experts-title">
        <header><div><span>固定会诊底座</span><h3 id="workflow-core-experts-title">核心临床专家</h3></div><small>{coreExperts.length} 位 · 运行合同 <code>selected</code></small></header>
        <div className="workflow-expert-grid">{coreExperts.map((expert) => <article className="workflow-expert-card" key={expert.roleId}>
          <div className="workflow-expert-card-head"><span className="workflow-expert-avatar"><Stethoscope size={17} /></span><div><strong>{expert.titleZh}</strong><small lang="en">{expert.titleEn}</small></div><StatusPill tone="success">已实现</StatusPill></div>
          <p>{expert.responsibility}</p>
          <details><summary>职责合同：输入、输出与召集条件</summary><div className="workflow-expert-contract"><section><strong>输入</strong><ul>{expert.inputs.map((item) => <li key={item}>{item}</li>)}</ul></section><section><strong>输出</strong><ul>{expert.outputs.map((item) => <li key={item}>{item}</li>)}</ul></section><section><strong>触发条件</strong><ul>{expert.triggers.map((item) => <li key={item}>{item}</li>)}</ul></section></div></details>
          <code className="workflow-expert-role-id">{expert.roleId}</code>
        </article>)}</div>
      </section>
      <section className="workflow-expert-group is-dynamic" aria-labelledby="workflow-dynamic-experts-title">
        <header><div><span>只按病例证据召集</span><h3 id="workflow-dynamic-experts-title">动态顶尖临床专科专家库</h3></div><small>{dynamicExperts.length} 位 · 每次最多 6 位 · <code>selected / not_applicable</code></small></header>
        <div className="workflow-expert-grid">{dynamicExperts.map((expert) => <article className="workflow-expert-card" key={expert.roleId}>
          <div className="workflow-expert-card-head"><span className="workflow-expert-avatar"><UserRoundCheck size={17} /></span><div><strong>{expert.titleZh}</strong><small lang="en">{expert.titleEn}</small></div><StatusPill tone="success">已实现</StatusPill></div>
          <p>{expert.responsibility}</p>
          <details><summary>职责合同：输入、输出与触发条件</summary><div className="workflow-expert-contract"><section><strong>输入</strong><ul>{expert.inputs.map((item) => <li key={item}>{item}</li>)}</ul></section><section><strong>输出</strong><ul>{expert.outputs.map((item) => <li key={item}>{item}</li>)}</ul></section><section><strong>触发条件</strong><ul>{expert.triggers.map((item) => <li key={item}>{item}</li>)}</ul></section></div></details>
          <code className="workflow-expert-role-id">{expert.roleId}</code>
        </article>)}</div>
      </section>
    </Card>

    <Card className="workflow-stage-index" aria-label="Workflow阶段索引">
      <div><strong>快速定位阶段</strong><small>点击跳转到对应流程</small></div>
      <nav>{LATEST_WORKFLOW_STAGES.map((stage) => <button key={stage.id} type="button" onClick={() => scrollToStage(stage.id)}><span>{stage.number}</span>{stage.titleZh}</button>)}</nav>
    </Card>

    <div className="workflow-filter" role="group" aria-label="筛选Workflow节点">
      <button type="button" className={filter === 'all' ? 'active' : ''} aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>完整目标Workflow</button>
      <button type="button" className={filter === 'implemented' ? 'active' : ''} aria-pressed={filter === 'implemented'} onClick={() => setFilter('implemented')}>只看已有基础</button>
      <button type="button" className={filter === 'upgrade' ? 'active' : ''} aria-pressed={filter === 'upgrade'} onClick={() => setFilter('upgrade')}>只看正在升级与规划</button>
    </div>

    <div className="workflow-explorer">
      <div className="workflow-stage-list">
        {LATEST_WORKFLOW_STAGES.map((stage) => {
          const nodes = stage.nodeIds.map((id) => nodeById.get(id)).filter((node): node is WorkflowNodeDefinition => Boolean(node && visibleIds.has(node.id)))
          if (!nodes.length) return null
          return <section id={`workflow-stage-${stage.id}`} className={cx('workflow-stage-card', stage.id === 'candidate_recall' && 'is-parallel', stage.id === 'candidate_verification' && 'is-verification', stage.id === 'final_reasoning' && 'is-loop')} key={stage.id}>
            <header>
              <span>{stage.number}</span>
              <div><h2>{stage.titleZh}</h2><small lang="en">{stage.titleEn}</small><p>{stage.summary}</p></div>
              <em>{nodes.length} 个节点</em>
            </header>
            <div className="workflow-stage-note"><CircleHelp size={15} /><span>{stage.note}</span></div>
            <div className="workflow-node-grid">
              {nodes.map((node) => <button
                type="button"
                className={cx('workflow-node-button', `maturity-${node.maturity}`, selectedId === node.id && 'is-selected')}
                key={node.id}
                aria-pressed={selectedId === node.id}
                onClick={() => selectWorkflowNode(node.id)}
              >
                <span className="workflow-node-icon">{node.kind === 'llm_agent' ? <BrainCircuit size={17} /> : node.kind === 'retrieval' ? <FileSearch size={17} /> : node.kind === 'validator' ? <SearchCheck size={17} /> : node.kind === 'clinical_model' ? <Gauge size={17} /> : node.kind === 'offline' ? <FlaskConical size={17} /> : <Network size={17} />}</span>
                <span><strong>{node.titleZh}</strong><small lang="en">{node.titleEn}</small></span>
                <StatusPill tone={workflowMaturityTone(node.maturity)}>{workflowMaturityLabel(node.maturity)}</StatusPill>
                <ChevronRight size={16} />
              </button>)}
            </div>
            {stage.id === 'candidate_verification' && <div className="workflow-parallel-caption"><span>支持证据</span><span>反证与替代解释</span><span>相似病例差异</span><span>治疗与检出率</span><strong>并行核验后汇合</strong></div>}
            {stage.id === 'final_reasoning' && <div className="workflow-loop-caption"><RotateCcw size={16} /><span>仅在审稿或合同发现明确问题时回到假设更新器，最多一次。</span></div>}
          </section>
        })}
      </div>

      <aside id="workflow-node-inspector" className="workflow-inspector" aria-live="polite">
        {selected ? <>
          <div className="workflow-inspector-head">
            <div><span>{workflowKindLabel(selected.kind)} · {workflowPriorityLabel(selected.priority)}</span><h2>{selected.titleZh}</h2><small lang="en">{selected.titleEn}</small></div>
            <StatusPill tone={workflowMaturityTone(selected.maturity)}>{workflowMaturityLabel(selected.maturity)}</StatusPill>
          </div>
          <p className="workflow-inspector-description">{selected.description}</p>
          <section><h3>它读取什么</h3><ul>{selected.inputs.map((item) => <li key={item}>{item}</li>)}</ul></section>
          <section><h3>它交出什么</h3><ul>{selected.outputs.map((item) => <li key={item}>{item}</li>)}</ul></section>
          <section className="workflow-inspector-reason"><h3>为什么需要它</h3><p>{selected.why}</p></section>
          <section className="workflow-inspector-compare"><div><strong>从公开研究系统学到</strong><p>{selected.referenceLesson}</p></div><div><strong>OwlPath的升级</strong><p>{selected.owlPathUpgrade}</p></div></section>
          <section className="workflow-inspector-validation"><h3>如何证明它有效</h3><p>{selected.validation}</p></section>
          {selected.dependsOn.length > 0 && <details><summary>工程依赖</summary><div>{selected.dependsOn.map((id) => <code key={id}>{nodeById.get(id)?.titleZh || id}</code>)}</div></details>}
          <code className="workflow-technical-id">{selected.id}</code>
        </> : <EmptyState icon={Network} title="当前筛选没有节点" description="切换筛选条件查看完整Workflow。" />}
      </aside>
    </div>

    <Card className="workflow-ledger-card">
      <div className="section-heading"><div><span className="eyebrow">患者级假设账本</span><h2>每个候选不只是一行名字，而是一张可更新的证据卡</h2></div><StatusPill tone="violet">P0 新建</StatusPill></div>
      <div className="workflow-ledger-grid">
        <article><span>候选身份</span><strong>规范拉丁名 · TaxID · 具体层级</strong><small>无法可靠解析时保留unresolved，不强配。</small></article>
        <article><span>患者证据</span><strong>支持 · 反对 · 中性 · 未知 · 冲突</strong><small>每条证据带片段ID、发生时间和可见时间。</small></article>
        <article><span>外部核验</span><strong>人体适用性 · 医学蕴含 · 来源等级</strong><small>URL能打开不等于文献支持当前论断。</small></article>
        <article><span>排名变化</span><strong>谁提出 · 为何上调或下调 · 仍缺什么</strong><small>保存版本和哈希，不保存隐藏思维链。</small></article>
      </div>
    </Card>

    <Card className="workflow-memory-card">
      <div className="section-heading"><div><span className="eyebrow">受控记忆，不是自由文本长记忆</span><h2>四种信息分开治理</h2></div></div>
      <div className="workflow-memory-grid">{WORKFLOW_MEMORY_LAYERS.map((layer) => <article key={layer.title}><StatusPill tone={workflowMaturityTone(layer.maturity)}>{workflowMaturityLabel(layer.maturity)}</StatusPill><strong>{layer.title}</strong><small lang="en">{layer.en}</small><p>{layer.detail}</p></article>)}</div>
    </Card>

    <Card className="workflow-evidence-card">
      <div className="section-heading"><div><span className="eyebrow">为什么选择这条升级路线</span><h2>候选验证闭环的设计依据</h2></div><small>第三方来源与独立开发边界见仓库说明</small></div>
      <div className="workflow-evidence-grid">{WORKFLOW_DESIGN_EVIDENCE.map((item) => <article key={item.label}><strong>{item.value}</strong><span>{item.label}</span><p>{item.detail}</p></article>)}</div>
    </Card>

    <Card className="workflow-governance-card">
      <div><ShieldCheck size={22} /><div><span className="eyebrow">贯穿每一步的治理控制</span><h2>借鉴公开研究方法，保持OwlPath独立的数据边界</h2></div></div>
      <ul>{WORKFLOW_GOVERNANCE.map((item) => <li key={item}>{item}</li>)}</ul>
    </Card>
  </div>
}

function ArchitecturePage({ view, onViewChange }: { view: ArchitectureView; onViewChange: (view: ArchitectureView) => void }) {
  const [architecture, setArchitecture] = useState<ArchitectureResponse>()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    api.architecture(controller.signal)
      .then(setArchitecture)
      .catch((reason) => { if (!controller.signal.aborted) setError(apiErrorMessage(reason)) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [])

  const graph = view === 'workflow' ? undefined : architecture?.[view]
  const maturityCounts = useMemo(() => graph?.nodes.reduce<Record<string, number>>((counts, node) => {
    const key = node.maturity || '未报告'
    counts[key] = (counts[key] || 0) + 1
    return counts
  }, {}) || {}, [graph])

  const orientation = view === 'workflow'
    ? [
        { number: '01', icon: Stethoscope, title: '冻结预测时点证据', detail: '完整保留原文，严格区分发生时间与医生可见时间' },
        { number: '02', icon: FileSearch, title: '逐候选找支持与反证', detail: '先形成Top‑10至15，再分别核验人体证据、差异和治疗影响' },
        { number: '03', icon: ClipboardCheck, title: '证据更新后形成Top‑5', detail: '独立挑战、最多一次修订，并进入真实双数据库验证' },
      ]
    : view === 'current'
      ? [
          { number: '01', icon: Stethoscope, title: '读取病例资料', detail: '完整保留原文，并生成可追溯证据片段' },
          { number: '02', icon: BrainCircuit, title: '核心会诊与动态专科协作', detail: '五个核心专家固定召集，按证据最多加入六个动态专科专家' },
          { number: '03', icon: ClipboardCheck, title: '给出具体病原体Top‑5', detail: '同步提供证据、下一检查和完整运行记录' },
        ]
      : [
          { number: '01', icon: Database, title: '扩展多模态与动态先验', detail: '逐步加入影像模型、地区季节和真实世界先验' },
          { number: '02', icon: ShieldCheck, title: '增强可靠性控制', detail: '独立校准、开放集识别和受控临床验证' },
          { number: '03', icon: FlaskConical, title: '形成科学改进闭环', detail: '时间外、地区外验证与前瞻静默运行后再发布' },
        ]

  return <div className="page-stack architecture-page">
    <PageHeader
      eyebrow="系统流程 · 临床主读，工程可追溯"
      title={view === 'workflow' ? 'OwlPath 最新核心 Workflow' : '系统如何完成一次病原体分析'}
      description={view === 'workflow'
        ? '详细展示从预测时点证据冻结、多路候选召回，到逐候选支持/反证核验、证据更新、受控修订和双数据库验证的完整蓝图。'
        : '先按临床顺序了解系统做了什么；需要时再展开智能分析单元（Agent）、模型服务、版本、依赖和运行记录。'}
      actions={architecture?.version ? <details className="header-technical-details"><summary>工程版本</summary><code>{architecture.version}</code></details> : undefined}
    />
    <Card className="architecture-orientation" tone="accent">
      {orientation.map((item, index) => {
        const Icon = item.icon
        return <div className="architecture-orientation-item" key={item.number}>
          <span>{item.number}</span><Icon size={20} /><strong>{item.title}</strong><small>{item.detail}</small>
          {index < orientation.length - 1 && <ArrowRight className="architecture-orientation-arrow" size={19} aria-hidden="true" />}
        </div>
      })}
    </Card>
    <div className="segmented-tabs architecture-tabs" role="tablist" aria-label="架构视图" onKeyDown={handleTabListKeyDown}>
      <button id="architecture-tab-current" role="tab" tabIndex={view === 'current' ? 0 : -1} aria-controls="architecture-tabpanel" aria-selected={view === 'current'} className={view === 'current' ? 'active' : ''} onClick={() => onViewChange('current')}>当前真实运行 <small>Current runtime</small></button>
      <button id="architecture-tab-workflow" role="tab" tabIndex={view === 'workflow' ? 0 : -1} aria-controls="architecture-tabpanel" aria-selected={view === 'workflow'} className={view === 'workflow' ? 'active' : ''} onClick={() => onViewChange('workflow')}>最新核心 Workflow <small>Candidate verification loop</small></button>
      <button id="architecture-tab-target" role="tab" tabIndex={view === 'target' ? 0 : -1} aria-controls="architecture-tabpanel" aria-selected={view === 'target'} className={view === 'target' ? 'active' : ''} onClick={() => onViewChange('target')}>长期临床目标 <small>Target roadmap</small></button>
    </div>
    <div id="architecture-tabpanel" role="tabpanel" aria-labelledby={`architecture-tab-${view}`} className="page-stack compact">
      {view === 'workflow' ? <LatestWorkflowBlueprint /> : loading ? <Card><LoadingState label="正在读取系统分析流程…" /></Card> : error ? <InlineNotice tone="danger" title="无法读取系统架构">{error}</InlineNotice> : !graph || !architecture ? <Card><EmptyState icon={Network} title="分析服务未返回架构" description="页面不会虚构不存在的分析步骤。" /></Card> : <>
        <Card className="architecture-intro">
          <div><span className="eyebrow">{view === 'current' ? '当前已实现的软件流程' : '长期临床目标 · 不是本次运行流程'}</span><h2>{view === 'current' ? '当前系统的分析流程' : '目标系统的增强方向'}</h2><p>{view === 'current' ? '系统完整读取病例，固定召集感染科、重症/急诊、临床流行病学、检验医学和临床微生物/培养实验室五个核心专家，再根据证据从二十个动态专科中最多选择六个。已运行意见进入证据板，经外部证据核验后由总诊 Agent 生成 Top-5，再做合同检查、独立反证和最多一次修订。未被路由选中的动态专家记为 not_applicable，不会伪造会诊意见。' : '长期目标将在现有流程上增加多模态模型、动态地区与季节信息、独立可靠性检查和受控临床验证；规划内容不会冒充当前能力。'}</p><details className="architecture-source-description"><summary>工程原始架构名称与说明</summary><BilingualCopy text={graph.name} /><BilingualCopy text={graph.description} /></details></div>
          <details className="maturity-summary"><summary>工程清单与成熟度</summary><div><span>{graph.nodes.length} 个步骤</span><span>{graph.edges.length} 条依赖关系</span>{Object.entries(maturityCounts).map(([name, count]) => <StatusPill key={name} tone={/planned|target/i.test(name) ? 'violet' : /partial|research/i.test(name) ? 'warning' : 'info'}>{maturityLabel(name)} {count}</StatusPill>)}</div></details>
        </Card>
        {view === 'target' && <InlineNotice tone="warning" title="这是长期临床研发蓝图，不是本次分析能力">规划步骤只展示目标和依赖，不会生成虚假的运行状态或结果。</InlineNotice>}
        <Card className="agent-graph-card"><AgentGraphExplorer graph={graph} planes={architecture.planes} edgeTypes={architecture.edgeTypes} /></Card>
      </>}
    </div>
  </div>
}

function RunTracePanel({ run }: { run: RunDetail }) {
  const [trace, setTrace] = useState<RunTrace>()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const developmentDemo = run.runMode === 'development_demo' || run.result?.developmentDemo === true
  const running = run.status === 'queued' || run.status === 'running'
  const displayTrace = useMemo(() => trace ? {
    ...trace,
    nodes: trace.nodes.map((node) => clampTraceNodeForRunStatus(node, run.status)),
  } : undefined, [trace, run.status])

  useEffect(() => {
    if (!run.traceVersion) { setLoading(false); return }
    const controller = new AbortController()
    let eventSource: EventSource | undefined
    let pollTimer: number | undefined
    let refreshing = false
    let disposed = false
    const refresh = async () => {
      if (refreshing || disposed) return
      refreshing = true
      try {
        const next = await api.runTrace(run.runId, controller.signal)
        if (!disposed) { setTrace(next); setError('') }
      } catch (reason) {
        if (!disposed && !controller.signal.aborted) setError(apiErrorMessage(reason))
      } finally {
        refreshing = false
        if (!disposed) setLoading(false)
      }
    }
    const startPollingFallback = () => {
      if (!running || pollTimer !== undefined || disposed) return
      pollTimer = window.setInterval(() => { void refresh() }, 1600)
    }
    setLoading(true); setError('')
    void refresh()
    if (running && typeof EventSource !== 'undefined') {
      eventSource = new EventSource(`/api/runs/${encodeURIComponent(run.runId)}/events`)
      const eventTypes = ['node_started', 'node_completed', 'node_failed', 'node_skipped', 'completed', 'failed']
      const onTraceEvent = () => { void refresh() }
      eventTypes.forEach((eventType) => eventSource?.addEventListener(eventType, onTraceEvent))
      eventSource.onerror = () => {
        eventSource?.close()
        eventSource = undefined
        startPollingFallback()
      }
    } else if (running) {
      startPollingFallback()
    }
    return () => {
      disposed = true
      controller.abort()
      eventSource?.close()
      if (pollTimer !== undefined) window.clearInterval(pollTimer)
    }
  }, [run.runId, run.traceVersion, running])

  if (!run.traceVersion) return <Card><EmptyState icon={Network} title="这条历史记录没有逐步骤记录" description="该记录创建于旧版系统，结果仍保留，但不能补造当时的执行过程。" /></Card>
  if (loading) return <Card><LoadingState label="正在读取完整分析过程…" /></Card>
  if (error) return <InlineNotice tone="danger" title="无法读取分析过程">{error}</InlineNotice>
  if (!displayTrace) return <Card><EmptyState icon={Network} title="分析服务未返回运行记录" description="分析结果仍会保留；页面不会构造不存在的执行流程。" /></Card>
  return <div className="trace-panel">
    {developmentDemo && <InlineNotice tone="warning" title="研发提示不会中断本次分析">系统会记录资料范围、时间是否一致、病例是否超出模型熟悉范围以及分数是否校准；在研发测试模式下，这些仅作为提示。原始工程代码可在节点详情中查看。</InlineNotice>}
    <details className="card trace-manifest trace-technical-details"><summary>工程信息：轨迹版本、流程图版本与步骤数</summary><div className="trace-manifest-grid"><div><span className="eyebrow">轨迹版本 · TRACE VERSION</span><strong>{displayTrace.traceVersion || run.traceVersion}</strong></div><div><span className="eyebrow">流程图版本 · GRAPH VERSION</span><strong>{displayTrace.version || '未报告'}</strong></div><div><span className="eyebrow">步骤数 · NODES</span><strong>{displayTrace.nodes.length}</strong></div></div></details>
    <Card className="agent-graph-card"><AgentGraphExplorer key={`${run.runId}-${run.status}`} graph={displayTrace} developmentDemo={developmentDemo} loadNodeDetail={(nodeId) => api.traceNode(run.runId, nodeId, undefined, run.status)} /></Card>
  </div>
}

function RunMonitor({ run, onGoCase }: { run?: RunDetail; onGoCase: () => void }) {
  const running = run?.status === 'running' || run?.status === 'queued'
  const terminalFailure = run?.status === 'failed' || run?.status === 'cancelled'
  const developmentDemo = run?.runMode === 'development_demo'
  const developmentV3 = run?.resultSchemaVersion === 'owlpath.result.v3' || run?.traceVersion === 'owlpath.trace.v2' || Boolean(run?.result?.developmentResult)
  const currentPhase = clinicalRunPhase(run?.currentStage)
  const runErrorCopy = run?.error ? clinicalTechnicalError(run.error) : undefined
  return (
    <div className="page-stack">
      <PageHeader
        eyebrow={developmentDemo ? '本次分析 · 研发测试' : '本次分析 · 可追溯运行'}
        title={terminalFailure ? '本次病原体分析未完成' : run?.status === 'completed' ? '本次病原体分析已完成' : '病原体分析进度'}
        description={terminalFailure
          ? '分析流程已经停止；页面会说明技术原因，并保留已完成的分析步骤和工程记录。'
          : developmentDemo
            ? developmentV3 ? '系统正在完成核心证据分析和按需专病路由，随后建立证据板、检索与核对多来源证据，并通过总诊和独立反证生成前5位具体病原体。' : '进度由分析服务实时返回；正在对虚构或已脱敏文本调用所选模型 Provider。'
            : '每一步都来自分析服务的真实记录；页面不会用动画伪造分析进度。'}
      />

      {developmentDemo && (developmentV3
        ? <InlineNotice tone="info">研发测试：候选分数只用于排序，不代表患病概率；资料和模型提示会被记录，但不会在流程仍可继续时中断。</InlineNotice>
        : <DevelopmentDemoBanner bypassedControls={run?.result?.demoBypassedControls} />)}

      {!run ? (
        <Card><EmptyState icon={Activity} title="当前没有正在分析的病例" description="先粘贴一段测试病例，再开始病原体分析。" action={<button className="button button-primary" onClick={onGoCase}><Stethoscope size={16} />去新建病原体分析</button>} /></Card>
      ) : (
        <>
          <Card className="run-overview-card" tone="accent">
            <div className="run-overview-head">
              <div>
                <div className="eyebrow">本次病例分析</div>
                <h2>{run.caseSummary || '病例推演'}</h2>
                <p>开始时间 {formatDate(run.createdAt)}{running && run.currentStage ? ` · 当前步骤：${currentPhase.primary}` : run.status === 'completed' ? ' · 分析流程已结束' : terminalFailure ? ' · 分析流程已停止' : ''}</p>
              </div>
              <StatusPill
                tone={run.status === 'completed' ? 'success' : run.status === 'failed' ? 'danger' : run.status === 'cancelled' ? 'warning' : 'info'}
                icon={run.status === 'completed' ? CheckCircle2 : run.status === 'failed' ? XCircle : running ? LoaderCircle : AlertCircle}
              >
                {runStatusLabel(run.status)}
              </StatusPill>
            </div>
            {terminalFailure ? <div className="run-terminal-summary"><XCircle size={18} /><div><strong>流程已停止，未生成完整结果</strong><span>已完成的步骤仍保留在下方；具体原因见技术提示和工程记录。</span></div></div> : <>
              <div className="progress-track"><div style={{ width: `${Math.max(0, Math.min(100, run.progress || 0))}%` }} /></div>
              <div className="progress-caption"><span>{run.currentStage ? currentPhase.primary : run.status === 'completed' ? '分析已完成' : '等待分析服务返回当前步骤'}</span><strong>{run.progress || 0}%</strong></div>
            </>}
            <details className="run-technical-details"><summary>工程信息：运行编号与原始阶段</summary><code>{run.runId}</code>{run.currentStage && <code>{run.currentStage}</code>}</details>
          </Card>

          {run.runMode === 'retrospective' && <InlineNotice tone="warning" title="回顾性科研回放">
            这是历史病例复盘，不是当前病例分析。预先规定的资料截止点：{run.retrospectiveAnchorId || '分析服务未返回编号'}。
          </InlineNotice>}

          {runErrorCopy && <InlineNotice tone={run.status === 'cancelled' ? 'warning' : 'danger'} title={runErrorCopy.title}><p>{runErrorCopy.reason}</p><p>{runErrorCopy.action}</p><details className="inline-technical-error"><summary>查看安全工程错误码</summary><code>{runErrorCopy.engineeringCode}</code></details></InlineNotice>}

          <div className="content-grid two-one">
            <Card>
              <SectionHeading icon={FileClock} title="分析步骤" description="每一步的完成情况和耗时均由分析服务记录。" />
              {run.stages.length === 0 ? (
                <EmptyState icon={FileClock} title="等待分析步骤" description="本次分析已经创建，正在等待服务返回逐步骤记录。" />
              ) : (
                <div className="run-timeline">
                  {run.stages.map((stage, index) => {
                    const phaseCopy = clinicalRunPhase(stage.name)
                    return (
                    <div className={cx('run-stage', `stage-${stage.status}`)} key={stage.id || index}>
                      <div className="stage-rail"><div className="stage-node">{stageIcon(stage.status)}</div>{index < run.stages.length - 1 && <div className="stage-line" />}</div>
                      <div className="stage-content">
                        <div><strong>{phaseCopy.primary}</strong><span>{formatDuration(stage.durationMs)}</span></div>
                        <p>{phaseCopy.detail}</p>
                        <details><summary>工程阶段与记录</summary><small>{stage.name}{stage.description ? ` · ${stage.description}` : ''}{stage.message ? ` · ${stage.message}` : ''}</small></details>
                      </div>
                    </div>
                  )})}
                </div>
              )}
            </Card>

            <Card>
              <SectionHeading icon={BrainCircuit} title="本次调用的模型" description="模型服务和详细状态可在工程信息中继续查看。" />
              {run.models.length === 0 ? (
                <EmptyState icon={BrainCircuit} title="尚无模型状态" description="等待分析服务报告本次调用的模型。" />
              ) : (
                <div className="model-status-list">
                  {run.models.map((model, modelIndex) => {
                    const modelStatus = clinicalDevelopmentAgentStatus(model.status)
                    return (
                    <div className="model-status-item" key={`${model.providerId}-${model.model || ''}-${modelIndex}`}>
                      <div className={cx('model-status-icon', `stage-${model.status}`)}>{stageIcon(model.status)}</div>
                      <div><strong>{model.providerName || model.providerId}</strong><small>{model.model || (model.providerKind ? kindMeta(model.providerKind).name : '后端模型')}</small></div>
                      <div className="model-status-time"><strong>{formatDuration(model.latencyMs)}</strong><small>{modelStatus.primary}{modelStatus.researchHint ? ` · ${modelStatus.researchHint}` : ''}</small></div>
                    </div>
                  )})}
                </div>
              )}
            </Card>
          </div>
          {run.traceVersion && <RunTracePanel run={run} />}
        </>
      )}
    </div>
  )
}

type DevelopmentViewResult = NonNullable<NonNullable<RunDetail['result']>['developmentResult']>

function developmentStatusLabel(status: DevelopmentViewResult['status']): string {
  return clinicalDevelopmentRunStatus(status).primary
}

function developmentAgentStatusLabel(status?: string): string {
  return clinicalDevelopmentAgentStatus(status).primary
}

function developmentReviewStatusLabel(status?: string, passed?: boolean): string {
  if (!status) return passed === false ? '发现问题' : '已完成'
  return {
    accepted: '复核已完成',
    critic_accepted: '独立审稿已通过',
    revision_completed_not_re_reviewed: '修订已完成，尚未再次审稿',
    critic_changes_not_closed: '审稿提出的修改尚未全部闭环',
    critic_unavailable: '独立审稿未返回；仅合同检查通过',
    technical_failure: '技术失败，未形成结果',
    approved: '未发现需修订问题',
    passed: '未发现需修订问题',
    revision_required: '需要修订',
    revision_rejected: '修订未采纳',
    failed: '技术失败',
    not_reviewed: '未审稿',
  }[status] || status.replaceAll('_', ' ')
}

function developmentAgentNodeKey(role?: string): string | undefined {
  if (!role) return undefined
  if (role.startsWith('specialist:')) return role
  const specialistRoles = [
    ...CORE_CLINICAL_EXPERT_ROLE_IDS,
    ...DYNAMIC_CLINICAL_EXPERT_ROLE_IDS,
    'timeline_course', 'host_susceptibility', 'syndrome_localization', 'exposure_one_health',
    'lab_pathophysiology', 'organ_severity', 'imaging_dissemination', 'microbiology_treatment',
    'neuroinfection', 'immunocompromised_opportunistic', 'travel_zoonotic', 'healthcare_device_amr',
    // v1 compatibility
    'timeline_host', 'syndrome_site', 'exposure_epidemiology', 'laboratory_organ_injury', 'imaging_microbiology_treatment',
  ]
  if (specialistRoles.includes(role)) return `specialist:${role}`
  return {
    evidence_retrieval: 'evidence_verifier',
    medical_evidence_retrieval: 'evidence_verifier',
    complexity_router: 'complexity_router',
    evidence_board: 'evidence_board',
    retrieval_planner: 'retrieval_planner',
    literature_retrieval: 'literature_retrieval',
    public_health_retrieval: 'public_health_retrieval',
    evidence_verifier: 'evidence_verifier',
    pathogen_synthesis: 'synthesis',
    pathogen_chief_synthesis: 'synthesis',
    independent_critic: 'critic',
    independent_medical_critic: 'critic',
  }[role]
}

function traceRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function traceLocalizedLine(value: unknown): string | undefined {
  if (typeof value === 'string' && value.trim()) return value.trim()
  const record = traceRecord(value)
  const localized = traceRecord(record.statement_i18n || record.statementI18n || record.message_i18n || record.messageI18n || value)
  const zh = typeof localized.zh_cn === 'string' ? localized.zh_cn : typeof localized.zhCn === 'string' ? localized.zhCn : undefined
  const en = typeof localized.en === 'string' ? localized.en : undefined
  return [zh, en && en !== zh ? en : undefined].filter(Boolean).join(' / ') || undefined
}

function enrichDevelopmentAgentObservation(agent: DevelopmentAgentObservation, node?: AgentNode): DevelopmentAgentObservation {
  if (!node) return agent
  const preferredArtifactKeys = node.nodeKey === 'retrieval' || node.nodeKey === 'evidence_verifier'
    ? ['federated_medical_evidence_metadata', 'medical_evidence_metadata']
    : ['development_agent_output', 'critic_issue_reconciliation']
  const artifact = preferredArtifactKeys
    .map((key) => node.artifacts?.find((item) => item.key === key))
    .find(Boolean)
  const output = artifact?.json
  const record = traceRecord(output)
  const observations = Array.isArray(record.observations) ? record.observations.map(traceRecord) : []
  const statements = (kind: string) => observations
    .filter((item) => item.kind === kind)
    .map((item) => traceLocalizedLine(item.statement_i18n || item.statementI18n || item))
    .filter((item): item is string => Boolean(item))
  const candidateValues = Array.isArray(record.candidate_pool)
    ? record.candidate_pool
    : Array.isArray(record.concrete_pathogens) ? record.concrete_pathogens : []
  const candidatePool = candidateValues.map((value) => {
    const candidate = traceRecord(value)
    const name = candidate.canonical_latin_name || candidate.canonicalName || candidate.name
    return typeof name === 'string' ? name : undefined
  }).filter((item): item is string => Boolean(item))
  const keyFacts = [
    ...statements('key_fact'),
    ...statements('supporting_pattern'),
    ...statements('opposing_pattern'),
  ]
  return {
    ...agent,
    provider: node.provider || agent.provider,
    model: node.model || agent.model,
    status: node.status === 'failed' ? 'failed' : agent.status,
    keyFacts: keyFacts.length ? keyFacts : agent.keyFacts,
    contradictions: statements('contradiction').length ? statements('contradiction') : agent.contradictions,
    missingInformation: statements('missing_information').length ? statements('missing_information') : agent.missingInformation,
    candidatePool: candidatePool.length ? candidatePool : agent.candidatePool,
    structuredOutput: output ?? (node.error ? { status: node.status, outcome: node.outcome, error: node.error } : agent.structuredOutput),
  }
}

function DevelopmentV3Result({
  run,
  resultTab,
  onTabChange,
  onGoCompare,
}: {
  run: RunDetail
  resultTab: ResultTab
  onTabChange: (tab: ResultTab) => void
  onGoCompare: () => void
}) {
  const result = run.result!
  const development = result.developmentResult!
  const presentation = clinicalDevelopmentResultPresentation(development.status)
  const failureCopy = presentation.technicalFailure ? clinicalTechnicalError(run.error || { code: 'development_technical_failure' }) : undefined
  const reviewTone = ['failed', 'technical_failure'].includes(development.review.status || '')
    ? 'danger'
    : ['revision_completed_not_re_reviewed', 'critic_changes_not_closed', 'critic_unavailable'].includes(development.review.status || '')
      || development.review.passed === false || development.review.issues.length
      ? 'warning'
      : 'success'
  const [agentNodeDetails, setAgentNodeDetails] = useState<Record<string, AgentNode>>({})
  const [agentDetailsLoading, setAgentDetailsLoading] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    setAgentNodeDetails({})
    setAgentDetailsLoading(true)
    void (async () => {
      try {
        const trace = await api.runTrace(run.runId, controller.signal)
        const detailEntries = await Promise.all(development.agentObservations.map(async (agent) => {
          const nodeKey = developmentAgentNodeKey(agent.role)
          const compatibleNodeKeys = agent.role === 'evidence_retrieval' || agent.role === 'medical_evidence_retrieval'
            ? [nodeKey, 'retrieval']
            : [nodeKey]
          const node = trace.nodes.find((item) => compatibleNodeKeys.includes(item.nodeKey))
          if (!node) return undefined
          return [agent.role || agent.id, await api.traceNode(run.runId, node.id, controller.signal, run.status)] as const
        }))
        if (!controller.signal.aborted) setAgentNodeDetails(Object.fromEntries(detailEntries.filter((item): item is readonly [string, AgentNode] => Boolean(item))))
      } catch (error) {
        if (!controller.signal.aborted) console.warn('Unable to enrich Agent result cards from trace', error)
      } finally {
        if (!controller.signal.aborted) setAgentDetailsLoading(false)
      }
    })()
    return () => controller.abort()
  }, [run.runId])

  const displayedAgentObservations = development.agentObservations.map((agent) => (
    enrichDevelopmentAgentObservation(agent, agentNodeDetails[agent.role || agent.id])
  ))
  const corePerspectiveAgents = displayedAgentObservations.filter((agent) => developmentAgentGroup(agent.role) === 'core_perspective')
  const dynamicSpecialistAgents = displayedAgentObservations.filter((agent) => developmentAgentGroup(agent.role) === 'dynamic_specialist')
  const systemProcessAgents = displayedAgentObservations.filter((agent) => developmentAgentGroup(agent.role) === 'system_process')
  const citedEvidenceIds = new Set(development.concretePathogens.flatMap((pathogen) => pathogen.citationIds))
  const citedEvidenceSources = development.evidenceSources.filter((source) => citedEvidenceIds.has(source.id))
  const pathogensByCitation = new Map<string, string[]>()
  development.concretePathogens.forEach((pathogen) => pathogen.citationIds.forEach((citationId) => {
    pathogensByCitation.set(citationId, [...(pathogensByCitation.get(citationId) || []), pathogen.displayNameI18n?.zhCn || pathogen.canonicalName])
  }))
  const providerCount = new Set(run.models.map((model) => model.providerId)).size
  const top5Panel = <Card className="development-top5-card">
    <SectionHeading icon={Microscope} title="最可能的5种具体病原体" description="Top 5 specific pathogens · 排名依据当前病例资料生成；点击病原体可查看支持、反对及不确定信息。" />
    {development.concretePathogens.length === 0 ? <EmptyState icon={Microscope} title="未生成具体病原体" description="本次结果格式或病原体名称核验未通过，请在“病例与文献依据”查看研发提示。" /> : <div className="pathogen-disclosure-list">
      {development.concretePathogens.map((pathogen) => <details className="pathogen-disclosure" key={`${pathogen.rank}-${pathogen.canonicalName}`}>
        <summary>
          <span className="development-pathogen-rank">{pathogen.rank}</span>
          <span className="pathogen-disclosure-name"><strong><BilingualCopy text={pathogen.displayNameI18n} zh={pathogen.canonicalName} /></strong></span>
          <span className="pathogen-disclosure-reason">{pathogen.specificityRationale?.zhCn || pathogen.specificityRationale?.en || '后端未返回入选理由'}</span>
          <span className="development-score"><small>排序分（未校准）</small><strong>{formatModelScore(pathogen.modelScore)}</strong></span>
          <span className="pathogen-disclosure-action">查看入选与排除依据 <ChevronDown size={15} /></span>
        </summary>
        <div className="pathogen-disclosure-body">
          {pathogen.specificityRationale && <p className="development-rationale"><strong>为什么考虑它</strong><BilingualCopy text={pathogen.specificityRationale} /></p>}
          <div className="development-evidence-columns">
            <div><strong>支持证据</strong>{pathogen.evidenceFor.length ? <ul>{pathogen.evidenceFor.map((item, index) => <li key={index}>{item}</li>)}</ul> : <small>未返回</small>}</div>
            <div><strong>反对证据</strong>{pathogen.evidenceAgainst.length ? <ul>{pathogen.evidenceAgainst.map((item, index) => <li key={index}>{item}</li>)}</ul> : <small>未返回</small>}</div>
          </div>
          {pathogen.uncertaintyReason && <p className="development-uncertainty"><strong>哪些信息还不能确定</strong><BilingualCopy text={pathogen.uncertaintyReason} /></p>}
          <details className="development-pathogen-engineering">
            <summary>工程与追溯信息：名称核验、病例片段、文献及分析来源</summary>
            <div className="development-taxonomy">
              <span>规范拉丁名：<strong><i>{pathogen.canonicalName}</i></strong></span>
              <span>NCBI Taxonomy ID：<strong>{pathogen.taxonomyId || '未解析'}</strong></span>
              <StatusPill tone={/resolved|verified|matched/i.test(pathogen.taxonomyStatus || '') ? 'success' : 'warning'}>{pathogen.taxonomyStatus || '解析状态未报告'}</StatusPill>
            </div>
            <div className="development-pathogen-trace">
              <span>病例片段 {pathogen.sourceFragmentIds.length ? pathogen.sourceFragmentIds.join(' · ') : '未引用'}</span>
              <span>文献 {pathogen.citationIds.length ? pathogen.citationIds.join(' · ') : '未引用'}</span>
              <span>分析单元（Agent）{pathogen.agentSources.length ? pathogen.agentSources.join(' · ') : '未报告'}</span>
            </div>
          </details>
        </div>
      </details>)}
    </div>}
  </Card>

  const summaryPanel = <Card className="bilingual-summary-card development-summary-card">
    <SectionHeading icon={FileSearch} title="综合判断 / Integrated assessment" description="结合核心证据 Agent、本次被路由选中的专病 Agent、统一证据板，以及可获得的外部证据与检索覆盖状态形成。" />
    {development.summaryI18n ? <p><BilingualCopy text={development.summaryI18n} /></p> : <p className="empty-inline">综合分析单元未返回摘要。</p>}
    <div className="development-overview-strip">
      <div><span>未知或系统未覆盖病原体分数（未校准）</span><strong>{formatModelScore(development.unknownScore)}</strong></div>
      <div><span>主要病原大类</span><strong>{development.categoryOverview[0] ? <BilingualCopy text={development.categoryOverview[0].nameI18n} zh={development.categoryOverview[0].name} /> : '未返回'}</strong></div>
      <div><span>是否考虑共感染</span><strong>{development.coinfectionHypotheses.length ? '是' : '本次未提出'}</strong></div>
    </div>
    {development.coinfectionHypotheses.length > 0 && <details className="development-clinical-details"><summary>查看具体共感染组合</summary><div>{development.coinfectionHypotheses.map((item, index) => <p key={index}><strong>{item.pathogens.join(' + ')}</strong>{item.modelScore !== undefined && <span> · 排序分（未校准）{formatModelScore(item.modelScore)}</span>}{item.rationaleI18n && <> · <BilingualCopy text={item.rationaleI18n} /></>}</p>)}</div></details>}
    {development.categoryOverview.length > 0 && <details className="development-clinical-details"><summary>查看病原大类参考分数（未校准）</summary><div className="development-category-row">{development.categoryOverview.map((item) => <span key={item.name}><BilingualCopy text={item.nameI18n} zh={item.name} /><strong>{formatModelScore(item.modelScore)}</strong></span>)}</div></details>}
    <details className="development-clinical-details"><summary>结果生成与复核记录</summary><p>独立复核：<StatusPill tone={reviewTone}>{developmentReviewStatusLabel(development.review.status, development.review.passed)}</StatusPill></p></details>
  </Card>

  const testsPanel = <Card className="development-next-tests">
    <SectionHeading icon={TestTube2} title="下一步优先完善的检查 / Suggested next tests" description="按预计能否缩小鉴别范围排序；仅供研发推演参考，不自动形成医嘱。" />
    {!development.nextTests.length ? <p className="empty-inline">本次没有返回下一检查建议。</p> : <div className="development-test-list">{development.nextTests.map((test, index) => <article key={`${test.code || test.name}-${index}`}>
      <span>{String(index + 1).padStart(2, '0')}</span>
      <div><h3><BilingualCopy text={test.nameI18n} zh={test.name} /></h3><p><BilingualCopy text={test.rationaleI18n} zh={test.rationale} /></p><small>{[test.specimen && `标本：${test.specimen}`, test.turnaround && `预计回报：${test.turnaround}`, test.availability && `可及性：${test.availability}`].filter(Boolean).join(' · ') || '未返回标本和周转信息'}</small></div>
      <strong><small>检查优先级分（未校准）</small>{formatModelScore(test.expectedInformationGain)}</strong>
    </article>)}</div>}
  </Card>

  const renderAgentObservation = (agent: DevelopmentAgentObservation) => {
      const providerDisplay = run.models.find((model) => model.providerId === agent.provider)?.providerName || agent.provider
      const roleCopy = clinicalAgentRole(agent.role)
      const statusCopy = clinicalDevelopmentAgentStatus(agent.status)
      return <details key={agent.id}>
      <summary><span><strong>{roleCopy.primary}</strong><small>{statusCopy.researchHint || roleCopy.detail}</small></span><StatusPill tone={statusCopy.tone === 'danger' ? 'danger' : statusCopy.tone === 'warning' ? 'warning' : 'info'}>{developmentAgentStatusLabel(agent.status)}</StatusPill></summary>
      <div className="development-agent-body">
        {agent.summaryI18n && <p className="development-agent-summary"><BilingualCopy text={agent.summaryI18n} /></p>}
        <div className="development-agent-columns">
          <div><strong>与判断相关的关键信息</strong>{agent.keyFacts.length ? <ul>{agent.keyFacts.map((item, itemIndex) => <li key={itemIndex}>{item}</li>)}</ul> : <small>未返回</small>}</div>
          <div><strong>需要核对的信息</strong>{agent.contradictions.length ? <ul>{agent.contradictions.map((item, itemIndex) => <li key={itemIndex}>{item}</li>)}</ul> : <small>未发现或未返回</small>}</div>
          <div><strong>建议补充的信息</strong>{agent.missingInformation.length ? <ul>{agent.missingInformation.map((item, itemIndex) => <li key={itemIndex}>{item}</li>)}</ul> : <small>未返回</small>}</div>
          <div><strong>该视角提出的病原体</strong>{agent.candidatePool.length ? <div className="development-agent-candidates">{agent.candidatePool.map((item) => <StatusPill key={item}>{item}</StatusPill>)}</div> : <small>未返回</small>}</div>
        </div>
        <details className="agent-engineering-details"><summary>工程信息：智能分析单元（Agent）、模型服务与角色代码</summary><dl><div><dt>系统原始名称</dt><dd><BilingualCopy text={agent.nameI18n} zh={agent.role || agent.id} /></dd></div><div><dt>角色代码</dt><dd>{agent.role || agent.id}</dd></div><div><dt>模型服务（Provider）</dt><dd>{providerDisplay || '未报告'}</dd></div><div><dt>模型</dt><dd>{agent.model || '未报告'}</dd></div><div><dt>原始状态</dt><dd>{agent.status || '未报告'}</dd></div></dl></details>
        {agent.structuredOutput !== undefined && <details className="safety-json"><summary>工程结构化输出（JSON）</summary><pre>{JSON.stringify(agent.structuredOutput, null, 2)}</pre></details>}
      </div>
    </details>
  }

  const agentsPanel = <Card className="development-agent-card">
    <SectionHeading icon={BrainCircuit} title="各分析环节意见" description="先看本次实际返回的核心临床专家和被路由召集的动态专科专家，再查看证据板、多源检索、总诊与独立反证等系统环节。" />
    {agentDetailsLoading && <LoadingState label="正在读取各分析环节的结构化意见…" />}
    {!displayedAgentObservations.length ? <p className="empty-inline">本次结果没有附带结构化分析意见；可前往系统运行记录查看技术节点。</p> : <div className="development-agent-groups">
      {corePerspectiveAgents.length > 0 && <section className="development-agent-group core-agent-group"><header><strong>本次返回的核心临床专家会诊</strong><small>感染科、重症/急诊、临床流行病学、检验医学和临床微生物/培养实验室分别交付结构化意见。</small></header><div className="development-agent-list">{corePerspectiveAgents.map(renderAgentObservation)}</div></section>}
      {dynamicSpecialistAgents.length > 0 && <section className="development-agent-group dynamic-agent-group"><header><strong>本次被召集的动态临床专科专家</strong><small>这里只展示实际返回的专家；哪些角色为 selected 或 not_applicable，以“系统运行记录”为准。</small></header><div className="development-agent-list">{dynamicSpecialistAgents.map(renderAgentObservation)}</div></section>}
      {systemProcessAgents.length > 0 && <section className="development-agent-group"><header><strong>证据综合、总诊与反证</strong><small>包括证据板、多源检索与核验、病原体综合排序和独立反证，不代表新的临床专科会诊。</small></header><div className="development-agent-list">{systemProcessAgents.map(renderAgentObservation)}</div></section>}
    </div>}
  </Card>

  const evidencePanel = <div className="page-stack compact">
    {citedEvidenceSources.length > 0 ? <Card>
      <SectionHeading icon={BookOpenCheck} title="系统检索到的外部医学证据 / Retrieved medical evidence" description="来源可包括医学文献、类似病例、权威指南和公共卫生/疫情信号；它们用于核对候选，不能替代患者本人的病原学结果。" />
      <div className="development-source-list">{citedEvidenceSources.map((source) => <article key={source.id}><div className="source-clinical-link"><small>关联候选病原体</small><strong>{pathogensByCitation.get(source.id)?.join('、') || '未建立候选关联'}</strong></div><div><strong>{plainCitationTitle(source.title) || source.source || '未命名来源'}</strong>{source.summaryI18n && <p><BilingualCopy text={source.summaryI18n} /></p>}<small>{[source.source, source.publishedAt].filter(Boolean).join(' · ')}</small><details><summary>工程引用编号</summary><code>{source.id}</code></details></div>{source.url && <a href={source.url} target="_blank" rel="noreferrer">打开文献原文 <ArrowRight size={13} /></a>}</article>)}</div>
    </Card> : <Card><EmptyState icon={BookOpenCheck} title="没有可展示的外部证据来源" description="多源检索离线、无命中或没有符合绑定规则的来源时，分析仍可完成，并在研发提示中说明；无命中不能被解释为无疫情或无相关证据。" /></Card>}
    <details className="development-diagnostics">
      <summary><span><TriangleAlert size={17} />研发与数据质量提示</span><small>资料缺失、时间不确定、结果复核和系统回退记录</small></summary>
      <div>
        <section><strong>研发提示</strong><p>{development.warnings.length ? `系统记录了 ${development.warnings.length} 项资料或技术提示；这些提示不等同于患者风险告警。` : '没有返回研发提示。'}</p>{development.warnings.length > 0 && <details className="agent-engineering-details"><summary>工程提示原始记录</summary><ul>{development.warnings.map((item, index) => <li key={index}>{item}</li>)}</ul></details>}</section>
        <section><strong>独立结果复核</strong><p>状态：{developmentReviewStatusLabel(development.review.status, development.review.passed)} · 修订：{development.review.revisionAttempted ? '已尝试' : '未尝试或未报告'} · 候选池回退：{development.review.fallbackUsed ? '已使用' : '未使用'}</p>{development.review.issues.length > 0 && <><p>复核记录了 {development.review.issues.length} 项需要处理的问题；医生主层不直接显示原始工程文本。</p><details className="agent-engineering-details"><summary>工程复核问题原始记录</summary><ul>{development.review.issues.map((item, index) => <li key={index}>{item}</li>)}</ul></details></>}</section>
      </div>
    </details>
    <details className="card version-footer engineering-version-footer"><summary>工程信息：模型、数据与版本</summary><div className="version-footer-grid"><div><span>结果格式合同</span><strong>{result.schemaVersion || 'owlpath.result.v3'}</strong></div><div><span>执行图</span><strong>{run.executionGraphVersion || '等待分析服务报告'}</strong></div><div><span>模型 / 知识</span><strong>{result.modelVersion || result.knowledgeVersion || '未报告'}</strong></div></div></details>
  </div>

  const failurePanel = <Card className="development-failure-result">
    <SectionHeading icon={XCircle} title="本次未生成可解释的病原体结果" description="技术流程没有完成结果合同，页面不会把不完整候选、1.00 等占位分数或部分摘要当作临床结论展示。" />
    <div className="development-failure-explainer"><strong>仍可查看的内容</strong><p>已经返回的核心或动态分析意见可以帮助定位系统在哪一步中断；它们只是研发记录，不等同于最终病原体排名。</p></div>
    <div className="development-failure-actions"><button className="button button-secondary" onClick={() => onTabChange('agents')}><BrainCircuit size={16} />查看已返回的分析意见</button><button className="button button-secondary" onClick={() => onTabChange('trace')}><Network size={16} />查看系统运行记录</button></div>
    <details className="agent-engineering-details"><summary>工程信息：失败状态、原始复核问题与运行编号</summary><dl><div><dt>运行编号</dt><dd>{run.runId}</dd></div><div><dt>结果状态</dt><dd>{development.status}</dd></div><div><dt>安全错误码</dt><dd>{failureCopy?.engineeringCode || 'development_result_contract_failed'}</dd></div></dl>{development.review.issues.length > 0 && <ul>{development.review.issues.map((item, index) => <li key={index}>{item}</li>)}</ul>}</details>
  </Card>

  return <div className="page-stack development-v3-result">
    <PageHeader
      eyebrow="研发模式 · 多视角联合分析"
      title={presentation.pageTitle}
      description={presentation.pageDescription}
      actions={presentation.technicalFailure ? <button className="button button-secondary" onClick={() => onTabChange('trace')}><Network size={16} />查看运行记录</button> : <button className="button button-secondary" onClick={onGoCompare}><BarChart3 size={16} />{providerCount > 1 ? '模型对比' : '各分析视角对照'}</button>}
    />

    <details className="card development-run-summary">
      <summary>
        <StatusPill tone={development.status === 'technical_failure' ? 'danger' : development.status === 'completed_with_warnings' ? 'warning' : 'success'}>{developmentStatusLabel(development.status)}</StatusPill>
        {development.status === 'completed_with_warnings' && <small>有研发提示</small>}
        <span>分析时点 {formatDate(run.decisionTime)}</span>
        <span>结果生成 {formatDate(result.generatedAt)}</span>
        <strong>本次分析信息 <ChevronDown size={15} /></strong>
      </summary>
      <div className="development-run-summary-grid">
        <div><span>病例</span><strong>{run.caseSummary || '本次测试病例'}</strong></div>
        <div><span>分析时点 / 结果生成</span><strong>{formatDate(run.decisionTime)} · {formatDate(result.generatedAt)}</strong></div>
        <div><span>分析视角意见</span><strong>{development.agentObservations.length || '—'} 份</strong></div>
        <div><span>工程运行编号</span><strong>{run.runId}</strong></div>
      </div>
    </details>

    <p className={cx('development-mode-note', presentation.technicalFailure && 'is-failure')} role="status"><CircleHelp size={16} />{presentation.modeNote}</p>

    {presentation.technicalFailure && failureCopy && <InlineNotice tone="danger" title={failureCopy.title}><p>{failureCopy.reason}</p><p>{failureCopy.action}</p></InlineNotice>}

    <div className="segmented-tabs result-tabs result-view-tabs" role="tablist" aria-label="结果视图" onKeyDown={handleTabListKeyDown}>
      <button id="result-tab-summary" role="tab" tabIndex={resultTab === 'summary' ? 0 : -1} aria-controls="result-tabpanel" aria-selected={resultTab === 'summary'} className={resultTab === 'summary' ? 'active' : ''} onClick={() => onTabChange('summary')}><ClipboardCheck size={16} />{presentation.summaryTabLabel} <small>{presentation.technicalFailure ? 'Failure summary' : 'Clinical result'}</small></button>
      <button id="result-tab-agents" role="tab" tabIndex={resultTab === 'agents' ? 0 : -1} aria-controls="result-tabpanel" aria-selected={resultTab === 'agents'} className={resultTab === 'agents' ? 'active' : ''} onClick={() => onTabChange('agents')}><BrainCircuit size={16} />{presentation.agentTabLabel} <small>Analysis views</small></button>
      <button id="result-tab-evidence" role="tab" tabIndex={resultTab === 'evidence' ? 0 : -1} aria-controls="result-tabpanel" aria-selected={resultTab === 'evidence'} className={resultTab === 'evidence' ? 'active' : ''} onClick={() => onTabChange('evidence')}><BookOpenCheck size={16} />病例与文献依据 <small>Evidence</small></button>
      <button id="result-tab-trace" role="tab" tabIndex={resultTab === 'trace' ? 0 : -1} aria-controls="result-tabpanel" aria-selected={resultTab === 'trace'} className={resultTab === 'trace' ? 'active' : ''} onClick={() => onTabChange('trace')}><Network size={16} />系统运行记录 <small>Technical trace</small></button>
    </div>

    <div id="result-tabpanel" role="tabpanel" aria-labelledby={`result-tab-${resultTab}`}>
      {resultTab === 'trace' ? <RunTracePanel run={run} /> : resultTab === 'agents' ? agentsPanel : resultTab === 'evidence' ? evidencePanel : presentation.showClinicalResult ? <div className="page-stack compact">{top5Panel}{summaryPanel}{testsPanel}</div> : failurePanel}
    </div>
  </div>
}

const DISPOSITION_META: Record<SafetyDisposition, { label: string; description: string; tone: 'success' | 'info' | 'warning' | 'danger' | 'violet'; icon: LucideIcon }> = {
  non_infection: { label: '当前更支持非感染性方向', description: '现有证据不支持继续发布病原体排名。', tone: 'violet', icon: ShieldCheck },
  species_supported: { label: '可报告物种级预测集合', description: '数据与可靠性支持到物种层级。', tone: 'success', icon: CheckCircle2 },
  category_only: { label: '仅报告病原大类', description: '可以支持大类方向，但不宜强行精确到物种。', tone: 'info', icon: Microscope },
  more_information_needed: { label: '需要更多关键信息', description: '补充信息后才可进一步缩小预测集合。', tone: 'warning', icon: TestTube2 },
  abstain: { label: '弃答并转人工', description: '当前证据不支持发布可靠的病原体结论。', tone: 'danger', icon: OctagonX },
}

const DISPOSITION_EN: Record<SafetyDisposition, string> = {
  non_infection: 'Current evidence favors a non-infectious direction',
  species_supported: 'Species-level prediction set may be reported',
  category_only: 'Report pathogen category only',
  more_information_needed: 'More critical information is needed',
  abstain: 'Abstain and refer for human review',
}

function ResultOverview({
  run,
  resultTab = 'summary',
  onTabChange,
  onGoRun,
  onGoCompare,
}: {
  run?: RunDetail
  resultTab?: ResultTab
  onTabChange: (tab: ResultTab) => void
  onGoRun: () => void
  onGoCompare: () => void
}) {
  const result = run?.result
  const developmentDemo = run?.runMode === 'development_demo' || result?.developmentDemo === true
  const hasDevelopmentV3 = Boolean(run && result?.developmentResult)
  const terminalWithoutResult = Boolean(run && !result && (run.status === 'failed' || run.status === 'cancelled'))
  const terminalWithoutResultCopy = terminalWithoutResult
    ? clinicalTechnicalError(run?.error || { code: run?.status === 'cancelled' ? 'run_interrupted' : 'development_technical_failure' })
    : undefined
  const legacyResultTab = resultTab === 'trace' ? 'trace' : 'summary'
  const compatibleResultTab = resultTabForLoadedContract(resultTab, Boolean(result), hasDevelopmentV3)
  useEffect(() => {
    // On a direct URL refresh, `run` is briefly undefined while the API request
    // is in flight. Do not let that loading gap erase ?tab=agents/evidence.
    if (result && resultTab !== compatibleResultTab) onTabChange(compatibleResultTab)
  }, [compatibleResultTab, onTabChange, result, resultTab])
  if (run && result?.developmentResult) {
    return <DevelopmentV3Result run={run} resultTab={resultTab} onTabChange={onTabChange} onGoCompare={onGoCompare} />
  }
  if (!result) {
    return (
      <div className="page-stack">
        <PageHeader
          eyebrow={terminalWithoutResult ? '本次分析 · 终止记录' : 'Development-first result'}
          title={terminalWithoutResult ? run?.status === 'cancelled' ? '本次分析已停止' : '本次分析未完成' : '可能病原体与下一步检查'}
          description={terminalWithoutResult
            ? '分析流程已经结束，但没有生成完整结果；页面将保留可查的运行记录。'
            : run
              ? '等待真实模型 API 完成核心证据分析、动态专病路由、多源证据核验、总诊与独立反证流程。'
            : '完成一次研发测试后，这里会显示前5位具体病原体、判断依据、下一步检查和完整运行记录。'}
        />
        {developmentDemo && <DevelopmentDemoBanner />}
        <div className="segmented-tabs result-tabs" role="tablist" aria-label="结果视图" onKeyDown={handleTabListKeyDown}>
          <button role="tab" tabIndex={legacyResultTab === 'summary' ? 0 : -1} aria-selected={legacyResultTab === 'summary'} className={legacyResultTab === 'summary' ? 'active' : ''} onClick={() => onTabChange('summary')}><ClipboardCheck size={16} />{terminalWithoutResult ? '未完成说明' : '综合结果'} <small>{terminalWithoutResult ? 'Failure summary' : 'Result'}</small></button>
          <button role="tab" tabIndex={legacyResultTab === 'trace' ? 0 : -1} aria-selected={legacyResultTab === 'trace'} className={legacyResultTab === 'trace' ? 'active' : ''} onClick={() => onTabChange('trace')}><Network size={16} />系统运行记录 <small>Technical trace</small></button>
        </div>
        {legacyResultTab === 'trace'
          ? run
            ? <RunTracePanel run={run} />
            : <Card><EmptyState icon={Network} title="尚无可查询的运行记录" description="当前没有已选运行；创建新分析后，这里只显示分析服务真实记录的过程。" /></Card>
          : terminalWithoutResult && terminalWithoutResultCopy
            ? <Card className="development-failure-result"><SectionHeading icon={XCircle} title={terminalWithoutResultCopy.title} description="本次没有可作为病原体结果解读的 Top-5 或分数。" /><div className="development-failure-explainer"><strong>本次发生了什么</strong><p>{terminalWithoutResultCopy.reason}</p></div><p className="empty-inline">{terminalWithoutResultCopy.action}</p><div className="development-failure-actions"><button className="button button-secondary" onClick={onGoRun}><Activity size={16} />查看已完成的运行记录</button></div><details className="agent-engineering-details"><summary>工程信息：安全错误码</summary><code>{terminalWithoutResultCopy.engineeringCode}</code></details></Card>
            : <Card><EmptyState icon={ClipboardCheck} title={run?.status === 'running' ? '分析尚未完成' : '尚无可显示的结果'} description={run ? '分析服务尚未返回通过完整性核验的结果，页面不会构造示例病原体。' : '请先创建并完成一次病原体分析。'} action={run ? <button className="button button-primary" onClick={onGoRun}><Activity size={16} />查看分析进度</button> : undefined} /></Card>}
      </div>
    )
  }

  const disposition = DISPOSITION_META[result.safety.disposition]
  const DispositionIcon = disposition.icon
  const supports = result.evidence.filter((item) => item.direction === 'support')
  const against = result.evidence.filter((item) => item.direction === 'against')
  const uncertain = result.evidence.filter((item) => item.direction === 'uncertain')

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow={developmentDemo ? `DEVELOPMENT DEMO RESULT · ${run?.runId || ''}` : `INTEGRITY-CHECKED RESULT · ${run?.runId || ''}`}
        title={developmentDemo ? '真实模型开发结果' : '结果总览'}
        description={developmentDemo ? '展示所选模型 Provider 实际返回的未校准 Top-K；它不是诊断、不是校准概率。' : '先展示安全结论、适用性和数据质量，再展示候选病原体。'}
        actions={<button className="button button-secondary" onClick={onGoCompare}><BarChart3 size={16} />模型横向比较</button>}
      />

      {developmentDemo && <DevelopmentDemoBanner bypassedControls={result.demoBypassedControls} />}

      {run?.runMode === 'retrospective' && <InlineNotice tone="warning" title="回顾性科研结果">
        本结果来自回顾性回放，不得误认为当前患者的实时输出。预注册锚点：{run.retrospectiveAnchorId || '后端未返回锚点'}。
      </InlineNotice>}

      <div className="segmented-tabs result-tabs" role="tablist" aria-label="结果视图" onKeyDown={handleTabListKeyDown}>
        <button role="tab" tabIndex={legacyResultTab === 'summary' ? 0 : -1} aria-selected={legacyResultTab === 'summary'} className={legacyResultTab === 'summary' ? 'active' : ''} onClick={() => onTabChange('summary')}><ClipboardCheck size={16} />综合结果 <small>Result</small></button>
        <button role="tab" tabIndex={legacyResultTab === 'trace' ? 0 : -1} aria-selected={legacyResultTab === 'trace'} className={legacyResultTab === 'trace' ? 'active' : ''} onClick={() => onTabChange('trace')}><Network size={16} />本次运行轨迹 <small>Trace</small></button>
      </div>

      {legacyResultTab === 'summary' ? <>

      {!developmentDemo && <Card className={cx('disposition-card', `disposition-${disposition.tone}`)}>
        <div className="disposition-icon"><DispositionIcon size={28} /></div>
        <div className="disposition-copy">
          <span className="eyebrow">五态安全结论</span>
          <h2><BilingualCopy text={result.safety.conclusionI18n} zh={result.safety.title || disposition.label} en={DISPOSITION_EN[result.safety.disposition]} /></h2>
          <p>{result.safety.explanation || disposition.description}</p>
          <div className="status-row">
            <StatusPill tone={result.safety.applicability === 'applicable' ? 'success' : result.safety.applicability === 'partially_applicable' ? 'warning' : 'danger'}>适用性：{{ applicable: '适用', partially_applicable: '部分适用', not_applicable: '不适用' }[result.safety.applicability]}</StatusPill>
            <StatusPill tone={result.safety.dataQuality === 'high' ? 'success' : result.safety.dataQuality === 'medium' ? 'warning' : 'danger'}>数据质量：{{ high: '高', medium: '中', low: '低' }[result.safety.dataQuality]}</StatusPill>
            {result.safety.outOfDistribution !== undefined && <StatusPill tone={result.safety.outOfDistribution ? 'danger' : 'success'}>OOD：{result.safety.outOfDistribution ? '是' : '否'}</StatusPill>}
          </div>
        </div>
        <div className="disposition-time"><small>生成时间</small><strong>{formatDate(result.generatedAt)}</strong></div>
      </Card>}

      {developmentDemo && (
        <Card className="demo-result-explainer" tone="accent">
          <Code2 size={24} />
          <div><span className="eyebrow">开发投影</span><h2><BilingualCopy zh="未校准模型分数" en="Uncalibrated model score" /></h2><p><BilingualCopy zh="下方排名来自本次真实模型调用，仅用于确认端到端流程。数值不是经过临床验证的概率。" en="The ranking comes from this real model run and only verifies the end-to-end flow. Values are not clinically validated probabilities." /></p></div>
        </Card>
      )}

      {result.humanSummaryI18n && <Card className="bilingual-summary-card">
        <SectionHeading icon={FileSearch} title="结果摘要 / Result summary" description="中文主行、英文副行；数值只在各自指标处显示一次。" />
        <p><BilingualCopy text={result.humanSummaryI18n} /></p>
      </Card>}

      {(result.safety.missingCriticalInformation?.length || result.safety.conflicts?.length) ? (
        <div className="content-grid two">
          {result.safety.missingCriticalInformation?.length ? <InlineNotice tone="warning" title="关键缺失">{result.safety.missingCriticalInformation.join('；')}</InlineNotice> : null}
          {result.safety.conflicts?.length ? <InlineNotice tone="danger" title="证据冲突">{result.safety.conflicts.join('；')}</InlineNotice> : null}
        </div>
      ) : null}

      {developmentDemo ? (
        <div className="metric-strip demo-metric-strip">
          <Card><span>返回候选</span><strong>{result.candidates.length}</strong><small>未校准 Top-K</small></Card>
          <Card><span>真实模型</span><strong>{run?.models.length || result.comparison.length}</strong><small>已向所选 Provider 发起请求</small></Card>
          <Card><span>校准状态</span><strong>未校准</strong><small>不可解读为临床概率</small></Card>
          <Card><span>用途</span><strong>非临床</strong><small>仅开发联调</small></Card>
        </div>
      ) : <div className="metric-strip">
        <Card><span>感染可能性</span><strong>{formatPercent(result.infectionProbability, 1)}</strong><BilingualCopy text={result.syndromeI18n} zh={result.syndrome || '未报告综合征'} en="Syndrome not reported" /></Card>
        <Card><span>Unknown</span><strong>{formatPercent(result.unknownProbability, 1)}</strong><small>未覆盖/未知病原</small></Card>
        <Card><span>共感染</span><strong>{formatPercent(result.coinfectionProbability, 1)}</strong><small>{result.coinfectionCandidates?.join(' + ') || '无候选组合'}</small></Card>
        <Card><span>预测集合</span><strong>{result.candidates.filter((item) => item.inPredictionSet).length}</strong><small>个候选被保留</small></Card>
      </div>}

      <div className="content-grid two-one">
        <Card>
          <SectionHeading icon={Microscope} title={developmentDemo ? '未校准 Top-K（开发演示）' : 'Top-K与预测集合'} description={developmentDemo ? '真实模型返回的候选排名；分数未校准，不得用于任何真实患者。' : '排名不等于确诊；“在预测集合内”表示安全层建议保留。'} />
          {result.candidates.length === 0 ? <EmptyState icon={Microscope} title="未返回候选病原体" description={developmentDemo ? '本次旧版开发结果没有生成具体候选，请在运行轨迹中查看模型输出。' : '当前临床安全结果没有发布候选病原体。'} /> : (
            <div className="candidate-list">
              {result.candidates.map((candidate) => (
                <div className={cx('candidate-row', candidate.inPredictionSet && 'in-set')} key={`${candidate.rank}-${candidate.name}`}>
                  <div className="candidate-rank">{candidate.rank}</div>
                  <div className="candidate-name"><strong><BilingualCopy text={candidate.displayNameI18n} zh={candidate.name} /></strong><small>{taxonomyLevelLabel(candidate.taxonomyLevel)}{developmentDemo ? ' · 未校准模型分数 / uncalibrated model score' : candidate.inPredictionSet ? ' · 预测集合内' : ''}</small></div>
                  <div className="candidate-bar"><div style={{ width: `${Math.max(0, Math.min(100, (candidate.probability || 0) * 100))}%` }} /></div>
                  <div className="candidate-value">{developmentDemo ? formatModelScore(candidate.probability) : formatPercent(candidate.probability, 1)}</div>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card>
          <SectionHeading icon={FlaskConical} title={developmentDemo ? '病原大类模型分数' : '病原大类'} />
          {!result.categoryProbabilities?.length ? <EmptyState icon={FlaskConical} title="未返回大类概率" description="请检查后端结果字段。" /> : (
            <div className="category-bars">
              {result.categoryProbabilities.map((item) => (
                <div key={item.name}><div><span>{item.name}</span><strong>{developmentDemo ? formatModelScore(item.probability) : formatPercent(item.probability, 1)}</strong></div><div className="mini-track"><span style={{ width: `${item.probability * 100}%` }} /></div></div>
              ))}
            </div>
          )}
        </Card>
      </div>

      <div className="evidence-grid">
        <Card>
          <SectionHeading icon={CheckCircle2} title="支持证据" description={`${supports.length} 条`} />
          <EvidenceList items={supports} empty="后端未返回支持证据。" />
        </Card>
        <Card>
          <SectionHeading icon={XCircle} title="反对证据" description={`${against.length} 条`} />
          <EvidenceList items={against} empty="后端未返回反对证据。" />
        </Card>
        <Card>
          <SectionHeading icon={CircleHelp} title="不确定证据" description={`${uncertain.length} 条`} />
          <EvidenceList items={uncertain} empty="后端未返回不确定证据。" />
        </Card>
      </div>

      <Card className="next-test-card">
        <SectionHeading icon={TestTube2} title="信息价值最高的下一项检查 / Next test" description={developmentDemo ? '真实 API 返回的非临床建议；不得下达医嘱。' : '用于降低当前不确定性，不代表自动下达医嘱。'} />
        {!result.nextTest ? <EmptyState icon={TestTube2} title="未推荐下一项检查" description={developmentDemo ? '旧版开发结果没有返回下一检查建议。' : '当前结果未发布下一检查建议。'} /> : (
          <div className="next-test-content">
            <div className="test-name"><span>01</span><div><h3><BilingualCopy text={result.nextTest.nameI18n} zh={result.nextTest.name} /></h3><p><BilingualCopy text={result.nextTest.rationaleI18n} zh={result.nextTest.rationale} /></p></div></div>
            <dl>
              <div><dt>{developmentDemo ? '未校准信息分数' : '预期信息增益'}</dt><dd>{developmentDemo ? formatModelScore(result.nextTest.expectedInformationGain) : formatPercent(result.nextTest.expectedInformationGain, 0)}</dd></div>
              <div><dt>返回时间</dt><dd>{result.nextTest.turnaround || '—'}</dd></div>
              <div><dt>可及性</dt><dd>{result.nextTest.availability || '—'}</dd></div>
              <div><dt>侵入性</dt><dd>{invasivenessLabel(result.nextTest.invasiveness)}</dd></div>
            </dl>
          </div>
        )}
      </Card>

      {result.narrative && <Card><SectionHeading icon={FileSearch} title={developmentDemo ? '模型输出说明（非临床）' : '医生可读报告'} description={developmentDemo ? '所选模型 Provider 生成的开发演示文本；不得当作临床报告。' : '根据通过完整性哈希核对的结构化结果生成；不是数字签名。'} /><div className="narrative">{result.narrative}</div></Card>}

      <Card className="version-footer">
        <div><span>模型版本</span><strong>{result.modelVersion || '未报告'}</strong></div>
        <div><span>校准器</span><strong>{result.calibrationVersion || '未报告'}</strong></div>
        <div><span>知识库</span><strong>{result.knowledgeVersion || '未报告'}</strong></div>
      </Card>
      </> : run ? <RunTracePanel run={run} /> : <Card><EmptyState icon={Network} title="尚无运行轨迹" description="请先完成一次真实后端运行。" /></Card>}
    </div>
  )
}

function EvidenceList({ items, empty }: { items: NonNullable<RunDetail['result']>['evidence']; empty: string }) {
  if (!items.length) return <p className="empty-inline">{empty}</p>
  const qualityLabel = { high: '高', medium: '中', low: '低', unknown: '未知' }
  return (
    <div className="evidence-list">
      {items.map((item) => (
        <div className="evidence-item" key={item.id}>
          <span className="evidence-bullet" />
          <div><p>{item.statement}</p><small>{item.source || item.sourceType}{item.quality ? ` · 质量 ${qualityLabel[item.quality]}` : ''}{item.observedAt ? ` · ${formatDate(item.observedAt)}` : ''}</small></div>
        </div>
      ))}
    </div>
  )
}

function ModelComparison({ run }: { run?: RunDetail }) {
  const rows = run?.result?.comparison || []
  const developmentDemo = run?.runMode === 'development_demo' || run?.result?.developmentDemo === true
  const developmentV3 = run?.resultSchemaVersion === 'owlpath.result.v3' || run?.traceVersion === 'owlpath.trace.v2' || Boolean(run?.result?.developmentResult)
  const developmentFailure = run?.result?.developmentResult?.status === 'technical_failure' || run?.status === 'failed' || run?.status === 'cancelled'
  const failureCopy = developmentFailure
    ? clinicalTechnicalError(run?.error || { code: run?.status === 'cancelled' ? 'run_interrupted' : 'development_technical_failure' })
    : undefined
  const providerCount = new Set(rows.map((row) => row.providerId)).size
  const singleProvider = providerCount <= 1
  const comparisonMode = clinicalComparisonMode(developmentDemo, developmentV3, providerCount)
  const perspectiveComparison = comparisonMode === 'clinical_perspectives'
  const comparisonTitle = perspectiveComparison ? '各分析视角对照' : singleProvider ? '本次模型输出' : '模型横向比较'

  if (developmentFailure && failureCopy) {
    return (
      <div className="page-stack">
        <PageHeader eyebrow={developmentDemo ? '研发测试 · 分析意见对照' : '本次分析 · 结果对照'} title="本次没有可比较的病原体结果" description="技术流程未生成完整的前5位具体病原体，因此不展示分数或模型对照，避免将中间输出误解为结果。" />
        {developmentDemo && <DevelopmentDemoBanner bypassedControls={run?.result?.demoBypassedControls} />}
        <Card className="development-failure-result">
          <SectionHeading icon={XCircle} title={failureCopy.title} description="已完成的分析环节仍保留在本次运行记录中。" />
          <div className="development-failure-explainer"><strong>为什么不显示对照</strong><p>{failureCopy.reason}未形成合格的最终结果时，中间分数不具有可比较的临床含义。</p></div>
          <p className="empty-inline">{failureCopy.action}</p>
          <details className="agent-engineering-details"><summary>工程信息：安全错误码</summary><code>{failureCopy.engineeringCode}</code></details>
        </Card>
      </div>
    )
  }

  return (
    <div className="page-stack">
      <PageHeader eyebrow={developmentDemo ? '研发测试 · 分析意见对照' : singleProvider ? '同一病例 · 模型输出' : '同一病例 · 多模型对照'} title={comparisonTitle} description={perspectiveComparison ? '比较不同临床视角分别提出了哪些病原体；各视角分数不可直接横向解释为概率高低。' : singleProvider ? '查看本次模型返回的候选、未知分数和耗时；工程明细仍保留在折叠区。' : developmentDemo ? '比较不同模型服务提出的候选与耗时；排序分未校准，仅用于研发评估。' : '比较同一当前证据快照下的候选、未知分数、建议保留范围和耗时；不能用多数投票代替独立安全检查。'} />
      {developmentDemo && <DevelopmentDemoBanner bypassedControls={run?.result?.demoBypassedControls} />}
      {!rows.length ? (
        <Card><EmptyState icon={BarChart3} title="尚无模型比较数据" description="至少完成一次包含一个或多个模型的后端运行。" /></Card>
      ) : (
        <>
          <div className="comparison-grid">
            {rows.map((row, rowIndex) => {
              const perspective = clinicalAgentRole(row.category)
              const status = clinicalDevelopmentAgentStatus(row.status)
              const errorCopy = row.error ? clinicalTechnicalError(row.error) : undefined
              return (
              <Card className="comparison-card" key={`${row.outputId || row.providerId}-${row.model || ''}-${rowIndex}`}>
                <div className="comparison-head"><div className="provider-logo"><BrainCircuit size={19} /></div><div><h2>{perspectiveComparison ? perspective.primary : row.providerName || row.providerId}</h2><p>{perspectiveComparison ? `${row.providerName || row.providerId} · ${row.model || '模型未报告'}` : row.model || (row.providerKind ? kindMeta(row.providerKind).name : '分析模型')}</p></div><StatusPill tone={status.tone === 'danger' ? 'danger' : status.tone === 'warning' ? 'warning' : 'success'}>{status.primary}</StatusPill></div>
                <div className="comparison-top"><span>{perspectiveComparison ? '本视角首位病原体（排序分未校准）' : developmentDemo ? '首位候选（排序分未校准）' : '首位候选'}</span><strong><BilingualCopy text={row.normalized?.candidates[0]?.displayNameI18n} zh={row.topCandidate || '—'} /></strong><b>{developmentDemo ? formatModelScore(row.topProbability) : formatPercent(row.topProbability, 1)}</b></div>
                <dl className="comparison-dl">
                  <div><dt>{perspectiveComparison ? '分析视角' : '病原大类方向'}</dt><dd>{perspectiveComparison ? perspective.primary : row.category || '—'}</dd></div>
                  <div><dt>{developmentDemo ? '未知或未覆盖病原体分数（未校准）' : '未知或未覆盖病原体分数'}</dt><dd>{developmentDemo ? formatModelScore(row.unknownProbability) : formatPercent(row.unknownProbability, 1)}</dd></div>
                  <div><dt>耗时</dt><dd>{formatDuration(row.latencyMs)}</dd></div>
                </dl>
                <div className="prediction-set"><span>{perspectiveComparison ? '该视角提出的病原体' : developmentDemo ? '候选病原体（排序分未校准）' : '建议保留的候选'}</span><div>{row.predictionSet?.length ? row.predictionSet.map((item) => <StatusPill key={item}>{item}</StatusPill>) : <small>未返回</small>}</div></div>
                {row.notes && <p className="comparison-note">{row.notes}</p>}
                {row.normalized && <details className="normalized-output">
                  <summary>完整标准化输出 / Full normalized output</summary>
                  <div className="normalized-summary"><BilingualCopy text={row.normalized.summaryI18n} zh={row.normalized.summary} /></div>
                  <pre>{JSON.stringify(row.normalized, null, 2)}</pre>
                </details>}
                {errorCopy && <InlineNotice tone="danger" title={errorCopy.title}><p>{errorCopy.reason}</p><p>{errorCopy.action}</p><details><summary>安全工程错误码</summary><code>{errorCopy.engineeringCode}</code></details></InlineNotice>}
              </Card>
            )})}
          </div>

          <Card className="table-card">
            <SectionHeading icon={BarChart3} title="对照表" description="空值代表分析服务未返回，页面不会自行估算。" />
            <div className="data-table-wrap"><table className="data-table"><thead><tr><th>{perspectiveComparison ? '分析视角' : '模型'}</th><th>状态</th><th>首位候选</th><th>{developmentDemo ? '排序分（未校准）' : '首位概率'}</th><th>未知或未覆盖</th><th>候选数量</th><th>耗时</th></tr></thead><tbody>{rows.map((row, rowIndex) => { const role = clinicalAgentRole(row.category); const status = clinicalDevelopmentAgentStatus(row.status); return <tr key={`${row.outputId || row.providerId}-${row.model || ''}-${rowIndex}`}><td><strong>{perspectiveComparison ? role.primary : row.providerName || row.providerId}</strong><small>{perspectiveComparison ? `${row.providerName || row.providerId} · ${row.model || ''}` : row.model || ''}</small></td><td>{status.primary}</td><td>{row.topCandidate || '—'}</td><td>{developmentDemo ? formatModelScore(row.topProbability) : formatPercent(row.topProbability, 1)}</td><td>{developmentDemo ? formatModelScore(row.unknownProbability) : formatPercent(row.unknownProbability, 1)}</td><td>{row.predictionSet?.length ?? '—'}</td><td>{formatDuration(row.latencyMs)}</td></tr>})}</tbody></table></div>
          </Card>
        </>
      )}
    </div>
  )
}

function HistoryPage({ onSelect }: { onSelect: (runId: string) => Promise<void> }) {
  const [items, setItems] = useState<RunHistoryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try { setItems((await api.history()).items) } catch (err) { setError(apiErrorMessage(err)) } finally { setLoading(false) }
  }, [])
  useEffect(() => { void load() }, [load])

  const filtered = items.filter((item) => `${item.runId} ${item.caseId || ''} ${item.caseSummary || ''} ${item.topCandidate || ''}`.toLowerCase().includes(query.toLowerCase()))
  const hasDevelopmentDemo = filtered.some((item) => item.runMode === 'development_demo')
  return (
    <div className="page-stack">
      <PageHeader eyebrow="可追溯记录" title="历史病例分析" description="查看每次病例分析、候选结果和完整运行过程；每条记录都有可复制的独立链接。" actions={<button className="button button-secondary" onClick={load} disabled={loading}><RefreshCw className={loading ? 'spin' : ''} size={16} />刷新</button>} />
      {hasDevelopmentDemo && <DevelopmentDemoBanner />}
      <Card className="history-toolbar"><div className="search-input"><FileSearch size={17} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索病例摘要、候选病原体或运行编号" /></div><span>{filtered.length} / {items.length} 条</span></Card>
      {loading ? <Card><LoadingState label="正在读取历史分析…" /></Card> : error ? <InlineNotice tone="danger" title="无法读取历史记录">{error}</InlineNotice> : filtered.length === 0 ? <Card><EmptyState icon={Archive} title={items.length ? '未找到匹配记录' : '尚无历史分析'} description={items.length ? '尝试更换搜索词。' : '完成一次病原体分析后，记录会显示在这里。'} /></Card> : (
        <Card className="table-card">
          <div className="data-table-wrap">
            <table className="data-table clickable">
              <thead><tr><th>运行记录</th><th>用途</th><th>病例摘要</th><th>资料截止时间</th><th>状态</th><th>结果用途与限制</th><th>首位候选病原体</th><th>使用模型</th><th /></tr></thead>
              <tbody>{filtered.map((item) => (
                <tr className={item.runMode === 'development_demo' ? 'demo-history-row' : undefined} key={item.runId} onClick={() => void onSelect(item.runId)}>
                  <td><strong>{item.runId.slice(0, 12)}</strong><small>{formatDate(item.createdAt)}</small></td>
                  <td>
                    <StatusPill tone={item.runMode === 'development_demo' ? 'danger' : item.runMode === 'retrospective' ? 'warning' : 'success'}>{item.runMode === 'development_demo' ? '研发测试（虚构/已脱敏）' : item.runMode === 'retrospective' ? '历史病例复盘（回顾性）' : '当前病例分析（实时）'}</StatusPill>
                    {item.runMode === 'retrospective' && <small>资料截止点：{item.retrospectiveAnchorId || '未报告'}</small>}
                    {item.runMode === 'development_demo' && <small>排序分数，非概率 · 不用于临床</small>}
                  </td>
                  <td>{item.caseSummary || item.caseId || '—'}</td>
                  <td>{formatDate(item.decisionTime)}</td>
                  <td><StatusPill tone={item.status === 'completed' ? 'success' : item.status === 'failed' ? 'danger' : item.status === 'cancelled' ? 'warning' : 'info'}>{runStatusLabel(item.status)}</StatusPill></td>
                  <td>{item.runMode === 'development_demo' ? '仅供研发，不用于临床' : item.disposition ? DISPOSITION_META[item.disposition].label : '—'}</td>
                  <td>{item.topCandidate || '—'}{item.runMode === 'development_demo' && <small>排序分数，非概率</small>}</td>
                  <td>{item.providers?.map((provider) => provider.name || provider.id).join('、') || '—'}</td>
                  <td><ChevronRight size={16} /></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}

function MetricCard({ metric }: { metric: MetricValue }) {
  const display = metric.value === undefined ? '—' : metric.unit === '%' ? `${(metric.value * 100).toFixed(1)}%` : `${metric.value.toFixed(metric.value < 10 ? 3 : 1)}${metric.unit ? ` ${metric.unit}` : ''}`
  const ratio = metric.value !== undefined && metric.target !== undefined && metric.target !== 0
    ? metric.lowerIsBetter ? Math.min(1, metric.target / Math.max(metric.value, 0.0001)) : Math.min(1, metric.value / metric.target)
    : undefined
  return (
    <Card className="metric-card">
      <span>{metric.label}</span><strong>{display}</strong>{metric.target !== undefined && <small>目标 {metric.unit === '%' ? `${metric.target * 100}%` : metric.target}</small>}
      {ratio !== undefined && <div className="metric-target"><span style={{ width: `${ratio * 100}%` }} /></div>}
      {metric.description && <p>{metric.description}</p>}
    </Card>
  )
}

function EvaluationPage() {
  const newCausalPathogen = (): CausalPathogenLabelInput => ({ canonicalId: '', name: '', certainty: 'confirmed' })
  const [data, setData] = useState<EvaluationResponse>()
  const [completedRuns, setCompletedRuns] = useState<RunHistoryItem[]>([])
  const [selectedRunId, setSelectedRunId] = useState('')
  const [infectionStatus, setInfectionStatus] = useState<InfectionStatus>('infectious')
  const [causalPathogens, setCausalPathogens] = useState<CausalPathogenLabelInput[]>([newCausalPathogen()])
  const [colonizers, setColonizers] = useState('')
  const [contaminants, setContaminants] = useState('')
  const [coinfection, setCoinfection] = useState<CoinfectionLabel>('unknown')
  const [adjudicationStatus, setAdjudicationStatus] = useState<AdjudicationStatus>('not_adjudicated')
  const [notes, setNotes] = useState('')
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const [summary, history] = await Promise.all([api.evaluation(), api.history(500)])
      const eligible = history.items.filter((item) => item.status === 'completed')
      setData(summary)
      setCompletedRuns(eligible)
      setSelectedRunId((current) => current && eligible.some((item) => item.runId === current) ? current : eligible[0]?.runId || '')
    } catch (err) {
      setError(apiErrorMessage(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const updateCausalPathogen = (index: number, patch: Partial<CausalPathogenLabelInput>) => {
    setCausalPathogens((current) => current.map((item, rowIndex) => rowIndex === index ? { ...item, ...patch } : item))
  }

  const parseTerms = (value: string): string[] => Array.from(new Set(value.split(/[\n,，;；]+/).map((item) => item.trim()).filter(Boolean)))

  const submit = async () => {
    setError(''); setMessage('')
    if (!selectedRunId) return setError('请先选择一个已完成的运行。')
    const pathogens = causalPathogens.filter((item) => item.canonicalId.trim() || item.name.trim())
    if (pathogens.some((item) => !item.canonicalId.trim() || !item.name.trim())) return setError('每个因果病原体都需要填写 canonical ID 和规范名称。')
    if (infectionStatus === 'infectious' && pathogens.length === 0) return setError('感染性标签至少需要一个因果病原体。')
    setSubmitting(true)
    try {
      const saved = await api.createEvaluation({
        runId: selectedRunId,
        label: {
          infectionStatus,
          causalPathogens: pathogens,
          colonizers: parseTerms(colonizers),
          contaminants: parseTerms(contaminants),
          coinfection,
          adjudicationStatus,
          notes,
        },
      })
      setData(await api.evaluation())
      setMessage(`因果标签已写入评价 ${saved.id}，汇总指标已刷新。`)
    } catch (err) {
      setError(apiErrorMessage(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="page-stack">
      <PageHeader eyebrow="Offline validation" title="离线评价与因果标签" description="先为已完成运行录入独立于模型预测的临床因果标签，再评价排名、概率误差和弃答表现。" actions={<button className="button button-secondary" onClick={load} disabled={loading || submitting}><RefreshCw className={loading ? 'spin' : ''} size={16} />刷新</button>} />

      <InlineNotice tone="info" title="标签必须来自运行后的独立证据">
        标签用于检验模型，不能照抄模型输出。建议结合最终微生物学、治疗反应及病例仲裁；每次运行仍按其不可变的当前证据快照评价。
      </InlineNotice>

      <Card className="evaluation-entry" tone="accent">
        <SectionHeading icon={ClipboardCheck} title="录入本次运行的因果标签" description="同一运行再次提交会更新其标签与指标；只有后端状态为 completed 的运行可评价。" />
        {loading ? <LoadingState label="正在读取可评价运行…" /> : completedRuns.length === 0 ? (
          <EmptyState icon={FileClock} title="暂无可评价运行" description="请先完成一次真实后端推演；页面不会创建示例运行或示例标签。" />
        ) : (
          <>
            <div className="form-grid three evaluation-form-top">
              <Field label="已完成运行" hint="请选择要写入金标准标签的证据快照。">
                <select value={selectedRunId} onChange={(event) => setSelectedRunId(event.target.value)}>
                  {completedRuns.map((run) => <option value={run.runId} key={run.runId}>{run.runId.slice(0, 18)} · {formatDate(run.decisionTime)} · {run.topCandidate || '无Top-1'}</option>)}
                </select>
              </Field>
              <Field label="感染状态">
                <select value={infectionStatus} onChange={(event) => setInfectionStatus(event.target.value as InfectionStatus)}>
                  <option value="infectious">感染性</option><option value="non_infectious">非感染性</option><option value="uncertain">不确定</option>
                </select>
              </Field>
              <Field label="仲裁状态">
                <select value={adjudicationStatus} onChange={(event) => setAdjudicationStatus(event.target.value as AdjudicationStatus)}>
                  <option value="not_adjudicated">尚未仲裁</option><option value="single_reviewer">单人复核</option><option value="independent_consensus">独立复核后共识</option><option value="panel_consensus">专家组共识</option>
                </select>
              </Field>
            </div>

            <div className="causal-label-section">
              <div className="subsection-heading"><div><strong>因果病原体</strong><small>感染性病例至少一项；canonical ID 应使用项目约定的稳定标识。</small></div><button className="button button-small button-secondary" onClick={() => setCausalPathogens((current) => [...current, newCausalPathogen()])}><Plus size={14} />添加病原体</button></div>
              <div className="causal-pathogen-list">
                {causalPathogens.map((item, index) => (
                  <div className="causal-pathogen-row" key={`causal-${index}`}>
                    <input value={item.canonicalId} onChange={(event) => updateCausalPathogen(index, { canonicalId: event.target.value })} placeholder="canonical ID，如 taxon:1313" aria-label="病原体 canonical ID" />
                    <input value={item.name} onChange={(event) => updateCausalPathogen(index, { name: event.target.value })} placeholder="规范名称，如 Streptococcus pneumoniae" aria-label="病原体规范名称" />
                    <select value={item.certainty} onChange={(event) => updateCausalPathogen(index, { certainty: event.target.value as CausalPathogenLabelInput['certainty'] })} aria-label="因果确定性"><option value="confirmed">确证</option><option value="probable">很可能</option><option value="possible">可能</option><option value="uncertain">不确定</option></select>
                    <button className="icon-button danger" onClick={() => setCausalPathogens((current) => current.length === 1 ? [newCausalPathogen()] : current.filter((_, rowIndex) => rowIndex !== index))} aria-label="删除因果病原体"><Trash2 size={15} /></button>
                  </div>
                ))}
              </div>
            </div>

            <div className="form-grid three">
              <Field label="共感染判断"><select value={coinfection} onChange={(event) => setCoinfection(event.target.value as CoinfectionLabel)}><option value="unknown">未知</option><option value="no">否</option><option value="yes">是</option><option value="possible">可能</option></select></Field>
              <Field label="定植菌" hint="多个名称用逗号或换行分隔。"><textarea rows={3} value={colonizers} onChange={(event) => setColonizers(event.target.value)} placeholder="不作为致病原因的定植菌" /></Field>
              <Field label="污染菌" hint="多个名称用逗号或换行分隔。"><textarea rows={3} value={contaminants} onChange={(event) => setContaminants(event.target.value)} placeholder="判断为采样或实验污染的微生物" /></Field>
              <Field label="仲裁依据与备注" wide><textarea rows={4} maxLength={3000} value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="最终病原学证据、标本质量、治疗反应、分歧及仲裁理由；请勿录入直接身份信息。" /></Field>
            </div>
            {error && <InlineNotice tone="danger">{error}</InlineNotice>}
            {message && <InlineNotice tone="success">{message}</InlineNotice>}
            <div className="evaluation-submit-row"><span>提交后由后端计算该运行的 Top-K、MRR 与 Brier 指标。</span><button className="button button-primary" onClick={submit} disabled={submitting}>{submitting ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />}{submitting ? '正在提交' : '提交标签并刷新汇总'}</button></div>
          </>
        )}
      </Card>

      {!loading && error && completedRuns.length === 0 ? <InlineNotice tone="danger" title="无法读取评价数据">{error}</InlineNotice> : null}
      {data ? (
        <>
          <Card className="evaluation-meta" tone="accent"><div><span>数据集版本</span><strong>{data.datasetVersion || '后端实时汇总'}</strong></div><div><span>已评价运行</span><strong>{data.sampleSize?.toLocaleString('zh-CN') ?? '—'}</strong></div><div><span>评价时间</span><strong>{formatDate(data.evaluatedAt)}</strong></div></Card>
          <div className="metrics-grid">{data.metrics.map((metric) => <MetricCard key={metric.key} metric={metric} />)}</div>
          <div className="content-grid two">
            <Card><SectionHeading icon={ClipboardCheck} title="标签政策" /><div className="policy-box"><h3>病原体因果标签</h3><p>{data.labelPolicy || 'confirmed 与 probable 病原体进入因果金标准；possible 与 uncertain 保留但不计为确定阳性。'}</p></div><div className="policy-box"><h3>决策时点政策</h3><p>{data.decisionTimePolicy || '预测只使用该次运行的当前证据快照；后续证据仅用于建立独立因果标签，不能回流到原预测输入。'}</p></div></Card>
            <Card><SectionHeading icon={BarChart3} title="标签分布" />{!data.labelDistribution?.length ? <p className="empty-inline">当前后端未返回标签分布。</p> : <div className="label-distribution">{data.labelDistribution.map((item) => <div key={item.label}><div><span>{item.label}</span><strong>{item.count}</strong></div><div className="mini-track"><span style={{ width: `${Math.max(4, item.count / Math.max(...data.labelDistribution!.map((row) => row.count)) * 100)}%`, background: item.color }} /></div></div>)}</div>}</Card>
          </div>
          <Card className="table-card"><SectionHeading icon={Network} title="人群与场景切片" description="总体表现不能遮盖特定医院、季节或宿主亚组的风险。" />{!data.slices.length ? <EmptyState icon={Network} title="尚无切片指标" description="当前后端只返回全体汇总；正式验证应至少按医院、时间、年龄和免疫状态预设切片。" /> : <div className="data-table-wrap"><table className="data-table"><thead><tr><th>切片</th><th>n</th>{Array.from(new Set(data.slices.flatMap((slice) => slice.metrics.map((metric) => metric.label)))).slice(0, 5).map((label) => <th key={label}>{label}</th>)}<th>提醒</th></tr></thead><tbody>{data.slices.map((slice) => { const labels = Array.from(new Set(data.slices.flatMap((row) => row.metrics.map((metric) => metric.label)))).slice(0, 5); return <tr key={slice.name}><td><strong>{slice.name}</strong></td><td>{slice.sampleSize}</td>{labels.map((label) => { const metric = slice.metrics.find((item) => item.label === label); return <td key={label}>{metric?.value === undefined ? '—' : metric.unit === '%' ? `${(metric.value * 100).toFixed(1)}%` : metric.value.toFixed(3)}</td>})}<td>{slice.warning || '—'}</td></tr> })}</tbody></table></div>}</Card>
          {data.notes?.length ? <InlineNotice tone="info" title="评价备注">{data.notes.join('；')}</InlineNotice> : null}
        </>
      ) : !loading ? <Card><EmptyState icon={Gauge} title="尚无汇总指标" description="提交第一条真实因果标签后，后端将在此返回汇总。" /></Card> : null}
    </div>
  )
}

function auditTone(result: AuditRecord['result']): 'success' | 'danger' | 'warning' {
  return result === 'success' ? 'success' : result === 'denied' ? 'warning' : 'danger'
}

function GovernancePage() {
  const [data, setData] = useState<GovernanceResponse>()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const load = useCallback(async () => { setLoading(true); setError(''); try { setData(await api.governance()) } catch (err) { setError(apiErrorMessage(err)) } finally { setLoading(false) } }, [])
  useEffect(() => { void load() }, [load])
  return (
    <div className="page-stack">
      <PageHeader eyebrow="Control plane" title="治理、版本与审计" description="系统先声明自己能用在哪里，再记录每一次模型、校准器和知识版本的变化。" actions={<button className="button button-secondary" onClick={load} disabled={loading}><RefreshCw className={loading ? 'spin' : ''} size={16} />刷新</button>} />
      {loading ? <Card><LoadingState label="正在读取治理信息…" /></Card> : error ? <InlineNotice tone="danger" title="无法读取治理数据">{error}</InlineNotice> : !data ? <Card><EmptyState icon={ShieldCheck} title="尚无治理数据" description="请由后端发布适用范围、活跃版本和审计日志。" /></Card> : (
        <>
          <Card className="scope-contract" tone="accent">
            <SectionHeading icon={ShieldCheck} title="适用范围契约" description="不是页面上的免责声明，而是后端执行的运行边界。" />
            {!data.scopeContract ? <EmptyState icon={ShieldCheck} title="后端未发布适用范围" description="在契约发布前，不应将系统视为已定义的临床产品。" /> : <div className="scope-grid"><div><span>人群</span><strong>{data.scopeContract.population}</strong></div><div><span>场景</span><strong>{data.scopeContract.scenario}</strong></div><div><span>决策时点原则</span><strong>{data.scopeContract.decisionTimeRule}</strong></div><div><span>预期用途</span><strong>{data.scopeContract.intendedUse}</strong></div><div className="scope-exclusions"><span>排除条件</span><div>{data.scopeContract.exclusions.map((item) => <StatusPill tone="warning" key={item}>{item}</StatusPill>)}</div></div></div>}
          </Card>

          <div className="monitoring-grid">
            {(data.monitoring || []).map((item) => <Card className="monitoring-card" key={item.label}><span className={cx('monitor-state', item.state)} /><div><span>{item.label}</span><strong>{item.value === undefined ? '—' : `${item.value}${item.unit || ''}`}</strong></div><small>{item.state}</small></Card>)}
          </div>

          <Card className="table-card">
            <SectionHeading icon={ServerCog} title="组件版本注册表" description="只有经过评审的active版本才应进入在线推演。" />
            {!data.versions.length ? <EmptyState icon={ServerCog} title="尚无版本记录" description="后端未返回模型、校准器、知识库或工具版本。" /> : <div className="data-table-wrap"><table className="data-table"><thead><tr><th>组件</th><th>版本</th><th>状态</th><th>批准人</th><th>发布时间</th><th>Checksum</th><th>备注</th></tr></thead><tbody>{data.versions.map((item) => <tr key={`${item.component}-${item.version}`}><td><strong>{item.component}</strong></td><td><code>{item.version}</code></td><td><StatusPill tone={item.status === 'active' ? 'success' : item.status === 'blocked' ? 'danger' : item.status === 'candidate' ? 'warning' : 'neutral'}>{item.status}</StatusPill></td><td>{item.approvedBy || '—'}</td><td>{formatDate(item.releasedAt)}</td><td><code>{item.checksum?.slice(0, 12) || '—'}</code></td><td>{item.notes || '—'}</td></tr>)}</tbody></table></div>}
          </Card>

          <Card>
            <SectionHeading icon={FileClock} title="审计日志" description="展示后端记录的关键配置、版本、权限和运行事件。" />
            {!data.audits.length ? <EmptyState icon={FileClock} title="尚无审计日志" description="前端不会自行生成审计记录。" /> : <div className="audit-list">{data.audits.map((item) => <div className="audit-item" key={item.id}><div className="audit-time"><strong>{formatDate(item.time)}</strong><small>{item.actor}</small></div><div className="audit-line"><span /><div><strong>{item.action}</strong><p>{item.target || ''}{item.detail ? ` · ${item.detail}` : ''}</p></div></div><StatusPill tone={auditTone(item.result)}>{item.result}</StatusPill></div>)}</div>}
          </Card>
        </>
      )}
    </div>
  )
}

export default function App() {
  const [route, setRoute] = useState<AppRoute>(() => parseAppRoute(window.location.hash))
  const page = route.page
  const [theme, setTheme] = useState<Theme>(() => (localStorage.getItem('owlpath-theme') as Theme) || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'))
  const [collapsed, setCollapsed] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)
  const [connection, setConnection] = useState<ConnectionState>('checking')
  const [connectionMessage, setConnectionMessage] = useState('')
  const [providers, setProviders] = useState<ProviderSummary[]>([])
  const [providersLoading, setProvidersLoading] = useState(false)
  const [providersError, setProvidersError] = useState('')
  const [activeRun, setActiveRun] = useState<RunDetail>()
  const pollAbort = useRef<AbortController>()
  const autoOpenEligibleRunId = useRef('')

  const navigate = useCallback((next: PageId, explicitRunId?: string, resultTab: ResultTab = 'summary') => {
    const runId = explicitRunId || activeRun?.runId
    const nextRoute: AppRoute = (next === 'run' || next === 'result' || next === 'compare') && runId
      ? { page: next, runId, resultTab: next === 'result' ? resultTab : undefined }
      : { page: next }
    setRoute(nextRoute)
    const nextHash = appRouteHash(nextRoute)
    if (window.location.hash !== nextHash) window.history.pushState(null, '', nextHash)
    setMobileOpen(false)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }, [activeRun?.runId])

  const navigateArchitecture = useCallback((architectureView: ArchitectureView) => {
    const nextRoute: AppRoute = { page: 'architecture', architectureView }
    setRoute(nextRoute)
    const nextHash = appRouteHash(nextRoute)
    if (window.location.hash !== nextHash) window.history.pushState(null, '', nextHash)
    setMobileOpen(false)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }, [])

  useEffect(() => {
    const syncRoute = () => setRoute(parseAppRoute(window.location.hash))
    syncRoute()
    window.addEventListener('hashchange', syncRoute)
    window.addEventListener('popstate', syncRoute)
    return () => {
      window.removeEventListener('hashchange', syncRoute)
      window.removeEventListener('popstate', syncRoute)
    }
  }, [])

  const loadProviders = useCallback(async () => {
    setProvidersLoading(true); setProvidersError('')
    try {
      setProviders(await api.providers())
    } catch (error) {
      setProvidersError(apiErrorMessage(error))
    } finally {
      setProvidersLoading(false)
    }
  }, [])

  const checkConnection = useCallback(async () => {
    setConnection('checking'); setConnectionMessage('')
    try {
      await api.health()
      setConnection('online')
      void loadProviders()
    } catch (error) {
      setConnection('offline')
      setConnectionMessage(apiErrorMessage(error))
    }
  }, [loadProviders])

  useEffect(() => { void checkConnection() }, [checkConnection])
  useEffect(() => { window.scrollTo(0, 0) }, [page])
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('owlpath-theme', theme)
    const meta = document.querySelector('meta[name="theme-color"]')
    meta?.setAttribute('content', theme === 'dark' ? '#07111f' : '#f5f7fa')
  }, [theme])

  const refreshRun = useCallback(async (runId: string) => {
    pollAbort.current?.abort()
    const controller = new AbortController()
    pollAbort.current = controller
    try {
      const run = await api.run(runId, controller.signal)
      setActiveRun(run)
      return run
    } finally {
      if (pollAbort.current === controller) pollAbort.current = undefined
    }
  }, [])

  useEffect(() => {
    // A result URL may be opened while the local service is temporarily down.
    // Retry must re-fetch the URL-authoritative run after health recovers; a
    // green connection badge alone must not leave the page in an empty state.
    if (connection !== 'online' || !route.runId || activeRun?.runId === route.runId) return
    setActiveRun(undefined)
    void refreshRun(route.runId).catch((error) => setConnectionMessage(`无法读取运行 ${route.runId}：${apiErrorMessage(error)}`))
  }, [connection, route.runId, activeRun?.runId, refreshRun])

  useEffect(() => {
    if (!activeRun || (activeRun.status !== 'running' && activeRun.status !== 'queued')) return
    const timer = window.setInterval(() => { void refreshRun(activeRun.runId).catch(() => undefined) }, 1600)
    return () => window.clearInterval(timer)
  }, [activeRun?.runId, activeRun?.status, refreshRun])

  useEffect(() => {
    if (
      activeRun?.runMode === 'development_demo'
      && activeRun.status === 'completed'
      && activeRun.result
      && autoOpenEligibleRunId.current === activeRun.runId
    ) {
      autoOpenEligibleRunId.current = ''
      if (route.page === 'run' && route.runId === activeRun.runId) navigate('result', activeRun.runId)
    }
  }, [activeRun, navigate, route.page, route.runId])

  const startRun = async (
    draft: CaseDraft,
    externalProviders: ProviderSummary[],
    review: {
      parserVersion: string
      sourceTextSha256: string
      confirmedAt: string
      transferConfirmedAt?: string
    },
  ) => {
    const created = await api.createRun({
      case: draft,
      clinicalReview: {
        accepted: true,
        confirmedAt: review.confirmedAt,
        statementVersion: 'owlpath-clinical-review-v1',
        parserVersion: review.parserVersion,
        sourceTextSha256: review.sourceTextSha256,
      },
      dataTransferConsent: externalProviders.length ? {
        accepted: true,
        confirmedAt: review.transferConfirmedAt || review.confirmedAt,
        statementVersion: 'owlpath-external-transfer-v1',
        externalProviderIds: externalProviders.map((provider) => provider.id),
        providerTargets: externalProviders.map((provider) => ({
          providerId: provider.id,
          kind: provider.kind,
          model: provider.model || '',
          baseUrl: provider.baseUrl,
          dataBoundary: provider.dataBoundary,
        })),
      } : undefined,
    })
    const run: RunDetail = {
      runId: created.runId,
      runMode: 'live',
      status: created.status,
      createdAt: created.createdAt || new Date().toISOString(),
      progress: 0,
      stages: [],
      models: draft.selectedProviders.map((providerId) => ({
        providerId,
        providerName: providerName(providerId, providers),
        providerKind: providers.find((item) => item.id === providerId)?.kind,
        model: providers.find((item) => item.id === providerId)?.model,
        status: 'pending',
      })),
    }
    setActiveRun(run)
    navigate('run', created.runId)
    void refreshRun(created.runId).catch(() => undefined)
  }

  const startDevelopmentDemo = async (text: string, externalProviders: ProviderSummary[]) => {
    const providerIds = externalProviders.map((provider) => provider.id)
    const created = await api.createDevelopmentDemoRun(text, providerIds)
    autoOpenEligibleRunId.current = created.runId
    const run: RunDetail = {
      runId: created.runId,
      runMode: 'development_demo',
      resultSchemaVersion: 'owlpath.result.v3',
      status: created.status,
      createdAt: created.createdAt || new Date().toISOString(),
      progress: 0,
      currentStage: '病例资料已提交，正在启动核心证据 Agent 与动态专病路由',
      stages: [],
      models: providerIds.map((providerId) => ({
        providerId,
        providerName: providerName(providerId, providers),
        providerKind: providers.find((item) => item.id === providerId)?.kind,
        model: providers.find((item) => item.id === providerId)?.model,
        status: 'pending',
      })),
    }
    setActiveRun(run)
    navigate('run', created.runId)
    void refreshRun(created.runId).catch(() => undefined)
  }

  const selectHistoryRun = async (runId: string) => {
    try {
      const run = await refreshRun(runId)
      navigate(run.result ? 'result' : 'run', runId)
    } catch (error) {
      setConnectionMessage(apiErrorMessage(error))
    }
  }

  const routedRun = route.runId && activeRun?.runId === route.runId ? activeRun : undefined
  const pageNode = (() => {
    switch (page) {
      case 'case': return null
      case 'models': return <ModelSettings providers={providers} loading={providersLoading} error={providersError} connection={connection} onRefresh={loadProviders} onChanged={(summary) => setProviders((current) => [...current.filter((item) => item.id !== summary.id), summary])} onDeleted={(id) => setProviders((current) => current.filter((item) => item.id !== id))} />
      case 'architecture': return <ArchitecturePage view={route.architectureView || 'current'} onViewChange={navigateArchitecture} />
      case 'run': return <RunMonitor run={routedRun} onGoCase={() => navigate('case')} />
      case 'result': return <ResultOverview run={routedRun} resultTab={route.resultTab} onTabChange={(tab) => route.runId && navigate('result', route.runId, tab)} onGoRun={() => route.runId && navigate('run', route.runId)} onGoCompare={() => route.runId && navigate('compare', route.runId)} />
      case 'compare': return <ModelComparison run={routedRun} />
      case 'history': return <HistoryPage onSelect={selectHistoryRun} />
      case 'evaluation': return <EvaluationPage />
      case 'governance': return <GovernancePage />
      default: return null
    }
  })()

  return (
    <div className={cx('app-shell', collapsed && 'sidebar-collapsed')}>
      <Sidebar page={page} collapsed={collapsed} mobileOpen={mobileOpen} connection={connection} onNavigate={navigate} onCollapse={() => setCollapsed((value) => !value)} onCloseMobile={() => setMobileOpen(false)} />
      <div className="app-main">
        <Topbar page={page} theme={theme} connection={connection} activeRun={activeRun} onTheme={() => setTheme((value) => value === 'light' ? 'dark' : 'light')} onMenu={() => setMobileOpen(true)} onNavigate={navigate} />
        <ConnectionBanner state={connection} message={connectionMessage} onRetry={() => void checkConnection()} />
        <main className="page-container">
          <div hidden={page !== 'case'}><CaseWorkbench providers={providers} connection={connection} onStart={startRun} onStartDevelopmentDemo={startDevelopmentDemo} onManageModels={() => navigate('models')} /></div>
          {page !== 'case' && pageNode}
        </main>
        <footer className="app-footer"><span>OwlPath｜鸮径</span><span>病原体鉴别分析与模型评估 · 研发测试，不用于临床诊疗</span></footer>
      </div>
    </div>
  )
}
