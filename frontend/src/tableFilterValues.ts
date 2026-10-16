import {compareDecimalStrings} from './numbers.ts'

export type TableFilterCondition='equals'|'greater'|'less'
export type TableFilterValue={condition:TableFilterCondition;value:string}
export type TableFilterValueKind='text'|'decimal'

const decimalPattern=/^-?\d+(?:\.\d+)?$/

export function normalizeDecimalFilter(value:string){
  const trimmed=value.trim()
  return /^-?\d+,\d+$/.test(trimmed)?trimmed.replace(',','.'):trimmed
}

export function normalizeFilterValue(value:string,kind:TableFilterValueKind){
  return kind==='decimal'?normalizeDecimalFilter(value):value
}

export function isValidDecimalFilter(value:string){
  return decimalPattern.test(normalizeDecimalFilter(value))
}

export function matchesTableFilter(actual:string|null|undefined,filter:TableFilterValue,kind:TableFilterValueKind){
  if(actual===null||actual===undefined||actual==='')return false
  const normalized=kind==='decimal'?normalizeDecimalFilter(filter.value):filter.value
  if(kind==='decimal'&&(!decimalPattern.test(actual)||!decimalPattern.test(normalized)))return false
  const comparison=kind==='decimal'
    ?compareDecimalStrings(actual,normalized)
    :actual.localeCompare(filter.value,'ru',{numeric:false,sensitivity:'variant'})
  return filter.condition==='equals'?comparison===0:filter.condition==='greater'?comparison>0:comparison<0
}
