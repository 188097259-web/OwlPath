import assert from 'node:assert/strict'
import test from 'node:test'

import {
  appRouteHash,
  parseAppRoute,
  resultTabForLoadedContract,
} from '../src/lib/route.ts'

const runId = 'run_cbb93765020c418e940533af10340328'

for (const tab of ['agents', 'evidence', 'trace']) {
  test(`preserves the ${tab} result tab in a hash URL round trip`, () => {
    const hash = `#/runs/${runId}/result?tab=${tab}`
    const route = parseAppRoute(hash)
    assert.deepEqual(route, { page: 'result', runId, resultTab: tab })
    assert.equal(appRouteHash(route), hash)
  })
}

test('keeps a URL-selected v3 tab while the run result is loading', () => {
  assert.equal(resultTabForLoadedContract('agents', false, false), 'agents')
  assert.equal(resultTabForLoadedContract('evidence', false, false), 'evidence')
  assert.equal(resultTabForLoadedContract('trace', false, false), 'trace')
})

test('keeps all result tabs after a v3 result is identified', () => {
  assert.equal(resultTabForLoadedContract('agents', true, true), 'agents')
  assert.equal(resultTabForLoadedContract('evidence', true, true), 'evidence')
  assert.equal(resultTabForLoadedContract('trace', true, true), 'trace')
})

test('retains legacy v1/v2 compatibility after the result contract is known', () => {
  assert.equal(resultTabForLoadedContract('agents', true, false), 'summary')
  assert.equal(resultTabForLoadedContract('evidence', true, false), 'summary')
  assert.equal(resultTabForLoadedContract('trace', true, false), 'trace')
})

test('falls back safely for an invalid run id or result tab', () => {
  assert.deepEqual(parseAppRoute('#/runs/not-a-run/result?tab=trace'), { page: 'case' })
  assert.deepEqual(parseAppRoute(`#/runs/${runId}/result?tab=unknown`), {
    page: 'result',
    runId,
    resultTab: 'summary',
  })
})

for (const view of ['workflow', 'target']) {
  test(`preserves the ${view} architecture view in a hash URL round trip`, () => {
    const hash = `#/architecture?view=${view}`
    const route = parseAppRoute(hash)
    assert.deepEqual(route, { page: 'architecture', architectureView: view })
    assert.equal(appRouteHash(route), hash)
  })
}

test('uses the current runtime as the safe architecture default', () => {
  assert.deepEqual(parseAppRoute('#/architecture'), { page: 'architecture', architectureView: 'current' })
  assert.deepEqual(parseAppRoute('#/architecture?view=unknown'), { page: 'architecture', architectureView: 'current' })
  assert.equal(appRouteHash({ page: 'architecture', architectureView: 'current' }), '#/architecture')
})
