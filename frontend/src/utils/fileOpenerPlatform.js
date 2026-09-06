// Platform hints guide the UI only; the server and Windows agent authorize access.
export function supportsWindowsFileOpener(platform = navigator.platform, userAgent = navigator.userAgent) {
  return /^Win/i.test(platform) && !/Android|iPhone|iPad|iPod/i.test(userAgent)
}

export const FILE_OPENER_LIMITATION = 'Opening network files requires the LawHand File Opener on a Windows computer connected to the firm network or VPN. This is not a phone document preview. Use a permitted provider link when available, or open this result on a supported Windows computer.'
