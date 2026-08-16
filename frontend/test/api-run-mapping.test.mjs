import assert from 'node:assert/strict'
import test from 'node:test'

import { ApiError, apiErrorMessage, clampTraceNodeForRunStatus, deriveRunDetail, mapRunTrace } from '../src/lib/api.ts'

function wireRun(overrides = {}) {
  return {
    id: 'run_failure_mapping_test',
    case_id: 'case_failure_mapping_test',
    decision_time: '2026-08-11T08:00:00Z',
    requested_at: '2026-08-11T08:00:01Z',
    run_mode: 'development_demo',
    status: 'failed',
    provider_ids: ['provider_deepseek'],
    include_baseline: false,
    governance_version: 'governance-test',
    schema_version: 'owlpath.result.v3',
    result: null,
    completed_at: '2026-08-11T08:01:00Z',
    ...overrides,
  }
}

function wireModel(overrides = {}) {
  return {
    id: 'output_failure_mapping_test',
    provider_id: 'provider_deepseek',
    provider_name: 'DeepSeek',
    provider_kind: 'openai_compatible',
    model: 'deepseek-chat',
    status: 'running',
    normalized: null,
    created_at: '2026-08-11T08:00:02Z',
    ...overrides,
  }
}

test('preserves the structured server_restarted code without exposing the raw backend message', () => {
  const secret = 'TEST_TOKEN_RAW_SECRET_MUST_NOT_REACH_BROWSER'
  const detail = deriveRunDetail(wireRun({
    error: {
      code: 'server_restarted',
      message: `Run interrupted; Authorization: Bearer ${secret}`,
    },
  }), [])

  assert.deepEqual(detail.error, {
    code: 'server_restarted',
    message: '分析服务在运行中重启，本次运行已中断。',
    retryable: true,
  })
  assert.doesNotMatch(JSON.stringify(detail), new RegExp(secret))
  assert.doesNotMatch(JSON.stringify(detail), /Authorization: Bearer/)
})

test('maps trace and execution graph versions as distinct run fields', () => {
  const detail = deriveRunDetail(wireRun({
    trace_version: 'owlpath.trace.v2',
    execution_graph_version: 'owlpath.execution-graph.v4',
  }), [])

  assert.equal(detail.traceVersion, 'owlpath.trace.v2')
  assert.equal(detail.executionGraphVersion, 'owlpath.execution-graph.v4')
})

for (const schemaVersion of ['owlpath.result.v1', 'owlpath.result.v2', 'owlpath.result.v3']) {
  test(`${schemaVersion} terminal failure cannot leave a model or stage running`, () => {
    const secret = `TEST_TOKEN_STALE_${schemaVersion.slice(-2)}`
    const detail = deriveRunDetail(
      wireRun({ schema_version: schemaVersion, error: { code: 'server_restarted', message: secret } }),
      [wireModel({ error: { code: 'vendor_internal_error', message: `provider dump ${secret}` } })],
    )

    assert.equal(detail.status, 'failed')
    assert.equal(detail.models[0].status, 'failed')
    assert.equal(detail.stages.some((stage) => stage.status === 'running'), false)
    assert.equal(detail.models.some((model) => model.status === 'running'), false)
    assert.ok(detail.progress < 100, `failed progress must be partial, received ${detail.progress}`)
    assert.doesNotMatch(JSON.stringify(detail), new RegExp(secret))
    assert.doesNotMatch(JSON.stringify(detail), /provider dump/)
  })
}

test('a cancelled run with no model rows marks expected work as skipped instead of running', () => {
  const detail = deriveRunDetail(wireRun({
    status: 'cancelled',
    schema_version: 'owlpath.result.v2',
    error: { code: 'run_cancelled', message: 'raw cancellation detail' },
  }), [])

  assert.equal(detail.currentStage, '已取消')
  assert.equal(detail.models.length, 1)
  assert.equal(detail.models[0].status, 'skipped')
  assert.equal(detail.models.some((model) => model.status === 'running'), false)
  assert.equal(detail.stages.some((stage) => stage.status === 'running'), false)
  assert.ok(detail.progress < 100)
  assert.doesNotMatch(JSON.stringify(detail), /raw cancellation detail/)
})

test('explicit retryability survives the safe structured mapping', () => {
  const detail = deriveRunDetail(wireRun({
    error: { code: 'provider_http_401', message: 'invalid key with vendor details', retryable: false },
  }), [])

  assert.equal(detail.error?.code, 'provider_http_401')
  assert.equal(detail.error?.retryable, false)
  assert.equal(detail.error?.message, '模型密钥或访问权限不可用。')
  assert.doesNotMatch(JSON.stringify(detail), /vendor details/)
})

test('token-shaped content is rejected even if a backend puts it in the error code field', () => {
  const secretCode = 'TEST_TOKEN_SECRET_VALUE_123456'
  const detail = deriveRunDetail(wireRun({
    error: { code: secretCode, message: 'raw error' },
  }), [])

  assert.equal(detail.error?.code, 'unclassified_technical_error')
  assert.doesNotMatch(JSON.stringify(detail), new RegExp(secretCode))
})

test('a failed run trace cannot leave stale nodes running or expose their raw error text', () => {
  const secret = 'TEST_TOKEN_TRACE_NODE_SECRET'
  const trace = mapRunTrace({
    run_id: 'run_failed_trace',
    trace_version: 'owlpath.trace.v2',
    execution_graph_version: 'owlpath.execution-graph.v2',
    nodes: [
      { id: 'node_completed', node_key: 'source_compiler', status: 'completed', sequence: 1 },
      {
        id: 'node_stale_running',
        node_key: 'specialist:timeline_host',
        status: 'running',
        sequence: 2,
        error: { code: 'vendor_internal_error', message: `Authorization: Bearer ${secret}` },
      },
      { id: 'node_never_started', node_key: 'synthesis', status: 'pending', sequence: 3 },
    ],
    edges: [],
  }, 'failed')

  assert.deepEqual(trace.nodes.map((node) => node.status), ['completed', 'failed', 'skipped'])
  assert.equal(trace.nodes.some((node) => node.status === 'running'), false)
  assert.doesNotMatch(JSON.stringify(trace), new RegExp(secret))
  assert.doesNotMatch(JSON.stringify(trace), /Authorization: Bearer/)
})

test('a cancelled trace maps both running and pending nodes to skipped', () => {
  const trace = mapRunTrace({
    run_id: 'run_cancelled_trace',
    nodes: [
      { id: 'node_running', node_key: 'critic', status: 'running' },
      { id: 'node_pending', node_key: 'persistence', status: 'pending' },
    ],
    edges: [],
  }, 'cancelled')

  assert.deepEqual(trace.nodes.map((node) => node.status), ['skipped', 'skipped'])
})

test('a single node detail cannot revive stale work after a failed parent run', () => {
  const node = clampTraceNodeForRunStatus({
    id: 'node_stale_detail',
    nodeKey: 'specialist:timeline_host',
    name: { zhCn: '梳理病程与宿主因素' },
    kind: 'specialist_agent',
    plane: 'online',
    dependsOn: [],
    status: 'running',
  }, 'failed')

  assert.equal(node.status, 'failed')
})

test('generic HTTP errors never echo an upstream message or bearer token', () => {
  const secret = 'TEST_TOKEN_HTTP_SECRET_MUST_NOT_RENDER'
  const message = apiErrorMessage(new ApiError(
    `Provider dump Authorization: Bearer ${secret}`,
    502,
    `raw upstream ${secret}`,
    'provider_http_502',
  ))

  assert.equal(message, '分析服务本次未正常完成请求，请稍后重试。')
  assert.doesNotMatch(message, new RegExp(secret))
  assert.doesNotMatch(message, /Authorization|Bearer|Provider dump/)
})

test('a v3 technical failure cannot leak compatibility scores or candidates', () => {
  const detail = deriveRunDetail(wireRun({
    schema_version: 'owlpath.result.v3',
    result: {
      schema_version: 'owlpath.result.v3',
      status: 'technical_failure',
      concrete_pathogens: [{ rank: 1, canonical_latin_name: 'Example pathogen', model_score: 0.99 }],
      unknown_score: 1,
      next_tests: [{ name: 'Example test', model_score: 1 }],
      review: { status: 'failed' },
    },
  }), [])

  assert.equal(detail.result?.developmentResult?.status, 'technical_failure')
  assert.deepEqual(detail.result?.candidates, [])
  assert.equal(detail.result?.unknownProbability, undefined)
  assert.deepEqual(detail.result?.nextTests, [])
  assert.equal(detail.result?.safety.title, '开发结果未生成')
  assert.notEqual(detail.result?.safety.disposition, 'species_supported')
})

test('maps a revision completed without re-review as a distinct review status', () => {
  const detail = deriveRunDetail(wireRun({
    status: 'completed',
    schema_version: 'owlpath.result.v3',
    result: {
      schema_version: 'owlpath.result.v3',
      status: 'completed_with_warnings',
      concrete_pathogens: [],
      unknown_score: 0.2,
      review: {
        accepted: true,
        status: 'revision_completed_not_re_reviewed',
        revision_count: 1,
      },
    },
  }), [])

  assert.equal(
    detail.result?.developmentResult?.review.status,
    'revision_completed_not_re_reviewed',
  )
  assert.equal(detail.result?.developmentResult?.review.passed, true)
})
