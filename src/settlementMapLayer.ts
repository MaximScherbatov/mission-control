import maplibregl, { type Map as MapLibreMap, type GeoJSONSource } from 'maplibre-gl'
import type * as GeoJSON from 'geojson'

type Coverage = { status:'COVERED'|'PARTIALLY_COVERED'|'NOT_COVERED'; tiles_total:number; tiles_fresh:number }
export type PolygonResponse = GeoJSON.FeatureCollection & { coverage:Coverage|null; truncated:boolean; last_fetch_error:string|null }
type LayerOptions = { onCountChange?:(count:number)=>void; onData?:(data:PolygonResponse)=>void; onSelect?:(feature:GeoJSON.Feature)=>void; canOpenPopup?:()=>boolean }

const empty:GeoJSON.FeatureCollection={type:'FeatureCollection',features:[]}
export const settlementLayerIds=['settlement-boundaries-fill','settlement-boundaries-line','settlement-boundaries-label'] as const

export function setSettlementMapVisibility(map:MapLibreMap,visible:boolean){
  for(const id of settlementLayerIds)if(map.getLayer(id))map.setLayoutProperty(id,'visibility',visible?'visible':'none')
}

/** Local, read-only OSM boundary overlay shared by all three maps. */
export function installSettlementMapLayer(map:MapLibreMap,beforeId?:string,options:LayerOptions={}):()=>void{
  const sourceId='settlement-boundaries'
  map.addSource(sourceId,{type:'geojson',data:empty})
  map.addLayer({id:'settlement-boundaries-fill',type:'fill',source:sourceId,
    paint:{'fill-color':'#bb4d83','fill-opacity':.14}},beforeId)
  map.addLayer({id:'settlement-boundaries-line',type:'line',source:sourceId,
    paint:{'line-color':'#a3326d','line-width':['interpolate',['linear'],['zoom'],7,1,12,2.3],
      'line-opacity':.9,'line-dasharray':[2,1]}},beforeId)
  map.addLayer({id:'settlement-boundaries-label',type:'symbol',source:sourceId,minzoom:10.5,
    layout:{'text-field':['coalesce',['get','name'],'НП'],'text-size':11,'text-max-width':12,
      'text-optional':true,'text-allow-overlap':false},
    paint:{'text-color':'#842956','text-halo-color':'#fff','text-halo-width':2}},beforeId)
  const openPopup=(event:maplibregl.MapLayerMouseEvent)=>{
    if(options.canOpenPopup?.()===false)return
    const feature=event.features?.[0]
    const properties=feature?.properties
    if(!properties)return
    if(feature)options.onSelect?.(feature as GeoJSON.Feature)
    const popup=document.createElement('div')
    popup.className='settlement-popup'
    const title=document.createElement('strong')
    title.textContent=String(properties.name||'Населённый пункт без названия')
    popup.append(title)
    const rows:[string,string][]=[
      ['Область',String(properties.region||'не указана в источнике')],
      ['Район',String(properties.district||'не указан в источнике')],
    ]
    for(const [label,value] of rows){
      const row=document.createElement('span')
      const heading=document.createElement('small');heading.textContent=label
      const detail=document.createElement('b');detail.textContent=value
      row.append(heading,detail);popup.append(row)
    }
    const source=document.createElement('em')
    source.textContent='Данные представлены OSM'
    popup.append(source)
    new maplibregl.Popup({offset:12,maxWidth:'300px'}).setLngLat(event.lngLat).setDOMContent(popup).addTo(map)
  }
  const pointer=()=>{if(options.canOpenPopup?.()!==false)map.getCanvas().style.cursor='pointer'}
  const defaultCursor=()=>{map.getCanvas().style.cursor=''}
  map.on('click','settlement-boundaries-fill',openPopup)
  map.on('mouseenter','settlement-boundaries-fill',pointer)
  map.on('mouseleave','settlement-boundaries-fill',defaultCursor)

  const control=document.createElement('div')
  control.className='maplibregl-ctrl settlement-map-control'
  const status=document.createElement('span')
  status.textContent='Границы НП · загрузка…'
  const refresh=document.createElement('button')
  refresh.type='button'
  refresh.title='Догрузить границы населённых пунктов в видимой области из Overpass'
  refresh.setAttribute('aria-label',refresh.title)
  refresh.textContent='↻'
  control.append(status,refresh)
  const controlApi={onAdd:()=>control,onRemove:()=>control.remove()}
  map.addControl(controlApi,'bottom-right')

  let requestId=0
  let disposed=false
  let busy=false
  let refreshError=''
  let lastCoverage:Coverage|null=null
  let lastCount=0
  const attempted=new Set<string>()
  const bbox=()=>{
    const bounds=map.getBounds()
    return [bounds.getWest(),bounds.getSouth(),bounds.getEast(),bounds.getNorth()]
      .map(value=>Number(value.toFixed(5))).join(',')
  }
  const render=()=>{
    const detail=lastCoverage?`${lastCoverage.tiles_fresh}/${lastCoverage.tiles_total} участков`:'широкий обзор'
    status.textContent=`НП: ${lastCount}${lastCoverage?.status==='COVERED'?' · проверено':` · ${detail}`}${busy?' · загрузка…':''}${refreshError?` · ${refreshError}`:''}`
    control.title='Границы OpenStreetMap — предварительные данные, не юридическое подтверждение границ.'
    refresh.disabled=busy||map.getZoom()<8
  }
  const load=async()=>{
    const current=++requestId
    try{
      const response=await fetch(`/api/settlements/polygons?bbox=${encodeURIComponent(bbox())}`,{credentials:'include'})
      if(!response.ok)throw new Error(`HTTP ${response.status}`)
      const data=await response.json() as PolygonResponse
      if(disposed||current!==requestId)return
      lastCoverage=data.coverage
      lastCount=data.features.length
      options.onCountChange?.(lastCount)
      options.onData?.(data)
      if(!busy)refreshError=data.last_fetch_error?`Overpass: ${data.last_fetch_error}`:''
      ;(map.getSource(sourceId) as GeoJSONSource)?.setData({type:'FeatureCollection',features:data.features})
      render()
      // A small local view can be filled in the background without blocking
      // route calculation or firing requests on every pan/zoom.
      if(map.getZoom()>=10&&data.coverage&&data.coverage.status!=='COVERED'&&data.coverage.tiles_total<=4){
        const key=bbox()
        if(!attempted.has(key)){attempted.add(key);void fetchMissing(key)}
      }
    }catch{
      if(!disposed&&current===requestId)status.textContent='Границы НП · справочник недоступен'
    }
  }
  const fetchMissing=async(area:string)=>{
    if(busy)return
    busy=true;refreshError='';render()
    try{
      const response=await fetch(`/api/settlements/refresh?bbox=${encodeURIComponent(area)}`,{method:'POST',credentials:'include'})
      if(!response.ok)throw new Error('Overpass unavailable')
      const result=await response.json() as {fetch_errors?:string[]}
      if(result.fetch_errors?.length)throw new Error(result.fetch_errors[0])
    }catch(error){
      refreshError=error instanceof Error&&error.message.startsWith('(')
        ? `Overpass: ${error.message.split(': ').pop()}`:'Overpass недоступен'
    }finally{
      busy=false
      if(!disposed)void load()
    }
  }
  refresh.addEventListener('click',()=>{if(map.getZoom()>=8)void fetchMissing(bbox())})
  map.on('moveend',load)
  window.addEventListener('settlements-updated',load)
  const poll=window.setInterval(()=>void load(),30000)
  void load()
  return()=>{
    disposed=true;requestId++
    window.clearInterval(poll)
    map.off('moveend',load)
    window.removeEventListener('settlements-updated',load)
    map.off('click','settlement-boundaries-fill',openPopup)
    map.off('mouseenter','settlement-boundaries-fill',pointer)
    map.off('mouseleave','settlement-boundaries-fill',defaultCursor)
    map.removeControl(controlApi)
  }
}

/** Fetch around a point or a small drawn segment while the operator edits. */
export function prefetchSettlementPoints(points:[number,number][]):Promise<void>{
  if(!points.length)return Promise.resolve()
  const west=Math.max(-180,Math.min(...points.map(point=>point[0]))-.003)
  const east=Math.min(180,Math.max(...points.map(point=>point[0]))+.003)
  const south=Math.max(-90,Math.min(...points.map(point=>point[1]))-.003)
  const north=Math.min(90,Math.max(...points.map(point=>point[1]))+.003)
  const bbox=[west,south,east,north].map(value=>value.toFixed(5)).join(',')
  return fetch(`/api/settlements/refresh?bbox=${encodeURIComponent(bbox)}`,{method:'POST',credentials:'include'})
    .then(response=>{if(response.ok)window.dispatchEvent(new Event('settlements-updated'))})
    .catch(()=>{})
}
