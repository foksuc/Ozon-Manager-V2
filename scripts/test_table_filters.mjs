import assert from 'node:assert/strict'
import {isValidDecimalFilter,matchesTableFilter,normalizeDecimalFilter} from '../frontend/src/tableFilterValues.ts'

const filter=(condition,value)=>({condition,value})

assert.equal(matchesTableFilter(null,filter('equals','0'),'decimal'),false)
assert.equal(matchesTableFilter(undefined,filter('greater','0'),'decimal'),false)
assert.equal(matchesTableFilter('',filter('equals',''),'text'),false)
assert.equal(matchesTableFilter('0',filter('equals','0'),'decimal'),true)
assert.equal(matchesTableFilter('0',filter('less','0.01'),'decimal'),true)

assert.equal(matchesTableFilter('-0.01',filter('less','0'),'decimal'),true)
assert.equal(matchesTableFilter('90071992547409931234567890.10',filter('equals','90071992547409931234567890.1'),'decimal'),true)
assert.equal(matchesTableFilter('90071992547409931234567890.10',filter('greater','90071992547409931234567890.09'),'decimal'),true)
assert.equal(matchesTableFilter('90071992547409931234567890.09',filter('less','90071992547409931234567890.10'),'decimal'),true)

assert.equal(normalizeDecimalFilter('  -12,50  '),'-12.50')
assert.equal(isValidDecimalFilter('12,50'),true)
assert.equal(matchesTableFilter('12.5',filter('equals','12,50'),'decimal'),true)
assert.equal(isValidDecimalFilter('12,5,0'),false)
assert.equal(matchesTableFilter('12.5',filter('greater','12,5,0'),'decimal'),false)

assert.equal(matchesTableFilter('10',filter('less','2'),'text'),true)
assert.equal(matchesTableFilter('10',filter('greater','2'),'text'),false)
assert.equal(matchesTableFilter('01',filter('equals','1'),'text'),false)
assert.equal(matchesTableFilter('01',filter('equals','01'),'text'),true)

console.log('PASS: table filter values preserve unknowns, exact Decimal strings, comma input, comparisons, and text semantics')
