export async function dismissReleaseAnnouncement(page) {
  const closeButton = page.getByRole('button', { name: 'Close release announcement' })
  try {
    await closeButton.waitFor({ state: 'visible', timeout: 1500 })
  } catch {
    // Older releases correctly skip the announcement after the recent window.
    return false
  }

  await closeButton.click()
  await closeButton.waitFor({ state: 'detached' })
  return true
}
