export const GENERAL_MODULES = new Set([
  'matters',
  'chat',
  'calendar',
  'tasks',
  'communications',
  'contacts',
  'intake',
  'intake-dashboard',
  'time-tracking',
])

export function isGeneralModule(module) {
  return GENERAL_MODULES.has(module)
}

export function canAccessModuleList(enabledModules, module) {
  if (!module) return true
  if (!Array.isArray(enabledModules)) return false
  return enabledModules.includes(module)
}
