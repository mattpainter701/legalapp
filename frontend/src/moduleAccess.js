export const GENERAL_MODULES = new Set([
  'matters',
  'chat',
  'calendar',
  'tasks',
  'communications',
  'intake',
  'intake-dashboard',
  'time-tracking',
])

export function isGeneralModule(module) {
  return GENERAL_MODULES.has(module)
}

export function canAccessModuleList(enabledModules, module) {
  if (!module || isGeneralModule(module)) return true
  if (!Array.isArray(enabledModules) || enabledModules.length === 0) return true
  return enabledModules.includes(module)
}
