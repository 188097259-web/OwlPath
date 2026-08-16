/**
 * Clinician-first presentation copy for the OwlPath development workflow.
 *
 * The functions in this module are deliberately pure and React-independent.
 * Clinical labels are the primary presentation; the original machine value is
 * retained separately for an expandable engineering view.
 */

export type ClinicalCopyTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger'

export interface ClinicalTermCopy {
  /** Short label suitable for the clinician-facing first layer. */
  primary: string
  /** One-sentence explanation of what this step contributes. */
  detail: string
  /** Unmodified phase, node key, or role for the engineering layer. */
  engineeringValue: string
}

export interface ClinicalStatusCopy {
  /** Short status suitable for a pill or heading. */
  primary: string
  /** Development-specific context kept visually secondary. */
  researchHint: string
  /** Unmodified backend status for the engineering layer. */
  engineeringValue: string
  tone: ClinicalCopyTone
  terminal: boolean
}

export interface ClinicalTechnicalErrorCopy {
  title: string
  reason: string
  action: string
  /** A safe, normalized code. Never contains the provider's raw message. */
  engineeringCode: string
  retryable: boolean
}

export interface TechnicalErrorInput {
  code?: string
  message?: string | { zhCn?: string; zh_cn?: string; en?: string }
  retryable?: boolean
}

export interface ClinicalDevelopmentResultPresentation {
  technicalFailure: boolean
  pageTitle: string
  pageDescription: string
  summaryTabLabel: string
  agentTabLabel: string
  modeNote: string
  showClinicalResult: boolean
}

export type DevelopmentAgentGroup = 'core_perspective' | 'dynamic_specialist' | 'system_process'
export type ClinicalComparisonMode = 'clinical_perspectives' | 'single_model' | 'multi_model'

export const CORE_CLINICAL_EXPERT_ROLE_IDS: readonly string[] = [
  'infectious_diseases',
  'critical_care_emergency',
  'clinical_epidemiology',
  'laboratory_medicine',
  'clinical_microbiology_culture',
]

export const DYNAMIC_CLINICAL_EXPERT_ROLE_IDS: readonly string[] = [
  'radiology',
  'pulmonology',
  'gastroenterology',
  'hepatobiliary_pancreatic',
  'urology',
  'nephrology',
  'neurology_neuroinfection',
  'cardiology_endocarditis',
  'hematology_immunology',
  'transplant_infectious_diseases',
  'surgery_source_control',
  'orthopedics_bone_joint',
  'dermatology_soft_tissue',
  'obstetrics_gynecology',
  'pediatrics_neonatology',
  'tropical_medicine_parasitology',
  'medical_mycology',
  'clinical_virology_molecular',
  'antimicrobial_stewardship',
  'healthcare_device_infection',
]

/** Distinguish development Agent roles from actual provider/model comparison. */
export function clinicalComparisonMode(developmentDemo: boolean, developmentV3: boolean, providerCount: number): ClinicalComparisonMode {
  if (developmentDemo && developmentV3) return 'clinical_perspectives'
  return providerCount <= 1 ? 'single_model' : 'multi_model'
}

type TermDefinition = Omit<ClinicalTermCopy, 'engineeringValue'>

const PHASES: Readonly<Record<string, TermDefinition>> = {
  queued: {
    primary: '等待开始',
    detail: '本次分析已建立，正在等待可用模型资源。',
  },
  preparation: {
    primary: '整理病例资料',
    detail: '读取全文，按原始顺序整理可追溯的病例证据。',
  },
  preflight: {
    primary: '检查资料与连接',
    detail: '在分析前确认输入、模型连接和运行记录可用。',
  },
  routing: {
    primary: '召集本次顶尖临床专家会诊',
    detail: '固定召集五个核心专家，再根据病例证据从专家库中最多选择六个动态专科；未选角色记为 not_applicable。',
  },
  specialists: {
    primary: '顶尖临床专家并行会诊',
    detail: '五个核心专家与本次被路由选中的动态专科专家并行阅读病例，分别交付结构化专业意见。',
  },
  retrieval: {
    primary: '查找医学证据',
    detail: '使用去标识化的医学问题检索公开文献，不向搜索服务发送病例全文。',
  },
  synthesis: {
    primary: '综合并排序病原体',
    detail: '综合病例全文、本次实际运行的 Agent 意见、证据板，以及可获得的外部证据与检索覆盖状态，生成具体病原体候选。',
  },
  validation: {
    primary: '核对名称与证据',
    detail: '检查病原体是否具体、唯一，排序、术语和引用是否完整。',
  },
  review: {
    primary: '独立复核结果',
    detail: '用独立提示词和新上下文复核排名、病例证据及明显遗漏。',
  },
  revision: {
    primary: '按复核意见修订',
    detail: '仅在发现明确问题时修订一次，不无限循环。',
  },
  enrichment: {
    primary: '补充候选病原体证据',
    detail: '针对已确定的候选再核对相关文献，便于追溯。',
  },
  compiling: {
    primary: '生成医生可读结果',
    detail: '将候选、理由、反对证据、下一项检查和研发提示组成双语结果。',
  },
  persistence: {
    primary: '保存结果与过程记录',
    detail: '保存本次结果、版本和完整性记录，方便之后复查。',
  },
  completed: {
    primary: '分析已完成',
    detail: '本次病原体分析结果已生成并保存。',
  },
  failed: {
    primary: '未能完成分析',
    detail: '技术过程未完成，请根据页面提示检查连接或重试。',
  },
}

const NODE_RESPONSIBILITIES: Readonly<Record<string, TermDefinition>> = {
  scope_contract: { primary: '说明系统的当前用途', detail: '区分开发分析与严格临床模式，并记录当前阶段的使用边界。' },
  provider_registry: { primary: '管理本次可用的分析模型', detail: '冻结已测试模型、优先级和版本，使本次运行可复查。' },
  privacy_audit: { primary: '保护数据传输并留存审计记录', detail: '检查外发地址、密钥脱敏和完整性记录。' },
  snapshot: { primary: '读取本次病例资料', detail: '保留输入全文和本次分析快照。' },
  source_input: { primary: '读取本次病例资料', detail: '使用纯虚构或已脱敏的病例全文。' },
  preflight: { primary: '检查资料和模型连接', detail: '确认输入、模型清单和运行记录完整。' },
  integrity_preflight: { primary: '检查资料和模型连接', detail: '确认输入、模型清单和运行记录完整。' },
  applicability: { primary: '记录适用范围提示', detail: '开发模式下只记录研发提示，不中断本次分析。' },
  observation_guards: { primary: '记录资料限制', detail: '记录适用范围、缺失和资料冲突，不在开发模式下阻断。' },
  input_quality: { primary: '检查资料质量', detail: '标记缺失、矛盾和时间不确定信息，供解读时参考。' },
  source_compiler: { primary: '整理可追溯病例证据', detail: '为原文句段编号，使每条判断都能指回原始资料。' },
  complexity_router: { primary: '决定本次需要哪些临床专家', detail: '固定召集五个核心专家，再根据感染部位、宿主、暴露与医疗场景最多选择六个动态专科专家；未选角色记为 not_applicable。' },
  infectious_diseases: { primary: '感染科首席专家会诊', detail: '统合综合征、宿主、暴露、病原学和治疗干扰，组织具体病原鉴别诊断。' },
  critical_care_emergency: { primary: '重症与急诊医学专家会诊', detail: '重建器官衰竭与干预时间线，识别源控制需求及重症模拟病。' },
  clinical_epidemiology: { primary: '临床流行病学专家会诊', detail: '审核暴露时序与可信度，区分患者级证据与地区季节先验。' },
  laboratory_medicine: { primary: '检验医学专家会诊', detail: '审核检验单位、趋势、质量和病理生理模式，区分病原线索与非特异重症反应。' },
  clinical_microbiology_culture: { primary: '临床微生物与培养实验室专家会诊', detail: '审核标本质量、染色、培养、鉴定、药敏、污染/定植和用药后检出率。' },
  radiology: { primary: '放射影像专家会诊', detail: '按解剖位置、形态和分布识别原发灶、播散灶、并发症及模拟病。' },
  pulmonology: { primary: '呼吸与危重呼吸专家会诊', detail: '鉴别肺部感染、吸入、弥漫性肺损伤及非感染浸润，评估呼吸道标本。' },
  gastroenterology: { primary: '消化内科专家会诊', detail: '分析胃肠道感染、腹膜炎、肠源性菌血症及重要非感染解释。' },
  hepatobiliary_pancreatic: { primary: '肝胆胰专家会诊', detail: '鉴别肝脏灶、胆道感染、胰腺炎及缺血/药物性肝损伤，评估引流需求。' },
  urology: { primary: '泌尿外科与复杂尿路感染专家会诊', detail: '识别梗阻性肾盂肾炎、前列腺感染、器械相关感染及引流需求。' },
  nephrology: { primary: '肾脏内科专家会诊', detail: '区分感染相关AKI、肾炎、血栓性微血管病、药物损伤及横纹肌溶解。' },
  neurology_neuroinfection: { primary: '神经内科与神经感染专家会诊', detail: '完成神经解剖定位、脑脊液解读，并保留非感染神经急症。' },
  cardiology_endocarditis: { primary: '心血管与心内膜炎专家会诊', detail: '鉴别心内膜炎、心肌/心包炎、感染性栓塞与非感染心肌损伤。' },
  hematology_immunology: { primary: '血液与临床免疫专家会诊', detail: '解读细胞减少、溶血、凝血障碍、免疫缺陷和炎症过度激活。' },
  transplant_infectious_diseases: { primary: '移植感染专家会诊', detail: '按移植类型、术后阶段、免疫抑制和预防方案重建机会感染谱。' },
  surgery_source_control: { primary: '外科与源控制专家会诊', detail: '识别需要手术或介入的腹腔、软组织、伤口、坏死组织及深部脓肿。' },
  orthopedics_bone_joint: { primary: '骨科与骨关节感染专家会诊', detail: '鉴别骨髓炎、化脓性关节炎、脊柱感染和植入物相关感染。' },
  dermatology_soft_tissue: { primary: '皮肤科与软组织感染专家会诊', detail: '从皮损形态、入口和暴露识别深部/坏死性感染与模拟病。' },
  obstetrics_gynecology: { primary: '妇产科感染专家会诊', detail: '按孕期、产后、宫腔/盆腔操作评估妇产科感染源与母胎风险。' },
  pediatrics_neonatology: { primary: '儿科、新生儿与儿童感染专家会诊', detail: '使用年龄特异生理、围产暴露、免疫发育和疫苗史解读病原谱。' },
  tropical_medicine_parasitology: { primary: '热带病与寄生虫专家会诊', detail: '以地理、旅行、媒介、食物/水体和嗜酸细胞线索识别特殊病原。' },
  medical_mycology: { primary: '医学真菌专家会诊', detail: '结合宿主、解剖模式和真菌学证据区分侵袭性真菌病、定植与污染。' },
  clinical_virology_molecular: { primary: '临床病毒学与分子诊断专家会诊', detail: '解读分子检测窗口，区分活动感染、再激活、潜伏与偶然检出。' },
  antimicrobial_stewardship: { primary: '抗菌药物管理与临床药学专家会诊', detail: '判断已用药覆盖、组织穿透、剂量暴露、检出率干扰和耐药选择压力。' },
  healthcare_device_infection: { primary: '医疗相关与器械感染专家会诊', detail: '围绕导管、假体和近期医疗暴露判断生物膜、污染/定植和耐药风险。' },
  timeline_course: { primary: '还原病程与检查用药顺序', detail: '分析起病、恶化、取样和用药的先后关系，避免把未来结果带入当前推理。' },
  host_susceptibility: { primary: '评估宿主易感因素', detail: '分析年龄、基础病、免疫状态、妊娠和其他会改变病原谱的宿主因素。' },
  syndrome_localization: { primary: '判断临床综合征与解剖定位', detail: '识别主要感染部位、可能入口以及局灶或播散模式。' },
  exposure_one_health: { primary: '分析暴露与 One Health 线索', detail: '检查水体、鱼类、动物、食物、职业、地理和季节暴露及矛盾。' },
  lab_pathophysiology: { primary: '解读实验室病理生理模式', detail: '归纳炎症、血细胞、凝血、生化和酸碱线索，区分病原提示与非特异重症反应。' },
  organ_severity: { primary: '评估器官损伤与严重度', detail: '整理休克、神经、呼吸、肝肾、心肌和凝血功能损伤图谱。' },
  imaging_dissemination: { primary: '解读影像与播散路径', detail: '整合影像解剖分布，区分原发灶、转移灶、血行播散和非感染模拟。' },
  microbiology_treatment: { primary: '解读微生物证据与治疗干扰', detail: '整理标本、已回、待回和阴性结果，并评估先行抗感染治疗对检出率的影响。' },
  neuroinfection: { primary: '进行神经感染专项分析', detail: '仅在中枢神经感染线索触发时运行，集中解读神经定位、脑脊液和相关病原谱。' },
  immunocompromised_opportunistic: { primary: '进行免疫抑制与机会感染分析', detail: '仅在免疫缺陷或机会感染线索触发时运行。' },
  travel_zoonotic: { primary: '进行旅行、人畜共患与环境感染分析', detail: '仅在旅行、动物、水体、鱼类、职业或地理暴露触发时运行。' },
  healthcare_device_amr: { primary: '进行医疗相关、器械与耐药分析', detail: '仅在医疗暴露、侵入器械、近期抗生素或耐药风险触发时运行。' },
  timeline_host: { primary: '梳理病程与宿主因素', detail: '分析起病顺序、基础情况、场景和治疗相对时间。' },
  syndrome_site: { primary: '判断感染部位与综合征', detail: '识别主要感染部位、多部位表现和需要注意的非感染模拟病。' },
  exposure_epidemiology: { primary: '分析暴露与流行病学线索', detail: '检查水体、鱼类、食物、动物、旅行和医疗暴露及其矛盾。' },
  laboratory_organ_injury: { primary: '分析实验室异常与器官损伤', detail: '归纳炎症、凝血、肝肾、心肌及酸碱平衡模式。' },
  imaging_microbiology_treatment: { primary: '综合影像、微生物与已用治疗', detail: '整合影像表现、阴性或待回报告、标本以及已用抗感染药物。' },
  evidence_board: { primary: '汇总证据与候选病原体', detail: '将各已运行 Agent 的支持、反对、矛盾、缺失与具体病原候选归入同一证据板。' },
  retrieval_planner: { primary: '规划多来源医学证据查询', detail: '生成去标识化的综合征、暴露、病原体、类似病例与公共卫生查询，不发送病例全文。' },
  literature_retrieval: { primary: '检索文献与类似病例', detail: '从 Europe PMC 与 PubMed/NCBI 查找文献、类似病例和病原分类信息。' },
  public_health_retrieval: { primary: '查找公共卫生、指南与疫情信号', detail: '实时查询 WHO 疫情通报；CDC、ECDC、中国疾控和专业学会当前只列入来源目录，尚未逐站实时检索。没有命中不代表一定没有疫情或相关指南。' },
  evidence_verifier: { primary: '核对外部证据与候选的关联', detail: '当前核对来源、引用链接及题名与检索概念的重叠，并排除只凭搜索排序的记录；摘要和全文核验仍在建设。' },
  retrieval: { primary: '检索并核对医学证据', detail: '用泛化后的医学问题查找公开文献，不外发病例全文。' },
  medical_retrieval: { primary: '检索并核对医学证据', detail: '用泛化后的医学问题查找公开文献，不外发病例全文。' },
  synthesis: { primary: '汇总并排序具体病原体', detail: '综合病例全文、本次实际运行的分析视角、证据板，以及可获得的外部证据与检索覆盖状态，生成具体 Top-5。' },
  contract_validator: { primary: '核对病原体名称和结果完整性', detail: '检查是否恰好五个具体、唯一的病原体，并核对排序和证据引用。' },
  critic: { primary: '独立寻找反证并复核病原体结果', detail: '从遗漏、替代解释、反对证据和引用错配角度独立挑战当前排名。' },
  revision: { primary: '按反证意见有限修订一次', detail: '仅对已确认的合同或反证问题进行一次有限修订，不进行无限循环。' },
  candidate_evidence_enrichment: { primary: '为候选病原体补充文献', detail: '针对已完成名称与格式核验的候选，按题名精确关联可追溯公开文献；同义词和摘要级召回仍需扩展。' },
  result_compiler: { primary: '生成中英文医生可读结果', detail: '将 Top-5、理由、反对证据、下一项检查和研发提示组成结果。' },
  persistence: { primary: '保存结果与可追溯记录', detail: '保存本次执行图、版本、结构化输出和完整性记录。' },
  result_persistence: { primary: '保存结果与可追溯记录', detail: '保存本次执行图、版本、结构化输出和完整性记录。' },
  offline_evaluation: { primary: '离线检验系统表现', detail: '用有结果标签的历史数据评价准确性、稳定性和不同人群表现。' },
  target_scope: { primary: '定义可使用的人群与场景', detail: '明确适用人群、临床场景、当前时点、用途和排除条件。' },
  target_registry: { primary: '管理经验证的模型与知识版本', detail: '只允许经验证并冻结版本的模型、校准器、知识库和工具进入发布流程。' },
  target_security: { primary: '管理权限、监测和可回滚发布', detail: '保持最小权限、持续监测、故障熔断和版本回滚能力。' },
  target_ledger: { primary: '整理当前时点已知的临床资料', detail: '只纳入在当前分析时间真正可见的证据，避免使用未来结果。' },
  target_quality: { primary: '检查资料质量与适用性', detail: '检查单位、缺失、冲突、信息泄漏和与训练人群的差异。' },
  target_compiler: { primary: '将不同类型的临床证据统一整理', detail: '统一病史、暴露、生命体征、连续检查、影像报告与地区季节信息。' },
  target_router: { primary: '先判断是否支持感染及主要部位', detail: '先区分感染和重要非感染情况，再选择合适的病原体分析路径。' },
  target_discriminative: { primary: '用多模态模型直接评估病原体', detail: '计划综合结构化数据、影像报告和未来的原始影像。' },
  target_world_model: { primary: '比较不同病原体假设与当前证据的相容性', detail: '评估在不同病原体假设下，当前证据和可能后续检查结果是否一致。' },
  target_bayesian_prior: { primary: '加入经治理的地区与季节先验', detail: '计划使用医院、地区、季节和疫情聚合信息修正候选顺序。' },
  target_fusion: { primary: '综合病原类别、具体名称与共感染', detail: '统一处理病原大类、属种、共感染、其他未覆盖病原体和模型分歧。' },
  target_safety: { primary: '独立检查结果的可靠性', detail: '独立评估校准、覆盖率、证据冲突、人群差异和未覆盖情况。' },
  target_decision: { primary: '根据证据充分程度选择输出层级', detail: '在感染不支持、具体病原集合、仅报大类、需要更多信息或不输出之间做出选择。' },
  target_result: { primary: '生成可追溯的结构化结果', detail: '冻结模型、数据、证据、版本和执行记录。' },
  target_report_agent: { primary: '将已审定结果整理为医生报告', detail: '计划仅读取已核对的结构化结果，不允许更改排名、分数或安全状态。' },
  target_bilingual: { primary: '生成中英文医生可读结果', detail: '中文为主、英文为辅，两种语言共用同一组数值。' },
  target_human: { primary: '由医生独立复核并做最终决定', detail: '系统提供决策支持，不取代医生结合完整临床资料作出判断。' },
  offline_governance: { primary: '管理多中心数据与最终标签', detail: '将预测时可见资料与事后病原体结果严格隔离。' },
  offline_experiments: { primary: '比较特征、模型与校准方案', detail: '通过可复现实验比较不同设计，包括消融实验。' },
  offline_external_validation: { primary: '在不同时间和地区验证', detail: '检验系统离开原始数据环境后是否仍然稳定。' },
  offline_agent_eval: { primary: '评测多视角协作过程并完成安全测试', detail: '检查每个节点的输出、失败隔离和对异常输入的处理。' },
  offline_silent: { primary: '在不影响临床决策的情况下前瞻运行', detail: '只记录系统结果，不向临床医生展示，用于评估真实环境表现。' },
  offline_release: { primary: '由人工评审决定是否发布', detail: '只有通过预先定义的验证门槛后，新版本才能进入应用。' },
  offline_monitor: { primary: '持续监测性能与数据变化', detail: '发布后持续检查数据分布、不同人群表现和故障信号。' },
}

const ROLE_RESPONSIBILITIES: Readonly<Record<string, TermDefinition>> = {
  infectious_diseases: NODE_RESPONSIBILITIES.infectious_diseases,
  critical_care_emergency: NODE_RESPONSIBILITIES.critical_care_emergency,
  clinical_epidemiology: NODE_RESPONSIBILITIES.clinical_epidemiology,
  laboratory_medicine: NODE_RESPONSIBILITIES.laboratory_medicine,
  clinical_microbiology_culture: NODE_RESPONSIBILITIES.clinical_microbiology_culture,
  radiology: NODE_RESPONSIBILITIES.radiology,
  pulmonology: NODE_RESPONSIBILITIES.pulmonology,
  gastroenterology: NODE_RESPONSIBILITIES.gastroenterology,
  hepatobiliary_pancreatic: NODE_RESPONSIBILITIES.hepatobiliary_pancreatic,
  urology: NODE_RESPONSIBILITIES.urology,
  nephrology: NODE_RESPONSIBILITIES.nephrology,
  neurology_neuroinfection: NODE_RESPONSIBILITIES.neurology_neuroinfection,
  cardiology_endocarditis: NODE_RESPONSIBILITIES.cardiology_endocarditis,
  hematology_immunology: NODE_RESPONSIBILITIES.hematology_immunology,
  transplant_infectious_diseases: NODE_RESPONSIBILITIES.transplant_infectious_diseases,
  surgery_source_control: NODE_RESPONSIBILITIES.surgery_source_control,
  orthopedics_bone_joint: NODE_RESPONSIBILITIES.orthopedics_bone_joint,
  dermatology_soft_tissue: NODE_RESPONSIBILITIES.dermatology_soft_tissue,
  obstetrics_gynecology: NODE_RESPONSIBILITIES.obstetrics_gynecology,
  pediatrics_neonatology: NODE_RESPONSIBILITIES.pediatrics_neonatology,
  tropical_medicine_parasitology: NODE_RESPONSIBILITIES.tropical_medicine_parasitology,
  medical_mycology: NODE_RESPONSIBILITIES.medical_mycology,
  clinical_virology_molecular: NODE_RESPONSIBILITIES.clinical_virology_molecular,
  antimicrobial_stewardship: NODE_RESPONSIBILITIES.antimicrobial_stewardship,
  healthcare_device_infection: NODE_RESPONSIBILITIES.healthcare_device_infection,
  timeline_course: NODE_RESPONSIBILITIES.timeline_course,
  host_susceptibility: NODE_RESPONSIBILITIES.host_susceptibility,
  syndrome_localization: NODE_RESPONSIBILITIES.syndrome_localization,
  exposure_one_health: NODE_RESPONSIBILITIES.exposure_one_health,
  lab_pathophysiology: NODE_RESPONSIBILITIES.lab_pathophysiology,
  organ_severity: NODE_RESPONSIBILITIES.organ_severity,
  imaging_dissemination: NODE_RESPONSIBILITIES.imaging_dissemination,
  microbiology_treatment: NODE_RESPONSIBILITIES.microbiology_treatment,
  neuroinfection: NODE_RESPONSIBILITIES.neuroinfection,
  immunocompromised_opportunistic: NODE_RESPONSIBILITIES.immunocompromised_opportunistic,
  travel_zoonotic: NODE_RESPONSIBILITIES.travel_zoonotic,
  healthcare_device_amr: NODE_RESPONSIBILITIES.healthcare_device_amr,
  timeline_host: NODE_RESPONSIBILITIES.timeline_host,
  syndrome_site: NODE_RESPONSIBILITIES.syndrome_site,
  exposure_epidemiology: NODE_RESPONSIBILITIES.exposure_epidemiology,
  laboratory_organ_injury: NODE_RESPONSIBILITIES.laboratory_organ_injury,
  imaging_microbiology_treatment: NODE_RESPONSIBILITIES.imaging_microbiology_treatment,
  evidence_retrieval: NODE_RESPONSIBILITIES.retrieval,
  medical_evidence_retrieval: NODE_RESPONSIBILITIES.retrieval,
  evidence_board: NODE_RESPONSIBILITIES.evidence_board,
  retrieval_planner: NODE_RESPONSIBILITIES.retrieval_planner,
  literature_retrieval: NODE_RESPONSIBILITIES.literature_retrieval,
  public_health_retrieval: NODE_RESPONSIBILITIES.public_health_retrieval,
  evidence_verifier: NODE_RESPONSIBILITIES.evidence_verifier,
  pathogen_synthesis: NODE_RESPONSIBILITIES.synthesis,
  pathogen_chief_synthesis: NODE_RESPONSIBILITIES.synthesis,
  taxonomy_and_top5_contract_validator: NODE_RESPONSIBILITIES.contract_validator,
  independent_critic: NODE_RESPONSIBILITIES.critic,
  independent_medical_critic: NODE_RESPONSIBILITIES.critic,
  single_pass_synthesis_revision: NODE_RESPONSIBILITIES.revision,
  candidate_specific_literature_enrichment: NODE_RESPONSIBILITIES.candidate_evidence_enrichment,
  development_result_compiler: NODE_RESPONSIBILITIES.result_compiler,
  result_persistence: NODE_RESPONSIBILITIES.persistence,
  snapshot_compiler: NODE_RESPONSIBILITIES.snapshot,
  technical_integrity_preflight: NODE_RESPONSIBILITIES.preflight,
  development_scope_observer: NODE_RESPONSIBILITIES.applicability,
  development_quality_observer: NODE_RESPONSIBILITIES.input_quality,
  source_fragment_compiler: NODE_RESPONSIBILITIES.source_compiler,
  pathogen_hypothesis_agent: { primary: '提出病原体候选', detail: '根据当前证据提出具体病原体候选及支持和反对理由。' },
  ensemble_aggregator: { primary: '综合多个模型的结果', detail: '将多个模型的候选、分歧和证据统一整理。' },
  release_safety_adjudicator: { primary: '独立检查结果可否展示', detail: '独立检查校准、冲突、资料限制和安全状态。' },
  bilingual_result_compiler: NODE_RESPONSIBILITIES.result_compiler,
}

function normalize(value: string | undefined): string {
  return (value || '').trim().toLowerCase().replace(/[\s./-]+/g, '_')
}

function copyTerm(definition: TermDefinition, engineeringValue: string): ClinicalTermCopy {
  return { ...definition, engineeringValue }
}

function phaseFamily(value: string): keyof typeof PHASES | undefined {
  if (!value) return undefined
  if (/fail|error|technical_failure|cancel/.test(value)) return 'failed'
  if (/complete|finished|done/.test(value) || /已完成/.test(value)) return 'completed'
  if (/queue|pending|waiting|排队|等待/.test(value)) return 'queued'
  if (/snapshot|source_fragment|source_compiler|local_preparation|prepar|原文|资料整理/.test(value)) return 'preparation'
  if (/preflight|integrity|input_quality|applicability|预检|质量检查/.test(value)) return 'preflight'
  if (/complexity_router|specialist_router|dynamic_routing|专病路由/.test(value)) return 'routing'
  if (CORE_CLINICAL_EXPERT_ROLE_IDS.includes(value) || DYNAMIC_CLINICAL_EXPERT_ROLE_IDS.includes(value)) return 'specialists'
  if (/specialist|timeline_host|syndrome_site|exposure_epidemiology|laboratory_organ|imaging_microbiology|timeline_course|host_susceptibility|syndrome_localization|exposure_one_health|lab_pathophysiology|organ_severity|imaging_dissemination|microbiology_treatment|neuroinfection|immunocompromised_opportunistic|travel_zoonotic|healthcare_device_amr|专病|agent.*推演|临床视角/.test(value)) return 'specialists'
  if (/retrieval|evidence_board|evidence_verifier|pubmed|europe_pmc|检索|证据板/.test(value)) return 'retrieval'
  if (/synthesis|总诊|汇总|排序病原/.test(value)) return 'synthesis'
  if (/contract_validator|taxonomy|validation|合同检查|术语检查/.test(value)) return 'validation'
  if (/critic|independent_review|审稿|独立复核/.test(value)) return 'review'
  if (/revision|修订/.test(value)) return 'revision'
  if (/enrichment|证据补充|文献补充/.test(value)) return 'enrichment'
  if (/result_compiler|bilingual_renderer|compil|结果编译|生成结果/.test(value)) return 'compiling'
  if (/persist|signed_result|hash|固化|保存/.test(value)) return 'persistence'
  return undefined
}

/** Map a backend phase to clinician-first copy while retaining the raw phase. */
export function clinicalRunPhase(phase?: string): ClinicalTermCopy {
  const engineeringValue = phase || 'unknown'
  const family = phaseFamily(normalize(phase))
  if (family) return copyTerm(PHASES[family], engineeringValue)
  return {
    primary: '正在处理本次分析',
    detail: '具体技术阶段可在“工程信息”中查看。',
    engineeringValue,
  }
}

function bareNodeKey(nodeKey: string): string {
  const normalized = normalize(nodeKey)
  return normalized.startsWith('specialist:')
    ? normalized.slice('specialist:'.length)
    : normalized.startsWith('specialist_')
      ? normalized.slice('specialist_'.length)
      : normalized
}

/** Map an execution-graph node key to its clinician-facing responsibility. */
export function clinicalNodeResponsibility(nodeKey?: string): ClinicalTermCopy {
  const engineeringValue = nodeKey || 'unknown'
  const key = bareNodeKey(engineeringValue)
  const definition = NODE_RESPONSIBILITIES[key]
  if (definition) return copyTerm(definition, engineeringValue)
  return {
    primary: '执行本次分析的一个步骤',
    detail: '该节点的精确职责尚未加入临床文案字典，可在工程信息中查看原始名称。',
    engineeringValue,
  }
}

/** Map a specialty or Agent role to a clinician-facing role name. */
export function clinicalAgentRole(role?: string): ClinicalTermCopy {
  const engineeringValue = role || 'unknown'
  const key = bareNodeKey(engineeringValue)
  const definition = ROLE_RESPONSIBILITIES[key] || NODE_RESPONSIBILITIES[key]
  if (definition) return copyTerm(definition, engineeringValue)
  return {
    primary: '其他分析职责',
    detail: '该职责的精确工程角色可在工程信息中查看。',
    engineeringValue,
  }
}

/** Clinician-first status for the whole development run. */
export function clinicalDevelopmentRunStatus(status?: string): ClinicalStatusCopy {
  const engineeringValue = status || 'unknown'
  switch (normalize(status)) {
    case 'queued':
    case 'pending':
      return { primary: '等待开始', researchHint: '运行已创建，正在等待模型资源。', engineeringValue, tone: 'neutral', terminal: false }
    case 'running':
    case 'in_progress':
      return { primary: '正在分析', researchHint: '各临床视角和复核步骤正在按顺序运行。', engineeringValue, tone: 'info', terminal: false }
    case 'completed':
      return { primary: '已完成', researchHint: '结果已生成；开发分数尚未做临床概率校准。', engineeringValue, tone: 'success', terminal: true }
    case 'completed_with_warnings':
    case 'completed_with_observations':
      return { primary: '已完成', researchHint: '结果已生成；同时记录了研发提示，可在工程信息中查看。', engineeringValue, tone: 'warning', terminal: true }
    case 'technical_failure':
    case 'failed':
      return { primary: '未能生成结果', researchHint: '技术流程未完成；请按错误提示检查模型连接后重试。', engineeringValue, tone: 'danger', terminal: true }
    case 'cancelled':
    case 'canceled':
      return { primary: '已停止', researchHint: '本次运行已终止，未生成完整结果。', engineeringValue, tone: 'warning', terminal: true }
    default:
      return { primary: '状态待确认', researchHint: '后端返回了未识别的研发状态，详细值可在工程信息中查看。', engineeringValue, tone: 'neutral', terminal: false }
  }
}

/** Clinician-first status for one specialist/tool/review Agent. */
export function clinicalDevelopmentAgentStatus(status?: string): ClinicalStatusCopy {
  const engineeringValue = status || 'unknown'
  const value = normalize(status)
  if (value === 'completed') {
    return { primary: '已返回', researchHint: '该分析环节已返回结构化意见。', engineeringValue, tone: 'success', terminal: true }
  }
  if (/completed_with_warnings|warning/.test(value)) {
    return { primary: '已返回', researchHint: '该分析环节已返回结果，并记录了可展开查看的技术提示。', engineeringValue, tone: 'warning', terminal: true }
  }
  if (/partial/.test(value)) {
    return { primary: '已返回部分内容', researchHint: '该分析环节的结构化内容不完整，其他环节仍可继续。', engineeringValue, tone: 'warning', terminal: true }
  }
  if (value === 'running' || value === 'in_progress') {
    return { primary: '正在分析', researchHint: '该分析环节尚在等待模型返回。', engineeringValue, tone: 'info', terminal: false }
  }
  if (value === 'pending' || value === 'queued' || value === 'not_started') {
    return { primary: '等待开始', researchHint: '该分析环节已加入本次执行清单。', engineeringValue, tone: 'neutral', terminal: false }
  }
  if (value === 'skipped' || value === 'not_applicable' || value === 'bypassed') {
    return { primary: '本次未运行', researchHint: '本次不需要或未触发该步骤，具体原因可在工程信息中查看。', engineeringValue, tone: 'neutral', terminal: true }
  }
  if (value === 'failed' || value === 'technical_failure') {
    return { primary: '本环节未返回', researchHint: '该分析环节发生技术故障；其他环节可继续时会继续，必要时可重试。', engineeringValue, tone: 'danger', terminal: true }
  }
  return { primary: '状态待确认', researchHint: '该步骤返回了未识别的研发状态，详细值可在工程信息中查看。', engineeringValue, tone: 'neutral', terminal: false }
}

/** Decide which result surfaces are honest for a completed or failed development run. */
export function clinicalDevelopmentResultPresentation(status?: string): ClinicalDevelopmentResultPresentation {
  const technicalFailure = normalize(status) === 'technical_failure' || normalize(status) === 'failed'
  if (technicalFailure) {
    return {
      technicalFailure: true,
      pageTitle: '本次分析未完成',
      pageDescription: '技术流程未能生成完整、可解释的病原体结果；已返回的分析意见和工程记录仍会保留。',
      summaryTabLabel: '失败说明',
      agentTabLabel: '已返回的分析意见',
      modeNote: '本次没有可作为病原体结果解读的分数；已完成的分析环节仅作为研发记录保留。',
      showClinicalResult: false,
    }
  }
  return {
    technicalFailure: false,
    pageTitle: '可能病原体与下一步检查',
    pageDescription: '先看最可能的5种病原体、主要判断依据，以及下一步优先完善哪些检查。',
    summaryTabLabel: '临床结果',
    agentTabLabel: '各分析环节意见',
    modeNote: '研发测试：所有分数只用于候选排序，不代表患病概率；研发提示会被记录，但在流程仍可继续时不会中断。',
    showClinicalResult: true,
  }
}

/** Separate always-on core perspectives, dynamically recruited specialists and system processes. */
export function developmentAgentGroup(role?: string): DevelopmentAgentGroup {
  const value = bareNodeKey(role || '')
  if (CORE_CLINICAL_EXPERT_ROLE_IDS.includes(value)) return 'core_perspective'
  if (DYNAMIC_CLINICAL_EXPERT_ROLE_IDS.includes(value)) return 'dynamic_specialist'
  if (['timeline_course', 'host_susceptibility', 'syndrome_localization', 'exposure_one_health', 'lab_pathophysiology', 'organ_severity', 'imaging_dissemination', 'microbiology_treatment'].includes(value)) return 'core_perspective'
  if (['neuroinfection', 'immunocompromised_opportunistic', 'travel_zoonotic', 'healthcare_device_amr'].includes(value)) return 'dynamic_specialist'
  // Keep v1 runs readable after the adaptive-team architecture is released.
  if (['timeline_host', 'syndrome_site', 'exposure_epidemiology', 'laboratory_organ_injury', 'imaging_microbiology_treatment'].includes(value)) return 'core_perspective'
  return 'system_process'
}

function errorText(input: string | TechnicalErrorInput | null | undefined): { code: string; text: string; retryable?: boolean } {
  if (typeof input === 'string') return { code: '', text: input }
  if (!input) return { code: '', text: '' }
  const message = typeof input.message === 'string'
    ? input.message
    : input.message?.zhCn || input.message?.zh_cn || input.message?.en || ''
  return { code: input.code || '', text: message, retryable: input.retryable }
}

/**
 * Convert structured or free-form technical failures into safe, actionable
 * clinician-facing copy. The provider's raw error text is never returned.
 */
export function clinicalTechnicalError(input: string | TechnicalErrorInput | null | undefined): ClinicalTechnicalErrorCopy {
  const error = errorText(input)
  const value = normalize(`${error.code} ${error.text}`)
  const retry = error.retryable

  if (/missing_api_key|secret_decryption|api_key.*(missing|invalid)|密钥.*(缺失|无法读取)/.test(value)) {
    return { title: '模型密钥不可用', reason: '系统未能读取当前模型的 API Key。', action: '请到“模型连接”重新填写 API Key，保存并测试连接。', engineeringCode: 'credential_unavailable', retryable: false }
  }
  if (/provider_http_401|provider_http_403|unauthorized|forbidden|authentication|permission|access_denied/.test(value)) {
    return { title: '模型鉴权未通过', reason: '密钥、账户权限或模型开通状态不符合当前请求。', action: '请核对 API Key、账户余额和模型权限，然后重新测试连接。', engineeringCode: 'provider_authentication_failed', retryable: false }
  }
  if (/provider_http_404|provider_not_found|model_not_found|missing_base_url|unsupported_provider/.test(value)) {
    return { title: '未找到配置的模型服务', reason: '模型 ID、服务地址或接口类型可能与厂商配置不一致。', action: '请对照厂商控制台核对模型 ID 和服务地址，保存后重新测试。', engineeringCode: 'provider_target_not_found', retryable: false }
  }
  if (/provider_http_429|rate_limit|quota|insufficient_quota|budget_exhausted|too_many_requests/.test(value)) {
    return { title: '模型额度或请求频率受限', reason: '当前账户可用额度不足，或短时间内请求过多。', action: '请检查厂商余额和限额，稍后重试，或切换到另一个已测试模型。', engineeringCode: 'provider_rate_or_quota_limited', retryable: retry ?? true }
  }
  if (/timeout|timed_out|deadline|http_read|http_connect|http_write|http_pool/.test(value)) {
    return { title: '模型返回超时', reason: '模型服务未在本次运行的时间上限内返回。', action: '请先重试一次；若反复发生，请检查网络并换用响应更稳定的模型。', engineeringCode: 'provider_timeout', retryable: retry ?? true }
  }
  if (/dns|network|connection|connect_error|transport|temporarily_unavailable/.test(value)) {
    return { title: '无法连接模型服务', reason: '本机与模型服务之间的网络或域名解析暂时不可用。', action: '请检查网络、代理和服务地址；若使用本地模型，请确认服务已启动。', engineeringCode: 'provider_network_unavailable', retryable: retry ?? true }
  }
  if (/provider_http_5\w*|provider_http_503|server_error|service_unavailable|provider_internal_error|provider_test_internal_error|run_internal_error|development_agent_internal_error/.test(value)) {
    return { title: '模型服务暂时异常', reason: '模型厂商或本地分析服务本次未正常完成请求。', action: '请稍后重试；若持续发生，请切换到另一个已测试模型并查看服务日志。', engineeringCode: 'service_internal_error', retryable: retry ?? true }
  }
  if (/invalid_provider_json|invalid_provider_envelope|provider_schema_mismatch|empty_provider_response|output_truncated|invalid.*json|schema|structured_output/.test(value)) {
    return { title: '模型返回内容无法整理', reason: '接口已响应，但返回内容不符合本次结构化结果要求。', action: '请先重试一次；若反复发生，请核对接口类型、模型 ID 和输出上限。', engineeringCode: 'provider_output_invalid', retryable: retry ?? true }
  }
  if (/unsafe_provider_url|requires_https|redirect_blocked|response_too_large|egress_policy|ssrf/.test(value)) {
    return { title: '模型服务地址未通过网络保护检查', reason: '当前地址、重定向或返回大小不符合系统的网络安全规则。', action: '请使用厂商公布的 HTTPS 服务地址，不要使用中转或会重定向的地址。', engineeringCode: 'provider_network_policy_blocked', retryable: false }
  }
  if (/run_integrity|config.*hash|manifest.*mismatch|provider_id_manifest_mismatch/.test(value)) {
    return { title: '本次运行配置校验未通过', reason: '运行期间的模型清单或版本记录与启动时不一致。', action: '请刷新页面后新建一次运行；不要继续使用该次未完成结果。', engineeringCode: 'run_integrity_failure', retryable: false }
  }
  if (/server_restarted|service.*restart/.test(value)) {
    return { title: '分析服务在运行中重启', reason: '服务重启使本次运行中断。', action: '请刷新页面，确认后端已连接，然后重新启动分析。', engineeringCode: 'service_restarted', retryable: true }
  }
  if (/run_interrupted|cancelled|canceled|aborted|interrupted/.test(value)) {
    return { title: '本次分析已停止', reason: '运行在生成完整病原体结果前终止。', action: '如果仍需要结果，请确认分析服务正常后重新开始一次分析。', engineeringCode: 'run_interrupted', retryable: retry ?? true }
  }
  if (/provider_not_available|provider_disabled_by_the_user|no_ready_provider|no_available_provider|all_providers_failed/.test(value)) {
    return { title: '当前没有可用模型', reason: '未找到已启用且连接测试通过的模型。', action: '请到“模型连接”完成至少一个模型的连接测试并启用它。', engineeringCode: 'no_ready_provider', retryable: false }
  }
  if (/development_technical_failure|specific_top5_contract_failed|top_?5.*contract|taxonomy_unresolved|no_usable_concrete_top|did_not_produce_a_usable_concrete_top/.test(value)) {
    return { title: '未生成完整的具体病原体列表', reason: '模型返回的病原体名称、数量或证据引用没有满足本次结果格式要求。', action: '请先重试一次；若反复发生，请在工程信息中记录运行编号并检查模型输出与名称核验记录。', engineeringCode: 'development_result_contract_failed', retryable: retry ?? true }
  }
  return {
    title: '本次分析遇到技术问题',
    reason: '系统未能完成当前步骤，且暂时无法将原因归入已知类型。',
    action: '请先重试一次；若仍失败，请在工程信息中记录运行 ID 并查看后端日志。',
    engineeringCode: 'unclassified_technical_error',
    retryable: retry ?? true,
  }
}
