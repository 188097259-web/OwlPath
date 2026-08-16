import assert from 'node:assert/strict'
import test from 'node:test'

import {
  EXPERT_CONSULT_REGISTRY,
  LATEST_WORKFLOW_NODES,
  LATEST_WORKFLOW_STAGES,
  WORKFLOW_MEMORY_LAYERS,
} from '../src/lib/latest-workflow.ts'

const FROZEN_CORE_ROLES = [
  'infectious_diseases', 'critical_care_emergency', 'clinical_epidemiology',
  'laboratory_medicine', 'clinical_microbiology_culture',
]

const FROZEN_DYNAMIC_ROLES = [
  'radiology', 'pulmonology', 'gastroenterology', 'hepatobiliary_pancreatic',
  'urology', 'nephrology', 'neurology_neuroinfection', 'cardiology_endocarditis',
  'hematology_immunology', 'transplant_infectious_diseases', 'surgery_source_control',
  'orthopedics_bone_joint', 'dermatology_soft_tissue', 'obstetrics_gynecology',
  'pediatrics_neonatology', 'tropical_medicine_parasitology', 'medical_mycology',
  'clinical_virology_molecular', 'antimicrobial_stewardship', 'healthcare_device_infection',
]

test('latest workflow has unique nodes and every stage reference is valid', () => {
  const nodeIds = LATEST_WORKFLOW_NODES.map((node) => node.id)
  const nodeIdSet = new Set(nodeIds)
  assert.equal(nodeIdSet.size, nodeIds.length)

  const stagedIds = LATEST_WORKFLOW_STAGES.flatMap((stage) => stage.nodeIds)
  assert.equal(new Set(stagedIds).size, stagedIds.length)
  assert.deepEqual(new Set(stagedIds), nodeIdSet)

  for (const node of LATEST_WORKFLOW_NODES) {
    assert.ok(node.titleZh && node.titleEn && node.description)
    assert.ok(node.inputs.length > 0)
    assert.ok(node.outputs.length > 0)
    assert.ok(node.why && node.referenceLesson && node.owlPathUpgrade && node.validation)
    for (const dependency of node.dependsOn) assert.ok(nodeIdSet.has(dependency), `${node.id} depends on unknown node ${dependency}`)
  }
})

test('candidate verification happens before final pathogen synthesis', () => {
  const order = new Map(LATEST_WORKFLOW_NODES.map((node, index) => [node.id, index]))
  const finalSynthesis = LATEST_WORKFLOW_NODES.find((node) => node.id === 'final_pathogen_synthesis')
  assert.ok(finalSynthesis)
  for (const nodeId of ['candidate_support_retrieval', 'candidate_counterevidence_retrieval', 'semantic_entailment_verifier', 'pathogen_hypothesis_updater']) {
    assert.ok(order.get(nodeId) < order.get('final_pathogen_synthesis'), `${nodeId} must precede final synthesis`)
  }
  assert.ok(finalSynthesis.dependsOn.includes('pathogen_hypothesis_updater'))
})

test('the blueprint explicitly bounds revision and separates controlled memory layers', () => {
  const revision = LATEST_WORKFLOW_NODES.find((node) => node.id === 'bounded_revision')
  assert.ok(revision)
  assert.match(`${revision.description} ${revision.outputs.join(' ')} ${revision.validation}`, /最多一次|一次/)
  assert.deepEqual(WORKFLOW_MEMORY_LAYERS.map((layer) => layer.title), ['患者证据账本', '病原体假设账本', '公共知识缓存', '受治理病例库'])
})

test('expert consultation registry matches the frozen v3 routing contract', () => {
  const core = EXPERT_CONSULT_REGISTRY.filter((expert) => expert.group === 'core')
  const dynamic = EXPERT_CONSULT_REGISTRY.filter((expert) => expert.group === 'dynamic')
  assert.deepEqual(new Set(core.map((expert) => expert.roleId)), new Set(FROZEN_CORE_ROLES))
  assert.deepEqual(new Set(dynamic.map((expert) => expert.roleId)), new Set(FROZEN_DYNAMIC_ROLES))
  assert.equal(new Set(EXPERT_CONSULT_REGISTRY.map((expert) => expert.roleId)).size, 25)
  for (const expert of EXPERT_CONSULT_REGISTRY) {
    assert.ok(expert.responsibility)
    assert.ok(expert.inputs.length > 0)
    assert.ok(expert.outputs.length > 0)
    assert.ok(expert.triggers.length > 0)
    assert.equal(expert.maturity, 'implemented')
  }
})
