import { useEffect, useMemo, useRef, useState } from 'react'
import maplibregl from 'maplibre-gl'
import type * as GeoJSON from 'geojson'
import { AlertTriangle, MapPin, Navigation, Plus, Search, ShieldAlert, TowerControl } from 'lucide-react'
import { AirspaceWorkspace } from './AirspaceWorkspace'
import { LaunchSitesWorkspace, type LaunchSite } from './LaunchSitesWorkspace'
import { ObjectCreateDialog, type ObjectType } from './ObjectCreateDialog'
import { installSettlementMapLayer, type PolygonResponse } from './settlementMapLayer'

type ObjectSection=ObjectType
type AirspaceSummary={counts:Record<'orvd'|'prohibited'|'danger'|'obstacle'|'custom',number>;editable:number}

function SettlementDirectory({query}:{query:string}){
  const container=useRef<HTMLDivElement>(null)
  const mapRef=useRef<maplibregl.Map|null>(null)
  const [data,setData]=useState<PolygonResponse|null>(null)
  const [selected,setSelected]=useState<GeoJSON.Feature|null>(null)
  useEffect(()=>{
    if(!container.current)return
    const map=new maplibregl.Map({container:container.current,style:'https://tiles.openfreemap.org/styles/liberty',center:[37.58,55.75],zoom:10,attributionControl:false})
    mapRef.current=map
    map.addControl(new maplibregl.NavigationControl(),'top-right')
    let dispose=()=>{}
    map.on('load',()=>{dispose=installSettlementMapLayer(map,undefined,{onData:setData,onSelect:setSelected});map.resize()})
    const observer=new ResizeObserver(()=>map.resize());observer.observe(container.current)
    return()=>{observer.disconnect();dispose();map.remove();mapRef.current=null}
  },[])
  const features=useMemo(()=>{
    const needle=query.trim().toLocaleLowerCase('ru-RU')
    return (data?.features||[]).filter(feature=>!needle||`${feature.properties?.name||''} ${feature.properties?.region||''} ${feature.properties?.district||''}`.toLocaleLowerCase('ru-RU').includes(needle))
  },[data,query])
  const select=(feature:GeoJSON.Feature)=>{
    setSelected(feature)
    const bounds=new maplibregl.LngLatBounds()
    const visit=(value:unknown):void=>{
      if(!Array.isArray(value))return
      if(value.length>=2&&typeof value[0]==='number'&&typeof value[1]==='number')bounds.extend([value[0],value[1]])
      else value.forEach(visit)
    }
    if('coordinates' in feature.geometry)visit(feature.geometry.coordinates)
    if(!bounds.isEmpty())mapRef.current?.fitBounds(bounds,{padding:75,maxZoom:13,duration:500})
  }
  const properties=selected?.properties||{}
  const placeLabel:Record<string,string>={city:'Город',town:'Город / посёлок',village:'Село / деревня',hamlet:'Небольшой населённый пункт'}
  return <section className="settlement-directory" aria-label="Населённые пункты">
    <div className="settlement-directory-layout">
      <aside className="settlement-directory-list"><header><strong>Населённые пункты</strong><small>{features.length} показано</small></header><div>{features.map(feature=><button key={String(feature.id)} className={String(selected?.id)===String(feature.id)?'selected':''} onClick={()=>select(feature)}><i/><span><b>{String(feature.properties?.name||'Без названия')}</b><small>{String(feature.properties?.district||feature.properties?.region||'Административная принадлежность не указана')}</small></span></button>)}</div></aside>
      <div ref={container} className="settlement-directory-map"/>
      <aside className="settlement-directory-detail">{selected?<><header><small>НАСЕЛЁННЫЙ ПУНКТ</small><h2>{String(properties.name||'Без названия')}</h2><p>{placeLabel[String(properties.place)]||'Тип не указан'}</p></header><h3>География</h3><dl><dt>Область</dt><dd>{String(properties.region||'не указана в источнике')}</dd><dt>Район</dt><dd>{String(properties.district||'не указан в источнике')}</dd><dt>Геометрия</dt><dd>{selected.geometry.type==='MultiPolygon'?'Несколько полигонов':'Полигон'}</dd></dl><h3>Источник</h3><p>Данные представлены OSM</p><small>Отсутствующее поле не подменяется предположением.</small></>:<div className="settlement-directory-empty"><MapPin size={27}/><strong>Выберите населённый пункт</strong><span>Название, область и район появятся в карточке. На карте можно нажать на полигон.</span></div>}</aside>
    </div>
  </section>
}

export function ObjectsWorkspace({sites,onSitesChange}:{sites:LaunchSite[];onSitesChange:(sites:LaunchSite[])=>void}){
  const [section,setSection]=useState<ObjectSection>('orvd')
  const [query,setQuery]=useState('')
  const [createOpen,setCreateOpen]=useState(false)
  const [settlementVersion,setSettlementVersion]=useState(0)
  const [summary,setSummary]=useState<AirspaceSummary|null>(null)
  const [settlementTotal,setSettlementTotal]=useState<number|null>(null)
  useEffect(()=>{
    let active=true
    const load=()=>{
      void fetch('/api/airspace/summary',{credentials:'include'}).then(response=>{
        if(!response.ok)throw new Error('Сводка зон недоступна')
        return response.json() as Promise<AirspaceSummary>
      }).then(data=>{if(active)setSummary(data)}).catch(()=>{if(active)setSummary(null)})
      void fetch('/api/settlements/summary',{credentials:'include'}).then(response=>{
        if(!response.ok)throw new Error('Справочник населённых пунктов недоступен')
        return response.json() as Promise<{total:number}>
      }).then(data=>{if(active)setSettlementTotal(data.total)}).catch(()=>{if(active)setSettlementTotal(null)})
    }
    load()
    window.addEventListener('airspace-updated',load)
    window.addEventListener('settlements-updated',load)
    return()=>{active=false;window.removeEventListener('airspace-updated',load);window.removeEventListener('settlements-updated',load)}
  },[])
  const tabs=[
    {id:'orvd',label:'ОрВД',Icon:TowerControl,count:summary?.counts.orvd,caption:'зоны ответственности',color:'#2d87c8'},
    {id:'sites',label:'Площадки',Icon:Navigation,count:sites.length,caption:'взлёт и посадка',color:'#20a88d'},
    {id:'prohibited',label:'Запретные зоны',Icon:ShieldAlert,count:summary?.counts.prohibited,caption:'нормативные ограничения',color:'#e74d35'},
    {id:'danger',label:'Опасные зоны',Icon:AlertTriangle,count:summary?.counts.danger,caption:'условия и ограничения',color:'#ef9e2f'},
    {id:'settlements',label:'Населённые пункты',Icon:MapPin,count:settlementTotal,caption:'полигоны OSM в справочнике',color:'#bb4d83'},
    {id:'obstacle',label:'Высотные объекты',Icon:TowerControl,count:summary?.counts.obstacle,caption:'препятствия',color:'#8058b4'},
    {id:'user',label:'Пользовательские объекты',Icon:Plus,count:summary?.editable,caption:'добавлены оператором',color:'#2c9b70'},
  ] as const
  const selectSection=(next:ObjectSection)=>{setSection(next);setQuery('')}
  const addObject=()=>setCreateOpen(true)
  const finishCreate=(createdType:ObjectType,newSites?:LaunchSite[])=>{
    if(newSites)onSitesChange([...sites,...newSites])
    if(createdType==='settlements')setSettlementVersion(value=>value+1)
    selectSection(createdType)
    setCreateOpen(false)
  }
  const searchPlaceholder=section==='sites'?'Название или покрытие':section==='settlements'?'Название, область или район':'Код или название объекта'
  return <section className="objects-workspace" aria-label="Объекты воздушного пространства">
    <header className="objects-heading"><div><span>ГЕОПРОСТРАНСТВЕННЫЙ СПРАВОЧНИК</span><h1>Объекты воздушного пространства</h1><p>Зоны, площадки, препятствия и населённые пункты, учитываемые при планировании миссий.</p></div></header>
    <div className="objects-overview-row"><div className="objects-kpis" aria-label="Типы объектов и их количество">{tabs.map(({id,label,Icon,count,caption,color})=><button key={id} type="button" className={section===id?'active':''} style={{'--object-color':color} as React.CSSProperties} onClick={()=>selectSection(id)} aria-pressed={section===id}><Icon size={18}/><span>{label}</span><strong>{count??'—'}</strong><small>{caption}</small></button>)}</div><button type="button" className="objects-create-button" onClick={addObject}><Plus size={16}/> Объект</button></div>
    <label className="objects-search"><Search size={16}/><input value={query} onChange={event=>setQuery(event.target.value)} placeholder={searchPlaceholder} aria-label="Поиск объектов"/></label>
    {section!=='sites'&&section!=='settlements'&&<AirspaceWorkspace key={section} initialCategory={section} embedded searchQuery={query}/>}
    {section==='settlements'&&<SettlementDirectory key={settlementVersion} query={query}/>}
    {section==='sites'&&<LaunchSitesWorkspace sites={sites} onSitesChange={onSitesChange} searchQuery={query}/>}
    {createOpen&&<ObjectCreateDialog initialType={section} onClose={()=>setCreateOpen(false)} onDone={finishCreate}/>}
  </section>
}
