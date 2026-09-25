import { useEffect, useMemo, useRef, useState } from 'react'
import maplibregl from 'maplibre-gl'
import type * as GeoJSON from 'geojson'
import { Building2, MapPin, Search, ShieldAlert, TowerControl } from 'lucide-react'
import { AirspaceWorkspace } from './AirspaceWorkspace'
import { LaunchSitesWorkspace, type LaunchSite } from './LaunchSitesWorkspace'
import { installSettlementMapLayer, type PolygonResponse } from './settlementMapLayer'

type ObjectSection='all'|'restrictions'|'settlements'|'sites'|'obstacles'

function SettlementDirectory(){
  const container=useRef<HTMLDivElement>(null)
  const mapRef=useRef<maplibregl.Map|null>(null)
  const [data,setData]=useState<PolygonResponse|null>(null)
  const [selected,setSelected]=useState<GeoJSON.Feature|null>(null)
  const [query,setQuery]=useState('')
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
  const status=data?.coverage?.status==='COVERED'?'Видимая область проверена':data?.coverage?'Границы загружены частично':'Широкий обзор справочника'
  return <section className="settlement-directory" aria-label="Населённые пункты">
    <div className="settlement-directory-toolbar"><label><Search size={16}/><input value={query} onChange={event=>setQuery(event.target.value)} placeholder="Название, область или район"/></label><span>{features.length} в видимой области · {status}</span></div>
    <div className="settlement-directory-layout">
      <aside className="settlement-directory-list"><header><strong>Населённые пункты</strong><small>{features.length} показано</small></header><div>{features.map(feature=><button key={String(feature.id)} className={String(selected?.id)===String(feature.id)?'selected':''} onClick={()=>select(feature)}><i/><span><b>{String(feature.properties?.name||'Без названия')}</b><small>{String(feature.properties?.district||feature.properties?.region||'Административная принадлежность не указана')}</small></span></button>)}</div></aside>
      <div ref={container} className="settlement-directory-map"/>
      <aside className="settlement-directory-detail">{selected?<><header><small>НАСЕЛЁННЫЙ ПУНКТ</small><h2>{String(properties.name||'Без названия')}</h2><p>{placeLabel[String(properties.place)]||'Тип не указан'}</p></header><h3>География</h3><dl><dt>Область</dt><dd>{String(properties.region||'не указана в источнике')}</dd><dt>Район</dt><dd>{String(properties.district||'не указан в источнике')}</dd><dt>Геометрия</dt><dd>{selected.geometry.type==='MultiPolygon'?'Несколько полигонов':'Полигон'}</dd></dl><h3>Источник</h3><p>Данные представлены OSM</p><small>Отсутствующее поле не подменяется предположением.</small></>:<div className="settlement-directory-empty"><MapPin size={27}/><strong>Выберите населённый пункт</strong><span>Название, область и район появятся в карточке. На карте можно нажать на полигон.</span></div>}</aside>
    </div>
  </section>
}

export function ObjectsWorkspace({sites,onSitesChange}:{sites:LaunchSite[];onSitesChange:(sites:LaunchSite[])=>void}){
  const [section,setSection]=useState<ObjectSection>('restrictions')
  const tabs:[ObjectSection,string,typeof MapPin][]=[['all','Все',Building2],['restrictions','Ограничения',ShieldAlert],['settlements','Населённые пункты',MapPin],['sites','Площадки',MapPin],['obstacles','Высотные объекты',TowerControl]]
  return <section className="objects-workspace" aria-label="Объекты воздушного пространства">
    <header className="objects-heading"><div><span>ГЕОПРОСТРАНСТВЕННЫЙ СПРАВОЧНИК</span><h1>Объекты воздушного пространства</h1><p>Ограничения, площадки, препятствия и территории, учитываемые при планировании миссий.</p></div></header>
    <nav className="objects-tabs" aria-label="Типы объектов">{tabs.map(([id,label,Icon])=><button key={id} className={section===id?'active':''} onClick={()=>setSection(id)} aria-pressed={section===id}><Icon size={16}/>{label}</button>)}</nav>
    {section==='all'&&<div className="objects-overview"><div><h2>Все объекты в одном разделе</h2><p>Выберите тип, чтобы открыть список, карту и карточку объекта. Источники ограничений, площадок и границ населённых пунктов сохраняются отдельно.</p></div><div className="objects-overview-links">{tabs.slice(1).map(([id,label,Icon])=><button key={id} onClick={()=>setSection(id)}><Icon size={19}/><strong>{label}</strong><span>Открыть справочник →</span></button>)}</div></div>}
    {(section==='restrictions'||section==='obstacles')&&<AirspaceWorkspace key={section} initialCategory={section==='obstacles'?'obstacle':'all'}/>}
    {section==='settlements'&&<SettlementDirectory/>}
    {section==='sites'&&<LaunchSitesWorkspace sites={sites} onSitesChange={onSitesChange}/>}
  </section>
}
