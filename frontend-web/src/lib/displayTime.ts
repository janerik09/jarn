import { useEffect, useState } from 'react'

const DEFAULT_TIMEZONE = 'UTC'

let timezone = DEFAULT_TIMEZONE
const listeners = new Set<() => void>()

export function getDisplayTimezone(): string {
  return timezone
}

export function setDisplayTimezone(zone: string | null | undefined) {
  const next = zone || DEFAULT_TIMEZONE
  if (next === timezone) return
  timezone = next
  listeners.forEach(listener => listener())
}

export function loadDisplayTimezone(): Promise<void> {
  return fetch(`/api/v1/admin/settings`)
    .then(res => (res.ok ? res.json() : null))
    .then(data => setDisplayTimezone(data?.general?.timezone))
    .catch(() => setDisplayTimezone(DEFAULT_TIMEZONE))
}

export function subscribeDisplayTimezone(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function useDisplayTimezone(): string {
  const [zone, setZone] = useState(timezone)
  useEffect(() => subscribeDisplayTimezone(() => setZone(getDisplayTimezone())), [])
  return zone
}

// The backend stores naive UTC timestamps, so a value without a zone designator is UTC.
export function toDate(value: string | Date | null | undefined): Date | null {
  if (value === null || value === undefined || value === '') return null
  if (value instanceof Date) return value
  const hasZone = /([zZ]|[+-]\d{2}:?\d{2})$/.test(value)
  const date = new Date(hasZone ? value : value + 'Z')
  return isNaN(date.getTime()) ? null : date
}

export function formatDateTime(value: string | Date | null | undefined): string {
  const date = toDate(value)
  if (!date) return '—'
  return new Intl.DateTimeFormat(undefined, {
    timeZone: timezone,
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    timeZoneName: 'short',
  }).format(date)
}

export function formatDate(value: string | Date | null | undefined): string {
  const date = toDate(value)
  if (!date) return '—'
  return new Intl.DateTimeFormat(undefined, { timeZone: timezone, year: 'numeric', month: 'short', day: '2-digit' }).format(date)
}

export function formatTime(value: string | Date | null | undefined): string {
  const date = toDate(value)
  if (!date) return '—'
  return new Intl.DateTimeFormat(undefined, {
    timeZone: timezone,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(date)
}

export function listTimezones(): string[] {
  const supported = (Intl as any).supportedValuesOf ? (Intl as any).supportedValuesOf('timeZone') as string[] : []
  return Array.from(new Set([DEFAULT_TIMEZONE, ...supported]))
}
