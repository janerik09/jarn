import { useEffect, useState } from 'react'
import { formatDateTime } from '../lib/displayTime'

interface Guest {
  vmid: number
  node: string
  type: 'qemu' | 'lxc'
  name: string
  status: string
  target: string
  target_name: string
  os: 'windows' | 'linux' | 'other'
  backup_storages: string[]
}

interface FileTarget {
  id: string
  name: string
}

interface Mapping {
  target?: string
  type: 'qemu' | 'lxc' | 'file'
  node?: string
  vmid?: number
}

interface Row {
  server_id: string
  hostname: string
  mapping: Mapping | null
  last_backup: { volid: string; created_at: string } | null
}

const base = () => `/api/v1`
const guestKey = (g: { target?: string; node: string; type: string; vmid: number }) =>
  `${g.target ?? ''}|${g.node}|${g.type}|${g.vmid}`
const fileKey = (targetId: string) => `file|${targetId}`
const shortName = (hostname: string) => hostname.split('.')[0].toLowerCase()

export function BackupMappings() {
  const [rows, setRows] = useState<Row[]>([])
  const [guests, setGuests] = useState<Guest[]>([])
  const [fileTargets, setFileTargets] = useState<FileTarget[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [savingId, setSavingId] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const [mapRes, guestRes] = await Promise.all([
        fetch(`${base()}/backup/mappings`),
        fetch(`${base()}/backup/guests`, { method: 'POST' }),
      ])
      const mapData = await mapRes.json()
      setRows(mapData.mappings || [])
      setFileTargets(mapData.file_targets || [])
      if (guestRes.ok) {
        const guestData = await guestRes.json()
        setGuests(guestData.guests || [])
        if (guestData.errors?.length) {
          setError(guestData.errors.map((e: any) => `${e.name}: ${e.message}`).join(' · '))
        }
      } else {
        const data = await guestRes.json().catch(() => ({}))
        setGuests([])
        // File targets still work without Proxmox, so only report the guest list as unavailable
        if (!mapData.file_targets?.length || data.detail) {
          setError(data.detail || 'Could not list Proxmox guests. Check the connection settings and save them first.')
        }
      }
    } catch (err) {
      setError('Could not reach the backend')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  // body is the mapping to save, or null to clear it
  const save = async (serverId: string, body: object | null) => {
    setSavingId(serverId)
    setError(null)
    try {
      const res = await fetch(`${base()}/backup/mappings/${serverId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        setError(data.detail || 'Could not save mapping')
        return
      }
      const data = await res.json()
      setRows(prev => prev.map(r => (r.server_id === serverId ? { ...r, mapping: data.mapping } : r)))
    } catch (err) {
      setError('Could not save mapping')
    } finally {
      setSavingId(null)
    }
  }

  const guestFor = (mapping: Mapping | null) =>
    mapping && mapping.type !== 'file'
      ? guests.find(g => g.node === mapping.node && g.type === mapping.type && g.vmid === mapping.vmid
          && (!mapping.target || g.target === mapping.target))
      : undefined

  const currentValue = (mapping: Mapping | null) => {
    if (mapping?.type === 'file' && mapping.target) return fileKey(mapping.target)
    const guest = guestFor(mapping)
    return guest ? guestKey(guest) : ''
  }

  const choose = (serverId: string, value: string) => {
    if (value === '') return save(serverId, null)
    if (value.startsWith('file|')) return save(serverId, { target: value.slice(5), type: 'file' })
    const guest = guests.find(g => guestKey(g) === value)
    if (guest) save(serverId, { target: guest.target, node: guest.node, vmid: guest.vmid, type: guest.type })
  }

  if (loading) return <p className="settings-help">Loading mappings…</p>

  return (
    <div className="backup-mappings">
      {error && <p className="settings-help" style={{ color: '#c8514c' }}>{error}</p>}
      <table className="compliance-table">
        <thead>
          <tr><th>Server</th><th>Backup source</th><th>Last backup</th></tr>
        </thead>
        <tbody>
          {rows.map(row => {
            const suggestion = !row.mapping
              ? guests.find(g => g.name.toLowerCase() === shortName(row.hostname))
              : undefined
            const value = currentValue(row.mapping)
            return (
              <tr key={row.server_id}>
                <td>{row.hostname}</td>
                <td>
                  <select
                    value={value}
                    disabled={savingId === row.server_id || (guests.length === 0 && fileTargets.length === 0)}
                    onChange={(e) => choose(row.server_id, e.target.value)}
                  >
                    <option value="">Not mapped</option>
                    {fileTargets.length > 0 && (
                      <optgroup label="Files (SSH to the server, stored on this host)">
                        {fileTargets.map(t => (
                          <option key={fileKey(t.id)} value={fileKey(t.id)}>
                            Files → {t.name}
                          </option>
                        ))}
                      </optgroup>
                    )}
                    {guests.length > 0 && (
                      <optgroup label="Proxmox VM or container">
                        {guests.filter(g => g.os !== 'windows' || guestKey(g) === value).map(g => (
                          <option key={guestKey(g)} value={guestKey(g)}>
                            {g.target_name}: {g.name || `vm ${g.vmid}`} ({g.type} {g.vmid} on {g.node}){' '}
                            {g.backup_storages.length ? `· backup: ${g.backup_storages.join(', ')}` : '· no backup job'}
                          </option>
                        ))}
                      </optgroup>
                    )}
                  </select>
                  {suggestion && (
                    <button onClick={() => save(row.server_id, { target: suggestion.target, node: suggestion.node, vmid: suggestion.vmid, type: suggestion.type })} style={{ marginLeft: 8 }}>
                      Use {suggestion.name}
                    </button>
                  )}
                </td>
                <td>{row.last_backup ? formatDateTime(row.last_backup.created_at) : '—'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <button onClick={load} style={{ marginTop: 8 }}>Reload backup sources</button>
    </div>
  )
}
