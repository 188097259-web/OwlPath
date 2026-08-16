export type PageId = 'case' | 'models' | 'architecture' | 'run' | 'result' | 'compare' | 'history' | 'evaluation' | 'governance'
export type ResultTab = 'summary' | 'agents' | 'evidence' | 'trace'
export type ArchitectureView = 'current' | 'workflow' | 'target'

export interface AppRoute {
  page: PageId
  runId?: string
  resultTab?: ResultTab
  architectureView?: ArchitectureView
}

const APP_PAGE_IDS: ReadonlySet<PageId> = new Set([
  'case',
  'models',
  'architecture',
  'run',
  'result',
  'compare',
  'history',
  'evaluation',
  'governance',
])

const RESULT_DETAIL_TABS: ReadonlySet<ResultTab> = new Set(['agents', 'evidence', 'trace'])
const ARCHITECTURE_VIEWS: ReadonlySet<ArchitectureView> = new Set(['current', 'workflow', 'target'])

export function parseAppRoute(hash: string): AppRoute {
  const raw = hash.replace(/^#\/?/, '')
  const queryStart = raw.indexOf('?')
  const path = queryStart >= 0 ? raw.slice(0, queryStart) : raw
  const query = queryStart >= 0 ? raw.slice(queryStart + 1) : ''
  const segments = path.split('/').filter(Boolean)
  if (segments[0] === 'runs' && segments[1] && ['progress', 'result', 'compare'].includes(segments[2] || '')) {
    const runId = segments[1]
    if (!/^run_[A-Za-z0-9_-]+$/.test(runId)) return { page: 'case' }
    const destination = segments[2]
    if (destination === 'progress') return { page: 'run', runId }
    if (destination === 'compare') return { page: 'compare', runId }
    const requestedTab = new URLSearchParams(query).get('tab') as ResultTab | null
    return {
      page: 'result',
      runId,
      resultTab: requestedTab && RESULT_DETAIL_TABS.has(requestedTab) ? requestedTab : 'summary',
    }
  }
  const legacyPage = segments[0] as PageId
  if (!APP_PAGE_IDS.has(legacyPage)) return { page: 'case' }
  if (legacyPage === 'architecture') {
    const requestedView = new URLSearchParams(query).get('view') as ArchitectureView | null
    return {
      page: 'architecture',
      architectureView: requestedView && ARCHITECTURE_VIEWS.has(requestedView) ? requestedView : 'current',
    }
  }
  return { page: legacyPage }
}

export function appRouteHash(route: AppRoute): string {
  if (route.runId && route.page === 'run') return `#/runs/${route.runId}/progress`
  if (route.runId && route.page === 'result') return `#/runs/${route.runId}/result${route.resultTab && route.resultTab !== 'summary' ? `?tab=${route.resultTab}` : ''}`
  if (route.runId && route.page === 'compare') return `#/runs/${route.runId}/compare`
  if (route.page === 'architecture') return `#/architecture${route.architectureView && route.architectureView !== 'current' ? `?view=${route.architectureView}` : ''}`
  return `#/${route.page}`
}

/**
 * Keep a URL-selected v3 tab while the requested run is still loading.
 * Only a confirmed legacy result may collapse unsupported v3 tabs to summary.
 */
export function resultTabForLoadedContract(
  requestedTab: ResultTab,
  resultLoaded: boolean,
  hasDevelopmentV3: boolean,
): ResultTab {
  if (!resultLoaded || hasDevelopmentV3) return requestedTab
  return requestedTab === 'trace' ? 'trace' : 'summary'
}
