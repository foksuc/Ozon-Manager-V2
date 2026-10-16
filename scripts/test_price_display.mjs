import assert from 'node:assert/strict'
import {formatPrice} from '../frontend/src/priceFormat.ts'

assert.equal(formatPrice('12.49', 'RUB'), '12 ₽')
assert.equal(formatPrice('12.50', 'RUB'), '13 ₽')
assert.equal(formatPrice('999999999999999999999.5', 'RUB'), '1 000 000 000 000 000 000 000 ₽')
assert.equal(formatPrice('-1.5', 'USD'), '−2 USD')
assert.equal(formatPrice('4.5', null), '5 · валюта не указана')
assert.equal(formatPrice(null, 'RUB'), 'Нет данных')
assert.equal(formatPrice('not-a-number', 'RUB'), 'Нет данных')
console.log('PASS: whole price display, signed HALF_UP, exact large strings, unknown currency and invalid values')
