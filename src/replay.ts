import type * as GeoJSON from 'geojson'
import type { ReplayMissionData, ScheduledMission } from './scheduleData'

export type ReplaySample = {
  lon:number;lat:number;heading:number;progress:number;phase:string;phaseLabel:string;
  altitude:number;speed:number;battery:number;verticalSpeed:number;roll:number;pitch:number;
}

const radians=(value:number)=>value*Math.PI/180
const distance=([aLon,aLat]:GeoJSON.Position,[bLon,bLat]:GeoJSON.Position)=>{
  const mean=radians((aLat+bLat)/2)
  const x=(bLon-aLon)*111_320*Math.cos(mean),y=(bLat-aLat)*110_540
  return Math.hypot(x,y)
}

const phaseBands=[
  {end:.08,phase:'takeoff',label:'Взлёт и набор высоты',factor:.48},
  {end:.28,phase:'transit',label:'Перелёт к объекту',factor:1},
  {end:.72,phase:'surveying',label:'Выполнение съёмки',factor:.72},
  {end:.92,phase:'returning',label:'Возврат на площадку',factor:1},
  {end:1,phase:'landing',label:'Заход и посадка',factor:.48},
] as const

export function replayDurationSeconds(data:ReplayMissionData):number{
  const points=data.route.coordinates
  const metres=points.slice(1).reduce((sum,point,index)=>sum+distance(points[index],point),0)
  const cruise=data.uavType==='fixed_wing'?84:36
  let start=0
  const seconds=phaseBands.reduce((sum,band)=>{
    const span=band.end-start;start=band.end
    return sum+metres*span/(Math.max(12,cruise*band.factor)/3.6)
  },0)
  return Math.max(1,seconds)
}

export function replayPhaseTimeline(data:ReplayMissionData){
  const metres=data.route.coordinates.slice(1).reduce((sum,point,index)=>sum+distance(data.route.coordinates[index],point),0)
  const cruise=data.uavType==='fixed_wing'?84:36
  let start=0
  return phaseBands.map(band=>{
    const seconds=metres*(band.end-start)/(Math.max(12,cruise*band.factor)/3.6)
    start=band.end
    return{phase:band.phase,label:band.label,seconds}
  })
}

function motionAt(data:ReplayMissionData,cursorSeconds:number){
  const points=data.route.coordinates
  const metres=points.slice(1).reduce((sum,point,index)=>sum+distance(points[index],point),0)
  const cruise=data.uavType==='fixed_wing'?84:36
  let elapsed=Math.max(0,Math.min(replayDurationSeconds(data),cursorSeconds)),start=0
  for(const band of phaseBands){
    const speed=Math.max(12,cruise*band.factor)
    const seconds=metres*(band.end-start)/(speed/3.6)
    if(elapsed<=seconds||band.end===1)return{progress:Math.min(1,start+(seconds?elapsed/seconds:1)*(band.end-start)),phase:band.phase,label:band.label,speed}
    elapsed-=seconds;start=band.end
  }
  return{progress:1,phase:'landing',label:'Заход и посадка',speed:12}
}

function routeSample(route:GeoJSON.LineString,progress:number){
  const points=route.coordinates
  const lengths=points.slice(1).map((point,index)=>distance(points[index],point))
  const total=lengths.reduce((sum,value)=>sum+value,0)
  let remaining=Math.max(0,Math.min(1,progress))*total
  for(let index=0;index<lengths.length;index+=1){
    const length=lengths[index]
    if(remaining<=length||index===lengths.length-1){
      const ratio=length?Math.min(1,remaining/length):0
      const start=points[index],end=points[index+1]
      const lon=start[0]+(end[0]-start[0])*ratio,lat=start[1]+(end[1]-start[1])*ratio
      const heading=(Math.atan2((end[0]-start[0])*Math.cos(radians(lat)),end[1]-start[1])*180/Math.PI+360)%360
      return{lon,lat,heading,total}
    }
    remaining-=length
  }
  const fallback=points[0]||[0,0]
  return{lon:fallback[0],lat:fallback[1],heading:0,total}
}

export function sampleReplay(data:ReplayMissionData,cursorSeconds:number):ReplaySample{
  const motion=motionAt(data,cursorSeconds),progress=motion.progress
  const position=routeSample(data.route,progress)
  const next=routeSample(data.route,Math.min(1,progress+.002))
  const climb=Math.min(1,progress/.08),descent=Math.min(1,(1-progress)/.08)
  const altitude=data.altitudeM*Math.min(climb,descent)
  const nextProgress=Math.min(1,progress+.002),nextClimb=Math.min(1,nextProgress/.08),nextDescent=Math.min(1,(1-nextProgress)/.08)
  const nextAltitude=data.altitudeM*Math.min(nextClimb,nextDescent)
  const turn=((next.heading-position.heading+540)%360)-180
  const roll=Math.max(data.uavType==='fixed_wing'?-30:-18,Math.min(data.uavType==='fixed_wing'?30:18,turn*1.8))
  return{...position,progress,phase:motion.phase,phaseLabel:motion.label,altitude,speed:motion.speed,battery:96-progress*57,verticalSpeed:(nextAltitude-altitude)/(replayDurationSeconds(data)*.002),roll,pitch:Math.max(-9,Math.min(9,(nextAltitude-altitude)*.35))}
}

export function replayTrail(route:GeoJSON.LineString,progress:number):GeoJSON.LineString{
  const points=route.coordinates
  if(points.length<2)return route
  const lengths=points.slice(1).map((point,index)=>distance(points[index],point))
  const total=lengths.reduce((sum,value)=>sum+value,0),target=Math.max(0,Math.min(1,progress))*total
  const result:GeoJSON.Position[]=[points[0]]
  let covered=0
  for(let index=0;index<lengths.length;index+=1){
    const length=lengths[index]
    if(covered+length<=target){result.push(points[index+1]);covered+=length;continue}
    const ratio=length?Math.max(0,Math.min(1,(target-covered)/length)):0
    const start=points[index],end=points[index+1]
    result.push([start[0]+(end[0]-start[0])*ratio,start[1]+(end[1]-start[1])*ratio])
    break
  }
  if(result.length<2)result.push([...points[0]])
  return{type:'LineString',coordinates:result}
}

export function replayTelemetry(mission:ScheduledMission,cursorSeconds:number,rate:number){
  const data=mission.simulation!
  const sample=sampleReplay(data,cursorSeconds)
  const duration=replayDurationSeconds(data),remaining=Math.max(0,duration-cursorSeconds)
  return{
    uav_id:mission.uavId,name:mission.uavName,lat:sample.lat,lon:sample.lon,altitude_m:Number(sample.altitude.toFixed(1)),
    speed_kmh:Number(sample.speed.toFixed(1)),heading_deg:Number(sample.heading.toFixed(1)),yaw_deg:Number(sample.heading.toFixed(1)),
    pitch_deg:Number(sample.pitch.toFixed(1)),roll_deg:Number(sample.roll.toFixed(1)),vertical_speed_mps:Number(sample.verticalSpeed.toFixed(1)),
    battery_percent:Number(sample.battery.toFixed(1)),link_quality_percent:96,satellites:22,mission_progress_percent:Number((sample.progress*100).toFixed(1)),
    status:sample.phase,source:'mission-replay',uav_type:data.uavType,task_name:mission.title,color:data.color,phase_label:sample.phaseLabel,
    compliance_note:data.reconstructed?'Реконструкция по плану и журналу задания':'Записанный расчётный маршрут · контроль безопасных дистанций',base_name:data.baseName,next_landing_seconds:remaining,
    next_takeoff_seconds:sample.progress===0?0:duration,simulation_rate:rate,
  }
}
