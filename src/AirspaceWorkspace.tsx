import { useEffect, useMemo, useRef, useState } from 'react'
import maplibregl, { type GeoJSONSource, type Map as MapLibreMap } from 'maplibre-gl'
import type * as GeoJSON from 'geojson'
import { AlertTriangle, Check, FileUp, MapPin, Plus, Search, ShieldAlert, TowerControl, Trash2, X } from 'lucide-react'

type Category = 'prohibited' | 'danger' | 'orvd' | 'obstacle' | 'custom'
type ZoneProperties = {
  id:string;category:Category;name:string;code?:string|null;bbox:number[];
  lower_limit?:string|null;upper_limit?:string|null;vertical_definition?:string|null;
  schedule?:string|null;valid_from?:string|null;valid_until?:string|null;
  enabled:boolean;editable:boolean;source_name:string;
}
type ZoneFeature = GeoJSON.Feature<GeoJSON.Point | GeoJSON.Polygon | GeoJSON.MultiPolygon, ZoneProperties>
type Summary = {total:number;counts:Record<Category,number>;editable:number;sources:string[];storage:string}
type Authority = {zone_id:string;zone_name:string;code?:string|null;contact_status:'verified'|'missing';contact?:{organization:string;dispatch_center?:string;phone?:string;email?:string;procedure:string}|null}

const emptyCollection:GeoJSON.FeatureCollection<GeoJSON.Geometry,ZoneProperties>={type:'FeatureCollection',features:[]}
const categoryMeta:Record<Category,{label:string;color:string}>={
  prohibited:{label:'Запретные',color:'#e74d35'},danger:{label:'Опасные',color:'#ef9e2f'},orvd:{label:'Зоны ОрВД',color:'#2d87c8'},
  obstacle:{label:'Высотные объекты',color:'#8058b4'},custom:{label:'Пользовательские',color:'#2c9b70'},
}

type KmlCoordinate=[number,number,number?]

function coordinatesFromText(value:string):KmlCoordinate[]{
  return value.trim().split(/\s+/).map(item=>item.split(',').slice(0,3).map(Number)).filter(pair=>pair.length>=2&&pair.slice(0,2).every(Number.isFinite)).map(pair=>[pair[0],pair[1],Number.isFinite(pair[2])?pair[2]:undefined])
}

function closeRing(points:[number,number][]):[number,number][]{
  if(!points.length)return points
  const first=points[0],last=points[points.length-1]
  return first[0]===last[0]&&first[1]===last[1]?points:[...points,first]
}

function lineBufferPolygon(coordinates:KmlCoordinate[],halfWidthM=5):GeoJSON.Polygon|null{
  if(coordinates.length<2)return null
  const lon0=coordinates[0][0],lat0=coordinates[0][1]
  const lonScale=111_320*Math.cos(lat0*Math.PI/180),latScale=110_540
  const points=coordinates.map(([lon,lat])=>[(lon-lon0)*lonScale,(lat-lat0)*latScale] as [number,number])
  const normals=points.slice(0,-1).map((point,index)=>{
    const next=points[index+1],dx=next[0]-point[0],dy=next[1]-point[1],length=Math.hypot(dx,dy)||1
    return [-dy/length,dx/length] as [number,number]
  })
  const offsets=points.map((_,index)=>{
    const before=normals[Math.max(0,index-1)],after=normals[Math.min(normals.length-1,index)]
    const x=before[0]+after[0],y=before[1]+after[1],length=Math.hypot(x,y)
    return length>.05?[x/length*halfWidthM,y/length*halfWidthM] as [number,number]:[after[0]*halfWidthM,after[1]*halfWidthM] as [number,number]
  })
  const toWgs=([x,y]:[number,number]):[number,number]=>[lon0+x/lonScale,lat0+y/latScale]
  const left=points.map((point,index)=>toWgs([point[0]+offsets[index][0],point[1]+offsets[index][1]]))
  const right=points.map((point,index)=>toWgs([point[0]-offsets[index][0],point[1]-offsets[index][1]])).reverse()
  return {type:'Polygon',coordinates:[closeRing([...left,...right])]}
}

const obstacleLabels:Record<string,string>={
  'OTHER:COMMUNICATION_TOWER':'Радиомачта',BUILDING:'Здание',VEGETATION:'Растительность',TREE:'Дерево',
  'OTHER:POWER_TRANSMISSION_PYLON':'Опора ЛЭП','OTHER:LIGHT_SUPPORT_STRUCTURE':'Опора освещения',
  'OTHER:CHIMNEY':'Дымовая труба',FENCE:'Ограждение',ANTENNA:'Антенна',SIGN:'Знак',NAVAID:'Радионавигационный объект',
  'OTHER:MAST':'Мачта','OTHER:LIGHTNING_ROD':'Молниеотвод',CRANE:'Кран',POLE:'Столб',TOWER:'Башня',
  CONTROL_TOWER:'Диспетчерская вышка',WATER_TOWER:'Водонапорная башня',LIGHTHOUSE:'Маяк',BRIDGE:'Мост',DAM:'Дамба',
}

function kmlProperties(placemark:Element,index:number,coordinates:KmlCoordinate[],linear=false):Record<string,unknown>{
  const rawName=(placemark.querySelector('name')?.textContent||'').trim().replace(/\s+/g,' ')
  const [code,...typeParts]=rawName.split(' ')
  const obstacleType=typeParts.join(' ')||null
  const label=obstacleType?(obstacleLabels[obstacleType]||obstacleType.replace(/^OTHER:/,'').replaceAll('_',' ').toLocaleLowerCase('ru-RU')):(linear?'Линейное высотное препятствие':'Высотное препятствие')
  const altitudeMode=placemark.querySelector('altitudeMode')?.textContent?.trim()||'clampToGround'
  const altitudes=coordinates.map(item=>item[2]).filter((value):value is number=>Number.isFinite(value))
  const top=altitudes.length?Math.max(...altitudes):null
  const reference=altitudeMode==='relativeToGround'?'AGL':altitudeMode==='absolute'?'AMSL':null
  const heightText=top!==null&&reference?`${top.toLocaleString('ru-RU',{maximumFractionDigits:1})} м ${reference}`:null
  return {
    code:code||`OBST-${index+1}`,
    name:code?`${label} · ${code}`:label,
    kml_name:rawName,
    obstacle_type:obstacleType||'LINEAR_OBSTACLE',
    altitude_mode:altitudeMode,
    lower_limit:'GND',
    upper_limit:heightText,
    official_vertical_definition:heightText?(altitudeMode==='relativeToGround'?`Высота объекта ${heightText}`:`Верхняя абсолютная отметка ${heightText}`):'Высота в исходном KML не указана',
    source_format:'KML',
    linear_obstacle:linear,
    safety_footprint_m:linear?10:undefined,
  }
}

export function importedCollection(fileName:string,text:string):GeoJSON.FeatureCollection{
  const extension=fileName.split('.').pop()?.toLowerCase()
  if(extension==='geojson'||extension==='json'){
    const value=JSON.parse(text) as GeoJSON.GeoJSON
    if(value.type==='FeatureCollection')return value
    if(value.type==='Feature')return {type:'FeatureCollection',features:[value]}
    return {type:'FeatureCollection',features:[{type:'Feature',properties:{name:fileName},geometry:value as GeoJSON.Geometry}]}
  }
  if(extension==='csv'){
    const points=text.split(/\r?\n/).map(row=>row.trim().split(/[;,\t]/).slice(0,2).map(Number)).filter(pair=>pair.length===2&&pair.every(Number.isFinite)) as [number,number][]
    if(!points.length)throw new Error('В CSV не найдены пары «долгота, широта»')
    const geometry:GeoJSON.Point|GeoJSON.Polygon=points.length===1?{type:'Point',coordinates:points[0]}:{type:'Polygon',coordinates:[closeRing(points)]}
    return {type:'FeatureCollection',features:[{type:'Feature',properties:{name:fileName},geometry}]}
  }
  const xml=new DOMParser().parseFromString(text,'application/xml')
  if(xml.querySelector('parsererror'))throw new Error('Некорректный XML-файл')
  const features:GeoJSON.Feature[]=[]
  if(extension==='kml'){
    xml.querySelectorAll('Placemark').forEach((placemark,index)=>{
      const polygon=placemark.querySelector('Polygon coordinates')
      const point=placemark.querySelector('Point coordinates')
      const line=placemark.querySelector('LineString coordinates')
      if(polygon){const coordinates=coordinatesFromText(polygon.textContent||'');const ring=closeRing(coordinates.map(([lon,lat])=>[lon,lat]));if(ring.length>=4)features.push({type:'Feature',properties:kmlProperties(placemark,index,coordinates),geometry:{type:'Polygon',coordinates:[ring]}})}
      else if(point){const coordinates=coordinatesFromText(point.textContent||'');const coordinate=coordinates[0];if(coordinate)features.push({type:'Feature',properties:kmlProperties(placemark,index,coordinates),geometry:{type:'Point',coordinates:[coordinate[0],coordinate[1]]}})}
      else if(line){const coordinates=coordinatesFromText(line.textContent||'');const geometry=lineBufferPolygon(coordinates);if(geometry)features.push({type:'Feature',properties:kmlProperties(placemark,index,coordinates,true),geometry})}
    })
  }else if(extension==='gpx'){
    xml.querySelectorAll('wpt').forEach((point,index)=>{const lon=Number(point.getAttribute('lon')),lat=Number(point.getAttribute('lat'));if(Number.isFinite(lon)&&Number.isFinite(lat))features.push({type:'Feature',properties:{name:point.querySelector('name')?.textContent?.trim()||`Точка ${index+1}`},geometry:{type:'Point',coordinates:[lon,lat]}})})
    ;[...xml.querySelectorAll('trkseg,rte')].forEach((track,index)=>{const points=[...track.querySelectorAll('trkpt,rtept')].map(point=>[Number(point.getAttribute('lon')),Number(point.getAttribute('lat'))] as [number,number]).filter(pair=>pair.every(Number.isFinite));if(points.length>=3)features.push({type:'Feature',properties:{name:track.parentElement?.querySelector('name')?.textContent?.trim()||`Контур ${index+1}`},geometry:{type:'Polygon',coordinates:[closeRing(points)]}})})
  }else throw new Error('Поддерживаются GeoJSON, KML, GPX и CSV')
  if(!features.length)throw new Error(`В файле ${fileName} не найдены точки или замкнутые контуры`)
  return {type:'FeatureCollection',features}
}

function AirspaceMap({data,selected,onSelect,drawing,onPoint,draft}:{
  data:GeoJSON.FeatureCollection<GeoJSON.Geometry,ZoneProperties>;selected:string|null;onSelect:(id:string)=>void;
  drawing:boolean;onPoint:(point:[number,number])=>void;draft:[number,number][];
}){
  const container=useRef<HTMLDivElement>(null)
  const mapRef=useRef<MapLibreMap|null>(null)
  const selectedMarker=useRef<maplibregl.Marker|null>(null)
  const [ready,setReady]=useState(false)
  useEffect(()=>{
    if(!container.current||mapRef.current)return
    const map=new maplibregl.Map({container:container.current,style:'https://tiles.openfreemap.org/styles/liberty',center:[67,61],zoom:2.3,attributionControl:false})
    const resizeObserver=new ResizeObserver(()=>map.resize())
    resizeObserver.observe(container.current)
    mapRef.current=map
    map.addControl(new maplibregl.NavigationControl({showCompass:true}),'top-right')
    map.on('load',()=>{
      const obstacleIcon=()=>{const canvas=document.createElement('canvas');canvas.width=64;canvas.height=64;const context=canvas.getContext('2d')!;context.translate(32,32);context.beginPath();context.arc(0,0,27,0,Math.PI*2);context.fillStyle='#7f57b5';context.strokeStyle='#fff';context.lineWidth=4;context.fill();context.stroke();context.strokeStyle='#fff';context.fillStyle='#fff';context.lineWidth=3;context.lineCap='round';context.lineJoin='round';context.beginPath();context.moveTo(0,-17);context.lineTo(-11,17);context.moveTo(0,-17);context.lineTo(11,17);context.moveTo(-7,7);context.lineTo(7,7);context.moveTo(-4,-3);context.lineTo(4,-3);context.stroke();context.beginPath();context.arc(0,-19,3,0,Math.PI*2);context.fill();return context.getImageData(0,0,64,64)}
      map.addImage('directory-height-obstacle-icon',obstacleIcon(),{pixelRatio:2})
      map.addSource('airspace-zones',{type:'geojson',data:emptyCollection})
      // ATC responsibility zones are deliberately rendered first. Operational
      // restrictions and hazards must remain visible and clickable above them.
      map.addLayer({id:'airspace-orvd-fill',type:'fill',source:'airspace-zones',filter:['all',['==','$type','Polygon'],['==','category','orvd']],paint:{'fill-color':'#2d87c8','fill-opacity':.13}})
      map.addLayer({id:'airspace-orvd-line',type:'line',source:'airspace-zones',filter:['all',['==','$type','Polygon'],['==','category','orvd']],paint:{'line-color':'#1775b4','line-width':2.2,'line-opacity':.9}})
      map.addLayer({id:'airspace-fill',type:'fill',source:'airspace-zones',filter:['all',['==','$type','Polygon'],['!=','category','orvd']],paint:{'fill-color':['match',['get','category'],'prohibited','#e74d35','danger','#ef9e2f','obstacle','#8058b4','#2c9b70'],'fill-opacity':.27}})
      map.addLayer({id:'airspace-line',type:'line',source:'airspace-zones',filter:['all',['==','$type','Polygon'],['!=','category','orvd']],paint:{'line-color':['match',['get','category'],'prohibited','#d93924','danger','#df861b','obstacle','#70439f','#14865c'],'line-width':2.4,'line-opacity':.98}})
      map.addLayer({id:'airspace-points',type:'circle',source:'airspace-zones',filter:['all',['==','$type','Point'],['!=','category','obstacle']],paint:{'circle-radius':6,'circle-color':['match',['get','category'],'prohibited','#e74d35','danger','#ef9e2f','orvd','#2d87c8','#2c9b70'],'circle-stroke-width':2,'circle-stroke-color':'#fff'}})
      map.addLayer({id:'airspace-obstacle-points',type:'symbol',source:'airspace-zones',filter:['all',['==','$type','Point'],['==','category','obstacle']],layout:{'icon-image':'directory-height-obstacle-icon','icon-size':['interpolate',['linear'],['zoom'],3,.62,8,.78,13,1.05],'icon-allow-overlap':true}})
      map.addSource('airspace-selected',{type:'geojson',data:emptyCollection})
      map.addLayer({id:'airspace-selected-fill',type:'fill',source:'airspace-selected',filter:['==','$type','Polygon'],paint:{'fill-color':['match',['get','category'],'prohibited','#f03f25','danger','#f49b22','orvd','#248ed1','obstacle','#8456ba','#21a776'],'fill-opacity':.48}})
      map.addLayer({id:'airspace-selected-glow',type:'line',source:'airspace-selected',filter:['==','$type','Polygon'],paint:{'line-color':'#fff','line-width':7,'line-opacity':.8,'line-blur':2}})
      map.addLayer({id:'airspace-selected-line',type:'line',source:'airspace-selected',filter:['==','$type','Polygon'],paint:{'line-color':['match',['get','category'],'prohibited','#c52f1b','danger','#cb7410','orvd','#116da9','obstacle','#63368e','#0f744d'],'line-width':4,'line-opacity':1}})
      map.addLayer({id:'airspace-selected-point',type:'circle',source:'airspace-selected',filter:['==','$type','Point'],paint:{'circle-radius':10,'circle-color':['match',['get','category'],'prohibited','#f03f25','danger','#f49b22','orvd','#248ed1','obstacle','#8456ba','#21a776'],'circle-stroke-width':4,'circle-stroke-color':'#fff'}})
      map.addSource('airspace-draft',{type:'geojson',data:emptyCollection})
      map.addLayer({id:'airspace-draft-fill',type:'fill',source:'airspace-draft',paint:{'fill-color':'#18acd5','fill-opacity':.18}})
      map.addLayer({id:'airspace-draft-line',type:'line',source:'airspace-draft',paint:{'line-color':'#0483aa','line-width':3,'line-dasharray':[2,1]}})
      map.addLayer({id:'airspace-draft-points',type:'circle',source:'airspace-draft',filter:['==',['geometry-type'],'Point'],paint:{'circle-radius':5,'circle-color':'#fff','circle-stroke-color':'#0483aa','circle-stroke-width':2}})
      const pick=(event:maplibregl.MapMouseEvent)=>{
        const feature=map.queryRenderedFeatures(event.point,{layers:['airspace-obstacle-points','airspace-points','airspace-fill','airspace-orvd-fill']})[0]
        const id=feature?.properties?.id;if(id)onSelect(String(id))
      }
      map.on('click',pick)
      ;['airspace-fill','airspace-points','airspace-obstacle-points','airspace-orvd-fill'].forEach(layer=>{map.on('mouseenter',layer,()=>{map.getCanvas().style.cursor='pointer'});map.on('mouseleave',layer,()=>{map.getCanvas().style.cursor=''})})
      map.resize()
      requestAnimationFrame(()=>map.resize())
      setReady(true)
    })
    return()=>{resizeObserver.disconnect();selectedMarker.current?.remove();map.remove();mapRef.current=null}
  },[onSelect])
  useEffect(()=>{
    const map=mapRef.current
    if(!ready||!map)return
    ;(map.getSource('airspace-zones') as GeoJSONSource)?.setData(data)
    if(selected||!data.features.length)return
    const bounds=new maplibregl.LngLatBounds()
    data.features.forEach(feature=>{const bbox=feature.properties.bbox;if(bbox?.length===4){bounds.extend([bbox[0],bbox[1]]);bounds.extend([bbox[2],bbox[3]])}})
    if(!bounds.isEmpty())requestAnimationFrame(()=>map.fitBounds(bounds,{padding:70,maxZoom:11,duration:550}))
  },[data,ready,selected])
  useEffect(()=>{
    if(!ready)return
    const features:GeoJSON.Feature[] = draft.map((coordinates,index)=>({type:'Feature',properties:{index},geometry:{type:'Point',coordinates}}))
    if(draft.length>=2)features.unshift({type:'Feature',properties:{},geometry:draft.length>=3?{type:'Polygon',coordinates:[[...draft,draft[0]]]}:{type:'LineString',coordinates:draft}})
    ;(mapRef.current?.getSource('airspace-draft') as GeoJSONSource)?.setData({type:'FeatureCollection',features})
  },[draft,ready])
  useEffect(()=>{
    const map=mapRef.current;if(!ready||!map)return
    const feature=data.features.find(item=>item.properties.id===selected)
    ;(map.getSource('airspace-selected') as GeoJSONSource)?.setData(feature?{type:'FeatureCollection',features:[feature]}:emptyCollection)
    selectedMarker.current?.remove();selectedMarker.current=null
    if(!feature)return
    const bbox=feature.properties.bbox
    if(bbox?.length===4){
      const markerElement=document.createElement('div')
      markerElement.className=`airspace-selected-marker ${feature?.properties.category||'custom'}`
      const markerCode=feature.properties.code||feature.properties.name
      const markerName=feature.properties.name!==markerCode?feature.properties.name:categoryMeta[feature.properties.category].label
      const codeElement=document.createElement('strong');codeElement.textContent=markerCode||'Зона'
      const nameElement=document.createElement('span');nameElement.textContent=markerName
      markerElement.append(codeElement,nameElement)
      selectedMarker.current=new maplibregl.Marker({element:markerElement,anchor:'bottom'}).setLngLat([(bbox[0]+bbox[2])/2,(bbox[1]+bbox[3])/2]).addTo(map)
      map.resize()
      requestAnimationFrame(()=>{
        map.resize()
        const pointLike=Math.abs(bbox[2]-bbox[0])<1e-7&&Math.abs(bbox[3]-bbox[1])<1e-7
        if(pointLike)map.easeTo({center:[bbox[0],bbox[1]],zoom:14,duration:650})
        else map.fitBounds([[bbox[0],bbox[1]],[bbox[2],bbox[3]]],{padding:105,maxZoom:13,duration:700})
      })
    }
  },[selected,data,ready])
  useEffect(()=>{
    const map=mapRef.current;if(!map||!ready)return
    const click=(event:maplibregl.MapMouseEvent)=>{if(drawing)onPoint([Number(event.lngLat.lng.toFixed(6)),Number(event.lngLat.lat.toFixed(6))])}
    map.on('click',click);map.getCanvas().style.cursor=drawing?'crosshair':''
    return()=>{map.off('click',click);map.getCanvas().style.cursor=''}
  },[drawing,onPoint,ready])
  return <div ref={container} className="airspace-map"/>
}

export function AirspaceWorkspace({initialCategory='all',embedded=false,searchQuery,createRequest=0,modalOnly=false,autoCreateCategory,onCreateClosed}:{initialCategory?:Category|'all'|'user';embedded?:boolean;searchQuery?:string;createRequest?:number;modalOnly?:boolean;autoCreateCategory?:Category|'settlement';onCreateClosed?:()=>void}={}){
  const [summary,setSummary]=useState<Summary|null>(null)
  const [data,setData]=useState<GeoJSON.FeatureCollection<GeoJSON.Geometry,ZoneProperties>>(emptyCollection)
  const [authorities,setAuthorities]=useState<Authority[]>([])
  const [category,setCategory]=useState<Category|'all'|'user'>(initialCategory)
  const [query,setQuery]=useState('')
  const [selected,setSelected]=useState<string|null>(null)
  const [busy,setBusy]=useState(true)
  const [notice,setNotice]=useState('')
  const [creating,setCreating]=useState(false)
  const [creationMode,setCreationMode]=useState<'manual'|'import'>('manual')
  const [drawing,setDrawing]=useState(false)
  const [draft,setDraft]=useState<[number,number][]>([])
  const [form,setForm]=useState({category:'prohibited' as Category|'settlement',name:'',code:'',lower_limit:'GND',upper_limit:'',schedule:'H24',coordinates:''})
  const fileInput=useRef<HTMLInputElement>(null)
  const lastCreateRequest=useRef(createRequest)
  const hadCreateDialog=useRef(false)

  const refresh=async()=>{
    setBusy(true)
    try{
      const [summaryResponse,zonesResponse,authoritiesResponse]=await Promise.all([
        fetch('/api/airspace/summary',{credentials:'include'}),fetch('/api/airspace/zones?limit=5000',{credentials:'include'}),fetch('/api/airspace/authorities',{credentials:'include'}),
      ])
      if(!summaryResponse.ok||!zonesResponse.ok)throw new Error('API справочника недоступен')
      setSummary(await summaryResponse.json());setData(await zonesResponse.json())
      if(authoritiesResponse.ok)setAuthorities((await authoritiesResponse.json()).items||[])
    }catch(error){setNotice(error instanceof Error?error.message:'Не удалось загрузить зоны')}finally{setBusy(false)}
  }
  useEffect(()=>{void refresh();window.addEventListener('airspace-updated',refresh);return()=>window.removeEventListener('airspace-updated',refresh)},[])
  const visible=useMemo(()=>{
    const needle=(searchQuery??query).trim().toLocaleLowerCase('ru-RU')
    return {...data,features:data.features.filter(feature=>(category==='all'||(category==='user'?feature.properties.editable:feature.properties.category===category))&&(!needle||`${feature.properties.code||''} ${feature.properties.name}`.toLocaleLowerCase('ru-RU').includes(needle)))}
  },[data,category,query,searchQuery])
  const selectedFeature=data.features.find(feature=>feature.properties.id===selected)
  const authority=selectedFeature?.properties.category==='orvd'?authorities.find(item=>item.zone_id===selected):undefined

  const importFile=async(file:File)=>{
    try{
      if(form.category==='settlement'){await importSettlementFile(file);return}
      const collection=importedCollection(file.name,await file.text())
      const kmlObstacleSet=file.name.toLocaleLowerCase('ru-RU').includes('препятств')||collection.features.some(feature=>Boolean(feature.properties?.altitude_mode||feature.properties?.obstacle_type))
      const importCategory:Category=kmlObstacleSet?'obstacle':form.category
      if(importCategory!==form.category)setForm(value=>({...value,category:importCategory}))
      const response=await fetch('/api/airspace/zones/import',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({category:importCategory,source_name:file.name,collection})})
      const body=await response.json().catch(()=>({}))
      if(!response.ok)throw new Error(typeof body.detail==='string'?body.detail:'Импорт отклонён')
      setCreating(false);setNotice(`Импортировано высотных объектов: ${body.imported}${body.skipped?`; пропущено: ${body.skipped}`:''}`);await refresh();window.dispatchEvent(new Event('airspace-updated'))
    }catch(error){setNotice(error instanceof Error?error.message:'Файл не удалось импортировать')}
  }
  const importSettlementFile=async(file:File)=>{
    try{
      const collection=JSON.parse(await file.text()) as GeoJSON.FeatureCollection
      if(collection.type!=='FeatureCollection')throw new Error('Нужен GeoJSON FeatureCollection с полигонами населённых пунктов')
      const response=await fetch(`/api/settlements/import?source_name=${encodeURIComponent(file.name)}`,{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify(collection)})
      const body=await response.json().catch(()=>({}))
      if(!response.ok)throw new Error(typeof body.detail==='string'?body.detail:'Импорт границ отклонён')
      setCreating(false)
      setNotice(`Полигоны населённых пунктов: загружено ${body.imported}, пропущено ${body.skipped}. Полнота покрытия этим импортом не подтверждается; фоновая проверка Overpass продолжится.`)
      window.dispatchEvent(new Event('settlements-updated'))
    }catch(error){setNotice(error instanceof Error?error.message:'Файл границ не удалось импортировать')}
  }
  const saveZone=async()=>{
    try{
      if(form.category==='settlement')throw new Error('Границы населённых пунктов загружаются из GeoJSON.')
      let points=draft
      if(!drawing){points=form.coordinates.split(/\r?\n/).filter(Boolean).map((row,index)=>{const pair=row.trim().split(/[;,\s]+/).map(Number);if(pair.length<2||pair.some(value=>!Number.isFinite(value)))throw new Error(`Строка ${index+1}: нужны долгота и широта`);return [pair[0],pair[1]] as [number,number]})}
      if(points.length<3)throw new Error('Нужно не менее трёх вершин')
      const geometry:GeoJSON.Polygon={type:'Polygon',coordinates:[[...points,points[0]]]}
      const response=await fetch('/api/airspace/zones',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({...form,geometry,properties:{input_mode:drawing?'map':'coordinates'}})})
      const body=await response.json().catch(()=>({}))
      if(!response.ok)throw new Error(body.detail||'Не удалось сохранить зону')
      setCreating(false);setDrawing(false);setDraft([]);setForm(value=>({...value,name:'',code:'',coordinates:''}));setNotice('Зона добавлена в справочник.');await refresh();setSelected(body.properties.id);window.dispatchEvent(new Event('airspace-updated'))
    }catch(error){setNotice(error instanceof Error?error.message:'Не удалось сохранить зону')}
  }
  const deleteSelected=async()=>{
    if(!selectedFeature?.properties.editable||!confirm(`Удалить «${selectedFeature.properties.name}»?`))return
    const response=await fetch(`/api/airspace/zones/${selectedFeature.properties.id}`,{method:'DELETE',credentials:'include'})
    if(response.ok){setSelected(null);setNotice('Пользовательская зона удалена.');await refresh();window.dispatchEvent(new Event('airspace-updated'))}else setNotice('Удаление не выполнено.')
  }
  const openCreate=()=>{
    setForm(value=>({...value,category:category==='all'||category==='user'?'custom':category}))
    setCreationMode('manual')
    setCreating(true)
  }
  useEffect(()=>{
    if(createRequest===lastCreateRequest.current)return
    lastCreateRequest.current=createRequest
    openCreate()
  },[createRequest])
  useEffect(()=>{
    if(!autoCreateCategory)return
    setForm(value=>({...value,category:autoCreateCategory}))
    setCreationMode(autoCreateCategory==='settlement'?'import':'manual')
    setCreating(true)
  },[autoCreateCategory])
  useEffect(()=>{
    if(creating)hadCreateDialog.current=true
    else if(hadCreateDialog.current&&modalOnly)onCreateClosed?.()
  },[creating,modalOnly,onCreateClosed])

  return <section className={`airspace-workspace${modalOnly?' modal-only':''}`} aria-label="Справочник воздушного пространства">
    {!modalOnly&&!embedded&&<header className="airspace-title"><div><span>БЕЗОПАСНОСТЬ И СОГЛАСОВАНИЯ</span><h1>Воздушное пространство</h1><p>Нормативные зоны, ограничения по высоте и времени, зоны ответственности ОрВД и пользовательские препятствия.</p></div><div><button className="primary" onClick={openCreate}><Plus size={17}/> Добавить</button></div></header>}
    {!modalOnly&&!embedded&&<div className="airspace-kpis">
      <article><ShieldAlert/><small>Запретные зоны</small><strong>{summary?.counts.prohibited??'—'}</strong><span>нормативный набор 2026</span></article>
      <article><AlertTriangle/><small>Опасные зоны</small><strong>{summary?.counts.danger??'—'}</strong><span>сведения 2022 года</span></article>
      <article><TowerControl/><small>Зоны ОрВД</small><strong>{summary?.counts.orvd??'—'}</strong><span>{authorities.filter(item=>item.contact_status==='verified').length} контактов заполнено</span></article>
      <article><MapPin/><small>Высотные объекты</small><strong>{summary?.counts.obstacle??'—'}</strong><span>препятствия и высотные ограничения</span></article>
      <article><Plus/><small>Пользовательские</small><strong>{summary?.counts.custom??'—'}</strong><span>добавлено оператором</span></article>
    </div>}
    {!modalOnly&&!embedded&&<div className="airspace-toolbar"><div className="airspace-filters"><button className={category==='all'?'active':''} onClick={()=>setCategory('all')}>Все <b>{summary?.total??0}</b></button>{(Object.keys(categoryMeta) as Category[]).map(id=><button key={id} className={category===id?'active':''} onClick={()=>setCategory(id)}><i style={{background:categoryMeta[id].color}}/>{categoryMeta[id].label}<b>{summary?.counts[id]??0}</b></button>)}</div><label><Search size={15}/><input value={query} onChange={event=>setQuery(event.target.value)} placeholder="Код или название объекта"/></label></div>}
    {!modalOnly&&<div className="airspace-layout">
      <aside className="airspace-list"><header><strong>Объекты</strong><span>{busy?'обновление…':`${visible.features.length} показано`}</span></header><div>{visible.features.slice(0,300).map(feature=><button key={feature.properties.id} className={selected===feature.properties.id?'selected':''} onClick={()=>setSelected(feature.properties.id)}><i style={{background:categoryMeta[feature.properties.category].color}}/><span><b>{feature.properties.code||feature.properties.name}</b><small>{feature.properties.code?feature.properties.name:categoryMeta[feature.properties.category].label}</small></span></button>)}</div></aside>
      <AirspaceMap data={visible} selected={selected} onSelect={setSelected} drawing={drawing} onPoint={point=>setDraft(points=>[...points,point])} draft={draft}/>
      <aside className="airspace-detail">{selectedFeature?<><header><div><small>{categoryMeta[selectedFeature.properties.category].label.toUpperCase()}</small><h2>{selectedFeature.properties.code||selectedFeature.properties.name}</h2><p>{selectedFeature.properties.code&&selectedFeature.properties.name}</p></div><button className="airspace-detail-close" title="Снять выбор" aria-label="Снять выбор" onClick={()=>setSelected(null)}><X size={15}/><span>Закрыть</span></button></header><dl><dt>Нижняя граница</dt><dd>{selectedFeature.properties.lower_limit||selectedFeature.properties.vertical_definition||'по источнику'}</dd><dt>Верхняя граница</dt><dd>{selectedFeature.properties.upper_limit||selectedFeature.properties.vertical_definition||'по источнику'}</dd><dt>Время действия</dt><dd>{selectedFeature.properties.schedule||'по нормативному источнику'}</dd><dt>Источник</dt><dd>{selectedFeature.properties.source_name}</dd></dl>{selectedFeature.properties.category==='orvd'&&<section className={`authority-status ${authority?.contact_status||'missing'}`}><TowerControl size={18}/><div><strong>{authority?.contact_status==='verified'?'Контакт ОрВД заполнен':'Контакт требует заполнения'}</strong><small>{authority?.contact?.organization||'География ответственности загружена; контактные сведения не подменяются предположениями.'}</small></div></section>}{selectedFeature.properties.editable&&<button className="delete-zone" onClick={()=>void deleteSelected()}><Trash2 size={15}/> Удалить пользовательский объект</button>}</>:<div className="airspace-detail-empty"><MapPin size={28}/><strong>Выберите объект</strong><span>Карточка покажет высотные и временные ограничения, источник и состояние контактов ОрВД.</span></div>}</aside>
    </div>}
    {creating&&!drawing&&<div className="airspace-modal-backdrop">
      <section className="airspace-modal" role="dialog" aria-modal="true" aria-label="Новый объект справочника">
        <header>
          <div><span>КАРТОЧКА ОБЪЕКТА</span><h2>Новый объект</h2></div>
          <button type="button" className="airspace-modal-close" aria-label="Закрыть форму" title="Закрыть" onClick={()=>{setCreating(false);setDrawing(false);setDraft([])}}><X size={20}/></button>
        </header>
        <label className="airspace-object-type">Тип объекта
          <select value={form.category} onChange={event=>{const next=event.target.value as Category|'settlement';setForm(value=>({...value,category:next}));if(next==='settlement')setCreationMode('import')}}>
            {Object.entries(categoryMeta).map(([id,item])=><option key={id} value={id}>{item.label}</option>)}
            <option value="settlement">Населённые пункты</option>
          </select>
        </label>
        {form.category!=='settlement'&&<div className="airspace-create-mode">
          <button type="button" className={creationMode==='manual'?'active':''} onClick={()=>setCreationMode('manual')}>Ручной ввод</button>
          <button type="button" className={creationMode==='import'?'active':''} onClick={()=>setCreationMode('import')}><FileUp size={15}/> Импорт из файла</button>
        </div>}
        <div className="airspace-form">
          {form.category==='settlement'||creationMode==='import'?<div className="wide airspace-import-box">
            <FileUp size={28}/>
            <strong>{form.category==='settlement'?'Границы населённых пунктов · GeoJSON':'GeoJSON, KML, GPX или CSV'}</strong>
            <span>{form.category==='settlement'?'Загрузите FeatureCollection с полигонами из QGIS. Покрытие не считается проверенным до фоновой сверки с OSM.':'KML сохраняет высоты AGL/AMSL; линейные препятствия превращаются в защитный коридор.'}</span>
            <button type="button" onClick={()=>fileInput.current?.click()}>Выбрать файл</button>
            <input ref={fileInput} hidden type="file" accept={form.category==='settlement'?'.geojson,.json,application/geo+json,application/json':'.geojson,.json,.kml,.gpx,.csv,application/geo+json,application/json,application/vnd.google-earth.kml+xml,application/gpx+xml,text/csv'} onChange={event=>{const file=event.target.files?.[0];if(file)void importFile(file);event.currentTarget.value=''}}/>
          </div>:<>
            <label className="airspace-field-name">Название<input value={form.name} onChange={event=>setForm(value=>({...value,name:event.target.value}))}/></label>
            <label>Код<input value={form.code} onChange={event=>setForm(value=>({...value,code:event.target.value}))}/></label>
            <label>Нижняя граница<input value={form.lower_limit} onChange={event=>setForm(value=>({...value,lower_limit:event.target.value}))}/></label>
            <label>Верхняя граница<input value={form.upper_limit} onChange={event=>setForm(value=>({...value,upper_limit:event.target.value}))}/></label>
            <label>Время действия (справочно)<input value={form.schedule} maxLength={1000} aria-describedby="airspace-schedule-hint" onChange={event=>setForm(value=>({...value,schedule:event.target.value}))}/></label>
            <p className="wide airspace-schedule-hint" id="airspace-schedule-hint">H24 — круглосуточно. Другой режим запишите текстом, например «ПН–ПТ 09:00–18:00 МСК». Пока расчёт не разбирает эту запись и считает объект действующим постоянно.</p>
            <div className="wide airspace-input-mode"><button type="button" className="active">Пары координат</button><button type="button" onClick={()=>setDrawing(true)}>Рисование на карте</button></div>
            <label className="wide airspace-coordinates">Координаты — одна пара на строку<textarea rows={3} placeholder={'37.6000, 55.7500\n37.6200, 55.7500\n37.6200, 55.7600'} value={form.coordinates} onChange={event=>setForm(value=>({...value,coordinates:event.target.value}))}/></label>
          </>}
        </div>
        <footer><span>{form.category==='settlement'?'Координаты WGS 84 · Импорт из QGIS не подтверждает полноту покрытия OSM.':'Координаты WGS 84 · Созданные объекты можно удалить из справочника.'}</span>{form.category!=='settlement'&&creationMode==='manual'&&<button type="button" disabled={!form.name.trim()} onClick={()=>void saveZone()}><Check size={16}/> Сохранить</button>}</footer>
      </section>
    </div>}
    {creating&&drawing&&<div className="airspace-drawing-bar"><MapPin size={16}/><span>Укажите вершины на карте · {draft.length} точек</span><button onClick={()=>setDraft(points=>points.slice(0,-1))}>Отменить точку</button><button onClick={()=>setDrawing(false)}>К форме</button><button className="primary" disabled={!form.name.trim()||draft.length<3} onClick={()=>void saveZone()}>Сохранить</button></div>}
    {notice&&<button className="airspace-notice" onClick={()=>setNotice('')}><Check size={15}/>{notice}</button>}
  </section>
}
