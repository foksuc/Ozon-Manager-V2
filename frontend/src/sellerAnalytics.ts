const attention=new Set(['DEFICIT','SURPLUS','NO_SALES','RESTRICTED_NO_SALES','GRADES_YELLOW','GRADES_RED','GRADES_CRITICAL','GRADES_NOSALES'])
export function sellerRowNeedsAttention(row:Record<string,unknown>):boolean {
  return [row.turnover_grade,row.idc_grade,row.turnover_grade_cluster].some(value=>attention.has(String(value)))
}
