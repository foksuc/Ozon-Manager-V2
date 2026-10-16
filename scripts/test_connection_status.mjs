import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import {createRequire} from 'node:module'

const require=createRequire(import.meta.url)
const ts=require('../frontend/node_modules/typescript')
const source=await fs.readFile(new URL('../frontend/src/connectionStatus.ts',import.meta.url),'utf8')
const {outputText}=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,verbatimModuleSyntax:true}})
const {verifyConnection,canCheckConnection}=await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

// No transport, credentials or real account data are used by these scenarios.
globalThis.fetch=()=>{throw new Error('External requests forbidden in synthetic connection tests')}
const accounts=(active_id='synthetic-a',revision=7,mode='LIVE_READ')=>({active_id,revision,mode,items:[],legacy_available:false})
const ok={status:'ok',read_only:true,expires_at:null,role_count:1,methods_count:1}
let passed=0
async function scenario(name,body){await body();passed++;console.log(`PASS ${name}`)}
function fixture({before=accounts(),after=accounts(),failure,fail=false,mode='LIVE_READ',accountId='synthetic-a',revision=7,current,abortDuring=false}={}) {
  const controller=new AbortController()
  const calls={accounts:0,checks:0}
  const options={accountId,revision,mode,signal:controller.signal,
    getAccounts:async(signal)=>{assert.equal(signal,controller.signal);return calls.accounts++===0?before:after},
    checkConnection:async(signal)=>{assert.equal(signal,controller.signal);calls.checks++;if(abortDuring)controller.abort(new Error('Synthetic cancellation'));if(fail)throw failure;return ok},
    currentAccounts:()=>current,
  }
  return {options,calls,controller}
}

await scenario('success belongs to the verified account',async()=>{
  const f=fixture();assert.deepEqual(await verifyConnection(f.options),{status:'ok'});assert.deepEqual(f.calls,{accounts:2,checks:1})
})
await scenario('connection error is preserved for the same revision',async()=>{
  const failure=new Error('Synthetic connection refused'),f=fixture({fail:true,failure})
  await assert.rejects(verifyConnection(f.options),error=>error===failure);assert.deepEqual(f.calls,{accounts:2,checks:1})
})
await scenario('switch before check sends no connection request',async()=>{
  const f=fixture({before:accounts('synthetic-b')});assert.deepEqual(await verifyConnection(f.options),{status:'unknown'});assert.deepEqual(f.calls,{accounts:1,checks:0})
})
await scenario('revision change before check sends no connection request',async()=>{
  const f=fixture({before:accounts('synthetic-a',8)});assert.deepEqual(await verifyConnection(f.options),{status:'unknown'});assert.deepEqual(f.calls,{accounts:1,checks:0})
})
await scenario('switch during successful check discards green status',async()=>{
  const f=fixture({after:accounts('synthetic-b',8)});assert.deepEqual(await verifyConnection(f.options),{status:'unknown'});assert.deepEqual(f.calls,{accounts:2,checks:1})
})
await scenario('revision change during successful check discards green status',async()=>{
  const f=fixture({after:accounts('synthetic-a',8)});assert.deepEqual(await verifyConnection(f.options),{status:'unknown'})
})
await scenario('failure from the previous store is discarded',async()=>{
  const f=fixture({after:accounts('synthetic-b',8),fail:true,failure:new Error('Obsolete synthetic error')});assert.deepEqual(await verifyConnection(f.options),{status:'unknown'})
})
await scenario('newer cache context discards a successful response',async()=>{
  const f=fixture({current:accounts('synthetic-b',8)});assert.deepEqual(await verifyConnection(f.options),{status:'unknown'})
})
await scenario('MOCK performs zero account or connection requests',async()=>{
  const f=fixture({mode:'MOCK'});assert.equal(canCheckConnection(f.options.accountId,f.options.revision,f.options.mode),false);assert.deepEqual(await verifyConnection(f.options),{status:'unknown'});assert.deepEqual(f.calls,{accounts:0,checks:0})
})
await scenario('absent account performs zero requests',async()=>{
  const f=fixture({accountId:null});assert.deepEqual(await verifyConnection(f.options),{status:'unknown'});assert.deepEqual(f.calls,{accounts:0,checks:0})
})
await scenario('aborted request performs zero requests',async()=>{
  const f=fixture();f.controller.abort(new Error('Synthetic cancellation'));await assert.rejects(verifyConnection(f.options),/Synthetic cancellation/);assert.deepEqual(f.calls,{accounts:0,checks:0})
})
await scenario('cancellation during check stops subsequent account request',async()=>{
  const f=fixture({abortDuring:true});await assert.rejects(verifyConnection(f.options),/Synthetic cancellation/);assert.deepEqual(f.calls,{accounts:1,checks:1})
})
console.log(`connection-status synthetic: ${passed} scenarios passed; 0 external requests`)
