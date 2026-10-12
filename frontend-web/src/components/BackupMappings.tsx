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

interface Mapping {
  target?: string
  node: string
  vmid: number
  type: 'qemu' | 'lxc'
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
const shortName = (hostname: string) => hostname.split('.')[0].toLowerCase()

export function BackupMappings() {
  const [rows, setRows] = useState<Row[]>([])
  const [guests, setGuests] = useState<Guest[]>([])
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
      if (guestRes.ok) {
        const guestData = await guestRes.json()
        setGuests(guestData.guests || [])
        if (guestData.errors?.length) {
          setError(guestData.errors.map((e: any) => `${e.name}: ${e.message}`).join(' · '))
        }
      } else {
        const data = await guestRes.json().catch(() => ({}))
        setGuests([])
        setError(data.detail || 'Could not list Proxmox guests. Check the connection settings and save them first.')
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

  const save = async (serverId: string, guest: Guest | null) => {
    setSavingId(serverId)
    setError(null)
    try {
      const res = await fetch(`${base()}/backup/mappings/${serverId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(
          guest ? { target: guest.target, node: guest.node, vmid: guest.vmid, type: guest.type } : null
        ),
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
    mapping
      ? guests.find(g => g.node === mapping.node && g.type === mapping.type && g.vmid === mapping.vmid
          && (!mapping.target || g.target === mapping.target))
      : undefined

  if (loading) return <p className="settings-help">Loading mappings…</p>

  return (
    <div className="backup-mappings">
      {error && <p className="settings-help" style={{ color: '#c8514c' }}>{error}</p>}
      <table className="compliance-table">
        <thead>
          <tr><th>Server</th><th>Proxmox VM / container</th><th>Last backup</th></tr>
        </thead>
        <tbody>
          {rows.map(row => {
            const current = guestFor(row.mapping)
            const suggestion = !row.mapping
              ? guests.find(g => g.name.toLowerCase() === shortName(row.hostname))
              : undefined
            return (
              <tr key={row.server_id}>
                <td>{row.hostname}</td>
                <td>
                  <select
                    value={current ? guestKey(current) : ''}
                    disabled={savingId === row.server_id || guests.length === 0}
                    onChange={(e) => {
                      const guest = guests.find(g => guestKey(g) === e.target.value) || null
                      save(row.server_id, guest)
                    }}
                  >
                    <option value="">Not mapped</option>
                    {guests.filter(g => g.os !== 'windows' || guestKey(g) === (current ? guestKey(current) : '')).map(g => (
                      <option key={guestKey(g)} value={guestKey(g)}>
                        {g.target_name}: {g.name || `vm ${g.vmid}`} ({g.type} {g.vmid} on {g.node}){' '}
                        {g.backup_storages.length ? `· backup: ${g.backup_storages.join(', ')}` : '· no backup job'}
                      </option>
                    ))}
                  </select>
                  {suggestion && (
                    <button onClick={() => save(row.server_id, suggestion)} style={{ marginLeft: 8 }}>
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
      <button onClick={load} style={{ marginTop: 8 }}>Reload VM list</button>
    </div>
  )
}
