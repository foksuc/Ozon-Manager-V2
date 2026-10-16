import assert from 'node:assert/strict'
import {financeCsv} from '../frontend/src/csv.ts'
import {compareDecimalStrings} from '../frontend/src/numbers.ts'
import {financeDisplayColumns} from '../frontend/src/financeColumns.ts'

const exported=financeCsv(['SKU','Комментарий','Сумма'],[['9007199254740993','=HYPERLINK("https://evil.invalid")','-0.01'],['Синтетический','кавычка " и ; перевод\nстроки',null]])
assert.ok(exported.startsWith('\ufeff'))
assert.ok(exported.includes('"9007199254740993"'))
assert.ok(exported.includes('"\'=HYPERLINK(""https://evil.invalid"")"'))
assert.ok(exported.includes('"\'-0.01"'))
assert.ok(exported.includes('"кавычка "" и ; перевод\nстроки"'))
for(const formula of ['=1+1',' +SUM(1;2)','\t@SUM(1;2)','\r-1+2'])assert.ok(financeCsv(['A'],[[formula]]).includes('"\''+formula+'"'))
assert.ok(compareDecimalStrings('9007199254740993.01','9007199254740993.02')<0)
assert.ok(compareDecimalStrings('-0.01','0')<0)
assert.equal(compareDecimalStrings('1.0','1.00'),0)
const schema=[{key:'date',label:'Дата'},{key:'posting',label:'Отправление'},{key:'sku',label:'SKU'},{key:'amount',label:'Сумма'}]
const totals=[{date:'2026-10-09',posting:'SYNTHETIC-1',sku:null,amount:'10.01'}]
assert.deepEqual(financeDisplayColumns(schema,totals).map(c=>c.key),['date','posting','amount'])
assert.deepEqual(financeDisplayColumns(schema,[{sku:''},{sku:'  '}]).map(c=>c.key),['date','posting','amount'])
assert.deepEqual(financeDisplayColumns(schema,[]).map(c=>c.key),['date','posting','amount'])
const detailed=[...totals,{date:'2026-10-09',posting:'SYNTHETIC-1',sku:'9007199254740993',amount:'2.00'}]
assert.deepEqual(financeDisplayColumns(schema,detailed).map(c=>c.key),['date','sku','posting','amount'])
assert.deepEqual(schema.map(c=>c.key),['date','posting','sku','amount'])
const summaryColumns=financeDisplayColumns(schema,totals)
const summaryExport=financeCsv(summaryColumns.map(c=>c.label),totals.map(row=>summaryColumns.map(c=>row[c.key])))
assert.ok(!summaryExport.includes('SKU'))
assert.ok(summaryExport.includes('SYNTHETIC-1'))
console.log('PASS: CSV protection and Decimal sorting; missing-summary SKU hidden, detailed SKU retained, original schema untouched, export matches visible columns')
