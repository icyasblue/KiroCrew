import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'

import { api } from '../api/client'
import ProjectBundlesPage from '../pages/ProjectBundlesPage'
import { renderWithProviders } from './helpers'

vi.mock('../api/client', () => ({
  api: {
    projectBundles: vi.fn(),
    createProjectBundle: vi.fn(),
    addProjectBundle: vi.fn(),
    syncProjectBundle: vi.fn(),
    reviewProjectBundle: vi.fn(),
    removeProjectBundle: vi.fn(),
    createChatSlot: vi.fn(),
    chatSlotProject: vi.fn(),
    setSlotColor: vi.fn(),
    setSlotColorHex: vi.fn(),
    deleteChatSlot: vi.fn(),
  },
}))

const localProject = {
  id: '018f4f4a-760f-7a8b-a5d4-5a7e0f130d4e',
  name: 'Payments Platform',
  description: 'Payments services and operational context.',
  workspace_source: 'payments-api',
  sources: [{
    id: 'payments-api',
    type: 'repo',
    url: 'https://github.com/acme/payments-api',
    default_branch: 'main',
  }],
  mcp: [
    { name: 'atlassian', scope: { site: 'acme', project: 'PAY' } },
    { name: 'datadog' },
  ],
  memory: { mode: 'project' as const },
  registrations: [{ origin: 'local' as const, path: '/work/payments', syncable: false }],
  health: { status: 'healthy' as const, code: 'project_healthy' },
  sessions: [{
    key: 'payments-chat',
    title: 'Investigate refunds',
    messages: 4,
    running: false,
    live: true,
  }],
}

const managedProject = {
  ...localProject,
  id: '018f4f4a-760f-7a8b-a5d4-5a7e0f130d5f',
  name: 'Shared Payments',
  registrations: [{
    origin: 'managed_git' as const,
    path: '/data/projects/shared-payments',
    syncable: true,
  }],
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.projectBundles).mockResolvedValue({ projects: [localProject] })
  vi.mocked(api.createProjectBundle).mockResolvedValue(localProject)
  vi.mocked(api.addProjectBundle).mockResolvedValue(localProject)
  vi.mocked(api.syncProjectBundle).mockResolvedValue(managedProject)
  vi.mocked(api.reviewProjectBundle).mockResolvedValue(localProject)
  vi.mocked(api.removeProjectBundle).mockResolvedValue({ ok: true, id: localProject.id })
  vi.mocked(api.createChatSlot).mockResolvedValue({
    key: 'new-project-chat',
    title: 'New Session',
    messages: 0,
    running: false,
    project: '/work/payments',
    project_id: localProject.id,
  })
})

describe('Projects portal (thin Project)', () => {
  it('opens a Project from a single-column list into a focused detail view', async () => {
    renderWithProviders(<ProjectBundlesPage />)

    const project = await screen.findByRole('button', { name: /Open project Payments Platform/ })
    expect(screen.queryByRole('button', { name: 'New session' })).not.toBeInTheDocument()

    fireEvent.click(project)

    expect(await screen.findByRole('heading', { name: 'Payments Platform' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Back to projects' })).toBeInTheDocument()
    expect(screen.getByText('Payments services and operational context.')).toBeInTheDocument()
    expect(screen.getAllByText('payments-api')).toHaveLength(2)
    expect(screen.getByText('https://github.com/acme/payments-api')).toBeInTheDocument()
    expect(screen.getByText('/work/payments')).toBeInTheDocument()
    expect(screen.getByText('Healthy')).toBeInTheDocument()
    // The thin Project installs nothing: no activation / capabilities control.
    expect(screen.queryByRole('button', { name: /Trust and activate/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Deactivate/ })).not.toBeInTheDocument()
    expect(screen.getByText('Investigate refunds')).toBeInTheDocument()
  })

  it('lists declared MCP servers and project memory as not yet active', async () => {
    renderWithProviders(<ProjectBundlesPage />)
    fireEvent.click(await screen.findByRole('button', { name: /Open project Payments Platform/ }))

    expect(screen.getByText('Declared context')).toBeInTheDocument()
    expect(screen.getByText('Declared — not yet active')).toBeInTheDocument()
    expect(screen.getByText('atlassian')).toBeInTheDocument()
    expect(screen.getByText(/site: acme/)).toBeInTheDocument()
    expect(screen.getByText('datadog')).toBeInTheDocument()
    expect(screen.getByText(/This Project gets its own private memory/)).toBeInTheDocument()
  })

  it('renders only repository sources in detail when provider data contains objects', async () => {
    const projectWithExtensionSource = {
      ...localProject,
      sources: [
        ...localProject.sources,
        { id: 'pay-board', type: 'jira', url: { board: 'PAY' } },
      ],
    }
    vi.mocked(api.projectBundles).mockResolvedValue({ projects: [projectWithExtensionSource] })
    renderWithProviders(<ProjectBundlesPage />)

    fireEvent.click(await screen.findByRole('button', { name: /Open project Payments Platform/ }))

    expect(screen.getByText('https://github.com/acme/payments-api')).toBeInTheDocument()
    expect(screen.queryByText('pay-board')).not.toBeInTheDocument()
  })

  it('starts a session with the Project identity in the create request', async () => {
    renderWithProviders(<ProjectBundlesPage />)

    fireEvent.click(await screen.findByRole('button', { name: /Open project Payments Platform/ }))
    fireEvent.click(screen.getByRole('button', { name: 'New session' }))

    await waitFor(() => {
      expect(api.createChatSlot).toHaveBeenCalledWith(
        undefined,
        undefined,
        undefined,
        undefined,
        // createSlot resolves the configured default memory mode before the
        // request; this test pins the Project identity, not that default.
        expect.any(String),
        undefined,
        undefined,
        undefined,
        undefined,
        // adopt_remote_slot — unset here.
        undefined,
        localProject.id,
      )
    })
  })

  it('explains how to populate an empty registry', async () => {
    vi.mocked(api.projectBundles).mockResolvedValue({ projects: [] })

    renderWithProviders(<ProjectBundlesPage />)

    expect(await screen.findByText('No projects yet')).toBeInTheDocument()
    expect(screen.getByText('Create a local Project or add one from a folder or Git URL.')).toBeInTheDocument()
  })

  it('creates a local bundle and refreshes the portal list', async () => {
    vi.mocked(api.projectBundles)
      .mockResolvedValueOnce({ projects: [] })
      .mockResolvedValue({ projects: [localProject] })

    renderWithProviders(<ProjectBundlesPage />)
    await screen.findByText('No projects yet')
    fireEvent.click(screen.getByRole('button', { name: 'Create project' }))
    fireEvent.change(screen.getByLabelText('Project name'), {
      target: { value: 'Payments Platform' },
    })
    const projectFolder = screen.getByLabelText('Project folder')
    fireEvent.change(projectFolder, {
      target: { value: '/work/payments' },
    })
    fireEvent.click(within(projectFolder.closest('form')!).getByRole('button', { name: 'Create project' }))

    expect(await screen.findByRole('button', { name: /Open project Payments Platform/ })).toBeInTheDocument()
  })

  it('adds an existing folder or Git URL and refreshes the portal list', async () => {
    vi.mocked(api.projectBundles)
      .mockResolvedValueOnce({ projects: [] })
      .mockResolvedValue({ projects: [localProject] })

    renderWithProviders(<ProjectBundlesPage />)
    await screen.findByText('No projects yet')
    fireEvent.click(screen.getByRole('button', { name: 'Add project' }))
    const projectSource = screen.getByLabelText('Folder or Git URL')
    fireEvent.change(projectSource, {
      target: { value: '/work/payments' },
    })
    fireEvent.click(within(projectSource.closest('form')!).getByRole('button', { name: 'Add project' }))

    expect(await screen.findByRole('button', { name: /Open project Payments Platform/ })).toBeInTheDocument()
  })

  it('syncs managed Git projects and confirms completion', async () => {
    vi.mocked(api.projectBundles).mockResolvedValue({ projects: [managedProject] })

    renderWithProviders(<ProjectBundlesPage />)
    fireEvent.click(await screen.findByRole('button', { name: /Open project Shared Payments/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Sync project' }))

    expect(await screen.findByText('Project synced.')).toBeInTheDocument()
  })

  it('offers recovery for an unavailable Git Project and explains why sessions are blocked', async () => {
    const unavailable = {
      ...managedProject,
      health: { status: 'unavailable' as const, code: 'project_manifest_unavailable' },
    }
    vi.mocked(api.projectBundles).mockResolvedValue({ projects: [unavailable] })

    renderWithProviders(<ProjectBundlesPage />)
    fireEvent.click(await screen.findByRole('button', { name: /Open project Shared Payments/ }))

    expect(screen.getByRole('alert')).toHaveTextContent('Project files are unavailable')
    expect(screen.getByRole('button', { name: 'Retry sync' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'New session' })).toBeDisabled()
  })

  it('flags a review-stale Project, lists the changed executable files, and clears on review', async () => {
    const reviewStale = {
      ...managedProject,
      health: {
        status: 'review_stale' as const,
        code: 'project_review_stale',
        stale_files: ['.kiro/settings/mcp.json', '.kiro/agents/payments.md'],
      },
    }
    vi.mocked(api.projectBundles)
      .mockResolvedValueOnce({ projects: [reviewStale] })
      .mockResolvedValue({ projects: [{ ...managedProject, name: 'Shared Payments' }] })
    vi.mocked(api.reviewProjectBundle).mockResolvedValue({ ...managedProject, name: 'Shared Payments' })

    renderWithProviders(<ProjectBundlesPage />)
    fireEvent.click(await screen.findByRole('button', { name: /Open project Shared Payments/ }))

    // Warning badge, its own copy (not the "files are unavailable" text), and the
    // changed executable surfaces listed as relative paths.
    expect(screen.getByText('Review needed')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('files that can run code have changed')
    expect(screen.getByText('.kiro/settings/mcp.json')).toBeInTheDocument()
    expect(screen.getByText('.kiro/agents/payments.md')).toBeInTheDocument()
    // Sessions stay blocked until the owner accepts the changes.
    expect(screen.getByRole('button', { name: 'New session' })).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: 'Review and accept changes' }))

    await waitFor(() => expect(api.reviewProjectBundle).toHaveBeenCalledWith(reviewStale.id))
    // The refetch returns a healthy Project: badge flips and the notice clears.
    expect(await screen.findByText('Healthy')).toBeInTheDocument()
    expect(screen.queryByText('Review needed')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'New session' })).not.toBeDisabled()
  })

  it('flags a sources-unavailable Project, lists the missing source ids in monospace, and blocks new sessions', async () => {
    const sourcesUnavailable = {
      ...managedProject,
      health: {
        status: 'sources_unavailable' as const,
        code: 'project_sources_unavailable',
        unavailable_sources: ['payments-infra-1a2b3c4d'],
      },
    }
    vi.mocked(api.projectBundles).mockResolvedValue({ projects: [sourcesUnavailable] })

    renderWithProviders(<ProjectBundlesPage />)
    fireEvent.click(await screen.findByRole('button', { name: /Open project Shared Payments/ }))

    // Error badge with its own label, distinct from the review-stale copy.
    expect(screen.getByText('Source unavailable')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('could not be cloned')
    // The failing source's synthesized id, rendered in monospace.
    const id = screen.getByText('payments-infra-1a2b3c4d')
    expect(id).toBeInTheDocument()
    expect(id.className).toContain('font-mono')
    // Session start is blocked by the existing status !== 'healthy' guard.
    expect(screen.getByRole('button', { name: 'New session' })).toBeDisabled()
  })

  it('shows both the review-stale notice and the unavailable-source list when they co-occur', async () => {
    const combined = {
      ...managedProject,
      health: {
        status: 'review_stale' as const,
        code: 'project_review_stale',
        stale_files: ['.kiro/settings/mcp.json'],
        unavailable_sources: ['payments-infra-1a2b3c4d'],
      },
    }
    vi.mocked(api.projectBundles).mockResolvedValue({ projects: [combined] })

    renderWithProviders(<ProjectBundlesPage />)
    fireEvent.click(await screen.findByRole('button', { name: /Open project Shared Payments/ }))

    // A stale digest outranks a missing secondary source: the badge is the
    // review-stale warning, not the error badge.
    expect(screen.getByText('Review needed')).toBeInTheDocument()
    expect(screen.queryByText('Source unavailable')).not.toBeInTheDocument()
    // Both notices render side by side.
    const alerts = screen.getAllByRole('alert')
    expect(alerts.length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('.kiro/settings/mcp.json')).toBeInTheDocument()
    expect(screen.getByText(/could not be cloned/)).toBeInTheDocument()
    expect(screen.getByText('payments-infra-1a2b3c4d')).toBeInTheDocument()
    // Sessions stay blocked while the digest is unreviewed.
    expect(screen.getByRole('button', { name: 'New session' })).toBeDisabled()
  })

  it('explains which files removal preserves', async () => {
    vi.mocked(api.projectBundles)
      .mockResolvedValueOnce({ projects: [localProject] })
      .mockResolvedValue({ projects: [] })
    renderWithProviders(<ProjectBundlesPage />)
    fireEvent.click(await screen.findByRole('button', { name: /Open project Payments Platform/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Remove from Kiro Crew' }))

    expect(await screen.findByText(
      'Folders you added stay on disk. Kiro Crew removes only storage it created for this project.',
    )).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Remove project' }))

    await waitFor(() => expect(api.removeProjectBundle).toHaveBeenCalledWith(localProject.id))
    expect(await screen.findByText('No projects yet')).toBeInTheDocument()
  })
})
