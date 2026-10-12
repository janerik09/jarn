import { useState, useEffect } from 'react'

import { Pagination } from './Pagination'
import { useDefaultPageSize } from '../lib/defaultPageSize'
import { formatDateTime } from '../lib/displayTime'
interface Patch {
  name: string
  deployed_on: string
  job_id: string
  job_name: string
  success_count: number
  total_servers: number
  status: string
}

export function PatchHistory() {
  const [patches, setPatches] = useState<Patch[]>([])
  const [loading, setLoading] = useState(true)
  const [currentPage, setCurrentPage] = useState(1)
  const defaultPageSize = useDefaultPageSize()
  const [pageSizeOverride, setPageSize] = useState<number | null>(null)
  const pageSize = pageSizeOverride ?? defaultPageSize

  useEffect(() => {
    fetchPatches()
  }, [])

  const fetchPatches = async () => {
    try {
      const response = await fetch(`/api/v1/patches`)
      const data = await response.json()
      setPatches(data.patches || [])
    } catch (err) {
      console.error('Failed to fetch patches:', err)
    } finally {
      setLoading(false)
    }
  }

  if (loading) {
    return <div style={{ color: '#8b95a5' }}>Loading patch history...</div>
  }

  if (patches.length === 0) {
    return <div style={{ color: '#8b95a5' }}>No patches deployed yet</div>
  }

  const paginatedPatches = patches.slice(
    (currentPage - 1) * pageSize,
    currentPage * pageSize
  )

  return (
    <div style={{ marginTop: '20px' }}>
      <table style={{
        width: '100%',
        borderCollapse: 'collapse',
        color: 'var(--text-primary)'
      }}>
        <thead>
          <tr style={{ borderBottom: '2px solid rgba(58, 157, 110, 0.3)' }}>
            <th style={{ textAlign: 'left', padding: '12px', color: 'var(--text-secondary)' }}>Patch</th>
            <th style={{ textAlign: 'left', padding: '12px', color: 'var(--text-secondary)' }}>Job</th>
            <th style={{ textAlign: 'left', padding: '12px', color: 'var(--text-secondary)' }}>Deployed</th>
            <th style={{ textAlign: 'left', padding: '12px', color: 'var(--text-secondary)' }}>Success</th>
            <th style={{ textAlign: 'left', padding: '12px', color: 'var(--text-secondary)' }}>Status</th>
          </tr>
        </thead>
        <tbody>
          {paginatedPatches.map((patch, idx) => (
            <tr key={idx} style={{ borderBottom: '1px solid rgba(58, 157, 110, 0.1)' }}>
              <td style={{ padding: '12px' }}>{patch.name}</td>
              <td style={{ padding: '12px', color: '#8b95a5', fontSize: '0.9em' }}>{patch.job_name}</td>
              <td style={{ padding: '12px', color: '#8b95a5', fontSize: '0.9em' }}>
                {formatDateTime(patch.deployed_on)}
              </td>
              <td style={{ padding: '12px' }}>
                {patch.success_count}/{patch.total_servers}
              </td>
              <td style={{ padding: '12px' }}>
                <span style={{
                  background: patch.status === 'deployed' ? 'rgba(58, 157, 110, 0.2)' : 'rgba(200, 81, 76, 0.2)',
                  color: patch.status === 'deployed' ? '#3a9d6e' : '#c8514c',
                  padding: '4px 8px',
                  borderRadius: '3px',
                  fontSize: '0.85em'
                }}>
                  {patch.status}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <Pagination
        currentPage={currentPage}
        totalItems={patches.length}
        pageSize={pageSize}
        onPageChange={setCurrentPage}
        onPageSizeChange={setPageSize}
      />
    </div>
  )
}
