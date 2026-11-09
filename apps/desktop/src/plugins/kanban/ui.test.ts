import { describe, expect, it } from 'vitest'

import { isLockedTarget } from './ui'

describe("Tim's Column", () => {
  it('is read-only while canonical workflow targets remain available', () => {
    expect(isLockedTarget('tim')).toBe(true)
    expect(isLockedTarget('blocked')).toBe(false)
    expect(isLockedTarget('todo')).toBe(false)
  })
})
