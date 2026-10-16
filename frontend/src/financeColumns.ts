type Column={key:string}
type Row=Record<string,string|null|undefined>

// Summary accruals belong to a posting/service. Only item-level details carry SKU.
export function financeDisplayColumns<T extends Column>(source:T[],rows:Row[]):T[]{
  const columns=source.filter(column=>column.key!=='sku'||rows.some(row=>row.sku!=null&&row.sku.trim()!==''))
  const skuIndex=columns.findIndex(column=>column.key==='sku')
  if(skuIndex>=0){const [sku]=columns.splice(skuIndex,1);columns.splice(Math.min(1,columns.length),0,sku!)}
  return columns
}
