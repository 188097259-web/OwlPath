import assert from 'node:assert/strict'
import test from 'node:test'

import {
  CORE_CLINICAL_EXPERT_ROLE_IDS,
  DYNAMIC_CLINICAL_EXPERT_ROLE_IDS,
  clinicalAgentRole,
  clinicalComparisonMode,
  clinicalDevelopmentAgentStatus,
  clinicalDevelopmentResultPresentation,
  clinicalDevelopmentRunStatus,
  clinicalNodeResponsibility,
  clinicalRunPhase,
  clinicalTechnicalError,
  developmentAgentGroup,
} from '../src/lib/clinical-copy.ts'

test('only development v3 rows are labeled as clinical perspectives', () => {
  assert.equal(clinicalComparisonMode(true, true, 1), 'clinical_perspectives')
  assert.equal(clinicalComparisonMode(false, false, 1), 'single_model')
  assert.equal(clinicalComparisonMode(false, false, 2), 'multi_model')
})

test('maps run phases to clinician language and keeps the raw engineering value', () => {
  const phase = clinicalRunPhase('provider_http_request')
  assert.equal(phase.primary, '正在处理本次分析')
  assert.equal(phase.engineeringValue, 'provider_http_request')

  const specialists = clinicalRunPhase('多专科 Agent 推演中')
  assert.equal(specialists.primary, '顶尖临床专家并行会诊')
  assert.equal(specialists.engineeringValue, '多专科 Agent 推演中')

  const routing = clinicalRunPhase('complexity_router')
  assert.equal(routing.primary, '召集本次顶尖临床专家会诊')
})

test('maps all development v3 node keys to specific clinical responsibilities', () => {
  const expected = {
    snapshot: '读取本次病例资料',
    preflight: '检查资料和模型连接',
    applicability: '记录适用范围提示',
    input_quality: '检查资料质量',
    source_compiler: '整理可追溯病例证据',
    complexity_router: '决定本次需要哪些临床专家',
    'specialist:timeline_course': '还原病程与检查用药顺序',
    'specialist:host_susceptibility': '评估宿主易感因素',
    'specialist:syndrome_localization': '判断临床综合征与解剖定位',
    'specialist:exposure_one_health': '分析暴露与 One Health 线索',
    'specialist:lab_pathophysiology': '解读实验室病理生理模式',
    'specialist:organ_severity': '评估器官损伤与严重度',
    'specialist:imaging_dissemination': '解读影像与播散路径',
    'specialist:microbiology_treatment': '解读微生物证据与治疗干扰',
    'specialist:neuroinfection': '进行神经感染专项分析',
    'specialist:immunocompromised_opportunistic': '进行免疫抑制与机会感染分析',
    'specialist:travel_zoonotic': '进行旅行、人畜共患与环境感染分析',
    'specialist:healthcare_device_amr': '进行医疗相关、器械与耐药分析',
    'specialist:timeline_host': '梳理病程与宿主因素',
    'specialist:syndrome_site': '判断感染部位与综合征',
    'specialist:exposure_epidemiology': '分析暴露与流行病学线索',
    'specialist:laboratory_organ_injury': '分析实验室异常与器官损伤',
    'specialist:imaging_microbiology_treatment': '综合影像、微生物与已用治疗',
    evidence_board: '汇总证据与候选病原体',
    retrieval_planner: '规划多来源医学证据查询',
    literature_retrieval: '检索文献与类似病例',
    public_health_retrieval: '查找公共卫生、指南与疫情信号',
    evidence_verifier: '核对外部证据与候选的关联',
    retrieval: '检索并核对医学证据',
    synthesis: '汇总并排序具体病原体',
    contract_validator: '核对病原体名称和结果完整性',
    critic: '独立寻找反证并复核病原体结果',
    revision: '按反证意见有限修订一次',
    candidate_evidence_enrichment: '为候选病原体补充文献',
    result_compiler: '生成中英文医生可读结果',
    persistence: '保存结果与可追溯记录',
  }

  for (const role of [...CORE_CLINICAL_EXPERT_ROLE_IDS, ...DYNAMIC_CLINICAL_EXPERT_ROLE_IDS]) {
    expected[`specialist:${role}`] = clinicalAgentRole(role).primary
  }

  for (const [nodeKey, primary] of Object.entries(expected)) {
    const copy = clinicalNodeResponsibility(nodeKey)
    assert.equal(copy.primary, primary, nodeKey)
    assert.equal(copy.engineeringValue, nodeKey, nodeKey)
  }
})

test('covers every current and target architecture node with non-generic clinical copy', () => {
  const architectureNodeKeys = [
    'scope_contract', 'provider_registry', 'privacy_audit', 'source_input',
    'integrity_preflight', 'observation_guards', 'source_compiler', 'complexity_router',
    ...CORE_CLINICAL_EXPERT_ROLE_IDS, ...DYNAMIC_CLINICAL_EXPERT_ROLE_IDS,
    'evidence_board', 'retrieval_planner', 'literature_retrieval', 'public_health_retrieval',
    'evidence_verifier', 'synthesis',
    'contract_validator', 'critic', 'revision', 'candidate_evidence_enrichment', 'result_compiler',
    'persistence', 'offline_evaluation', 'target_scope', 'target_registry',
    'target_security', 'target_ledger', 'target_quality', 'target_compiler',
    'target_router', 'target_discriminative', 'target_world_model',
    'target_bayesian_prior', 'target_fusion', 'target_safety', 'target_decision',
    'target_result', 'target_report_agent', 'target_bilingual', 'target_human',
    'offline_governance', 'offline_experiments', 'offline_external_validation',
    'offline_agent_eval', 'offline_silent', 'offline_release', 'offline_monitor',
  ]

  for (const nodeKey of architectureNodeKeys) {
    const copy = clinicalNodeResponsibility(nodeKey)
    assert.notEqual(copy.primary, '执行本次分析的一个步骤', nodeKey)
    assert.equal(copy.engineeringValue, nodeKey, nodeKey)
  }
})

test('maps specialty and system Agent roles without putting Agent jargon in the primary name', () => {
  const specialty = clinicalAgentRole('exposure_one_health')
  assert.equal(specialty.primary, '分析暴露与 One Health 线索')
  assert.equal(specialty.engineeringValue, 'exposure_one_health')
  assert.doesNotMatch(specialty.primary, /Agent|provider|schema|DAG|OOD/i)

  const critic = clinicalAgentRole('independent_medical_critic')
  assert.equal(critic.primary, '独立寻找反证并复核病原体结果')
})

test('registers exactly five core and twenty dynamic clinical experts', () => {
  assert.equal(CORE_CLINICAL_EXPERT_ROLE_IDS.length, 5)
  assert.equal(DYNAMIC_CLINICAL_EXPERT_ROLE_IDS.length, 20)
  assert.equal(new Set([...CORE_CLINICAL_EXPERT_ROLE_IDS, ...DYNAMIC_CLINICAL_EXPERT_ROLE_IDS]).size, 25)
  for (const role of CORE_CLINICAL_EXPERT_ROLE_IDS) assert.equal(developmentAgentGroup(role), 'core_perspective', role)
  for (const role of DYNAMIC_CLINICAL_EXPERT_ROLE_IDS) assert.equal(developmentAgentGroup(role), 'dynamic_specialist', role)
})

test('separates the primary run status from the secondary development hint', () => {
  const warning = clinicalDevelopmentRunStatus('completed_with_warnings')
  assert.equal(warning.primary, '已完成')
  assert.match(warning.researchHint, /研发提示/)
  assert.equal(warning.engineeringValue, 'completed_with_warnings')
  assert.equal(warning.tone, 'warning')
  assert.equal(warning.terminal, true)

  const failure = clinicalDevelopmentRunStatus('technical_failure')
  assert.equal(failure.primary, '未能生成结果')
  assert.match(failure.researchHint, /检查模型连接/)
})

test('separates an Agent status from its secondary development hint', () => {
  const warning = clinicalDevelopmentAgentStatus('completed_with_warnings')
  assert.equal(warning.primary, '已返回')
  assert.match(warning.researchHint, /技术提示/)
  assert.equal(warning.engineeringValue, 'completed_with_warnings')

  const failed = clinicalDevelopmentAgentStatus('failed')
  assert.equal(failed.primary, '本环节未返回')
  assert.match(failed.researchHint, /其他环节可继续时会继续/)
})

test('turns common provider errors into concise actionable Chinese copy', () => {
  const auth = clinicalTechnicalError({
    code: 'provider_http_401',
    message: '401 Invalid API key TEST_TOKEN_SECRET_VALUE',
    retryable: false,
  })
  assert.equal(auth.title, '模型鉴权未通过')
  assert.match(auth.action, /API Key/)
  assert.equal(auth.engineeringCode, 'provider_authentication_failed')
  assert.equal(auth.retryable, false)

  const timeout = clinicalTechnicalError({ code: 'development_agent_role_timeout', retryable: true })
  assert.equal(timeout.title, '模型返回超时')
  assert.match(timeout.action, /重试/)

  const interrupted = clinicalTechnicalError({ code: 'run_interrupted', retryable: true })
  assert.equal(interrupted.title, '本次分析已停止')
  assert.match(interrupted.reason, /完整病原体结果/)
})

test('never echoes raw provider errors, including unknown failures', () => {
  const secret = 'TEST_TOKEN_RAW_SECRET_12345'
  const raw = `Unexpected provider crash; Authorization: Bearer ${secret}`
  const copy = clinicalTechnicalError({ code: 'vendor_unknown_987', message: raw })
  const rendered = JSON.stringify(copy)

  assert.equal(copy.engineeringCode, 'unclassified_technical_error')
  assert.doesNotMatch(rendered, /vendor_unknown_987/)
  assert.doesNotMatch(rendered, /Unexpected provider crash/)
  assert.doesNotMatch(rendered, new RegExp(secret))
  assert.match(copy.action, /运行 ID/)
})

test('a technical failure suppresses clinical result panels and uses a failure explanation', () => {
  const failure = clinicalDevelopmentResultPresentation('technical_failure')
  assert.equal(failure.technicalFailure, true)
  assert.equal(failure.showClinicalResult, false)
  assert.equal(failure.pageTitle, '本次分析未完成')
  assert.match(failure.modeNote, /没有可作为病原体结果解读的分数/)

  const completed = clinicalDevelopmentResultPresentation('completed_with_warnings')
  assert.equal(completed.technicalFailure, false)
  assert.equal(completed.showClinicalResult, true)
  assert.match(completed.modeNote, /不代表患病概率/)
})

test('separates core perspectives, dynamically recruited specialists and system roles', () => {
  assert.equal(developmentAgentGroup('timeline_course'), 'core_perspective')
  assert.equal(developmentAgentGroup('specialist:microbiology_treatment'), 'core_perspective')
  assert.equal(developmentAgentGroup('specialist:travel_zoonotic'), 'dynamic_specialist')
  assert.equal(developmentAgentGroup('healthcare_device_amr'), 'dynamic_specialist')
  assert.equal(developmentAgentGroup('timeline_host'), 'core_perspective')
  assert.equal(developmentAgentGroup('evidence_retrieval'), 'system_process')
  assert.equal(developmentAgentGroup('pathogen_synthesis'), 'system_process')
  assert.equal(developmentAgentGroup('independent_critic'), 'system_process')
})

test('translates a Top-5 contract failure without exposing the English engineering issue', () => {
  const raw = 'Development Agent pipeline did not produce a usable concrete Top-5'
  const copy = clinicalTechnicalError({ code: 'development_technical_failure', message: raw })
  assert.equal(copy.title, '未生成完整的具体病原体列表')
  assert.doesNotMatch(JSON.stringify(copy), /Development Agent|usable concrete Top-5/)
  assert.equal(copy.engineeringCode, 'development_result_contract_failed')
})
