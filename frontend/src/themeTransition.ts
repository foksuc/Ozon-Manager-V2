type ThemeTransition = {skipTransition:()=>void;finished:Promise<void>}
let active:ThemeTransition|undefined

// One root snapshot, only while changing theme; no cloned DOM or scroll listeners.
export function revealTheme(update:()=>void,reducedMotion:boolean,dark=true){
  active?.skipTransition()
  const root=document.documentElement
  if(reducedMotion||typeof document.startViewTransition!=='function'){
    delete root.dataset.themeTransition
    delete root.dataset.themeDirection
    update()
    return
  }
  root.dataset.themeTransition='reveal'
  root.dataset.themeDirection=dark?'to-dark':'to-light'
  try{
    const transition=document.startViewTransition(update)
    active=transition
    void transition.ready.catch(()=>transition.skipTransition())
    void transition.finished.catch(()=>{}).finally(()=>{
      if(active===transition){active=undefined;delete root.dataset.themeTransition;delete root.dataset.themeDirection}
    })
  }catch{
    active=undefined
    delete root.dataset.themeTransition
    delete root.dataset.themeDirection
    update()
  }
}
