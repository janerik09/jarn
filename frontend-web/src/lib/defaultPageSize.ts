import { useEffect, useState } from 'react'

export const FALLBACK_PAGE_SIZE = 25

let cached: number | null = null
let pending: Promise<number> | null = null

export function fetchDefaultPageSize(): Promise<number> {
  if (cached !== null) return Promise.resolve(cached)
  if (!pending) {
    pending = fetch(`/api/v1/admin/settings`)
      .then(res => (res.ok ? res.json() : null))
      .then(data => {
        cached = data?.general?.default_page_size ?? FALLBACK_PAGE_SIZE
        return cached as number
      })
      .catch(() => FALLBACK_PAGE_SIZE)
      .finally(() => {
        pending = null
      })
  }
  return pending
}

export function setCachedDefaultPageSize(size: number) {
  cached = size
}

export function useDefaultPageSize(): number {
  const [size, setSize] = useState<number>(cached ?? FALLBACK_PAGE_SIZE)
  useEffect(() => {
    let active = true
    fetchDefaultPageSize().then(value => {
      if (active) setSize(value)
    })
    return () => {
      active = false
    }
  }, [])
  return size
}
