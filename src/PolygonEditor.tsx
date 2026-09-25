import { useEffect, useRef, useState } from 'react'
import maplibregl, { type GeoJSONSource } from 'maplibre-gl'
import type * as GeoJSON from 'geojson'
import { MapPin } from 'lucide-react'
import type { LaunchSite } from './LaunchSitesWorkspace'
import { installSettlementMapLayer, prefetchSettlementPoints } from './settlementMapLayer'

export type MissionGeometry = GeoJSON.Feature<GeoJSON.Polygon | GeoJSON.LineString>
type GeometryMode = 'area' | 'corridor'

export function PolygonEditor({ area, onChange, routes, restrictions, onEditing, launch, launchSites, selectedLaunchSiteId, onLaunchSite, resultsOverlay, onLaunch, mode, corridorAllowed, corridorWidth, onModeChange, onCorridorWidth, onImport, onReset, areaSummary }: {
  area: MissionGeometry; onChange: (value: MissionGeometry) => void; routes: GeoJSON.FeatureCollection<GeoJSON.LineString | GeoJSON.MultiLineString>;
  restrictions: GeoJSON.FeatureCollection;
  settlements?: GeoJSON.FeatureCollection; // retained for callers of the previous overlay API
  onEditing: (value: boolean) => void; launch: [number,number] | null; onLaunch:(p:[number,number] | null)=>void;
  launchSites:LaunchSite[];
  selectedLaunchSiteId:string|null;onLaunchSite:(id:string)=>void;
  resultsOverlay:boolean;
  mode: GeometryMode; corridorAllowed:boolean; corridorWidth:number; onModeChange:(mode:GeometryMode)=>void; onCorridorWidth:(width:number)=>void;
  onImport:(file:File)=>void;onReset:()=>void;areaSummary:string;
}) {
  const coordinatesFor = (feature: MissionGeometry) => feature.geometry.type === 'LineString' ? feature.geometry.coordinates : feature.geometry.coordinates[0].slice(0, -1)
  const container = useRef<HTMLDivElement>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const vertices = useRef<number[][]>(coordinatesFor(area))
  const markers = useRef<maplibregl.Marker[]>([])
  const launchMarker = useRef<maplibregl.Marker | null>(null)
  const launchCallback = useRef(onLaunch)
  const modeRef = useRef(mode)
  const launchPick = useRef(false)
  const fittedRouteSignature = useRef('')
  const [pickingLaunch,setPickingLaunch] = useState(false)
  const history = useRef<number[][][]>([])
  const changeRef = useRef(onChange)
  const drawingRef = useRef(false)
  const [drawing, setDrawing] = useState(false)
  const [count, setCount] = useState(vertices.current.length)
  const [undoCount, setUndoCount] = useState(0)
  const [ready, setReady] = useState(false)
  const [mapError, setMapError] = useState(false)
  const prefetchTimer=useRef<ReturnType<typeof setTimeout>|null>(null)
  const prefetchPending=useRef<[number,number][]|null>(null)
  const prefetchBusy=useRef(false)
  changeRef.current = onChange; launchCallback.current = onLaunch; modeRef.current = mode

  const queuePrefetch=(points:[number,number][])=>{
    prefetchPending.current=points
    if(prefetchTimer.current)clearTimeout(prefetchTimer.current)
    prefetchTimer.current=setTimeout(async()=>{
      prefetchTimer.current=null
      if(prefetchBusy.current)return
      const pending=prefetchPending.current
      prefetchPending.current=null
      if(!pending)return
      prefetchBusy.current=true
      try{await prefetchSettlementPoints(pending)}finally{
        prefetchBusy.current=false
        if(prefetchPending.current)queuePrefetch(prefetchPending.current)
      }
    },900)
  }
  const minimumPoints = () => modeRef.current === 'corridor' ? 2 : 3
  const remember = () => { history.current.push(vertices.current.map(p => [...p])); setUndoCount(history.current.length) }
  const render = () => {
    const map = mapRef.current
    if (!map?.getSource('editor-area')) return
    const points = vertices.current
    setCount(points.length)
    const polygon = modeRef.current === 'area' && points.length >= 3 ? [{ type: 'Feature' as const, properties: {}, geometry: { type: 'Polygon' as const, coordinates: [[...points, points[0]]] } }] : []
    const corridor = modeRef.current === 'corridor' && points.length >= 2 ? [{type:'Feature' as const,properties:{},geometry:{type:'LineString' as const,coordinates:points}}] : []
    ;(map.getSource('editor-area') as GeoJSONSource).setData({ type: 'FeatureCollection', features: polygon })
    ;(map.getSource('editor-line') as GeoJSONSource).setData({type:'FeatureCollection',features:corridor})
    markers.current.forEach(marker => marker.remove())
    markers.current = points.map((point, index) => {
      const element = document.createElement('button')
      element.className = 'editor-vertex'; element.textContent = String(index + 1)
      element.title = `Точка ${index + 1}: перетащите; двойной клик — удалить`
      element.setAttribute('aria-label', `Точка ${index + 1}`)
      element.addEventListener('click', event => event.stopPropagation())
      element.addEventListener('dblclick', event => { event.stopPropagation(); remember(); vertices.current.splice(index, 1); render(); publish();queuePrefetch(vertices.current as [number,number][]) })
      const marker = new maplibregl.Marker({ element, draggable: true }).setLngLat(point as [number,number]).addTo(map)
      marker.on('dragstart', remember)
      marker.on('dragend', () => { const p = marker.getLngLat(); vertices.current[index] = [p.lng,p.lat]; render(); publish();queuePrefetch([[p.lng,p.lat]]) })
      return marker
    })
  }
  const publish = () => {
    onEditing(drawingRef.current || vertices.current.length < minimumPoints())
    if (modeRef.current === 'corridor' && vertices.current.length >= 2) changeRef.current({type:'Feature',properties:{source:'editor'},geometry:{type:'LineString',coordinates:vertices.current}})
    if (modeRef.current === 'area' && vertices.current.length >= 3) changeRef.current({type:'Feature',properties:{source:'editor'},geometry:{type:'Polygon',coordinates:[[...vertices.current,vertices.current[0]]]}})
  }
  useEffect(() => {
    if (!container.current) return
    const initial = coordinatesFor(area)
    const map = new maplibregl.Map({container:container.current,style:'https://tiles.openfreemap.org/styles/liberty',center:initial[0] as [number,number],zoom:12,pitch:0})
    let disposeSettlements=()=>{}
    mapRef.current = map
    map.addControl(new maplibregl.NavigationControl())
    map.doubleClickZoom.disable()
    map.on('error', () => setMapError(true))
    map.on('load', () => {
      map.addSource('editor-restrictions',{type:'geojson',data:restrictions})
      map.addLayer({id:'editor-restrictions-fill',type:'fill',source:'editor-restrictions',paint:{'fill-color':['match',['get','category'],'prohibited','#e34c35','danger','#ed9a29','obstacle','#8058b4','#2d9a70'],'fill-opacity':.20}})
      map.addLayer({id:'editor-restrictions-line',type:'line',source:'editor-restrictions',paint:{'line-color':['match',['get','category'],'prohibited','#c83220','danger','#cf7610','obstacle','#66408f','#13724f'],'line-width':2.5,'line-dasharray':[3,1]}})
      map.addSource('editor-area',{type:'geojson',data:{type:'FeatureCollection',features:[]}})
      map.addLayer({id:'editor-fill',type:'fill',source:'editor-area',paint:{'fill-color':'#16add3','fill-opacity':.24}})
      map.addLayer({id:'editor-border',type:'line',source:'editor-area',layout:{'line-cap':'round','line-join':'round'},paint:{'line-color':'#008db7','line-width':3}})
      map.addSource('editor-line',{type:'geojson',data:{type:'FeatureCollection',features:[]}})
      map.addLayer({id:'editor-corridor-band',type:'line',source:'editor-line',layout:{'line-cap':'round','line-join':'round'},paint:{'line-color':'#16add3','line-width':24,'line-opacity':.18}})
      map.addLayer({id:'editor-corridor-axis',type:'line',source:'editor-line',layout:{'line-cap':'round','line-join':'round'},paint:{'line-color':'#008db7','line-width':3}})
      map.addSource('editor-routes',{type:'geojson',data:routes})
      map.addLayer({id:'editor-galss',type:'line',source:'editor-routes',layout:{'line-cap':'round','line-join':'round'},paint:{'line-color':['get','color'],'line-width':1.7,'line-opacity':.78}})
      map.addSource('editor-launch-sites',{type:'geojson',data:{type:'FeatureCollection',features:launchSites.map(site=>({type:'Feature',properties:{id:site.id,name:site.name,status:site.status},geometry:{type:'Point',coordinates:[site.lon,site.lat]}}))}})
      map.addLayer({id:'editor-launch-sites-halo',type:'circle',source:'editor-launch-sites',paint:{'circle-radius':9,'circle-color':['match',['get','status'],'closed','#90999d','limited','#e1a13d','#1ca68b'],'circle-stroke-color':'#fff','circle-stroke-width':2}})
      map.addLayer({id:'editor-launch-sites-label',type:'symbol',source:'editor-launch-sites',layout:{'text-field':['get','name'],'text-size':10,'text-offset':[0,1.4],'text-anchor':'top','text-optional':true},paint:{'text-color':'#17353e','text-halo-color':'#fff','text-halo-width':2}})
      disposeSettlements=installSettlementMapLayer(map,'editor-fill',{canOpenPopup:()=>!drawingRef.current&&!launchPick.current})
      render(); setReady(true)
      const bounds = new maplibregl.LngLatBounds(); initial.forEach(p => bounds.extend(p as [number,number]))
      map.fitBounds(bounds,{padding:55,maxZoom:15,duration:0})
    })
    map.on('click', event => { if (launchPick.current) {launchCallback.current([event.lngLat.lng,event.lngLat.lat]);launchPick.current=false;setPickingLaunch(false);onEditing(false);return} if (!drawingRef.current) return; remember(); vertices.current.push([event.lngLat.lng,event.lngLat.lat]); render(); publish();queuePrefetch(vertices.current.slice(-2) as [number,number][]) })
    const observer = new ResizeObserver(() => map.resize()); observer.observe(container.current)
    return () => { if(prefetchTimer.current)clearTimeout(prefetchTimer.current);prefetchPending.current=null;observer.disconnect(); disposeSettlements(); markers.current.forEach(m => m.remove()); map.remove(); mapRef.current = null; onEditing(false) }
  }, [])
  useEffect(() => {
    if (!ready) return
    const incoming = coordinatesFor(area)
    if (JSON.stringify(incoming) !== JSON.stringify(vertices.current)) { vertices.current = incoming; history.current=[]; setUndoCount(0) }
    render()
  }, [area, mode, ready])
  useEffect(() => {
    if (!ready) return
    ;(mapRef.current?.getSource('editor-routes') as GeoJSONSource)?.setData(routes)
    if (!routes.features.length || !mapRef.current) { fittedRouteSignature.current = ''; return }
    const signature = `${resultsOverlay?'drawer':'full'}:${JSON.stringify(routes.features.map(feature => ({
      name: feature.properties?.name,
      geometry: feature.geometry,
    })))}`
    if (signature === fittedRouteSignature.current) return
    fittedRouteSignature.current = signature
    const bounds=new maplibregl.LngLatBounds()
    const add=(coordinates:GeoJSON.Position[])=>coordinates.forEach(point=>bounds.extend(point as [number,number]))
    routes.features.forEach(feature=>feature.geometry.type==='LineString'?add(feature.geometry.coordinates):feature.geometry.coordinates.forEach(add))
    if(launch)bounds.extend(launch)
    const leftInset=resultsOverlay&&window.innerWidth>=1000?330:65
    if(!bounds.isEmpty())mapRef.current.fitBounds(bounds,{padding:{top:65,right:65,bottom:65,left:leftInset},maxZoom:14,duration:650})
  }, [routes, ready, launch, resultsOverlay])
  useEffect(() => { if (ready) (mapRef.current?.getSource('editor-restrictions') as GeoJSONSource)?.setData(restrictions) }, [restrictions, ready])
  useEffect(()=>{if(ready)(mapRef.current?.getSource('editor-launch-sites') as GeoJSONSource)?.setData({type:'FeatureCollection',features:launchSites.map(site=>({type:'Feature',properties:{id:site.id,name:site.name,status:site.status},geometry:{type:'Point',coordinates:[site.lon,site.lat]}}))})},[launchSites,ready])
  useEffect(() => {
    launchMarker.current?.remove()
    if (!ready || !launch || !mapRef.current) return
    queuePrefetch([launch])
    const element=document.createElement('button');element.className='launch-marker';element.textContent='СТАРТ';element.setAttribute('aria-label','Планируемая точка старта')
    const marker=new maplibregl.Marker({element,draggable:true}).setLngLat(launch).addTo(mapRef.current)
    marker.on('dragend',()=>{const p=marker.getLngLat();launchCallback.current([p.lng,p.lat])})
    launchMarker.current=marker
    return ()=>{marker.remove()}
  },[launch,ready])
  return <section className="polygon-editor">
    <header><div className="territory-heading"><b>01 / {mode==='corridor'?'Трасса задания':'Территория задания'}</b><small>{count} точек · WGS 84 · {areaSummary}</small></div>
    <div className="territory-actions">
      {corridorAllowed && <div className="geometry-mode" role="group" aria-label="Тип геометрии"><button className={mode==='area'?'selected':''} onClick={()=>onModeChange('area')}>Площадь</button><button className={mode==='corridor'?'selected':''} onClick={()=>onModeChange('corridor')}>Коридор</button></div>}
      <label className="territory-launch-select"><MapPin size={13}/><select aria-label="Площадка старта на карте задания" value={selectedLaunchSiteId||(launch?'custom':'auto')} onChange={event=>onLaunchSite(event.target.value)}><option value="auto">Автоподбор площадки</option>{launchSites.filter(site=>site.status!=='closed').map(site=><option key={site.id} value={site.id}>{site.name} · {site.runway_length_m} м</option>)}<option value="custom">Точка на карте</option></select></label>
      <button disabled={drawing} onClick={()=>{launchPick.current=!pickingLaunch;setPickingLaunch(!pickingLaunch);onEditing(!pickingLaunch)}}>{pickingLaunch?'Отменить точку':'Точка на карте'}</button>
      <button onClick={() => { if (!drawing) { remember(); vertices.current = []; render() } drawingRef.current = !drawing; setDrawing(!drawing); onEditing(!drawing) }} disabled={pickingLaunch || (drawing && count < minimumPoints())}>{drawing ? 'Завершить' : mode==='corridor'?'Новая трасса':'Новый контур'}</button>
      <button disabled={!undoCount} onClick={() => { vertices.current = history.current.pop()!; setUndoCount(history.current.length); render(); publish() }}>↶ Отменить</button>
      <button onClick={()=>fileInput.current?.click()}>Импорт из файла</button><button onClick={onReset}>Сбросить</button><input ref={fileInput} hidden type="file" accept=".geojson,.json,.kml,.gpx,.csv,application/geo+json,application/json,application/vnd.google-earth.kml+xml,application/gpx+xml,text/csv" onChange={event=>{const file=event.target.files?.[0];if(file)onImport(file);event.currentTarget.value='' }}/>
    </div></header>
    {mode==='corridor' && <div className="corridor-toolbar"><label>Ширина обследования <b>{corridorWidth} м</b><input type="range" min="20" max="300" step="10" value={corridorWidth} onChange={event=>onCorridorWidth(Number(event.target.value))}/></label><small>Рабочие проходы строятся вдоль оси линейного объекта.</small></div>}
    <div className="launch-toolbar"><small>{pickingLaunch?'Кликните по предполагаемой площадке':selectedLaunchSiteId?`Выбрана площадка: ${launchSites.find(site=>site.id===selectedLaunchSiteId)?.name||'—'} · курс ${launchSites.find(site=>site.id===selectedLaunchSiteId)?.heading_deg??'—'}°`:launch?`Полевой старт и возврат: ${launch[1].toFixed(5)}°, ${launch[0].toFixed(5)}° · только БВС без ВПП`:'Площадка будет подобрана автоматически с учётом типа БВС'}</small>{routes.features.length>0&&<div className="editor-route-legend" aria-label="Маршруты назначенных БВС">{routes.features.map((feature,index)=><span key={`${String(feature.properties?.name||index)}-${index}`}><i style={{backgroundColor:String(feature.properties?.color||'#168fb2')}}/>{String(feature.properties?.name||`БВС ${index+1}`)}</span>)}</div>}</div>
    <div ref={container} className="editor-map" />
    <footer>{drawing ? `Ставьте точки кликом. Для ${mode==='corridor'?'трассы нужны минимум 2 точки':'контура нужны минимум 3 вершины'}.` : 'Перетаскивайте точки. Двойной клик удаляет точку. «Отменить» возвращает предыдущую геометрию.'}{mapError && ' Подложка может быть недоступна; редактирование геометрии сохранено.'}</footer>
  </section>
}
