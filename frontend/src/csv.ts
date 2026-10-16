/** Explicit user export: quoted cells and spreadsheet formulas kept as text. */
export function financeCsv(columns:string[],rows:(string|null)[][],options:{delimiter?:';'|','|'\t';bom?:boolean}={}){
  const delimiter=options.delimiter??';'
  const cell=(value:string|null)=>{
    const raw=value??''
    const safe=/^\s*[=+@\-\t\r]/.test(raw)?"'"+raw:raw
    return '"'+safe.replaceAll('"','""')+'"'
  }
  return (options.bom===false?'':'\ufeff')+[columns,...rows].map(row=>row.map(cell).join(delimiter)).join('\r\n')
}
