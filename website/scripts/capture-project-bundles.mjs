/**
 * Screenshots for the Projects (thin Project bundles) page.
 *
 * Drives the isolated capture entry (website/capture/project-bundles.html),
 * which mounts the REAL ProjectBundlesPage. Every REAL /api call is answered
 * by page.route on the pathname; the Project payloads MATCH the backend
 * (src/kiro_crew/dashboard/handlers_project.py `_project_payload`) field for
 * field. Every frame ASSERTS its state before writing, so a frame cannot
 * document the wrong state:
 *   fe-01-empty       no Projects -> the empty-state card
 *   fe-02-list        two healthy Projects (repo-backed + source-less)
 *   fe-03-detail      payments-platform detail: repo, Declared context, sessions
 *   fe-04-review      review_stale: Review-needed badge, stale file, disabled start
 *   fe-05-unavailable sources_unavailable: badge + the missing source id listed
 *   fe-06-list-light  the two-Project list in light theme
 *
 * Usage:
 *   npx vite --host 127.0.0.1 --port 6832 --strictPort   # in another shell
 *   node scripts/capture-project-bundles.mjs http://127.0.0.1:6832 <evidence dir>
 */
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const BASE = process.argv[2] || 'http://127.0.0.1:6832'
const OUT = process.argv[3] || '../temp-screenshots/project-bundles'
mkdirSync(OUT, { recursive: true })

// ── Fixtures: shaped exactly like `_project_payload` output ──────────────────
// A repo-backed, healthy Project. `workspace_source` names the primary source
// id (the checkout that supplies the working dir), matching the backend.
const PAYMENTS_ID = '5f2c9a71-8e0d-4b3a-9c14-7d6e2f0a1b33'
const PRIMARY_SOURCE_ID = 'payments-api-1a2b3c4d'
const INFRA_SOURCE_ID = 'payments-infra-3f9a1c2b'

const paymentsSources = [
  { id: PRIMARY_SOURCE_ID, type: 'repo', url: 'https://github.com/acme/payments-api', default_branch: 'main', role: 'primary' },
]
const paymentsMcp = [{ name: 'atlassian', scope: {} }]
const paymentsRegistrations = [
  { origin: 'managed_git', path: '~/.kiro/crew/projects/managed/' + PAYMENTS_ID + '/bundle', syncable: true },
]
const paymentsSessions = [
  { key: 'sess-onboard-4821', title: 'Add idempotency keys to charge intents', messages: 34, running: false, live: false },
  { key: 'sess-refund-1190', title: 'Refund webhook replay audit', messages: 12, running: true, live: true },
]

function payments(health) {
  return {
    id: PAYMENTS_ID,
    name: 'payments-platform',
    description: 'Charge, refund and webhook services for the payments platform.',
    workspace_source: PRIMARY_SOURCE_ID,
    sources: paymentsSources,
    mcp: paymentsMcp,
    memory: { mode: 'project' },
    registrations: paymentsRegistrations,
    health,
    sessions: paymentsSessions,
  }
}

const docsSite = {
  id: 'a1d47e90-3c22-49f5-8b6e-0f9c1a2b4d55',
  name: 'docs-site',
  description: 'Public documentation site.',
  workspace_source: 'self',
  sources: [],
  mcp: [],
  memory: { mode: 'none' },
  registrations: [{ origin: 'local', path: '~/projects/docs-site', syncable: false }],
  health: { status: 'healthy', code: 'project_healthy' },
  sessions: [],
}

const HEALTHY = { status: 'healthy', code: 'project_healthy' }
const REVIEW_STALE = { status: 'review_stale', code: 'project_review_stale', stale_files: ['.kiro/settings/mcp.json'] }
const SOURCES_UNAVAILABLE = { status: 'sources_unavailable', code: 'project_sources_unavailable', unavailable_sources: [INFRA_SOURCE_ID] }

const browser = await chromium.launch()
let failed = false

function check(name, ok, detail) {
  console.log(`${name}: ${ok ? 'OK' : 'MISMATCH'} ${detail}`)
  if (!ok) failed = true
  return ok
}

/** Open the page with the given Project list and initial route/theme.
 *  Gateway-free: answer every REAL /api call. Predicate on the pathname so a
 *  glob does not swallow vite-served source modules. Array-shaped endpoints
 *  answer [] ({} crashes their .map consumers). */
async function open(projects, { theme = 'dark', route = '/project-bundles' } = {}) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 })
  await page.route(u => new URL(u).pathname.startsWith('/api/'), route2 => {
    const path = new URL(route2.request().url()).pathname
    if (path === '/api/project-bundles') {
      return route2.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ projects }) })
    }
    const isList = /commands|skills|agents|sessions|files|history|models|artifacts|folders|slots$/.test(path)
    return route2.fulfill({ status: 200, contentType: 'application/json', body: isList ? '[]' : '{}' })
  })
  const url = `${BASE}/capture/project-bundles.html?theme=${theme}&route=${encodeURIComponent(route)}`
  await page.goto(url)
  await page.waitForSelector('[data-capture-root]')
  return page
}

// fe-01 — empty list
{
  const page = await open([])
  await page.getByTestId('project-bundles-empty').waitFor()
  const rows = await page.locator('[data-project-id]').count()
  check('fe-01 empty no rows', rows === 0, `rows=${rows}`)
  const empty = await page.getByTestId('project-bundles-empty').isVisible()
  check('fe-01 empty state visible', empty, 'empty-state card shown')
  await page.screenshot({ path: `${OUT}/fe-01-empty.png` })
  await page.close()
}

// fe-02 — two healthy Projects
{
  const page = await open([payments(HEALTHY), docsSite])
  await page.locator('[data-project-id]').first().waitFor()
  const rows = await page.locator('[data-project-id]').count()
  check('fe-02 two rows', rows === 2, `rows=${rows}`)
  const healthyBadges = await page.getByText('Healthy', { exact: true }).count()
  check('fe-02 both healthy', healthyBadges === 2, `healthy-badges=${healthyBadges}`)
  const names = (await page.locator('[data-project-id]').allTextContents()).join(' | ')
  check('fe-02 names present', /payments-platform/.test(names) && /docs-site/.test(names), 'both project names rendered')
  await page.screenshot({ path: `${OUT}/fe-02-list.png` })
  await page.close()
}

// fe-03 — payments-platform detail (healthy)
{
  const page = await open([payments(HEALTHY)], { route: `/project-bundles?project=${PAYMENTS_ID}` })
  await page.getByRole('heading', { name: 'payments-platform' }).waitFor()
  const headerHealthy = await page.getByText('Healthy', { exact: true }).count()
  check('fe-03 header healthy badge', headerHealthy >= 1, `healthy=${headerHealthy}`)
  const declared = await page.getByText('Declared context', { exact: true }).isVisible()
  check('fe-03 declared context card', declared, 'Declared context present')
  const repoUrl = await page.getByText('https://github.com/acme/payments-api').isVisible()
  check('fe-03 repository url', repoUrl, 'primary repo url shown')
  const s1 = await page.getByText('Add idempotency keys to charge intents').isVisible()
  const s2 = await page.getByText('Refund webhook replay audit').isVisible()
  check('fe-03 two sessions listed', s1 && s2, 'both session titles rendered')
  const startDisabled = await page.getByRole('button', { name: 'New session' }).isDisabled()
  check('fe-03 new-session enabled (healthy)', startDisabled === false, `disabled=${startDisabled}`)
  await page.screenshot({ path: `${OUT}/fe-03-detail.png` })
  await page.close()
}

// fe-04 — review_stale
{
  const page = await open([payments(REVIEW_STALE)], { route: `/project-bundles?project=${PAYMENTS_ID}` })
  await page.getByRole('heading', { name: 'payments-platform' }).waitFor()
  const badge = await page.getByText('Review needed', { exact: true }).isVisible()
  check('fe-04 review-needed badge', badge, 'Review needed badge shown')
  const staleFile = await page.getByText('.kiro/settings/mcp.json').isVisible()
  check('fe-04 stale file listed', staleFile, 'stale file path rendered')
  const reviewBtn = await page.getByRole('button', { name: 'Review and accept changes' }).isVisible()
  check('fe-04 review button', reviewBtn, 'Review and accept changes button visible')
  const startDisabled = await page.getByRole('button', { name: 'New session' }).isDisabled()
  check('fe-04 new-session disabled', startDisabled === true, `disabled=${startDisabled}`)
  await page.screenshot({ path: `${OUT}/fe-04-review.png` })
  await page.close()
}

// fe-05 — sources_unavailable
{
  const page = await open([payments(SOURCES_UNAVAILABLE)], { route: `/project-bundles?project=${PAYMENTS_ID}` })
  await page.getByRole('heading', { name: 'payments-platform' }).waitFor()
  const badge = await page.getByText('Source unavailable', { exact: true }).isVisible()
  check('fe-05 source-unavailable badge', badge, 'Source unavailable badge shown')
  const idListed = await page.getByText(INFRA_SOURCE_ID).isVisible()
  check('fe-05 missing source id listed', idListed, `notice lists ${INFRA_SOURCE_ID}`)
  const startDisabled = await page.getByRole('button', { name: 'New session' }).isDisabled()
  check('fe-05 new-session disabled', startDisabled === true, `disabled=${startDisabled}`)
  await page.screenshot({ path: `${OUT}/fe-05-unavailable.png` })
  await page.close()
}

// fe-06 — the two-Project list in light theme
{
  const page = await open([payments(HEALTHY), docsSite], { theme: 'light' })
  await page.locator('[data-project-id]').first().waitFor()
  const rows = await page.locator('[data-project-id]').count()
  check('fe-06 two rows (light)', rows === 2, `rows=${rows}`)
  const themeAttr = await page.evaluate(() => document.documentElement.getAttribute('data-theme'))
  check('fe-06 light theme', themeAttr === 'kiro-light', `data-theme=${themeAttr}`)
  await page.screenshot({ path: `${OUT}/fe-06-list-light.png` })
  await page.close()
}

await browser.close()
if (failed) {
  console.error('CAPTURE FAILED: at least one frame did not match its asserted state')
  process.exit(1)
}
console.log('all frames verified')
