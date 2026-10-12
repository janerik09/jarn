import { useState, useEffect } from 'react'
import './RiskDashboard.css'
import { formatDate } from '../lib/displayTime'
import { Pagination } from './Pagination'
import { useDefaultPageSize } from '../lib/defaultPageSize'

interface Patch {
  name: string
  safety_level: string
  risk_factors: string[]
  deployed_on: string
  success_count: number
  total_servers: number
  status: string
}

interface ComplianceMetric {
  framework: string
  score: number | null
  passed: number
  failed: number
  status: string
  lastAudit: string | null
}

// Same thresholds as the Compliance Reporting page
const complianceStatus = (score: number | null): string => {
  if (score === null) return 'not_assessed'
  if (score >= 90) return 'compliant'
  if (score >= 70) return 'partial'
  return 'non-compliant'
}

interface BlockedPatch {
  name: string
  reason: string
  riskFactors: string[]
  affectedServers: number
}

export function RiskDashboard({ onNavigate }: { onNavigate?: (view: string) => void }) {
  const [patches, setPatches] = useState<Patch[]>([])
  const [blockedPatches, setBlockedPatches] = useState<BlockedPatch[]>([])
  const [complianceMetrics, setComplianceMetrics] = useState<ComplianceMetric[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedSafety, setSelectedSafety] = useState<string | null>(null)
  const [patchPage, setPatchPage] = useState(1)
  const [patchPageSizeOverride, setPatchPageSize] = useState<number | null>(null)
  const [approvalPage, setApprovalPage] = useState(1)
  const [approvalPageSizeOverride, setApprovalPageSize] = useState<number | null>(null)
  const defaultPageSize = useDefaultPageSize()
  const patchPageSize = patchPageSizeOverride ?? defaultPageSize
  const approvalPageSize = approvalPageSizeOverride ?? defaultPageSize

  useEffect(() => {
    fetchData()
  }, [])

  const loadComplianceMetrics = async (): Promise<ComplianceMetric[]> => {
    const listRes = await fetch('/api/v1/compliance/assessments?limit=100')
    const list = await listRes.json()
    const latest = (list.assessments || []).find((a: any) => a.status === 'completed' && a.framework === 'all')
    if (!latest) return []
    const detailRes = await fetch(`/api/v1/compliance/assessments/${latest.id}`)
    const detail = await detailRes.json()
    const summary: Record<string, any> = detail.results?.summary || {}
    return Object.values(summary).map((fw: any) => ({
      framework: fw.name,
      score: fw.score,
      passed: fw.passed,
      failed: fw.failed,
      status: complianceStatus(fw.score),
      lastAudit: latest.completed_at,
    }))
  }

  const fetchData = async () => {
    try {
      // Fetch patches
      const patchesRes = await fetch(`/api/v1/patches`)
      const patchesData = await patchesRes.json()
      const allPatches = patchesData.patches || []

      setPatches(allPatches)

      // Extract blocked patches
      const blocked = allPatches
        .filter((p: any) => p.safety_level === 'blocked')
        .map((p: any) => ({
          name: p.name,
          reason: 'Dangerous patch combination or critical vulnerability',
          riskFactors: p.risk_factors || [],
          affectedServers: p.total_servers || 0
        }))
      setBlockedPatches(blocked)

      // Compliance scores come from the latest completed assessment across all frameworks
      try {
        setComplianceMetrics(await loadComplianceMetrics())
      } catch (err) {
        console.error('Failed to load compliance results:', err)
        setComplianceMetrics([])
      }
    } catch (err) {
      console.error('Failed to fetch risk data:', err)
    } finally {
      setLoading(false)
    }
  }

  if (loading) {
    return <div className="risk-loading">Loading risk assessment...</div>
  }

  const criticalPatches = patches.filter(p => p.safety_level === 'danger' || p.safety_level === 'blocked')
  const cautionPatches = patches.filter(p => p.safety_level === 'caution')
  const safePatches = patches.filter(p => p.safety_level === 'safe')

  const filteredPatches = patches.filter(p => selectedSafety === null || p.safety_level === selectedSafety)
  const patchPageItems = filteredPatches.slice((patchPage - 1) * patchPageSize, patchPage * patchPageSize)
  const approvalItems = blockedPatches.slice((approvalPage - 1) * approvalPageSize, approvalPage * approvalPageSize)

  const getSafetyColor = (level: string): string => {
    switch (level) {
      case 'safe': return '#3a9d6e'
      case 'caution': return '#d4a132'
      case 'danger': return '#c8514c'
      case 'blocked': return '#c8514c'
      default: return '#8b95a5'
    }
  }

  const getSafetyLabel = (level: string): string => {
    return level.charAt(0).toUpperCase() + level.slice(1)
  }

  const getComplianceColor = (status: string): string => {
    switch (status) {
      case 'compliant': return '#3a9d6e'
      case 'partial': return '#d4a132'
      case 'non-compliant': return '#c8514c'
      default: return '#8b95a5'
    }
  }

  return (
    <div className="risk-dashboard">
      <div className="page-header">
        <h1>Risk & Compliance Assessment</h1>
        <p>Vulnerability analysis and compliance framework tracking</p>
      </div>

      {/* Vulnerability Overview */}
      <div className="vulnerability-section">
        <h2>Vulnerability Overview</h2>

        <div className="risk-summary-grid">
          <div className="risk-summary-card critical">
            <div className="summary-icon">🚨</div>
            <div className="summary-info">
              <div className="summary-label">Critical Vulnerabilities</div>
              <div className="summary-value">{criticalPatches.length}</div>
              <div className="summary-desc">Require immediate action</div>
            </div>
          </div>

          <div className="risk-summary-card warning">
            <div className="summary-icon">⚠️</div>
            <div className="summary-info">
              <div className="summary-label">Caution Patches</div>
              <div className="summary-value">{cautionPatches.length}</div>
              <div className="summary-desc">Review before deployment</div>
            </div>
          </div>

          <div className="risk-summary-card safe">
            <div className="summary-icon">✓</div>
            <div className="summary-info">
              <div className="summary-label">Safe to Deploy</div>
              <div className="summary-value">{safePatches.length}</div>
              <div className="summary-desc">No known risks</div>
            </div>
          </div>

          <div className="risk-summary-card blocked">
            <div className="summary-icon">🔒</div>
            <div className="summary-info">
              <div className="summary-label">Blocked Patches</div>
              <div className="summary-value">{blockedPatches.length}</div>
              <div className="summary-desc">Cannot be deployed</div>
            </div>
          </div>
        </div>

        {/* Patch Details by Safety Level */}
        <div className="patch-details-section">
          <h3>Patches by Risk Level</h3>

          <div className="patch-filter">
            <button
              className={`filter-btn ${selectedSafety === null ? 'active' : ''}`}
              onClick={() => { setSelectedSafety(null); setPatchPage(1) }}
            >
              All ({patches.length})
            </button>
            <button
              className={`filter-btn danger ${selectedSafety === 'danger' ? 'active' : ''}`}
              onClick={() => { setSelectedSafety('danger'); setPatchPage(1) }}
            >
              Danger ({criticalPatches.filter(p => p.safety_level === 'danger').length})
            </button>
            <button
              className={`filter-btn blocked ${selectedSafety === 'blocked' ? 'active' : ''}`}
              onClick={() => { setSelectedSafety('blocked'); setPatchPage(1) }}
            >
              Blocked ({blockedPatches.length})
            </button>
            <button
              className={`filter-btn warning ${selectedSafety === 'caution' ? 'active' : ''}`}
              onClick={() => { setSelectedSafety('caution'); setPatchPage(1) }}
            >
              Caution ({cautionPatches.length})
            </button>
            <button
              className={`filter-btn safe ${selectedSafety === 'safe' ? 'active' : ''}`}
              onClick={() => { setSelectedSafety('safe'); setPatchPage(1) }}
            >
              Safe ({safePatches.length})
            </button>
          </div>

          <div className="risk-table-wrap">
            <table className="risk-table">
              <thead>
                <tr>
                  <th>Patch</th>
                  <th>Risk</th>
                  <th>Risk factors</th>
                  <th>Servers</th>
                  <th>Last update</th>
                </tr>
              </thead>
              <tbody>
                {patchPageItems.map((patch, idx) => (
                  <tr key={`${patch.name}-${idx}`}>
                    <td className="risk-name">
                      {patch.name}
                      {patch.safety_level === 'danger' && (
                        <span className="risk-note">High risk: plan and test first</span>
                      )}
                      {patch.safety_level === 'blocked' && (
                        <span className="risk-note blocked">Cannot be deployed under current safety rules</span>
                      )}
                    </td>
                    <td>
                      <span
                        className="risk-pill"
                        style={{ color: getSafetyColor(patch.safety_level), borderColor: getSafetyColor(patch.safety_level) }}
                      >
                        {getSafetyLabel(patch.safety_level)}
                      </span>
                    </td>
                    <td className="risk-factors">
                      {patch.risk_factors && patch.risk_factors.length > 0 ? patch.risk_factors.join(', ') : 'None'}
                    </td>
                    <td className="risk-num">{patch.success_count}/{patch.total_servers}</td>
                    <td className="risk-num">{formatDate(patch.deployed_on)}</td>
                  </tr>
                ))}
                {patchPageItems.length === 0 && (
                  <tr>
                    <td colSpan={5} className="risk-empty">No patches at this risk level</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <Pagination
            currentPage={patchPage}
            totalItems={filteredPatches.length}
            pageSize={patchPageSize}
            onPageChange={setPatchPage}
            onPageSizeChange={(size) => { setPatchPageSize(size); setPatchPage(1) }}
          />
        </div>
      </div>

      {/* Compliance Framework Status */}
      <div className="compliance-section">
        <h2>Compliance Framework Status</h2>

        {complianceMetrics.length === 0 ? (
          <p className="upgrade-empty">
            No completed compliance assessment yet. Run one from Compliance Reporting to see scores here.
          </p>
        ) : (
        <div className="compliance-grid">
          {complianceMetrics.map((metric, idx) => (
            <div key={idx} className={`compliance-card status-${metric.status}`}>
              <div className="compliance-header">
                <div className="framework-name">{metric.framework}</div>
                <div
                  className="compliance-status"
                  style={{ color: getComplianceColor(metric.status) }}
                >
                  {metric.status === 'compliant' ? '✓ Compliant' :
                   metric.status === 'partial' ? '⚠ Partial' :
                   metric.status === 'not_assessed' ? 'Not assessed' : '✗ Non-Compliant'}
                </div>
              </div>

              <div className="compliance-score">
                <div className="score-value">{metric.score === null ? 'n/a' : `${metric.score}%`}</div>
                <div className="score-bar-container">
                  <div
                    className="score-bar"
                    style={{
                      width: `${metric.score ?? 0}%`,
                      backgroundColor: getComplianceColor(metric.status)
                    }}
                  />
                </div>
              </div>

              <div className="compliance-footer">
                <span className="audit-date">
                  {metric.passed} passed · {metric.failed} failed
                </span>
                <span className="audit-date">Last audit: {formatDate(metric.lastAudit)}</span>
              </div>
            </div>
          ))}
        </div>
        )}

        {/* Compliance Recommendations */}
        <div className="recommendations-section">
          <h3>Compliance Recommendations</h3>

          <div className="recommendation-cards">
            <div className="recommendation-card priority-high">
              <div className="rec-priority">🔴 High Priority</div>
              <div className="rec-title">Address Critical Patches</div>
              <div className="rec-desc">
                {criticalPatches.length} critical patches pending. Schedule deployment in next maintenance window.
              </div>
              <button className="rec-action" onClick={() => onNavigate?.('create-job')}>Schedule Deployment</button>
            </div>

            <div className="recommendation-card priority-medium">
              <div className="rec-priority">🟡 Medium Priority</div>
              <div className="rec-title">Review Caution Patches</div>
              <div className="rec-desc">
                {cautionPatches.length} patches require review before deployment. Test in staging first.
              </div>
              <button
                className="rec-action"
                onClick={() => {
                  setSelectedSafety('caution')
                  setPatchPage(1)
                  document.querySelector('.patch-details-section')?.scrollIntoView({ behavior: 'smooth' })
                }}
              >
                Review Patches
              </button>
            </div>

            <div className="recommendation-card priority-low">
              <div className="rec-priority">🟢 Low Priority</div>
              <div className="rec-title">CIS Benchmark Audit</div>
              <div className="rec-desc">
                Last CIS Benchmark audit was 7 days ago. Run a new audit to verify compliance status.
              </div>
              <button className="rec-action" onClick={() => onNavigate?.('compliance')}>Run Audit</button>
            </div>
          </div>
        </div>
      </div>

      {/* Approval Queue */}
      {blockedPatches.length > 0 && (
        <div className="approval-section">
          <h2>Patch Approval Queue</h2>
          <p>The following patches require special approval to deploy:</p>

          <div className="risk-table-wrap">
            <table className="risk-table">
              <thead>
                <tr>
                  <th>Patch</th>
                  <th>Reason</th>
                  <th>Risk factors</th>
                  <th>Servers</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {approvalItems.map((patch, idx) => (
                  <tr key={`${patch.name}-${idx}`}>
                    <td className="risk-name">{patch.name}</td>
                    <td className="risk-factors">{patch.reason}</td>
                    <td className="risk-factors">{patch.riskFactors.join(', ') || 'Multiple'}</td>
                    <td className="risk-num">{patch.affectedServers}</td>
                    <td className="risk-actions">
                      <button className="btn-approve">Request approval</button>
                      <button className="btn-defer">Defer</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination
            currentPage={approvalPage}
            totalItems={blockedPatches.length}
            pageSize={approvalPageSize}
            onPageChange={setApprovalPage}
            onPageSizeChange={(size) => { setApprovalPageSize(size); setApprovalPage(1) }}
          />
        </div>
      )}
    </div>
  )
}
