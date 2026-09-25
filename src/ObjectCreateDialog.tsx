import { useEffect, useRef, useState } from 'react'
import maplibregl from 'maplibre-gl'
import type * as GeoJSON from 'geojson'
import { Check, FileUp, MapPin, X } from 'lucide-react'
import { importedCollection } from './AirspaceWorkspace'
import type { LaunchSite } from './LaunchSitesWorkspace'

export type ObjectType='orvd'|'sites'|'prohibited'|'danger'|'settlements'|'obstacle'|'user'
type ZoneType=Exclude<ObjectType,'sites'|'settlements'>
type SiteDraft=Omit<LaunchSite,'id'>
const labels:Record<ObjectType,string>={orvd:'ОрВД',sites:'Площадки',prohibited:'Запретные зоны',danger:'Опасные зоны',settlements:'Населённые пункты',obstacle:'Высотные объекты',user:'Пользовательские объекты'}
const emptySite:SiteDraft={name:'',kind:'mixed',lat:55.7558,lon:37.6173,surface:'полевой старт',runway_length_m:80,heading_deg:0,supports:['multirotor'],has_charging:false,has_fuel:false,status:'open',notes:'',editable:true}
const emptyZone={name:'',code:'',lower_limit:'GND',upper_limit:'',schedule:'H24',coordinates:''}
const fileAccept='.geojson,.json,.kml,.gpx,.csv'

function PickMap({point,onPick}:{point:[number,number];onPick:(point:[number,number])=>void}){
  const container=useRef<HTMLDivElement>(null)
  const callback=useRef(onPick)
  callback.current=onPick
  useEffect(()=>{
    if(!container.current)return
    const map=new maplibregl.Map({container:container.current,style:'https://tiles.openfreemap.org/styles/liberty',center:point,zoom:10,attributionControl:false})
    map.addControl(new maplibregl.NavigationControl(),'top-right')
    map.on('click',event=>callback.current([Number(event.lngLat.lng.toFixed(6)),Number(event.lngLat.lat.toFixed(6))]))
    const observer=new ResizeObserver(()=>map.resize())
    observer.observe(container.current)
    return()=>{observer.disconnect();map.remove()}
  },[])
  return <div ref={container} className="object-create-map" aria-label="Выбор координат на карте"/>
}

function apiError(body:unknown,fallback:string){
  const detail=(body as {detail?:unknown})?.detail
  return typeof detail==='string'?detail:fallback
}

export function ObjectCreateDialog({initialType,onClose,onDone}:{initialType:ObjectType;onClose:()=>void;onDone:(type:ObjectType,sites?:LaunchSite[])=>void}){
  const [type,setType]=useState<ObjectType>(initialType)
  const [mode,setMode]=useState<'manual'|'import'>(initialType==='settlements'?'import':'manual')
  const [site,setSite]=useState<SiteDraft>(emptySite)
  const [zone,setZone]=useState(emptyZone)
  const [file,setFile]=useState<File|null>(null)
  const [picking,setPicking]=useState(false)
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const changeType=(next:ObjectType)=>{setType(next);setMode(next==='settlements'?'import':'manual');setFile(null);setPicking(false);setError('')}
  const updateSite=<K extends keyof SiteDraft>(key:K,value:SiteDraft[K])=>setSite(previous=>({...previous,[key]:value}))
  const updateZone=(key:keyof typeof emptyZone,value:string)=>setZone(previous=>({...previous,[key]:value}))
  const toggleSupport=(support:LaunchSite['supports'][number])=>setSite(previous=>({...previous,supports:previous.supports.includes(support)?previous.supports.filter(item=>item!==support):[...previous.supports,support]}))
  const onMapPick=([lon,lat]:[number,number])=>{
    if(type==='sites'){setSite(previous=>({...previous,lon,lat}));setPicking(false)}
    else setZone(previous=>({...previous,coordinates:previous.coordinates+(previous.coordinates.trim()?'\n':'')+lon+', '+lat}))
  }
  const request=async(url:string,payload:unknown)=>{
    const response=await fetch(url,{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
    const body=await response.json().catch(()=>({}))
    if(!response.ok)throw new Error(apiError(body,'Не удалось сохранить объект'))
    return body
  }
  const save=async()=>{
    setBusy(true);setError('')
    try{
      if(mode==='import'||type==='settlements'){
        if(!file)throw new Error('Выберите файл для загрузки')
        if(type==='settlements'){
          const collection=JSON.parse(await file.text()) as GeoJSON.FeatureCollection
          if(collection.type!=='FeatureCollection'||!collection.features?.length||collection.features.some(feature=>!['Polygon','MultiPolygon'].includes(feature.geometry?.type)))throw new Error('Нужен GeoJSON FeatureCollection с полигонами населённых пунктов')
          await request('/api/settlements/import?source_name='+encodeURIComponent(file.name),collection)
          window.dispatchEvent(new Event('settlements-updated'))
        }else if(type==='sites'){
          const collection=importedCollection(file.name,await file.text())
          if(collection.features.length!==1||collection.features[0].geometry.type!=='Point')throw new Error('Для площадки нужен файл с одной точкой WGS 84')
          const point=collection.features[0].geometry.coordinates
          const properties=collection.features[0].properties||{}
          const payload={...site,name:String(properties.name||site.name||file.name.replace(/\.[^.]+$/,'')),lon:point[0],lat:point[1]}
          const saved=await request('/api/catalog/bases',payload) as LaunchSite
          onDone(type,[saved]);return
        }else{
          const collection=importedCollection(file.name,await file.text())
          await request('/api/airspace/zones/import',{category:type==='user'?'custom':type,source_name:file.name,collection})
          window.dispatchEvent(new Event('airspace-updated'))
        }
      }else if(type==='sites'){
        if(site.name.trim().length<3)throw new Error('Название площадки должно содержать не менее трёх символов')
        if(!site.supports.length)throw new Error('Выберите хотя бы один поддерживаемый тип БВС')
        const saved=await request('/api/catalog/bases',site) as LaunchSite
        onDone(type,[saved]);return
      }else{
        if(zone.name.trim().length<2)throw new Error('Укажите название объекта')
        const points=zone.coordinates.split(/\r?\n/).filter(Boolean).map((row,index)=>{
          const pair=row.trim().split(/[;,\s]+/).map(Number)
          if(pair.length!==2||!pair.every(Number.isFinite)||Math.abs(pair[0])>180||Math.abs(pair[1])>90)throw new Error('Строка '+(index+1)+': укажите долготу и широту WGS 84')
          return pair as [number,number]
        })
        if(points.length<3)throw new Error('Для контура нужно не менее трёх вершин')
        const first=points[0],last=points[points.length-1]
        const ring=first[0]===last[0]&&first[1]===last[1]?points:[...points,first]
        await request('/api/airspace/zones',{category:type==='user'?'custom':type, ...zone,geometry:{type:'Polygon',coordinates:[ring]},properties:{input_mode:picking?'map':'coordinates'}})
        window.dispatchEvent(new Event('airspace-updated'))
      }
      onDone(type)
    }catch(cause){setError(cause instanceof Error?cause.message:'Не удалось сохранить объект')}
    finally{setBusy(false)}
  }
  return <div className="launch-site-modal-backdrop" onMouseDown={event=>{if(event.target===event.currentTarget)onClose()}}>
    <section className="launch-site-modal object-create-dialog" role="dialog" aria-modal="true" aria-labelledby="object-create-title">
      <header><div><span>КАРТОЧКА ОБЪЕКТА</span><h2 id="object-create-title">Новый объект</h2></div><button type="button" className="launch-site-modal-close" aria-label="Закрыть форму" onClick={onClose}><X size={20}/></button></header>
      <div className="launch-site-form object-create-fields">
        <label className="wide">Тип объекта
          <select value={type} onChange={event=>changeType(event.target.value as ObjectType)}>{Object.entries(labels).map(([value,label])=><option value={value} key={value}>{label}</option>)}</select>
        </label>
        {type!=='settlements'&&<div className="wide object-create-mode"><button type="button" className={mode==='manual'?'active':''} onClick={()=>{setMode('manual');setPicking(false)}}>Ручной ввод</button><button type="button" className={mode==='import'?'active':''} onClick={()=>{setMode('import');setPicking(false)}}><FileUp size={15}/> Импорт из файла</button></div>}
        {mode==='import'||type==='settlements'?<div className="wide object-create-import"><FileUp size={26}/><strong>{type==='settlements'?'Границы населённых пунктов · GeoJSON':type==='sites'?'Площадка · GeoJSON с одной точкой':'GeoJSON, KML, GPX или CSV'}</strong><span>{type==='settlements'?'Загрузите FeatureCollection с полигонами WGS 84. Полнота покрытия проверяется отдельно по OSM.':type==='sites'?'Файл должен содержать одну точку; остальные параметры площадки будут заданы по умолчанию.':'Объекты из файла будут добавлены в выбранный слой.'}</span><label className="object-create-file">Выбрать файл<input type="file" accept={type==='settlements'||type==='sites'?'.geojson,.json':fileAccept} onChange={event=>setFile(event.target.files?.[0]||null)}/></label>{file&&<small>{file.name}</small>}</div>:type==='sites'?<>
          <label className="wide">Название<input value={site.name} onChange={event=>updateSite('name',event.target.value)}/></label>
          <label>Тип<select value={site.kind} onChange={event=>updateSite('kind',event.target.value as LaunchSite['kind'])}><option value="mixed">Смешанная площадка</option><option value="runway">Взлётно-посадочная полоса</option><option value="vtol">VTOL-площадка</option></select></label>
          <label>Статус<select value={site.status} onChange={event=>updateSite('status',event.target.value as LaunchSite['status'])}><option value="open">Доступна</option><option value="limited">Ограниченно доступна</option><option value="closed">Закрыта</option></select></label>
          <label>Широта<input type="number" step="any" value={site.lat} onChange={event=>updateSite('lat',Number(event.target.value))}/></label>
          <label>Долгота<input type="number" step="any" value={site.lon} onChange={event=>updateSite('lon',Number(event.target.value))}/></label>
          <button type="button" className={'pick-site'+(picking?' active':'')} onClick={()=>setPicking(!picking)}><MapPin size={15}/> {picking?'Скрыть карту':'Указать на карте'}</button>
          <label>Покрытие<input value={site.surface} onChange={event=>updateSite('surface',event.target.value)}/></label>
          {picking&&<PickMap point={[site.lon,site.lat]} onPick={onMapPick}/>}
          <label>Длина ВПП, м<input type="number" min="20" max="5000" value={site.runway_length_m} onChange={event=>updateSite('runway_length_m',Number(event.target.value))}/></label>
          <label>Курс, °<input type="number" min="0" max="359" value={site.heading_deg} onChange={event=>updateSite('heading_deg',Number(event.target.value))}/></label>
          <fieldset className="wide"><legend>Поддерживаемые типы</legend>{([['fixed_wing','Самолёт'],['multirotor','Мультикоптер'],['vtol','VTOL']] as const).map(([value,label])=><label key={value}><input type="checkbox" checked={site.supports.includes(value)} onChange={()=>toggleSupport(value)}/>{label}</label>)}</fieldset>
          <label className="object-create-check"><input type="checkbox" checked={site.has_charging} onChange={event=>updateSite('has_charging',event.target.checked)}/> Есть зарядка</label>
          <label className="object-create-check"><input type="checkbox" checked={site.has_fuel} onChange={event=>updateSite('has_fuel',event.target.checked)}/> Есть топливо</label>
          <label className="wide">Примечание<textarea rows={2} value={site.notes||''} onChange={event=>updateSite('notes',event.target.value)}/></label>
        </>:<>
          <label className="wide">Название<input value={zone.name} onChange={event=>updateZone('name',event.target.value)}/></label>
          <label>Код<input value={zone.code} onChange={event=>updateZone('code',event.target.value)}/></label>
          <label>Нижняя граница<input value={zone.lower_limit} onChange={event=>updateZone('lower_limit',event.target.value)}/></label>
          <label>Верхняя граница<input value={zone.upper_limit} onChange={event=>updateZone('upper_limit',event.target.value)}/></label>
          <label>Время действия (справочно)<input value={zone.schedule} onChange={event=>updateZone('schedule',event.target.value)}/></label>
          <p className="wide object-create-hint">H24 — круглосуточно. Другой режим запишите текстом, например «ПН–ПТ 09:00–18:00 МСК». Пока расчёт считает такой объект действующим постоянно.</p>
          <div className="wide object-create-mode"><button type="button" className={!picking?'active':''} onClick={()=>setPicking(false)}>Пары координат</button><button type="button" className={picking?'active':''} onClick={()=>setPicking(true)}>Рисование на карте</button></div>
          {picking&&<PickMap point={[37.6173,55.7558]} onPick={onMapPick}/>}
          <label className="wide">Координаты — долгота, широта; одна пара на строку<textarea rows={3} placeholder={'37.6000, 55.7500\n37.6200, 55.7500\n37.6200, 55.7600'} value={zone.coordinates} onChange={event=>updateZone('coordinates',event.target.value)}/></label>
        </>}
      </div>
      {error&&<p className="object-create-error" role="alert">{error}</p>}
      <footer><span>Координаты WGS 84 · {type==='settlements'?'Импорт не подтверждает полноту покрытия OSM.':'Созданные объекты можно удалить из справочника.'}</span><button type="button" disabled={busy} onClick={()=>void save()}><Check size={16}/>{busy?'Сохранение…':mode==='import'||type==='settlements'?'Загрузить':'Сохранить'}</button></footer>
    </section>
  </div>
}
