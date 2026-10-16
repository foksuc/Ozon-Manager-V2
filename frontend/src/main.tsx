import React, {useRef,useState} from 'react'
import ReactDOM from 'react-dom/client'
import {flushSync} from 'react-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Theme } from '@radix-ui/themes'
import '@radix-ui/themes/styles.css'
import App from './App'
import './styles.css'
import {UIPreferencesProvider,usePreferences} from './uiPreferences'
import {revealTheme} from './themeTransition'

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 0, refetchOnWindowFocus: false } },
})

function ApplicationTheme() {
  const [dark,setDark]=useState(()=>{try{return localStorage.getItem('ozon-v2-theme')==='dark'}catch{return false}})
  const selected=useRef(dark)
  const {value}=usePreferences()
  const change=(next=!selected.current)=>{
    if(selected.current===next)return
    selected.current=next
    try{localStorage.setItem('ozon-v2-theme',next?'dark':'light')}catch{/* The selected theme still works in memory. */}
    revealTheme(()=>flushSync(()=>setDark(selected.current)),value.reduceMotion||window.matchMedia('(prefers-reduced-motion: reduce)').matches,next)
  }
  return <Theme appearance={dark?'dark':'light'} accentColor="blue" grayColor="slate" radius="large" scaling="100%"><App dark={dark} onThemeChange={change}/></Theme>
}
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <UIPreferencesProvider><ApplicationTheme /></UIPreferencesProvider>
    </QueryClientProvider>
  </React.StrictMode>,
)
