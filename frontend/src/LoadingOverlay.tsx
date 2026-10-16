export function LoadingOverlay({active,label='Загружаем данные…',success=false}:{active:boolean;label?:string;success?:boolean}){
  if(!active)return null
  return <div className="loading-overlay" role="status" aria-live="polite" aria-label={label}><div className="loading-card">{success?<span className="loading-overlay-success" aria-hidden="true">✓</span>:<span className="loading-overlay-indicator" aria-hidden="true"/>}<span>{label}</span></div></div>
}
