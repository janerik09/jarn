import { useState, useEffect } from 'react'
import { Pagination } from './Pagination'
import './JobList.css'
import { useDefaultPageSize } from '../lib/defaultPageSize'
import { formatDateTime } from '../lib/displayTime'

interface Job {
  id: string
  name: string
  status: string
  server_count: number
  patch_count: number
  priority: string
  dry_run: boolean
  created_at: string
  start_time?: string
  end_time?: string
}

interface JobListProps {
  onJobSelected?: (jobId: string) => void
  refreshTrigger?: number
}

export function JobList({ onJobSelected, refreshTrigger }: JobListProps) {
  const [allJobs, setAllJobs] = useState<Job[]>([])
  const [loading, setLoading] = useState(true)
  const [executing, setExecuting] = useState<Set<string>>(new Set())
  const [error, setError] = useState<string | null>(null)
  const [currentPage, setCurrentPage] = useState(1)
  const defaultPageSize = useDefaultPageSize()
  const [pageSizeOverride, setPageSize] = useState<number | null>(null)
  const pageSize = pageSizeOverride ?? defaultPageSize

  useEffect(() => {
    fetchJobs()
  }, [refreshTrigger])

  const fetchJobs = async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await fetch(`/api/v1/jobs`)
      const data = await response.json()
      setAllJobs(data.jobs || [])
      setCurrentPage(1)
    } catch (err) {
      setError('Failed to fetch jobs')
      console.error(err)
    } finally {
      setLoading(false)
    }
  }

  const paginatedJobs = allJobs.slice(
    (currentPage - 1) * pageSize,
    currentPage * pageSize
  )

  const handleExecuteJob = async (jobId: string) => {
    const updated = new Set(executing)
    updated.add(jobId)
    setExecuting(updated)

    try {
      const response = await fetch(`/api/v1/jobs/${jobId}/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      })

      if (!response.ok) {
        const data = await response.json()
        throw new Error(data.detail || 'Failed to execute job')
      }

      await fetchJobs()

      if (onJobSelected) {
        onJobSelected(jobId)
      }
    } catch (err) {
      alert(`Error: ${err instanceof Error ? err.message : 'Unknown error'}`)
      console.error('Job execution error:', err)
    } finally {
      updated.delete(jobId)
      setExecuting(updated)
    }
  }

  const handleRetryFailed = async (jobId: string) => {
    const updated = new Set(executing)
    updated.add(jobId)
    setExecuting(updated)

    try {
      const response = await fetch(`/api/v1/jobs/${jobId}/retry-failed`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      })

      if (!response.ok) {
        const data = await response.json()
        throw new Error(data.detail || 'Failed to retry job')
      }

      const retryJob = await response.json()
      alert(`Retry job created: ${retryJob.name}`)
      await fetchJobs()

      if (onJobSelected) {
        onJobSelected(retryJob.id)
      }
    } catch (err) {
      alert(`Error: ${err instanceof Error ? err.message : 'Unknown error'}`)
      console.error('Job retry error:', err)
    } finally {
      updated.delete(jobId)
      setExecuting(updated)
    }
  }

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'pending':
        return '#d4a132'
      case 'running':
        return '#4a8fd4'
      case 'completed':
        return '#3a9d6e'
      case 'failed':
        return '#c8514c'
      case 'rolled_back':
        return '#d4a132'
      default:
        return '#8b95a5'
    }
  }

  const getPriorityBadge = (priority: string) => {
    const colors: Record<string, string> = {
      low: '#8b95a5',
      medium: '#3a9d6e',
      high: '#d4a132',
      critical: '#c8514c',
    }
    return colors[priority] || '#8b95a5'
  }

  if (loading) {
    return (
      <div style={{ padding: '20px', color: '#8b95a5', textAlign: 'center' }}>
        Loading jobs...
      </div>
    )
  }

  if (error) {
    return (
      <div style={{
        padding: '20px',
        background: 'rgba(200, 81, 76, 0.2)',
        border: '2px solid #c8514c',
        color: '#c8514c',
        borderRadius: '4px'
      }}>
        {error}
      </div>
    )
  }

  if (allJobs.length === 0) {
    return (
      <div style={{
        padding: '40px 20px',
        color: '#8b95a5',
        textAlign: 'center'
      }}>
        No jobs created yet. Create one in the Jobs tab!
      </div>
    )
  }

  return (
    <div style={{ padding: '20px' }}>
      <div style={{
        marginBottom: '20px',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center'
      }}>
        <h2 style={{ margin: 0, color: '#ffffff' }}>
          Patch Jobs ({allJobs.length})
        </h2>
        <button
          onClick={fetchJobs}
          style={{
            padding: '8px 16px',
            background: 'rgba(58, 157, 110, 0.2)',
            border: '2px solid #4a8fd4',
            color: '#4a8fd4',
            cursor: 'pointer',
            borderRadius: '3px',
            fontWeight: 'bold'
          }}
        >
          Refresh
        </button>
      </div>

      {paginatedJobs.length === 0 ? (
        <div style={{ padding: '20px', color: '#8b95a5', textAlign: 'center' }}>
          No jobs on this page
        </div>
      ) : (
        paginatedJobs.map((job) => (
          <div
            key={job.id}
            onClick={() => onJobSelected?.(job.id)}
            style={{
              marginBottom: '15px',
              padding: '15px',
              background: 'rgba(74, 143, 212, 0.1)',
              border: '2px solid rgba(58, 157, 110, 0.3)',
              borderRadius: '4px',
              cursor: 'pointer',
              transition: 'all 0.2s ease'
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.borderColor = '#4a8fd4'
              e.currentTarget.style.background = 'rgba(58, 157, 110, 0.1)'
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.borderColor = 'rgba(58, 157, 110, 0.3)'
              e.currentTarget.style.background = 'rgba(74, 143, 212, 0.1)'
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '10px' }}>
              <div>
                <h3 style={{ margin: 0, color: '#ffffff', fontSize: '16px' }}>
                  {job.name}
                </h3>
                <p style={{ margin: '5px 0 0 0', color: '#8b95a5', fontSize: '12px' }}>
                  {formatDateTime(job.created_at)}
                </p>
              </div>
              <div style={{ display: 'flex', gap: '10px', alignItems: 'flex-start' }}>
                <span
                  style={{
                    padding: '4px 12px',
                    background: getStatusColor(job.status),
                    color: job.status === 'completed' ? '#000' : '#e6ebf1',
                    borderRadius: '12px',
                    fontSize: '12px',
                    fontWeight: 'bold',
                    textTransform: 'capitalize'
                  }}
                >
                  {job.status}
                </span>
                <span
                  style={{
                    padding: '4px 12px',
                    background: getPriorityBadge(job.priority),
                    color: '#000',
                    borderRadius: '12px',
                    fontSize: '12px',
                    fontWeight: 'bold',
                    textTransform: 'capitalize'
                  }}
                >
                  {job.priority}
                </span>
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '15px', marginBottom: '10px', fontSize: '14px' }}>
              <div>
                <span style={{ color: '#8b95a5' }}>Servers:</span>
                <span style={{ color: '#e6eaf0', marginLeft: '5px', fontWeight: 'bold' }}>
                  {job.server_count}
                </span>
              </div>
              <div>
                <span style={{ color: '#8b95a5' }}>Patches:</span>
                <span style={{ color: '#e6eaf0', marginLeft: '5px', fontWeight: 'bold' }}>
                  {job.patch_count}
                </span>
              </div>
              <div>
                <span style={{ color: '#8b95a5' }}>Dry run:</span>
                <span style={{ color: job.dry_run ? '#d4a132' : '#3a9d6e', marginLeft: '5px', fontWeight: 'bold' }}>
                  {job.dry_run ? 'Yes' : 'No'}
                </span>
              </div>
            </div>

            <div style={{ display: 'flex', gap: '10px' }}>
              {job.status !== 'running' && job.status !== 'completed' && (
                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    handleExecuteJob(job.id)
                  }}
                  disabled={executing.has(job.id)}
                  style={{
                    padding: '6px 12px',
                    background: '#2f6aa8',
                    color: '#ffffff',
                    border: 'none',
                    borderRadius: '2px',
                    cursor: executing.has(job.id) ? 'wait' : 'pointer',
                    fontWeight: 'bold',
                    fontSize: '12px'
                  }}
                >
                  {executing.has(job.id) ? 'Executing...' : 'Execute'}
                </button>
              )}
              {job.status === 'failed' && (
                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    handleRetryFailed(job.id)
                  }}
                  disabled={executing.has(job.id)}
                  style={{
                    padding: '6px 12px',
                    background: '#d4a132',
                    color: '#000',
                    border: 'none',
                    borderRadius: '2px',
                    cursor: executing.has(job.id) ? 'wait' : 'pointer',
                    fontWeight: 'bold',
                    fontSize: '12px'
                  }}
                >
                  {executing.has(job.id) ? 'Retrying...' : 'Retry Failed'}
                </button>
              )}
            </div>
          </div>
        ))
      )}

      <Pagination
        currentPage={currentPage}
        totalItems={allJobs.length}
        pageSize={pageSize}
        onPageChange={setCurrentPage}
        onPageSizeChange={setPageSize}
      />
    </div>
  )
}
