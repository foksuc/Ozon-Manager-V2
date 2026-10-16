export function compareDecimalStrings(leftValue: string, rightValue: string): number {
  const parse = (value: string) => {
    const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value.trim())
    if (!match) return { sign: 1, integer: value, fraction: '' }
    return {
      sign: match[1] === '-' ? -1 : 1,
      integer: (match[2] ?? '').replace(/^0+(?=\d)/, ''),
      fraction: match[3] ?? '',
    }
  }
  const left = parse(leftValue)
  const right = parse(rightValue)
  if (left.sign !== right.sign) return left.sign - right.sign
  const magnitude = left.integer.length === right.integer.length
    ? left.integer.localeCompare(right.integer)
    : left.integer.length - right.integer.length
  if (magnitude !== 0) return magnitude * left.sign
  const width = Math.max(left.fraction.length, right.fraction.length)
  return left.fraction.padEnd(width, '0').localeCompare(right.fraction.padEnd(width, '0')) * left.sign
}

