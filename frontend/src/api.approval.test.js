import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiClient = vi.hoisted(() => ({
  get: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  interceptors: {
    response: { use: vi.fn() },
  },
}))

vi.mock('axios', () => ({
  default: {
    create: vi.fn(() => apiClient),
    post: vi.fn(),
  },
}))

import { approveProposedTask } from './api'

describe('approveProposedTask reviewed-version safety', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('transitions an untouched proposal using exactly the version the attorney reviewed', async () => {
    apiClient.post.mockResolvedValueOnce({ data: { status: 'in_progress' } })

    await approveProposedTask('task-1', { expectedVersion: 7 })

    expect(apiClient.get).not.toHaveBeenCalled()
    expect(apiClient.patch).not.toHaveBeenCalled()
    expect(apiClient.post).toHaveBeenCalledWith('/tasks/task-1/transition', {
      to_status: 'in_progress',
      expected_version: 7,
    })
  })

  it('guards an edited draft with the reviewed version and transitions its returned version', async () => {
    apiClient.patch.mockResolvedValueOnce({ data: { version: 8 } })
    apiClient.post.mockResolvedValueOnce({ data: { status: 'in_progress' } })

    await approveProposedTask('task-2', {
      body: 'Reviewed revised body',
      expectedVersion: 7,
    })

    expect(apiClient.get).not.toHaveBeenCalled()
    expect(apiClient.patch).toHaveBeenCalledWith('/tasks/task-2/pending-action', {
      body: 'Reviewed revised body',
      subject: undefined,
      expected_version: 7,
    })
    expect(apiClient.post).toHaveBeenCalledWith('/tasks/task-2/transition', {
      to_status: 'in_progress',
      expected_version: 8,
    })
  })

  it('sends delivery-risk acknowledgment only when the attorney explicitly supplied it', async () => {
    apiClient.post.mockResolvedValueOnce({ data: { status: 'in_progress' } })

    await approveProposedTask('task-risk', {
      expectedVersion: 9,
      acknowledge_prior_delivery_risk: true,
    })

    expect(apiClient.post).toHaveBeenCalledWith('/tasks/task-risk/transition', {
      to_status: 'in_progress',
      expected_version: 9,
      acknowledge_prior_delivery_risk: true,
    })
  })

  it('fails closed instead of fetching and approving a newer unseen version', async () => {
    await expect(approveProposedTask('task-3')).rejects.toThrow(
      'reviewed task version is required',
    )

    expect(apiClient.get).not.toHaveBeenCalled()
    expect(apiClient.patch).not.toHaveBeenCalled()
    expect(apiClient.post).not.toHaveBeenCalled()
  })

  it('propagates a stale-version conflict and never transitions the task', async () => {
    const conflict = {
      response: { status: 409, data: { detail: 'Task changed since review' } },
    }
    apiClient.patch.mockRejectedValueOnce(conflict)

    await expect(approveProposedTask('task-4', {
      body: 'Reviewed revised body',
      expectedVersion: 7,
    })).rejects.toMatchObject({
      message: 'Task changed since review',
      response: { status: 409, data: { detail: 'Task changed since review' } },
    })

    expect(apiClient.post).not.toHaveBeenCalled()
  })

  it('propagates a stale-version conflict from an untouched proposal', async () => {
    const conflict = {
      response: { status: 409, data: { detail: 'Task changed since review' } },
    }
    apiClient.post.mockRejectedValueOnce(conflict)

    await expect(approveProposedTask('task-5', {
      expectedVersion: 7,
    })).rejects.toMatchObject({
      message: 'Task changed since review',
      response: { status: 409, data: { detail: 'Task changed since review' } },
    })

    expect(apiClient.post).toHaveBeenCalledWith('/tasks/task-5/transition', {
      to_status: 'in_progress',
      expected_version: 7,
    })
  })

  it('makes the structured transition conflict safe for the proposal card to render', async () => {
    const currentTask = { id: 'task-structured', status: 'review', version: 8 }
    apiClient.post.mockRejectedValueOnce({
      response: {
        status: 409,
        data: {
          detail: {
            message: 'This task changed after it was loaded. Review the latest task and try again.',
            current_task: currentTask,
          },
        },
      },
    })

    let caught
    try {
      await approveProposedTask('task-structured', { expectedVersion: 7 })
    } catch (error) {
      caught = error
    }

    expect(caught).toBeInstanceOf(Error)
    expect(caught.message).toBe(
      'This task changed after it was loaded. Review the latest task and try again.',
    )
    expect(caught.response.data.detail).toBe(caught.message)
    expect(caught.response.data.current_task).toEqual(currentTask)
    expect(caught.current_task).toEqual(currentTask)
  })

  it('does not approve when an edited task response omits its new version', async () => {
    apiClient.patch.mockResolvedValueOnce({ data: { status: 'review' } })

    await expect(approveProposedTask('task-6', {
      body: 'Reviewed revised body',
      expectedVersion: 7,
    })).rejects.toThrow('did not return a valid version')

    expect(apiClient.post).not.toHaveBeenCalled()
  })
})
