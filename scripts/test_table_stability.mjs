import assert from 'node:assert/strict'
import {createTable,getCoreRowModel} from '../frontend/node_modules/@tanstack/table-core/build/lib/index.mjs'

async function simulate(stableData,autoResetPageIndex){
  const original=[{id:'synthetic-1',name:'Синтетический товар'}]
  let state={pagination:{pageIndex:0,pageSize:10}},updates=0
  const table=createTable({data:original,columns:[{accessorKey:'name'}],state,
    autoResetPageIndex,getCoreRowModel:getCoreRowModel(),getRowId:row=>row.id,
    onStateChange:update=>{state=typeof update==='function'?update(state):update;updates++;if(updates<30)queueMicrotask(render)}})
  function render(){table.setOptions(previous=>({...previous,state,data:stableData?original:[...original]}));table.getRowModel()}
  render()
  await new Promise(resolve=>setImmediate(resolve))
  render() // A menu, selection or resize causes another render.
  await new Promise(resolve=>setImmediate(resolve))
  return updates
}

const previous=await simulate(false,true)
assert.equal(previous,30,'Previous options must reproduce the automatic reset render storm')
assert.equal(await simulate(true,false),0,'Stable data with no unused pagination resets must settle')
assert.equal(await simulate(false,false),0,'A genuine new filtered array must also settle')
console.log('PASS: reproduced 30 automatic reset updates; fixed table settles with no reset updates')
