import { describe, expect, it } from 'vitest'
import { supportsWindowsFileOpener } from './fileOpenerPlatform'
describe('Windows opener platform guidance', () => {
  it.each(['iPhone', 'iPad', 'MacIntel', 'Linux armv8l', ''])('does not offer the opener on %s', platform => {
    expect(supportsWindowsFileOpener(platform, '')).toBe(false)
  })
  it('offers Windows and rejects mobile user agents even with Windows platform hints', () => {
    expect(supportsWindowsFileOpener('Win32', 'Windows NT 10')).toBe(true)
    expect(supportsWindowsFileOpener('Win32', 'Android')).toBe(false)
  })
})
