import type * as GeoJSON from 'geojson'

export type MissionStatus = 'completed' | 'active' | 'planned' | 'maintenance'

export type ReplayMissionData = {
  missionId:string;planId:string;route:GeoJSON.LineString;area?:GeoJSON.Polygon;
  uavType:'fixed_wing'|'multirotor';altitudeM:number;color:string;baseName:string;
  durationSeconds:number;actualDurationMin:number;
  settlementCoverage?:'COVERED'|'PARTIALLY_COVERED'|'NOT_COVERED';
  reconstructed?:boolean;
}

export type ScheduledMission = {
  id: string
  date: string
  time: string
  title: string
  location: string
  uavId: string
  uavName: string
  duration: number
  status: MissionStatus
  product: string
  payloadId?: string
  areaKm2?: number
  costRub?: number
  source?: 'demo' | 'planner'
  orderId?: string
  orderNumber?: string
  customerName?: string
  missionGroupId?: string
  simulation?: ReplayMissionData
  demoStartedAt?: string
  demoCompletedAt?: string
}

export type FleetResourceSnapshot = {
  airframe:number
  propulsion:number
  power:number
  plannedHours:number
  projectedRemainingHours:number
  nextFlight?:ScheduledMission
  nextMaintenance?:ScheduledMission
  recommendedMaintenanceDate:string
  risk:'normal'|'attention'|'critical'
}

export function fleetResourceSnapshot(uav:{id:string;maintenance_due_hours:number},missions:ScheduledMission[],now=new Date()):FleetResourceSnapshot{
  const nowMs=now.getTime()
  const future=missions.filter(mission=>mission.uavId===uav.id&&slotStart(mission.date,mission.time)>=nowMs).sort((a,b)=>slotStart(a.date,a.time)-slotStart(b.date,b.time))
  const futureFlights=future.filter(mission=>mission.status!=='maintenance')
  const plannedHours=futureFlights.reduce((sum,mission)=>sum+mission.duration,0)
  const projectedRemainingHours=Number((uav.maintenance_due_hours-plannedHours).toFixed(1))
  const idSeed=[...uav.id].reduce((sum,char)=>sum+char.charCodeAt(0),0)
  const base=Math.max(8,Math.min(92,Math.round(100-uav.maintenance_due_hours*.76+plannedHours*.65)))
  const airframe=Math.max(7,Math.min(96,base-5+(idSeed%7)))
  const propulsion=Math.max(8,Math.min(97,base+3+(idSeed%5)))
  const power=Math.max(9,Math.min(98,base+7-(idSeed%4)))
  const dailyRate=Math.max(.65,plannedHours/21)
  const forecast=new Date(now);forecast.setDate(forecast.getDate()+Math.max(1,Math.round(Math.max(0,projectedRemainingHours)/dailyRate)))
  return{
    airframe,propulsion,power,plannedHours,projectedRemainingHours,
    nextFlight:futureFlights[0],nextMaintenance:future.find(mission=>mission.status==='maintenance'),
    recommendedMaintenanceDate:localDateIso(forecast),
    risk:projectedRemainingHours<=0?'critical':projectedRemainingHours<=15?'attention':'normal',
  }
}

export const initialScheduledMissions: ScheduledMission[] = [
  { id:'m-0818',date:'2026-08-18',time:'08:30',title:'Ортофотоплан · квартал 17',location:'Хорошёво-Мнёвники',uavId:'uav-geoscan-201-01',uavName:'Геоскан 201 · 01',duration:2.8,status:'completed',product:'RGB / ортофото',payloadId:'payload-sony-61',areaKm2:2.1,costRub:28600,source:'demo' },
  { id:'m-0826',date:'2026-08-26',time:'10:00',title:'Тепловая инспекция кровель',location:'Покровское-Стрешнево',uavId:'uav-geoscan-201-02',uavName:'Геоскан 201 · 02',duration:1.4,status:'completed',product:'Тепловая карта',payloadId:'payload-thermal-814',areaKm2:.7,costRub:15400,source:'demo' },
  { id:'m-0902',date:'2026-09-02',time:'07:40',title:'Контроль строительной площадки',location:'Тушино',uavId:'uav-geoscan-gemini-01',uavName:'Геоскан Gemini · 01',duration:1.9,status:'completed',product:'3D-модель',payloadId:'payload-gemini-rgb',areaKm2:.35,costRub:12900,source:'demo' },
  { id:'m-0905',date:'2026-09-05',time:'09:10',title:'Коридорная съёмка · участок D',location:'Крылатское',uavId:'uav-geoscan-201-01',uavName:'Геоскан 201 · 01',duration:3.2,status:'completed',product:'Ортофотоплан',payloadId:'payload-sony-61',areaKm2:3.8,costRub:33400,source:'demo' },
  { id:'m-0911',date:'2026-09-11',time:'10:30',title:'Мониторинг городской застройки',location:'Строгино',uavId:'uav-geoscan-701-01',uavName:'Геоскан 701 · 01',duration:4.6,status:'active',product:'Ортофото + 3D',payloadId:'payload-sony-61',areaKm2:8.6,costRub:82100,source:'demo' },
  { id:'m-0912',date:'2026-09-12',time:'08:00',title:'Диагностика теплотрассы',location:'Щукино',uavId:'uav-geoscan-201-02',uavName:'Геоскан 201 · 02',duration:2.1,status:'planned',product:'Тепловая карта',payloadId:'payload-thermal-814',areaKm2:1.2,costRub:21800,source:'demo' },
  { id:'m-0915',date:'2026-09-15',time:'09:00',title:'Плановое ТО силовой установки',location:'База Север',uavId:'uav-geoscan-201-01',uavName:'Геоскан 201 · 01',duration:4,status:'maintenance',product:'Техническое обслуживание',source:'demo' },

  // 18 сентября, 09:00–13:00: все четыре носителя Sony ILX-LR1 заняты.
  { id:'m-0918-a',date:'2026-09-18',time:'09:00',title:'Ортофото · промзона Север',location:'Химки',uavId:'uav-geoscan-201-01',uavName:'Геоскан 201 · 01',duration:4,status:'planned',product:'Ортофотоплан',payloadId:'payload-sony-61',areaKm2:4.7,costRub:41200,source:'demo' },
  { id:'m-0918-b',date:'2026-09-18',time:'09:00',title:'Цифровой двойник · технопарк',location:'Сколково',uavId:'uav-geoscan-201-02',uavName:'Геоскан 201 · 02',duration:4,status:'planned',product:'3D-модель',payloadId:'payload-sony-61',areaKm2:2.9,costRub:43800,source:'demo' },
  { id:'m-0918-c',date:'2026-09-18',time:'09:00',title:'Коридорная съёмка · М-11',location:'Солнечногорск',uavId:'uav-geoscan-701-01',uavName:'Геоскан 701 · 01',duration:4,status:'planned',product:'Отчёт по ЛЭП',payloadId:'payload-sony-61',areaKm2:7.4,costRub:71400,source:'demo' },
  { id:'m-0918-d',date:'2026-09-18',time:'09:00',title:'Фасады и кровля · квартал 8',location:'Митино',uavId:'uav-geoscan-401-geo',uavName:'Геоскан 401 · Геодезия',duration:4,status:'planned',product:'3D-модель',payloadId:'payload-sony-61',areaKm2:.9,costRub:36500,source:'demo' },
  { id:'m-0918-e',date:'2026-09-18',time:'08:00',title:'NDVI · опытные поля',location:'Красногорский район',uavId:'uav-geoscan-gemini-ms',uavName:'Геоскан Gemini · Мультиспектр',duration:2.7,status:'planned',product:'Мультиспектр / NDVI',payloadId:'payload-pollux',areaKm2:1.6,costRub:20100,source:'demo' },

  { id:'m-0922',date:'2026-09-22',time:'11:00',title:'Обмер фасадов комплекса',location:'Пресненский район',uavId:'uav-geoscan-gemini-01',uavName:'Геоскан Gemini · 01',duration:1.6,status:'planned',product:'3D-модель',payloadId:'payload-gemini-rgb',areaKm2:.25,costRub:10900,source:'demo' },
  // 24 сентября: оба борта, совместимых с тепловизором, заняты в одном окне.
  { id:'m-0924-a',date:'2026-09-24',time:'10:00',title:'Тепловая диагностика · корпус А',location:'Зеленоград',uavId:'uav-geoscan-201-01',uavName:'Геоскан 201 · 01',duration:3,status:'planned',product:'Тепловая карта',payloadId:'payload-thermal-814',areaKm2:1.1,costRub:28700,source:'demo' },
  { id:'m-0924-b',date:'2026-09-24',time:'10:00',title:'Тепловая диагностика · корпус Б',location:'Зеленоград',uavId:'uav-geoscan-201-02',uavName:'Геоскан 201 · 02',duration:3,status:'planned',product:'Тепловая карта',payloadId:'payload-thermal-814',areaKm2:1.0,costRub:27900,source:'demo' },
  { id:'m-0925',date:'2026-09-25',time:'07:30',title:'Аэромагнитная съёмка',location:'Новая Москва',uavId:'uav-geoscan-401-mag',uavName:'Геоскан 401 · Геофизика',duration:3.4,status:'planned',product:'Магнитное поле',payloadId:'payload-quantum-mag',areaKm2:1.8,costRub:45100,source:'demo' },
  { id:'m-0928',date:'2026-09-28',time:'08:20',title:'ЛЭП · повторный облёт',location:'Новая Москва',uavId:'uav-geoscan-701-01',uavName:'Геоскан 701 · 01',duration:5.1,status:'planned',product:'ЛЭП / ортофото',payloadId:'payload-sony-61',areaKm2:9.2,costRub:89700,source:'demo' },
  { id:'m-1003',date:'2026-10-03',time:'09:30',title:'Лазерное сканирование карьера',location:'Домодедовский район',uavId:'uav-geoscan-401-lidar',uavName:'Геоскан 401 · Лидар',duration:3.5,status:'planned',product:'LiDAR',payloadId:'payload-agm-lidar',areaKm2:1.4,costRub:46800,source:'demo' },
  { id:'m-1014',date:'2026-10-14',time:'08:45',title:'Инвентаризация земель',location:'Подольский район',uavId:'uav-geoscan-201-02',uavName:'Геоскан 201 · 02',duration:3.8,status:'planned',product:'Ортофотоплан',payloadId:'payload-sony-61',areaKm2:5.1,costRub:39200,source:'demo' },
]

export function localDateIso(date = new Date()) {
  return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`
}

export function slotStart(date: string, time: string) {
  const [year,month,day] = date.split('-').map(Number)
  const [hour,minute] = time.split(':').map(Number)
  return new Date(year,month-1,day,hour,minute).getTime()
}

export function overlapsSlot(mission: ScheduledMission, date: string, time: string, durationHours: number) {
  const requestedStart = slotStart(date,time)
  const requestedEnd = requestedStart + durationHours * 3_600_000
  const missionStart = slotStart(mission.date,mission.time)
  const missionEnd = missionStart + mission.duration * 3_600_000
  return requestedStart < missionEnd && requestedEnd > missionStart
}

export function availableUavIds(candidateIds:string[], missions:ScheduledMission[], date:string, time:string, durationHours:number) {
  return candidateIds.filter(id=>!missions.some(mission=>mission.uavId===id&&mission.status!=='completed'&&overlapsSlot(mission,date,time,durationHours)))
}
