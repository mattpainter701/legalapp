import type { HostAdapter } from '../contracts/office'
import { ExcelAdapter } from './excelAdapter'
import { OutlookAdapter } from './outlookAdapter'
import { WordAdapter } from './wordAdapter'

export function createAdapter(host: Office.HostType): HostAdapter | null {
  if (host === Office.HostType.Word) return new WordAdapter()
  if (host === Office.HostType.Excel) return new ExcelAdapter()
  if (host === Office.HostType.Outlook) return new OutlookAdapter()
  return null
}
