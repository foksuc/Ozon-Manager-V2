/** Formats display-only price strings without converting them to JS numbers. */
export function formatPrice(value: string | null | undefined, currency: string | null | undefined) {
  if (value === null || value === undefined || value === '') return 'Нет данных'
  const match = /^([+-]?)(\d+)(?:\.(\d*))?$/.exec(value)
  if (!match) return 'Нет данных'

  const [, sign, whole, fraction = ''] = match
  let rounded = BigInt(whole!)
  if (fraction[0] && fraction[0] >= '5') rounded += 1n
  const digits = rounded.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ' ')
  const amount = `${sign === '-' && rounded !== 0n ? '−' : ''}${digits}`
  if (!currency) return `${amount} · валюта не указана`
  if (currency === 'RUB') return `${amount} ₽`
  return `${amount} ${currency}`
}
