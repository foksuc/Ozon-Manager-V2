export type IconName = 'power' | 'reports' | 'strategies' | 'promotions' | 'stocks' | 'finance' | 'operations' | 'settings' | 'account' | 'refresh' | 'chevron' | 'moon' | 'sun' | 'overview' | 'payouts' | 'accruals' | 'documents' | 'advertising' | 'analytics' | 'statistics' | 'undo'
const paths: Record<IconName, string> = {
  statistics:'M3 21h18M6 17V9h3v8M11 17V3h3v14M16 17v-6h3v6',
  power:'M12 2v10M6 5a9 9 0 1 0 12 0',
  reports:'M5 3h10l4 4v14H5V3Zm10 0v5h4M8 12h8M8 16h8',
  undo:'M8 4 3 9l5 5M3 9h11a7 7 0 0 1 0 14',
  strategies: 'M3 18h5v-6h5V6h8M17 3l4 3-4 3M3 5h6M6 2v6',
  promotions: 'M3 3h7l11 11-7 7L3 10V3Zm4 4h.01',
  stocks: 'm3 7 9-4 9 4v10l-9 4-9-4V7Zm0 0 9 4 9-4M12 11v10M7.5 5 17 9',
  finance: 'M20 7H5a2 2 0 0 1 0-4h13v4M5 7H3v14h18V7h-1Zm16 5h-6v5h6M17 14.5h.01',
  overview: 'M3 3h7v7H3V3Zm11 0h7v7h-7V3ZM3 14h7v7H3v-7Zm11 0h7v7h-7v-7Z',
  payouts: 'M3 5h18v14H3V5ZM3 9h18M12 11v6m-3-3 3 3 3-3',
  accruals: 'M5 3h14v18l-3-2-4 2-4-2-3 2V3Zm4 5h6M9 12h6M9 16h3',
  documents: 'M5 3h9l5 5v13H5V3Zm9 0v5h5M9 12h6M9 16h6',
  advertising: 'M3 10v4h4l12 5V5L7 10H3Zm4 4 2 7h4l-3-6M21 9v6',
  analytics: 'M3 3v18h18M7 16l4-5 4 2 6-8',
  operations: 'M8 3h8v4H8V3ZM8 5H5v16h14V5h-3M8 12h8M8 16h5',
  settings: 'M4 6h16M4 12h16M4 18h16M8 4v4M16 10v4M10 16v4',
  account: 'M20 21v-2a6 6 0 0 0-6-6h-4a6 6 0 0 0-6 6v2M12 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z',
  refresh: 'M20 7V3l-3 3a8 8 0 0 0-13 6M4 17v4l3-3a8 8 0 0 0 13-6M20 3h-5M4 21h5',
  chevron: 'm9 5 7 7-7 7',
  moon: 'M21 13a9 9 0 0 1-10-10A9 9 0 1 0 21 13Z',
  sun: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8ZM12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5 19 19M5 19l1.5-1.5M17.5 6.5 19 5',
}
export function Icon({name,className=''}:{name:IconName;className?:string}) {
  return <svg className={`ui-icon ${className}`} viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]}/></svg>
}
