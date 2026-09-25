import { useCallback, useEffect, useRef, useState } from 'react'
import maplibregl, { type GeoJSONSource, type Map as MapLibreMap } from 'maplibre-gl'
import type * as GeoJSON from 'geojson'
import { PolygonEditor, type MissionGeometry } from './PolygonEditor'
import { installSettlementMapLayer, setSettlementMapVisibility } from './settlementMapLayer'
import { CalendarDashboard, type MaintenanceDraft, type PlannerCalendarMode } from './CalendarDashboard'
import { MissionExportActions } from './MissionExportActions'
import { OrdersWorkspace, type CustomerOrder } from './OrdersWorkspace'
import { ObjectsWorkspace } from './ObjectsWorkspace'
import type { LaunchSite } from './LaunchSitesWorkspace'
import { AttitudeIndicator } from './AttitudeIndicator'
import { HeadingIndicator } from './HeadingIndicator'
import { availableUavIds, fleetResourceSnapshot, initialScheduledMissions, localDateIso, slotStart, type ScheduledMission } from './scheduleData'
import { replayTelemetry, replayTrail, sampleReplay } from './replay'
import './revision.css'
import { payloadReferences, technologyContent } from './catalogContent'
import {
  Activity,
  Aperture,
  BatteryMedium,
  Box,
  Camera,
  CalendarClock,
  CalendarDays,
  Check,
  ChevronDown,
  CircleUserRound,
  CloudSun,
  ClipboardList,
  Command,
  Database,
  Download,
  Droplets,
  Eye,
  Gauge,
  Grid3X3,
  CircleHelp,
  KeyRound,
  LogOut,
  Magnet,
  Map as MapIcon,
  MapPin,
  Navigation,
  Pause,
  Plane,
  Play,
  Radio,
  Ruler,
  RotateCcw,
  Route,
  ScanLine,
  Signal,
  SlidersHorizontal,
  ShieldCheck,
  ShieldAlert,
  Sparkles,
  Thermometer,
  Wind,
  Wrench,
  X,
  Zap,
} from 'lucide-react'

const initialMissionArea: GeoJSON.Feature<GeoJSON.Polygon> = {
  type: 'Feature' as const,
  properties: {},
  geometry: {
    type: 'Polygon' as const,
    coordinates: [[[37.335,55.787],[37.358,55.787],[37.358,55.777],[37.335,55.777],[37.335,55.787]]],
  },
}

const fleetEconomicsDemoArea: GeoJSON.Feature<GeoJSON.Polygon> = {
  type:'Feature', properties:{scenario:'fleet-economics-demo'},
  geometry:{type:'Polygon',coordinates:[[[37.21,55.97],[37.28,55.97],[37.29,55.93],[37.21,55.93],[37.21,55.97]]]},
}

const initialMissionCorridor: GeoJSON.Feature<GeoJSON.LineString> = {
  type: 'Feature', properties: {}, geometry: {
    type: 'LineString', coordinates: [[37.455,55.786],[37.462,55.783],[37.470,55.784],[37.480,55.778]],
  },
}

const bases = {
  type: 'FeatureCollection' as const,
  features: [
    { type: 'Feature' as const, properties: { name: 'Полевой аэродром Клин', kind: 'ВПП', surface: 'подготовленный грунт', runway: '420 м', heading: '064° / 244°', support: 'зарядка · связь · метеопост', status: 'Готов к приёму' }, geometry: { type: 'Point' as const, coordinates: [36.72, 56.24] } },
    { type: 'Feature' as const, properties: { name: 'Площадка Яхрома', kind: 'VTOL-площадка', surface: 'бетон', runway: '40 × 40 м', heading: 'вертикальный взлёт', support: 'зарядка · RTK', status: 'Готова к приёму' }, geometry: { type: 'Point' as const, coordinates: [36.95, 56.35] } },
    { type: 'Feature' as const, properties: { name: 'Аэродром Дмитров', kind: 'ВПП', surface: 'асфальт', runway: '680 м', heading: '082° / 262°', support: 'ангар · топливо · зарядка · RTK', status: 'Готов к приёму' }, geometry: { type: 'Point' as const, coordinates: [37.35, 56.25] } },
    { type: 'Feature' as const, properties: { name: 'Площадка Сергиев Посад', kind: 'смешанная', surface: 'полевой старт', runway: '310 м', heading: '118° / 298°', support: 'мобильный пункт управления', status: 'Готова к приёму' }, geometry: { type: 'Point' as const, coordinates: [37.55, 56.27] } },
  ],
}

const centerLaunchSites:LaunchSite[] = [
  {id:'base-klin',name:'Полевой аэродром Клин',kind:'runway',lat:56.24,lon:36.72,surface:'подготовленный грунт',runway_length_m:420,heading_deg:64,supports:['fixed_wing','multirotor','vtol'],has_charging:true,has_fuel:false,status:'open',notes:'зарядка · связь · метеопост',editable:true},
  {id:'base-yakhroma',name:'Площадка Яхрома',kind:'vtol',lat:56.35,lon:36.95,surface:'бетон',runway_length_m:40,heading_deg:0,supports:['multirotor','vtol'],has_charging:true,has_fuel:false,status:'open',notes:'зарядка · RTK',editable:true},
  {id:'base-dmitrov',name:'Аэродром Дмитров',kind:'runway',lat:56.25,lon:37.35,surface:'асфальт',runway_length_m:680,heading_deg:82,supports:['fixed_wing','multirotor','vtol'],has_charging:true,has_fuel:true,status:'open',notes:'ангар · топливо · зарядка · RTK',editable:true},
  {id:'base-sergiev-posad',name:'Площадка Сергиев Посад',kind:'runway',lat:56.27,lon:37.55,surface:'полевой старт',runway_length_m:310,heading_deg:118,supports:['fixed_wing','multirotor','vtol'],has_charging:true,has_fuel:false,status:'open',notes:'мобильный пункт управления',editable:true},
]

const baseFootprints:GeoJSON.FeatureCollection<GeoJSON.Polygon>={
  type:'FeatureCollection',
  features:bases.features.map(feature=>{
    const [lon,lat]=feature.geometry.coordinates
    const properties=feature.properties
    const isVtol=properties.kind==='VTOL-площадка'
    const runwayLength=Number.parseFloat(properties.runway)||60
    const length=isVtol?56:runwayLength
    const width=isVtol?56:Math.min(64,Math.max(38,length*.11))
    const heading=Number.parseFloat(properties.heading)||0
    const radians=heading*Math.PI/180
    const along:[number,number]=[Math.sin(radians),Math.cos(radians)]
    const across:[number,number]=[Math.cos(radians),-Math.sin(radians)]
    const point=(a:number,b:number):[number,number]=>[
      lon+(along[0]*a+across[0]*b)/(111320*Math.cos(lat*Math.PI/180)),
      lat+(along[1]*a+across[1]*b)/110540,
    ]
    const ring=[point(-length/2,-width/2),point(length/2,-width/2),point(length/2,width/2),point(-length/2,width/2)]
    return {type:'Feature',properties,geometry:{type:'Polygon',coordinates:[[...ring,ring[0]]]}}
  }),
}

function parseMissionFile(fileName:string,text:string):GeoJSON.Geometry[]{
  const extension=fileName.split('.').pop()?.toLowerCase()
  if(extension==='geojson'||extension==='json'){
    const value=JSON.parse(text) as GeoJSON.GeoJSON
    return value.type==='FeatureCollection'?value.features.map(feature=>feature.geometry).filter(Boolean):value.type==='Feature'?[value.geometry]:[value as GeoJSON.Geometry]
  }
  if(extension==='kml'){
    const xml=new DOMParser().parseFromString(text,'application/xml')
    if(xml.querySelector('parsererror'))throw new Error('Некорректный KML')
    const polygon=xml.querySelector('Polygon coordinates')?.textContent
    const line=xml.querySelector('LineString coordinates')?.textContent
    const parse=(raw:string)=>raw.trim().split(/\s+/).map(item=>item.split(',').slice(0,2).map(Number) as [number,number])
    if(polygon)return[{type:'Polygon',coordinates:[parse(polygon)]}]
    if(line)return[{type:'LineString',coordinates:parse(line)}]
    throw new Error('В KML не найден Polygon или LineString')
  }
  if(extension==='gpx'){
    const xml=new DOMParser().parseFromString(text,'application/xml')
    if(xml.querySelector('parsererror'))throw new Error('Некорректный GPX')
    const nodes=[...xml.querySelectorAll('trkpt,rtept')]
    const coordinates=nodes.map(node=>[Number(node.getAttribute('lon')),Number(node.getAttribute('lat'))] as [number,number])
    if(coordinates.length<2)throw new Error('В GPX требуется минимум две маршрутные точки')
    return[{type:'LineString',coordinates}]
  }
  if(extension==='csv'){
    const coordinates=text.split(/\r?\n/).map(line=>line.trim()).filter(Boolean).map(line=>line.split(/[;,\t]/).map(value=>Number(value.trim())).slice(0,2) as [number,number]).filter(pair=>pair.every(Number.isFinite))
    if(coordinates.length<2)throw new Error('В CSV нужны пары «долгота; широта»')
    const closed=coordinates.length>3&&coordinates[0][0]===coordinates.at(-1)?.[0]&&coordinates[0][1]===coordinates.at(-1)?.[1]
    return[closed?{type:'Polygon',coordinates:[coordinates]}:{type:'LineString',coordinates}]
  }
  throw new Error('Поддерживаются GeoJSON, KML, GPX и CSV')
}

function obstacleRadius(properties:Record<string,unknown>):number{
  const explicit=Number(properties.clearance_m||properties.safety_radius_m)
  if(Number.isFinite(explicit)&&explicit>0)return explicit
  const definition=String(properties.vertical_definition||'')
  const match=definition.match(/радиус[^\d]*(\d+(?:[.,]\d+)?)\s*м/i)
  return match?Number(match[1].replace(',','.')):50
}

type OperationalAirspaceCategory = 'prohibited' | 'danger' | 'obstacle'
type OperationalAirspaceVisibility = Record<OperationalAirspaceCategory, boolean>

function AircraftGlyph({ type, size = 42 }: { type: string; size?: number }) {
  if (type === 'multirotor') return (
    <svg className="quadcopter-glyph" width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
      <path d="M20 22 44 42M44 22 20 42" /><circle cx="15" cy="17" r="9" /><circle cx="49" cy="17" r="9" /><circle cx="15" cy="47" r="9" /><circle cx="49" cy="47" r="9" /><rect x="24" y="24" width="16" height="16" rx="4" /><path d="M28 40v7m8-7v7" />
    </svg>
  )
  return <Plane size={size} strokeWidth={1.25} aria-hidden="true" />
}

type PlanId = 'fast' | 'economy' | 'safe'
type ProductId = 'orthophoto' | 'thermal_map' | 'point_cloud' | 'magnetic_map' | 'powerline_report' | 'digital_twin' | 'ndvi'
type SurveyType = 'rgb' | 'ir' | 'multispectral' | 'lidar' | 'geophysical'
type CatalogSection = 'uavs' | 'payloads' | 'technologies'

type User = { username: string; full_name: string; role: 'admin' | 'dispatcher' | 'manager' }
type Telemetry = {
  uav_id: string; name: string; lat: number; lon: number; altitude_m: number;
  speed_kmh: number; heading_deg: number; yaw_deg: number; pitch_deg: number; roll_deg: number;
  vertical_speed_mps: number; battery_percent: number; link_quality_percent: number; satellites: number;
  mission_progress_percent: number; status: string; source: string; uav_type: 'fixed_wing' | 'multirotor';
  task_name: string; color: string;
  phase_label?: string; compliance_note?: string; base_name?: string;
  next_takeoff_seconds?:number; next_landing_seconds?:number; simulation_rate?:number;
}
type PlaybackState = { missionId:string; mode:'live'|'replay'|'simulation'; cursor:number; playing:boolean; rate:number }
type ExternalAircraft = {
  id: string; callsign: string | null; lat: number; lon: number; altitude_m: number | null;
  speed_kmh: number | null; heading_deg: number; vertical_speed_mps: number | null;
  aircraft_type: string | null; on_ground: boolean; position_source: string; source: string;
  position_time?: number; last_contact_time?: number; position_age_seconds?: number; last_contact_age_seconds?: number;
}
type AirTrafficResponse = {
  status: 'live' | 'stale' | 'unavailable'; observed_at: string; source: string | null;
  count: number; airborne_count: number; ground_count: number; refresh_after_seconds: number;
  aircraft: ExternalAircraft[]; age_seconds?: number; cache?: string; notice: string;
}
type WeatherResponse = {
  status:'live'|'stale';source:string;observed_at:string;age_seconds?:number;
  current:{temperature_c:number;relative_humidity_percent?:number|null;surface_pressure_hpa?:number|null;precipitation_mm:number;rain_mm:number;snowfall_cm:number;cloud_cover_percent:number;wind_speed_mps:number;wind_direction_deg:number;visibility_m:number;weather_code:number;is_day:number};
  hourly:{time:string;temperature_c:number;precipitation_probability:number;precipitation_mm:number;weather_code:number;wind_speed_mps:number;wind_direction_deg:number;wind_gust_mps:number;visibility_m:number;cloud_cover_percent:number}[];
  risk:{score:number;level:'good'|'attention'|'critical';reasons:string[]};
}
type RadarManifest={status:'live'|'stale';source:string;updated:number;bbox:[[number,number],[number,number]];frames:{index:number;time:number;url:string}[]}
type ApiVehicle = {
  phases?: { name: string; minutes: number }[];
  cost_components?: Record<string, number>;
  sorties?: number;
  endurance_min?: number;
  usable_endurance_min?: number;
  max_sortie_min?: number;
  turn_radius_m?:number;
  turn_geometry?:'dubins_csc'|'hover_spline';
  turn_bank_deg?:number;turn_speed_kmh?:number;runway_heading_deg?:number;runway_length_m?:number;departure_heading_deg?:number;
  runway_headwind_mps?:number;runway_crosswind_mps?:number;
  reserve_percent?: number;
  uav_id:string;
  uav_name: string;
  altitude_m: number;
  cost_rub: number;
  elapsed_time_min:number;
  route: GeoJSON.LineString;
  coverage_route?: GeoJSON.MultiLineString;
  transit_route?: GeoJSON.MultiLineString;
  color: string;
  departure_offset_min?:number;
  spatial_conflicts?:number;
  engineering?: EngineeringMetrics;
  link_assessment?: LinkAssessment;
  deployment?: DeploymentAssessment;
}
type LinkAssessment = {mode:'radio'|'external';status:'within_planning_limit'|'coverage_unverified';max_distance_km:number;planning_limit_km:number|null;coverage_verified:boolean}
type DeploymentAssessment = {required:boolean;max_distance_km?:number;distance_km?:number;cost_included:boolean}
type EngineeringMetrics = {
  trigger_design_speed_mps?: number;
  requested_gsd_cm_px: number; achieved_gsd_cm_px: number; sensor_width_mm: number; focal_length_mm: number;
  resolution_width_px: number; footprint_width_m: number; footprint_length_m: number; line_spacing_m: number;
  trigger_spacing_m: number; trigger_interval_s: number; required_fps: number; camera_fps: number;
  estimated_frames: number; forward_overlap_percent: number; side_overlap_percent: number;
}
type ApiPlan = {
  total_flight_time_min:number;
  maintenance_policy?:string;
  reserve_percent?: number; explanations?: string[];
  sorties?: number; selection_reason?: string;
  airspace_avoidance?: {detours:number;unresolved:number};
  deconfliction?:{minimum_distance_m:number;spatial_conflicts:number;departure_slots_applied:number;method:string;verified_4d:boolean};
  link_assessment?:LinkAssessment;deployment?:DeploymentAssessment;
  id: PlanId; label: string; duration_min: number; cost_rub: number; uav_count: number;
  risk: number; vehicles: ApiVehicle[]; savings_vs_baseline_rub: number;
}
type AirspaceConflict = {
  id:string;category:'prohibited'|'danger'|'obstacle'|'custom';code?:string|null;name:string;clearance_m:number;
  mission_intersection:boolean;vertical_definition?:string|null;schedule?:string|null;source_name:string;geometry:GeoJSON.Geometry;
}
type AirspaceAssessment = {
  status:'clear'|'notification_required'|'permission_required'|'adjustment_required'|'not_checked';
  operation_mode:'none'|'notification'|'permission'|'unknown';
  conflicts:AirspaceConflict[];authorities:{id:string;code?:string|null;name:string;source_name:string;contact_status?:'verified'|'missing';contact?:{organization:string;procedure:string;phone?:string;email?:string}|null}[];
  clearances_m:Record<string,number>;messages:string[];target_time:string;
}
type SettlementAssessment = {status:'COVERED'|'PARTIALLY_COVERED'|'NOT_COVERED';tiles_total:number;tiles_fresh:number;corridor_margin_m:number;boundary_authority:string;intersections:{osm_type:string;osm_id:number;name:string|null;place:string;region:string|null;district:string|null;source:string;geometry:GeoJSON.Geometry}[]}
type AirspaceSettings = {prohibited:number;danger:number;obstacle:number;settlement:number;separation:number}
type ControlLinkSettings = {mode:'radio'|'external';equipmentRangeKm:number;groundAntennaHeightM:number}
type FleetComparison = {
  uav_count:number;sorties:number;duration_min:number;total_flight_time_min:number;cost_rub:number;
  vehicle_names:string[];vehicle_sorties:number[];cost_components:Record<string,number>;recommended:boolean;
}
type OptimizationResult = {
  mission_id:string;
  recommended_plan_id: PlanId; plans: ApiPlan[]; calculation_ms: number;
  timings_ms?:{route_planning:number;settlement_lookup:number};
  fleet_comparison?:FleetComparison[];
  economics?:{method:string;candidate_count:number;uav_mobilization_cost_rub:number;sortie_preparation_cost_rub:number;ground_relocation_cost_included:boolean;tariff_status:string};
  analysis?: { available_uavs: number; compatible_uavs: number; bases: number; weather_window_score: number };
  engineering?: { method: string; altitude_formula: string; swath_formula: string; line_spacing_formula: string; trigger_formula: string; assumptions: string[] };
  airspace?: AirspaceAssessment;
  settlement_assessment?: SettlementAssessment;
  settlement_assessments?: Record<string,SettlementAssessment>;
}

type CatalogUav = {
  id: string; name: string; series?: string; modification?: string; type: string; payload_kg: number;
  cruise_speed_kmh: number; endurance_min: number; energy_kind: string; max_wind_mps: number;
  status: string; flight_hours: number; maintenance_due_hours: number;
  maintenance_metric?:'flights'|'engine_hours'|'unverified'; maintenance_interval?:number|null; flights_since_maintenance?:number|null;
}
type CatalogPayload = {
  id: string; name: string; category?: string; mass_kg: number; spectrums: string[];
  sensor_type: string; max_resolution_cm_px: number; compatible_uav_ids?: string[];
  focal_length_mm?: number; sensor_width_mm?: number; sensor_height_mm?: number;
  resolution_width_px?: number; resolution_height_px?: number; fps?: number;
}
function opticalAltitudeForGsd(gsdCmPx:number,payload:CatalogPayload|undefined):number|null {
  if (!payload?.focal_length_mm || !payload.sensor_width_mm || !payload.resolution_width_px) return null
  return gsdCmPx / 100 * payload.focal_length_mm * payload.resolution_width_px / payload.sensor_width_mm
}
type TechnologyProfile = {
  id: string; name: string; result_type: ProductId; survey_type: SurveyType; purpose: string;
  acceptance: string[]; deliverables: string[]; recommended_payload_ids: string[];
}

const missionProducts: Record<ProductId, {
  title: string; short: string; icon: typeof MapIcon; surveyType: SurveyType; gsd: number;
  forwardOverlap: number; sideOverlap: number; acceptance: string; format: string; technology: string;
}> = {
  orthophoto: { title: 'Ортофотоплан', short: 'Ортофото', icon: MapIcon, surveyType: 'rgb', gsd: 5, forwardOverlap: .70, sideOverlap: .60, acceptance: 'GSD ≤ 5 см/пикс', format: 'GeoTIFF · ЦМР', technology: 'Фотограмметрия' },
  thermal_map: { title: 'Тепловая карта', short: 'Тепло', icon: Thermometer, surveyType: 'ir', gsd: 8, forwardOverlap: .75, sideOverlap: .65, acceptance: 'RGB/ИК совмещены', format: 'GeoTIFF · отчёт', technology: 'Термография 8–14 мкм' },
  point_cloud: { title: 'Облако точек', short: 'LiDAR', icon: ScanLine, surveyType: 'lidar', gsd: 5, forwardOverlap: .65, sideOverlap: .50, acceptance: '≥ 60 точек/м²', format: 'LAS/LAZ · ЦМР', technology: 'Воздушное лазерное сканирование' },
  magnetic_map: { title: 'Магнитное поле', short: 'Магнитика', icon: Magnet, surveyType: 'geophysical', gsd: 10, forwardOverlap: .50, sideOverlap: .20, acceptance: 'СКО ≤ ±2 нТ', format: 'GeoTIFF · XYZ', technology: 'Аэромагнитная съёмка' },
  powerline_report: { title: 'Отчёт по ЛЭП', short: 'ЛЭП', icon: Radio, surveyType: 'rgb', gsd: 5, forwardOverlap: .75, sideOverlap: .65, acceptance: 'Габариты и дефекты', format: 'KML · XLSX · фото', technology: 'Коридорное обследование' },
  digital_twin: { title: 'Цифровой двойник', short: '3D-модель', icon: Box, surveyType: 'rgb', gsd: 3, forwardOverlap: .80, sideOverlap: .70, acceptance: 'Полнота модели ≥ 98%', format: '3D Tiles · glTF', technology: 'Наклонная фотограмметрия' },
  ndvi: { title: 'Карта состояния', short: 'NDVI', icon: Grid3X3, surveyType: 'multispectral', gsd: 8, forwardOverlap: .75, sideOverlap: .65, acceptance: 'Калибровка каналов', format: 'GeoTIFF · SHP', technology: 'Мультиспектральная съёмка' },
}

const plans = {
  fast: { label: 'Быстрый', icon: Zap, time: '—', cost: '—', uavs: 0, risk: '—', accent: '#f15a32' },
  economy: { label: 'Минимум налёта', icon: Gauge, time: '—', cost: '—', uavs: 0, risk: '—', accent: '#22b5e5' },
  safe: { label: 'Сбалансированный', icon: ShieldCheck, time: '—', cost: '—', uavs: 0, risk: '—', accent: '#89a9ff' },
}

function MapCanvas({ routeData, areaData, drawMode, onAreaChange, onDrawingComplete, telemetry, selectedUavId, onSelectUav, operationAreas, externalAircraft, radarFrame, airspaceZones, airspaceVisibility, settlementsVisible, onSettlementCount, showMapTools }: {
  operationAreas: GeoJSON.FeatureCollection<GeoJSON.Polygon>;
  routeData: GeoJSON.FeatureCollection<GeoJSON.LineString | GeoJSON.MultiLineString>;
  areaData: MissionGeometry;
  drawMode: boolean;
  onAreaChange: (area: MissionGeometry) => void;
  onDrawingComplete: () => void;
  telemetry: Telemetry[];
  selectedUavId: string | null;
  onSelectUav: (uavId: string) => void;
  externalAircraft: ExternalAircraft[];
  radarFrame: {url:string;coordinates:[[number,number],[number,number],[number,number],[number,number]]}|null;
  airspaceZones:GeoJSON.FeatureCollection;airspaceVisibility:OperationalAirspaceVisibility;
  settlementsVisible:boolean;onSettlementCount:(count:number)=>void;
  showMapTools:boolean;
}) {
  const container = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const loaded = useRef(false)
  const focusedUavRef = useRef<string | null>(null)
  const routeDataRef = useRef(routeData)
  const operationsRef = useRef(operationAreas)
  const telemetryRef=useRef(telemetry)
  const airspaceRef=useRef(airspaceZones)
  const airspaceVisibilityRef=useRef(airspaceVisibility)
  const settlementsVisibleRef=useRef(settlementsVisible)
  const obstacleBufferRef=useRef<maplibregl.Marker|null>(null)
  operationsRef.current = operationAreas
  telemetryRef.current=telemetry
  airspaceRef.current=airspaceZones
  airspaceVisibilityRef.current=airspaceVisibility
  settlementsVisibleRef.current=settlementsVisible
  const areaDataRef = useRef(areaData)
  const markersRef = useRef(new Map<string, maplibregl.Marker>())
  const headingsRef = useRef(new Map<string, number>())
  const markerElementsRef = useRef(new Map<string, HTMLButtonElement>())
  const markerInnerRef = useRef(new Map<string, HTMLSpanElement>())
  const markerPositionRef = useRef(new Map<string, [number, number]>())
  const markerAnimationRef = useRef(new Map<string, number>())
  const measurementPointsRef=useRef<[number,number][]>([])
  const [measurementMode,setMeasurementMode]=useState<'idle'|'distance'|'distance-complete'|'area'|'area-complete'>('idle')
  const [measurementLabel,setMeasurementLabel]=useState('')
  routeDataRef.current = routeData
  areaDataRef.current = areaData

  useEffect(() => {
    if (!container.current || mapRef.current) return
    let disposeSettlements=()=>{}
    const map = new maplibregl.Map({
      container: container.current,
      style: 'https://tiles.openfreemap.org/styles/liberty',
      center: [37.48, 55.78],
      zoom: 13.2,
      pitch: 56,
      bearing: -24,
      attributionControl: false,
    })
    mapRef.current = map
    map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right')
    map.on('load', () => {
      loaded.current = true
      map.addSource('operational-airspace',{type:'geojson',data:airspaceRef.current})
      const obstacleIcon=()=>{const canvas=document.createElement('canvas');canvas.width=64;canvas.height=64;const context=canvas.getContext('2d')!;context.translate(32,32);context.beginPath();context.arc(0,0,27,0,Math.PI*2);context.fillStyle='#7f57b5';context.strokeStyle='#fff';context.lineWidth=4;context.fill();context.stroke();context.strokeStyle='#fff';context.fillStyle='#fff';context.lineWidth=3;context.lineCap='round';context.lineJoin='round';context.beginPath();context.moveTo(0,-17);context.lineTo(-11,17);context.moveTo(0,-17);context.lineTo(11,17);context.moveTo(-7,7);context.lineTo(7,7);context.moveTo(-4,-3);context.lineTo(4,-3);context.stroke();context.beginPath();context.arc(0,-19,3,0,Math.PI*2);context.fill();return context.getImageData(0,0,64,64)}
      map.addImage('height-obstacle-icon',obstacleIcon(),{pixelRatio:2})
      const airspaceStyles:Record<OperationalAirspaceCategory,{color:string;opacity:number}>={danger:{color:'#e89524',opacity:.13},prohibited:{color:'#df4a35',opacity:.18},obstacle:{color:'#7f57b5',opacity:.22}}
      ;(['danger','prohibited','obstacle'] as OperationalAirspaceCategory[]).forEach(category=>{
        const style=airspaceStyles[category];const visibility=airspaceVisibilityRef.current[category]?'visible':'none';const filter=['==',['get','category'],category] as maplibregl.FilterSpecification
        map.addLayer({id:`airspace-${category}-fill`,type:'fill',source:'operational-airspace',filter,layout:{visibility},paint:{'fill-color':style.color,'fill-opacity':style.opacity}})
        map.addLayer({id:`airspace-${category}-line`,type:'line',source:'operational-airspace',filter,layout:{visibility},paint:{'line-color':style.color,'line-width':category==='obstacle'?3:2.4,'line-opacity':.95}})
        if(category==='obstacle')map.addLayer({id:'airspace-obstacle-point',type:'symbol',source:'operational-airspace',filter,layout:{visibility,'icon-image':'height-obstacle-icon','icon-size':['interpolate',['linear'],['zoom'],7,.72,12,1,15,1.22],'icon-allow-overlap':true}})
      })
      const airspaceLayers=['airspace-danger-fill','airspace-danger-line','airspace-prohibited-fill','airspace-prohibited-line','airspace-obstacle-fill','airspace-obstacle-line','airspace-obstacle-point']
      const openAirspacePopup=(event:maplibregl.MapLayerMouseEvent)=>{const feature=event.features?.[0];if(!feature)return;const p=feature.properties||{};const title=String(p.name||p.code||'Объект воздушного пространства');const category=String(p.category||'');const label=category==='prohibited'?'Запретная зона':category==='danger'?'Опасная зона':'Высотный объект';const popup=document.createElement('div');popup.className='airspace-map-popup';const heading=document.createElement('strong');heading.textContent=title;const code=document.createElement('span');code.textContent=`${label}${p.code?` · ${p.code}`:''}`;const limits=document.createElement('span');limits.textContent=`Высоты: ${p.lower_limit||'GND'} — ${p.upper_limit||p.vertical_definition||'не указано'}`;const schedule=document.createElement('span');schedule.textContent=`Действие: ${p.schedule||'по данным справочника'}`;popup.append(heading,code,limits,schedule);if(category==='obstacle'&&feature.geometry.type==='Point'){const radius=obstacleRadius(p as Record<string,unknown>)*.9;const buffer=document.createElement('div');buffer.className='obstacle-safety-buffer';const marker=new maplibregl.Marker({element:buffer,anchor:'center'}).setLngLat(feature.geometry.coordinates as [number,number]).addTo(map);const resize=()=>{const latitude=feature.geometry.type==='Point'?Number(feature.geometry.coordinates[1]):event.lngLat.lat;const metersPerPixel=156543.03392*Math.cos(latitude*Math.PI/180)/2**map.getZoom();const diameter=Math.max(38,radius*2/metersPerPixel);buffer.style.width=`${diameter}px`;buffer.style.height=`${diameter}px`;buffer.title=`90% дистанции облёта: ${Math.round(radius)} м`};obstacleBufferRef.current?.remove();obstacleBufferRef.current=marker;resize();map.once('zoomend',resize);const safety=document.createElement('span');safety.textContent=`Анимированный буфер: ${Math.round(radius)} м (90% дистанции облёта)`;popup.append(safety)}new maplibregl.Popup({offset:16}).setLngLat(event.lngLat).setDOMContent(popup).addTo(map)}
      airspaceLayers.forEach(id=>{map.on('mouseenter',id,()=>{map.getCanvas().style.cursor='pointer'});map.on('mouseleave',id,()=>{map.getCanvas().style.cursor=''});map.on('click',id,openAirspacePopup)})
      map.addSource('mission-area', { type: 'geojson', data: {type:'FeatureCollection',features:[]} })
      map.addLayer({ id: 'mission-glow', type: 'line', source: 'mission-area', paint: { 'line-color': '#f15a32', 'line-width': 15, 'line-opacity': 0.34, 'line-blur': 7 } })
      map.addLayer({ id: 'mission-fill', type: 'fill', source: 'mission-area', paint: { 'fill-color': '#ff6b35', 'fill-opacity': 0.20 } })
      map.addLayer({ id: 'mission-line', type: 'line', source: 'mission-area', paint: { 'line-color': '#ff4f20', 'line-width': ['interpolate', ['linear'], ['zoom'], 8, 2.8, 13, 5.2], 'line-opacity': 0.98 } })
      map.addSource('active-missions', { type: 'geojson', data: operationsRef.current })
      map.addLayer({ id: 'active-mission-fill', type: 'fill', source: 'active-missions', paint: { 'fill-color': ['get', 'color'], 'fill-opacity': 0.18 } })
      map.addLayer({ id: 'active-mission-line', type: 'line', source: 'active-missions', paint: { 'line-color': ['get', 'color'], 'line-width': 2.8, 'line-opacity': 0.9 } })
      map.addSource('draw-points', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      map.addLayer({ id: 'draw-points-halo', type: 'circle', source: 'draw-points', paint: { 'circle-radius': 8, 'circle-color': '#ffb45b', 'circle-opacity': 0.16 } })
      map.addLayer({ id: 'draw-points-core', type: 'circle', source: 'draw-points', paint: { 'circle-radius': 3.5, 'circle-color': '#ffd19a', 'circle-stroke-color': '#071017', 'circle-stroke-width': 1.5 } })
      map.addSource('measurement', { type:'geojson', data:{type:'FeatureCollection',features:[]} })
      map.addLayer({id:'measurement-fill',type:'fill',source:'measurement',filter:['==',['geometry-type'],'Polygon'],paint:{'fill-color':'#30c8e5','fill-opacity':.18}})
      map.addLayer({id:'measurement-line',type:'line',source:'measurement',filter:['==',['geometry-type'],'LineString'],paint:{'line-color':'#176f8a','line-width':3,'line-dasharray':[2,1.4]}})
      map.addLayer({id:'measurement-outline',type:'line',source:'measurement',filter:['==',['geometry-type'],'Polygon'],paint:{'line-color':'#176f8a','line-width':3,'line-dasharray':[2,1.4]}})
      map.addLayer({id:'measurement-points',type:'circle',source:'measurement',filter:['==',['geometry-type'],'Point'],paint:{'circle-radius':6,'circle-color':'#fff','circle-stroke-color':'#176f8a','circle-stroke-width':3}})
      map.addSource('routes', { type: 'geojson', data: routeDataRef.current })
      map.addLayer({ id: 'routes-glow', type: 'line', source: 'routes', filter: ['==', ['get', 'kind'], 'coverage'], paint: { 'line-color': ['get', 'color'], 'line-width': 7, 'line-opacity': 0.16, 'line-blur': 4 } })
      map.addLayer({ id: 'routes-main', type: 'line', source: 'routes', filter: ['==', ['get', 'kind'], 'coverage'], paint: { 'line-color': ['get', 'color'], 'line-width': ['interpolate', ['linear'], ['zoom'], 8, 1.05, 11, 1.8, 14, 2.8], 'line-opacity': ['interpolate', ['linear'], ['zoom'], 8, 0.58, 11, 0.82, 14, 0.96] } })
      map.addLayer({ id: 'routes-transit-glow', type: 'line', source: 'routes', filter: ['==', ['get', 'kind'], 'transit'], paint: { 'line-color': ['get', 'color'], 'line-width': 6, 'line-opacity': 0.08, 'line-blur': 3, 'line-offset':['case',['==',['get','direction'],'outbound'],-2,2] } })
      map.addLayer({ id: 'routes-transit', type: 'line', source: 'routes', filter: ['==', ['get', 'kind'], 'transit'], paint: { 'line-color': ['get', 'color'], 'line-width': ['interpolate',['linear'],['zoom'],8,1,13,1.8], 'line-opacity': 0.62, 'line-dasharray': [2.2, 2.2], 'line-offset':['case',['==',['get','direction'],'outbound'],-2,2] } })
      map.addSource('base-footprints',{type:'geojson',data:baseFootprints})
      map.addLayer({id:'base-footprint-fill',type:'fill',source:'base-footprints',paint:{'fill-color':'#1eb7c8','fill-opacity':.2}})
      map.addLayer({id:'base-footprint-line',type:'line',source:'base-footprints',paint:{'line-color':'#087e91','line-width':['interpolate',['linear'],['zoom'],8,2,13,3.5],'line-opacity':.95}})
      map.addSource('bases', { type: 'geojson', data: bases })
      map.addLayer({ id: 'bases-halo', type: 'circle', source: 'bases', paint: { 'circle-radius': 13, 'circle-color': '#061116', 'circle-stroke-color': '#78d9c4', 'circle-stroke-width': 1.5, 'circle-opacity': 0.88 } })
      map.addLayer({ id: 'bases-core', type: 'circle', source: 'bases', paint: { 'circle-radius': 4, 'circle-color': '#78d9c4' } })
      map.addLayer({id:'bases-label',type:'symbol',source:'bases',minzoom:8.5,layout:{'text-field':['get','name'],'text-size':11,'text-offset':[0,1.8],'text-anchor':'top','text-optional':true},paint:{'text-color':'#18353c','text-halo-color':'rgba(255,255,255,.96)','text-halo-width':2}})
      map.on('mouseenter','base-footprint-fill',()=>{map.getCanvas().style.cursor='pointer'});map.on('mouseleave','base-footprint-fill',()=>{map.getCanvas().style.cursor=''})
      map.on('click','base-footprint-fill',event=>{const feature=event.features?.[0];if(!feature)return;const p=feature.properties||{};const name=String(p.name||'Площадка');const popup=document.createElement('div');popup.className='base-popup';const title=document.createElement('strong');title.textContent=name;const kind=document.createElement('span');kind.textContent=`${p.kind} · ${p.surface}`;const runway=document.createElement('span');runway.textContent=`ВПП / площадка: ${p.runway} · курс ${p.heading}`;const support=document.createElement('span');support.textContent=`Оснащение: ${p.support}`;const status=document.createElement('em');status.textContent=String(p.status);popup.append(title,kind,runway,support,status)
        const schedule=document.createElement('section');schedule.className='base-schedule';const scheduleTitle=document.createElement('b');scheduleTitle.textContent='Ближайшие операции';schedule.append(scheduleTitle)
        const events=telemetryRef.current.filter(item=>item.base_name===name).flatMap(item=>[
          {seconds:item.next_takeoff_seconds,type:'Взлёт',name:item.name.replace('Геоскан ','')},
          {seconds:item.next_landing_seconds,type:'Посадка',name:item.name.replace('Геоскан ','')},
        ]).filter(item=>item.seconds!==undefined).sort((a,b)=>(a.seconds||0)-(b.seconds||0)).slice(0,6)
        events.forEach(item=>{const row=document.createElement('span');row.className=item.type==='Посадка'?'landing':'takeoff';const time=document.createElement('time');const configuredRate=telemetryRef.current[0]?.simulation_rate??1;const rate=configuredRate>0?configuredRate:1;time.textContent=new Date(Date.now()+(item.seconds||0)*1000/rate).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'});const operation=document.createElement('strong');operation.textContent=item.type;const aircraft=document.createElement('i');aircraft.textContent=item.name;row.append(time,operation,aircraft);schedule.append(row)})
        const note=document.createElement('small');note.textContent=events.length?'Расчётное время демонстрационного цикла':'Нет запланированных операций';schedule.append(note);popup.append(schedule)
        new maplibregl.Popup({offset:18}).setLngLat(event.lngLat).setDOMContent(popup).addTo(map)})
      const trafficIcon = (ground=false) => {
        const canvas=document.createElement('canvas');canvas.width=64;canvas.height=64
        const context=canvas.getContext('2d')!;context.translate(32,32)
        context.beginPath();context.arc(0,0,28,0,Math.PI*2);context.fillStyle=ground?'#68767c':'#102d39';context.strokeStyle=ground?'#f1f5f6':'#38c9ef';context.lineWidth=4;context.fill();context.stroke()
        context.save();context.scale(1.2,1.2);context.beginPath()
        context.moveTo(0,-20);context.lineTo(3,-7);context.lineTo(18,3);context.lineTo(18,8)
        context.lineTo(4,4);context.lineTo(3,13);context.lineTo(9,17);context.lineTo(9,20)
        context.lineTo(0,17);context.lineTo(-9,20);context.lineTo(-9,17);context.lineTo(-3,13)
        context.lineTo(-4,4);context.lineTo(-18,8);context.lineTo(-18,3);context.lineTo(-3,-7)
        context.closePath();context.fillStyle='#fff';context.strokeStyle='#071116';context.lineWidth=2;context.fill();context.stroke();context.restore()
        return context.getImageData(0,0,64,64)
      }
      map.addImage('external-aircraft-icon',trafficIcon(),{pixelRatio:2})
      map.addImage('external-ground-icon',trafficIcon(true),{pixelRatio:2})
      map.addSource('external-aircraft', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      map.addLayer({
        id: 'external-aircraft-symbols', type: 'symbol', source: 'external-aircraft',
        layout: { 'icon-image': ['case',['get','on_ground'],'external-ground-icon','external-aircraft-icon'], 'icon-size': ['interpolate',['linear'],['zoom'],7,.82,10,1.02,14,1.16], 'icon-rotate': ['get', 'heading'], 'icon-rotation-alignment': 'map', 'icon-allow-overlap': true },
        paint: { 'icon-opacity': ['case',['get','on_ground'],.78,1] },
      })
      map.addLayer({
        id: 'external-aircraft-labels', type: 'symbol', source: 'external-aircraft', minzoom: 9.5,
        layout: { 'text-field': ['get', 'callsign'], 'text-size': 11.5, 'text-offset': [0, 1.75], 'text-anchor': 'top', 'text-optional': true },
        paint: { 'text-color': '#102b36', 'text-halo-color': 'rgba(255,255,255,.98)', 'text-halo-width': 2 },
      })
      map.on('mouseenter', 'external-aircraft-symbols', () => { map.getCanvas().style.cursor = 'pointer' })
      map.on('mouseleave', 'external-aircraft-symbols', () => { map.getCanvas().style.cursor = '' })
      map.on('click', 'external-aircraft-symbols', (event) => {
        const feature = event.features?.[0]
        if (!feature || feature.geometry.type !== 'Point') return
        const properties = feature.properties || {}
        const altitude = Number(properties.altitude_m)
        const speed = Number(properties.speed_kmh)
        const popup = document.createElement('div')
        popup.className = 'traffic-popup'
        const title = document.createElement('strong'); title.textContent = String(properties.callsign || properties.id || 'Борт')
        const onGround=properties.on_ground===true||properties.on_ground==='true'
        const altitudeRow = document.createElement('span'); altitudeRow.textContent = onGround?'Состояние: на земле':`Баро. высота MSL: ${Number.isFinite(altitude) ? `${altitude.toLocaleString('ru-RU')} м` : '—'}`
        const speedRow = document.createElement('span'); speedRow.textContent = `Скорость: ${Number.isFinite(speed) ? `${speed.toLocaleString('ru-RU')} км/ч` : '—'}`
        const courseRow = document.createElement('span'); courseRow.textContent = `Курс: ${Math.round(Number(properties.heading) || 0)}° · ${String(properties.position_source || 'ADS-B')}`
        const ageRow=document.createElement('span');ageRow.textContent=`Координата: ${Math.round(Number(properties.position_age_seconds)||0)} с назад`
        popup.append(title, altitudeRow, speedRow, courseRow,ageRow)
        new maplibregl.Popup({ offset: 18, closeButton: true })
          .setLngLat(feature.geometry.coordinates as [number, number])
          .setDOMContent(popup)
          .addTo(map)
      })
      if (map.getSource('openmaptiles') && !map.getLayer('mission-3d-buildings')) {
        const firstLabel = map.getStyle().layers?.find((layer) => layer.type === 'symbol')?.id
        map.addLayer({
          id: 'mission-3d-buildings', type: 'fill-extrusion', source: 'openmaptiles', 'source-layer': 'building', minzoom: 12.8,
          paint: {
            'fill-extrusion-color': ['interpolate', ['linear'], ['coalesce', ['get', 'render_height'], ['get', 'height'], 8], 0, '#dce2e4', 80, '#aeb9bd'],
            'fill-extrusion-height': ['coalesce', ['get', 'render_height'], ['get', 'height'], 8],
            'fill-extrusion-base': ['coalesce', ['get', 'render_min_height'], 0],
            'fill-extrusion-opacity': 0.76,
          },
        }, firstLabel)
      }
      disposeSettlements=installSettlementMapLayer(map,'mission-fill',{onCountChange:onSettlementCount})
      setSettlementMapVisibility(map,settlementsVisibleRef.current)
    })
    return () => {
      disposeSettlements()
      markerAnimationRef.current.forEach((id) => cancelAnimationFrame(id))
      obstacleBufferRef.current?.remove()
      markersRef.current.forEach((marker) => marker.remove())
      markersRef.current.clear()
      map.remove()
      mapRef.current = null
      loaded.current = false
    }
  }, [])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !loaded.current) return
    ;(map.getSource('active-missions') as GeoJSONSource | undefined)?.setData(operationAreas)
  }, [operationAreas])

  useEffect(()=>{
    const map=mapRef.current;if(!map||!loaded.current)return
    ;(map.getSource('operational-airspace') as GeoJSONSource|undefined)?.setData(airspaceZones)
  },[airspaceZones])

  useEffect(()=>{
    const map=mapRef.current;if(!map||!loaded.current)return
    ;(['danger','prohibited','obstacle'] as OperationalAirspaceCategory[]).forEach(category=>{
      const visibility=airspaceVisibility[category]?'visible':'none'
      ;[`airspace-${category}-fill`,`airspace-${category}-line`,...(category==='obstacle'?['airspace-obstacle-point']:[])].forEach(id=>{if(map.getLayer(id))map.setLayoutProperty(id,'visibility',visibility)})
    })
  },[airspaceVisibility])
  useEffect(()=>{
    const map=mapRef.current;if(map&&loaded.current)setSettlementMapVisibility(map,settlementsVisible)
  },[settlementsVisible])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !loaded.current || !selectedUavId || focusedUavRef.current === selectedUavId) return
    const selected = telemetry.find((item) => item.uav_id === selectedUavId)
    if (!selected) return
    focusedUavRef.current = selectedUavId
    map.flyTo({
      center: [selected.lon, selected.lat],
      zoom: selected.uav_type === 'multirotor' ? 15.1 : 14.6,
      pitch: 58,
      bearing: selected.heading_deg - 28,
      duration: 1350,
      essential: true,
      padding: { top: 100, right: Math.min(410, window.innerWidth * .34), bottom: 90, left: 70 },
    })
  }, [selectedUavId, telemetry])

  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    const points: [number, number][] = []
    const pointSource = () => map.getSource('draw-points') as GeoJSONSource | undefined
    const renderPoints = () => pointSource()?.setData({
      type: 'FeatureCollection',
      features: points.map((coordinates, index) => ({
        type: 'Feature', properties: { index }, geometry: { type: 'Point', coordinates },
      })),
    })
    const addPoint = (event: maplibregl.MapMouseEvent) => {
      if (!drawMode) return
      points.push([event.lngLat.lng, event.lngLat.lat])
      renderPoints()
      if (points.length >= 3) {
        onAreaChange({
          type: 'Feature',
          properties: { source: 'manual' },
          geometry: { type: 'Polygon', coordinates: [[...points, points[0]]] },
        })
      }
    }
    const complete = (event: maplibregl.MapMouseEvent) => {
      if (!drawMode || points.length < 3) return
      event.preventDefault()
      onDrawingComplete()
    }
    map.getCanvas().style.cursor = drawMode ? 'crosshair' : ''
    if (drawMode) map.doubleClickZoom.disable()
    else map.doubleClickZoom.enable()
    map.on('click', addPoint)
    map.on('dblclick', complete)
    if (!drawMode) renderPoints()
    return () => {
      map.off('click', addPoint)
      map.off('dblclick', complete)
      map.getCanvas().style.cursor = ''
      map.doubleClickZoom.enable()
    }
  }, [drawMode, onAreaChange, onDrawingComplete])

  useEffect(()=>{
    const map=mapRef.current
    if(!map||!loaded.current)return
    const source=()=>map.getSource('measurement') as GeoJSONSource|undefined
    const distanceBetween=(a:[number,number],b:[number,number])=>{const toRad=Math.PI/180;const dLat=(b[1]-a[1])*toRad;const dLon=(b[0]-a[0])*toRad;const lat1=a[1]*toRad;const lat2=b[1]*toRad;const value=Math.sin(dLat/2)**2+Math.cos(lat1)*Math.cos(lat2)*Math.sin(dLon/2)**2;return 12_742_000*Math.asin(Math.min(1,Math.sqrt(value)))}
    const polygonArea=(points:[number,number][])=>{const meanLat=points.reduce((sum,point)=>sum+point[1],0)/points.length*Math.PI/180;const projected=points.map(([lon,lat])=>[6_371_000*lon*Math.PI/180*Math.cos(meanLat),6_371_000*lat*Math.PI/180]);return Math.abs(projected.reduce((sum,point,index)=>{const next=projected[(index+1)%projected.length];return sum+point[0]*next[1]-next[0]*point[1]},0)/2)}
    const render=(preview?:[number,number])=>{const fixed=measurementPointsRef.current;const drawingDistance=measurementMode==='distance';const drawingArea=measurementMode==='area';const coordinates=preview&&(drawingDistance&&fixed.length===1||drawingArea&&fixed.length>0)?[...fixed,preview]:fixed;const features:GeoJSON.Feature[]=[...fixed.map((coordinates,index)=>({type:'Feature' as const,properties:{index},geometry:{type:'Point' as const,coordinates}}))];if(coordinates.length>=2)features.push({type:'Feature',properties:{},geometry:{type:'LineString',coordinates:drawingArea||measurementMode==='area-complete'?[...coordinates,coordinates[0]]:coordinates}});if((drawingArea||measurementMode==='area-complete')&&coordinates.length>=3)features.push({type:'Feature',properties:{},geometry:{type:'Polygon',coordinates:[[...coordinates,coordinates[0]]]}});source()?.setData({type:'FeatureCollection',features});if((drawingDistance||measurementMode==='distance-complete')&&coordinates.length===2){const meters=distanceBetween(coordinates[0],coordinates[1]);setMeasurementLabel(meters<1000?`${Math.round(meters)} м`:`${(meters/1000).toFixed(2)} км`)}else if((drawingArea||measurementMode==='area-complete')&&coordinates.length>=3){const area=polygonArea(coordinates);setMeasurementLabel(area<10_000?`${Math.round(area)} м²`:area<1_000_000?`${(area/10_000).toFixed(2)} га`:`${(area/1_000_000).toFixed(2)} км²`)}}
    const click=(event:maplibregl.MapMouseEvent)=>{if(measurementMode!=='distance'&&measurementMode!=='area')return;const point:[number,number]=[event.lngLat.lng,event.lngLat.lat];if(measurementMode==='distance'){if(measurementPointsRef.current.length===0){measurementPointsRef.current=[point];render()}else{measurementPointsRef.current=[measurementPointsRef.current[0],point];render();setMeasurementMode('distance-complete')}}else{measurementPointsRef.current=[...measurementPointsRef.current,point];render()}}
    const doubleClick=(event:maplibregl.MapMouseEvent)=>{if(measurementMode!=='area'||measurementPointsRef.current.length<3)return;event.preventDefault();const points=measurementPointsRef.current;if(points.length>3&&distanceBetween(points.at(-1)!,points.at(-2)!)<2)measurementPointsRef.current=points.slice(0,-1);render();setMeasurementMode('area-complete')}
    const move=(event:maplibregl.MapMouseEvent)=>{if((measurementMode==='distance'&&measurementPointsRef.current.length===1)||(measurementMode==='area'&&measurementPointsRef.current.length>0))render([event.lngLat.lng,event.lngLat.lat])}
    if(measurementMode==='idle'){measurementPointsRef.current=[];setMeasurementLabel('');render();map.getCanvas().style.cursor=''}
    else if(measurementMode==='distance'||measurementMode==='area'){map.getCanvas().style.cursor='crosshair';map.on('click',click);map.on('mousemove',move);if(measurementMode==='area')map.doubleClickZoom.disable()}
    else render()
    map.on('dblclick',doubleClick)
    return()=>{map.off('click',click);map.off('mousemove',move);map.off('dblclick',doubleClick);if(measurementMode==='distance'||measurementMode==='area')map.getCanvas().style.cursor='';if(measurementMode==='area')map.doubleClickZoom.enable()}
  },[measurementMode])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !loaded.current) return
    ;(map.getSource('routes') as GeoJSONSource | undefined)?.setData(routeData)
  }, [routeData])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !loaded.current) return
    const liveIds = new Set(telemetry.map((item) => item.uav_id))
    markersRef.current.forEach((marker, id) => {
      if (!liveIds.has(id)) {
        marker.remove(); markersRef.current.delete(id); markerElementsRef.current.delete(id); markerInnerRef.current.delete(id); markerPositionRef.current.delete(id)
      }
    })
    telemetry.forEach((item) => {
      const target: [number, number] = [item.lon, item.lat]
      if (!markersRef.current.has(item.uav_id)) {
        const markerElement = document.createElement('button')
        markerElement.type = 'button'
        markerElement.className = 'uav-map-marker'
        markerElement.style.setProperty('--uav-color', item.color)
        markerElement.setAttribute('aria-label', `Выбрать ${item.name}: ${item.task_name}`)
        const glyph = item.uav_type === 'multirotor'
          ? '<svg viewBox="0 0 64 64" aria-hidden="true"><path d="M20 22 44 42M44 22 20 42"/><circle cx="15" cy="17" r="9"/><circle cx="49" cy="17" r="9"/><circle cx="15" cy="47" r="9"/><circle cx="49" cy="47" r="9"/><rect x="24" y="24" width="16" height="16" rx="4"/></svg>'
          : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M17.8 19 16 11l3.5-2.5C21 7.5 22 6 22 5c0-.5-.2-1-.5-1.4C21.1 3.2 20.6 3 20 3c-1 0-2.5 1-3.5 2.5L14 9 6 7.2 4 9l6 3-3 4H4l-2 2 5 1 1 5 2-2v-3l4-3 3.8 6 2-2z"/></svg>'
        markerElement.innerHTML = `<span class="uav-map-marker__pulse"></span><span class="uav-map-marker__aircraft ${item.uav_type}">${glyph}</span>`
        const label = document.createElement('span'); label.className = 'uav-map-marker__label'
        const labelName=document.createElement('strong');labelName.className='uav-map-marker__name';labelName.textContent=item.name.replace('Геоскан ', '')
        const labelPhase=document.createElement('small');labelPhase.className='uav-map-marker__phase'
        label.append(labelName,labelPhase)
        markerElement.append(label)
        markerInnerRef.current.set(item.uav_id, markerElement.querySelector('.uav-map-marker__aircraft') as HTMLSpanElement)
        markerElement.addEventListener('click', (event) => {
          event.stopPropagation()
          onSelectUav(item.uav_id)
        })
        markersRef.current.set(item.uav_id, new maplibregl.Marker({ element: markerElement, anchor: 'center' }).setLngLat(target).addTo(map))
        markerElementsRef.current.set(item.uav_id, markerElement)
        markerPositionRef.current.set(item.uav_id, target)
      }
      const markerElement = markerElementsRef.current.get(item.uav_id)
      markerElement?.classList.toggle('selected', item.uav_id === selectedUavId)
      const compactPhase:Record<string,string>={takeoff:'Взлёт',transit:'К объекту',surveying:'Съёмка',returning:'Возврат',landing:'Посадка',servicing:'Обслуживание'}
      const phaseLabel=markerElement?.querySelector('.uav-map-marker__phase')
      if(phaseLabel)phaseLabel.textContent=compactPhase[item.status]||item.phase_label||item.status
      const inner = markerInnerRef.current.get(item.uav_id)
      const priorHeading = headingsRef.current.get(item.uav_id) ?? item.heading_deg
      const heading = priorHeading + (((item.heading_deg - priorHeading) % 360 + 540) % 360 - 180)
      headingsRef.current.set(item.uav_id, heading)
      if (inner) inner.style.transform = `rotate(${heading - map.getBearing() - (item.uav_type === 'fixed_wing' ? 45 : 0)}deg)`
      const marker = markersRef.current.get(item.uav_id)!
      const from = markerPositionRef.current.get(item.uav_id) || target
      const started = performance.now()
      const priorAnimation = markerAnimationRef.current.get(item.uav_id)
      if (priorAnimation !== undefined) cancelAnimationFrame(priorAnimation)
      const animate = (now: number) => {
        const progress = Math.min(1, (now - started) / 280)
        const eased = progress
        const current: [number, number] = [
          from[0] + (target[0] - from[0]) * eased,
          from[1] + (target[1] - from[1]) * eased,
        ]
        marker.setLngLat(current)
        markerPositionRef.current.set(item.uav_id, current)
        if (progress < 1) markerAnimationRef.current.set(item.uav_id, requestAnimationFrame(animate))
      }
      markerAnimationRef.current.set(item.uav_id, requestAnimationFrame(animate))
    })
  }, [telemetry, selectedUavId, onSelectUav])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !loaded.current) return
    let animation=0,lastFrame=0
    const render=(now:number)=>{
      if(now-lastFrame>=100){lastFrame=now;const epoch=Date.now()/1000
        const data:GeoJSON.FeatureCollection<GeoJSON.Point>={type:'FeatureCollection',features:externalAircraft.map(item=>{
          const seconds=Math.max(0,Math.min(30,item.position_time?epoch-item.position_time:item.position_age_seconds||0))
          const distance=(item.speed_kmh||0)/3.6*seconds;const heading=item.heading_deg*Math.PI/180
          const lat=item.lat+Math.cos(heading)*distance/111_320
          const lon=item.lon+Math.sin(heading)*distance/(111_320*Math.max(.2,Math.cos(item.lat*Math.PI/180)))
          return{type:'Feature',properties:{id:item.id,callsign:item.callsign||item.id,altitude_m:item.altitude_m,speed_kmh:item.speed_kmh,heading:item.heading_deg,on_ground:item.on_ground,position_age_seconds:seconds,position_source:item.position_source},geometry:{type:'Point',coordinates:[lon,lat]}}
        })};(map.getSource('external-aircraft') as GeoJSONSource|undefined)?.setData(data)}
      animation=requestAnimationFrame(render)
    }
    animation=requestAnimationFrame(render)
    return()=>cancelAnimationFrame(animation)
  }, [externalAircraft])

  useEffect(()=>{
    const map=mapRef.current
    if(!map||!loaded.current)return
    const layer='weather-radar-layer',sourceId='weather-radar'
    if(!radarFrame){if(map.getLayer(layer))map.removeLayer(layer);if(map.getSource(sourceId))map.removeSource(sourceId);return}
    const source=map.getSource(sourceId) as unknown as {updateImage:(options:{url:string;coordinates:[[number,number],[number,number],[number,number],[number,number]]})=>void}|undefined
    if(source)source.updateImage(radarFrame)
    else {map.addSource(sourceId,{type:'image',url:radarFrame.url,coordinates:radarFrame.coordinates});map.addLayer({id:layer,type:'raster',source:sourceId,paint:{'raster-opacity':.67,'raster-fade-duration':0}},'mission-fill')}
  },[radarFrame])

  const selectMeasurement=(tool:'distance'|'area')=>{measurementPointsRef.current=[];setMeasurementLabel('');setMeasurementMode(mode=>mode.startsWith(tool)?'idle':tool)}
  return <><div ref={container} className="map-canvas" aria-label="Карта полётной миссии" />{showMapTools&&<div className="map-tools glass-panel" aria-label="Инструменты карты"><button onClick={()=>mapRef.current?.easeTo({bearing:0,pitch:0,duration:700})} title="Ориентировать карту на север"><Navigation size={13}/><span>Север</span></button><button className={measurementMode.startsWith('distance')?'active':''} onClick={()=>selectMeasurement('distance')} title={measurementMode.startsWith('distance')?'Очистить измерение':'Измерить расстояние между двумя точками'}><Ruler size={13}/><span>{measurementMode==='distance-complete'?measurementLabel:'Длина'}</span></button><button className={measurementMode.startsWith('area')?'active':''} onClick={()=>selectMeasurement('area')} title={measurementMode.startsWith('area')?'Очистить измерение':'Измерить площадь: ставьте точки, завершите двойным кликом'}><Grid3X3 size={13}/><span>{measurementMode==='area-complete'?measurementLabel:'Площадь'}</span></button></div>}</>
}

function Brand() {
  return (
    <div className="brand">
      <div>
        <div className="geoscan-wordmark" aria-label="Geoscan"><span>GEOSC</span><i /><span>N</span></div>
        <div className="brand-sub">MISSION CONTROL · CITYMETRICS</div>
      </div>
    </div>
  )
}

function MissionPanel({ onCalculate, onLoadEconomicsDemo, onSchedule, onOpenResults, missions, scheduledDate, onScheduledDate, scheduledTime, onScheduledTime, deadlineEnabled, onDeadlineEnabled, deadlineDate, onDeadlineDate, deadlineTime, onDeadlineTime, maxUavs, onMaxUavs, selectedPlan, confirmedPlan, drawMode, selectedProduct, onSelectProduct, result, catalogCounts, payloads, uavs, selectedPayloadId, onPayloadChange, gsd, onGsdChange, sideOverlap, onSideOverlapChange, forwardOverlap, onForwardOverlapChange, maxAltitude, onMaxAltitude, selectedProducts, lineSpacing, onLineSpacing, planningWeather, airspaceSettings, onAirspaceSettings, controlLinkSettings, onControlLinkSettings, areaSummary }: {
  lineSpacing: number; onLineSpacing: (value:number) => void;
  maxAltitude: number; onMaxAltitude: (value: number) => void; selectedProducts: ProductId[];
  onCalculate: () => void;
  onLoadEconomicsDemo:()=>void;
  onSchedule: () => void;
  onOpenResults:()=>void;
  missions: ScheduledMission[];
  scheduledDate: string; onScheduledDate: (value:string)=>void;
  scheduledTime: string; onScheduledTime: (value:string)=>void;
  deadlineEnabled:boolean; onDeadlineEnabled:(value:boolean)=>void;
  deadlineDate:string; onDeadlineDate:(value:string)=>void;
  deadlineTime:string; onDeadlineTime:(value:string)=>void;
  maxUavs:number|null; onMaxUavs:(value:number|null)=>void;
  selectedPlan: PlanId;
  confirmedPlan:PlanId|null;
  drawMode: boolean;
  selectedProduct: ProductId;
  onSelectProduct: (product: ProductId) => void;
  result: OptimizationResult | null;
  catalogCounts: { uavs: number; payloads: number };
  payloads: CatalogPayload[];
  uavs: CatalogUav[];
  selectedPayloadId: string;
  onPayloadChange: (id: string) => void;
  gsd: number;
  onGsdChange: (value: number) => void;
  sideOverlap: number;
  onSideOverlapChange: (value: number) => void;
  forwardOverlap: number;
  onForwardOverlapChange: (value: number) => void;
  planningWeather?: WeatherResponse['hourly'][number];
  airspaceSettings:AirspaceSettings;onAirspaceSettings:(value:AirspaceSettings)=>void;
  controlLinkSettings:ControlLinkSettings;onControlLinkSettings:(value:ControlLinkSettings)=>void;
  launchSites:LaunchSite[];launchPoint:[number,number]|null;selectedLaunchSiteId:string|null;onLaunchSite:(id:string)=>void;
  areaSummary?:string;
}) {
  const [engineeringOpen, setEngineeringOpen] = useState(false)
  const [operationalChecksConfirmed,setOperationalChecksConfirmed]=useState(false)
  const [stage,setStage]=useState(1)
  const product = missionProducts[selectedProduct]
  const compatiblePayloads = payloads.filter((payload) => payload.spectrums.includes(product.surveyType))
  const payload = compatiblePayloads.find((item) => item.id === selectedPayloadId) || compatiblePayloads[0]
  const optical = product.surveyType !== 'lidar' && product.surveyType !== 'geophysical'
  const engineering = result?.plans.find((plan) => plan.id === result.recommended_plan_id)?.vehicles[0]?.engineering
  const requiredAltitude = opticalAltitudeForGsd(gsd,payload)
  const calculatedAltitude = Math.min(5000, requiredAltitude ?? 5000)
  const calculatedSwath = payload?.focal_length_mm && payload.sensor_width_mm
    ? calculatedAltitude * payload.sensor_width_mm / payload.focal_length_mm
    : 160
  const calculatedGsd = payload?.focal_length_mm && payload.sensor_width_mm && payload.resolution_width_px
    ? calculatedAltitude * payload.sensor_width_mm / payload.focal_length_mm / payload.resolution_width_px * 100
    : 0
  const suitableUavs = payload ? uavs.filter((uav) => uav.status === 'ready' && payload.compatible_uav_ids?.includes(uav.id)) : []
  const selectedPlanResult = result?.plans.find((plan) => plan.id === selectedPlan)
  const settlementAssessment=result?.settlement_assessments?.[selectedPlan]||result?.settlement_assessment
  const settlementDataIncomplete=Boolean(settlementAssessment&&settlementAssessment.status!=='COVERED')
  const needsOperationalCheck=Boolean(selectedPlanResult?.deployment?.required||selectedPlanResult?.link_assessment?.status==='coverage_unverified'||settlementDataIncomplete)
  const routeNeedsCorrection=Boolean(selectedPlanResult?.airspace_avoidance?.unresolved)
  useEffect(()=>setOperationalChecksConfirmed(false),[result,selectedPlan])
  const plannedDuration = Math.max(1, (selectedPlanResult?.duration_min || 180) / 60)
  const freeIds = availableUavIds(suitableUavs.map(uav=>uav.id), missions, scheduledDate, scheduledTime, plannedDuration)
  const availableUavs = suitableUavs.filter(uav=>freeIds.includes(uav.id))
  const plannedVehicles=selectedPlanResult?.vehicles||[]
  const busyPlanVehicles=plannedVehicles.filter(vehicle=>!availableUavIds([vehicle.uav_id],missions,scheduledDate,scheduledTime,plannedDuration).includes(vehicle.uav_id))
  const fleetAvailable=plannedVehicles.length>0&&busyPlanVehicles.length===0
  const pastDate = scheduledDate < localDateIso()
  const weatherScore=planningWeather?Math.max(0,Math.round(100-Math.min(45,planningWeather.wind_speed_mps*3)-Math.min(30,planningWeather.precipitation_probability*.3)-(planningWeather.visibility_m<5000?25:planningWeather.visibility_m<10000?10:0))):null
  const airspace=result?.airspace
  const airspaceLabel=airspace?.status==='permission_required'?'Требуется разрешение':airspace?.status==='notification_required'?'Требуется уведомление':airspace?.status==='adjustment_required'?'Маршрут требует проверки':airspace?.status==='clear'?'Прямых конфликтов нет':'Проверка при расчёте'
  return (
    <aside className={`mission-panel glass-panel mission-stage-${stage}`}>
      <div className="panel-heading">
        <div>
          <div className="eyebrow"><Sparkles size={13} /> НОВОЕ ЗАДАНИЕ</div>
          <h1>{stage===1?'Территория задания':stage===3?'Ресурсы и время':stage===4?'Проверка и расчёт':product.title}</h1>
          <p className="mission-subtitle">{stage===1?'Контур, стартовая площадка и ограничения':stage===2?'Результат определяет технологию, сенсор и профиль приёмки':stage===3?'БВС, доступность и время выполнения':'Контроль условий перед запуском оптимизации'}</p>
        </div>
      </div>

      <nav className="wizard-progress" aria-label="Этапы задания">
        {(['Территория','Съёмка','Ресурсы','Расчёт'] as const).map((label,index)=><button key={label} type="button" className={stage===index+1?'active':stage>index+1?'complete':''} onClick={()=>setStage(index+1)} aria-current={stage===index+1?'step':undefined}><b>{String(index+1).padStart(2,'0')}</b>{label}</button>)}
      </nav>
      <p className="wizard-stage-note">{stage===1?`${areaSummary?`Контур на карте: ${areaSummary}. `:''}Площадку старта и контур выберите на карте справа.`:stage===2?'Выберите результат съёмки. Сенсор и инженерные параметры доступны ниже.':stage===3?'Проверьте совместимые борта, канал связи и время выполнения.':'Проверьте ограничения, затем рассчитайте и подтвердите сценарий.'}</p>

      <div className="field-group result-picker">
        <label><b>02</b> Результат выполнения</label>
        <div className="result-grid">
          {(Object.entries(missionProducts) as [ProductId, typeof product][]).map(([id, item]) => {
            const Icon = item.icon
            return <button key={id} aria-pressed={selectedProducts.includes(id)} className={selectedProducts.includes(id) ? 'selected' : ''} onClick={() => onSelectProduct(id)} title={item.title}><Icon size={15} /><span>{item.short}</span>{selectedProducts.includes(id) && <Check size={11} />}</button>
          })}
        </div>
      </div>

      <div className="mission-engineering-grid"><div className="mission-engineering-main">
      <details className="engineering-disclosure" open={engineeringOpen} onToggle={(event) => setEngineeringOpen(event.currentTarget.open)}>
        <summary><span><SlidersHorizontal size={15} /> Инженерные параметры</span><em>{optical ? 'ОПТИКА И КАЧЕСТВО' : 'ПРОФИЛЬНАЯ СЪЁМКА'}</em><ChevronDown size={16} /></summary>
      <div className="engineering-card">
        <label className="payload-select"><small>Полезная нагрузка <FieldHelp label="Полезная нагрузка" text="Камера, сканер или другой датчик. Его матрица, объектив, масса и частота срабатывания ограничивают высоту, скорость и совместимые БВС." /></small><select value={payload?.id || ''} onChange={(event) => onPayloadChange(event.target.value)}>{compatiblePayloads.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
        <div className="uav-candidates"><small>Совместимые борта</small>{suitableUavs.length ? suitableUavs.map((uav) => <span key={uav.id}><Plane size={13} /> {uav.name}</span>) : <span>Нет готовых совместимых бортов</span>}</div>
        {optical ? <>
        <div className="engineering-inputs">
          <label><span>GSD ≤ <FieldHelp label="GSD" text="Размер одного пикселя на земле. Чем меньше значение, тем выше детализация и обычно больше снимков и время обработки." /><b>{gsd.toFixed(1)} см/пикс</b></span><input aria-label="Требуемый GSD" type="range" min="0.5" max="12" step="0.1" value={gsd} onChange={(event) => onGsdChange(Number(event.target.value))} /></label>
          <label><span>Продольное <FieldHelp label="Продольное перекрытие" text="Доля наложения соседних кадров вдоль направления полёта. Большее значение сокращает шаг фотографирования и повышает число снимков." /><b>{Math.round(forwardOverlap * 100)}%</b></span><input aria-label="Продольное перекрытие" type="range" min="55" max="90" step="5" value={forwardOverlap * 100} onChange={(event) => onForwardOverlapChange(Number(event.target.value) / 100)} /></label>
          <label><span>Поперечное <FieldHelp label="Поперечное перекрытие" text="Доля наложения соседних полос съёмки. Большее значение уменьшает расстояние между галсами и увеличивает объём полёта." /><b>{Math.round(sideOverlap * 100)}%</b></span><input aria-label="Поперечное перекрытие" type="range" min="30" max="85" step="5" value={sideOverlap * 100} onChange={(event) => onSideOverlapChange(Number(event.target.value) / 100)} /></label>
        </div>
        <p className="planner-note">{requiredAltitude===null?'Параметры камеры ещё не загружены.':`По выбранному GSD и камере предварительная высота съёмки — ${requiredAltitude.toFixed(0)} м AGL. В расчёте она дополнительно ограничивается характеристиками совместимого БВС.`} Допустимую высоту, воздушное пространство и необходимые разрешения проверяют отдельно: расчёт не даёт права на полёт.</p>
        <div className="engineering-proof">
          <span><Ruler size={12} /><small>Высота</small><b>{engineering ? Math.round(result!.plans.find((plan) => plan.id === result!.recommended_plan_id)!.vehicles[0].altitude_m) : Math.round(calculatedAltitude)} м</b></span>
          <span><Camera size={12} /><small>Захват</small><b>{engineering?.footprint_width_m ?? Math.round(calculatedSwath)} м</b></span>
          <span><Gauge size={12} /><small>GSD факт</small><b>{(engineering?.achieved_gsd_cm_px ?? calculatedGsd).toFixed(2)} см</b></span>
        </div>
        <div className="engineering-result"><span>Шаг галсов <b>{(calculatedSwath * (1-sideOverlap)).toFixed(1)} м</b></span><span>Шаг кадров <b>{(calculatedAltitude * (payload?.sensor_height_mm || 0) / (payload?.focal_length_mm || 1) * (1-forwardOverlap)).toFixed(1)} м</b></span></div>
        {engineering && <div className="engineering-result">
          <span>Шаг галсов <b>{engineering.line_spacing_m} м</b></span>
          <span>Интервал кадров <b>{engineering.trigger_interval_s} с</b></span>
          <span>Кадров на сектор <b>≈ {engineering.estimated_frames.toLocaleString('ru-RU')}</b></span>
          <span>Частота <b>{engineering.required_fps} / {engineering.camera_fps} Гц</b></span>
          <span>V земли для затвора <b>{engineering.trigger_design_speed_mps} м/с</b></span>
        </div>}
        <p className="planner-note">После расчёта интервал кадров проверяется для попутного ветра; время полёта — для встречного. Скорость может снижаться из-за частоты камеры. Минимум самолёта 60 км/ч — допущение, которое требует паспорта борта.</p>
        <code>H = GSD × f × Nₓ / Sₓ · шаг = захват × (1 − overlap)</code>
        </> : <><div className="engineering-inputs"><label><span>Шаг профилей <FieldHelp label="Шаг профилей" text="Расстояние между соседними линиями профильной съёмки. Меньший шаг повышает плотность покрытия, но увеличивает длину маршрута." /><b>{lineSpacing} м</b></span><input aria-label="Шаг профилей" type="range" min="5" max="150" step="5" value={lineSpacing} onChange={e=>onLineSpacing(Number(e.target.value))}/></label></div><details className="sensor-altitude-details"><summary>Высота датчика · {maxAltitude} м AGL <ChevronDown size={14}/></summary><label><span>Сценарная высота профиля</span><input aria-label="Высота профильной съёмки" type="range" min="30" max="500" step="10" value={maxAltitude} onChange={e=>onMaxAltitude(Number(e.target.value))}/></label><small>Параметр датчика, не правовой предел. Рельеф и условия полёта проверяются отдельно.</small></details><p className="planner-note">{product.surveyType === 'lidar' ? 'Полоса 160 м — сценарное допущение, не паспортная константа сканера. Рассчитываются профили, время и затраты. Для плотности нужны PRR, угол сканирования, доля полезных импульсов и скорость; точность требует GNSS/IMU и калибровки.' : 'Считаем длину основных профилей, перелёты и стоимость. Шаг профилей задаётся проектом. Высота датчика зависит от длины подвеса; контрольные линии, рельеф и магнитные вариации не рассчитаны.'} GSD и фотографическое перекрытие к этому расчёту неприменимы.</p></>}
      </div>
      </details>

      </div><div className="mission-engineering-side">

      <section className={`airspace-check-card ${airspace?.status||'pending'}`}>
        <header><span><ShieldAlert size={16}/><strong>Воздушное пространство</strong></span><em>{airspaceLabel}</em></header>
        <div className="airspace-clearance-grid">
          <label>Запретные <b>{airspaceSettings.prohibited} м</b><input type="range" min="0" max="1000" step="50" value={airspaceSettings.prohibited} onChange={event=>onAirspaceSettings({...airspaceSettings,prohibited:Number(event.target.value)})}/></label>
          <label>Опасные <b>{airspaceSettings.danger} м</b><input type="range" min="0" max="1000" step="50" value={airspaceSettings.danger} onChange={event=>onAirspaceSettings({...airspaceSettings,danger:Number(event.target.value)})}/></label>
          <label>Препятствия <b>{airspaceSettings.obstacle} м</b><input type="range" min="20" max="500" step="10" value={airspaceSettings.obstacle} onChange={event=>onAirspaceSettings({...airspaceSettings,obstacle:Number(event.target.value)})}/></label>
          <label>Между БВС <b>{airspaceSettings.separation} м</b><input type="range" min="20" max="500" step="20" value={airspaceSettings.separation} onChange={event=>onAirspaceSettings({...airspaceSettings,separation:Number(event.target.value)})}/></label>
          <label className="settlement-clearance">Населённые пункты <b>{airspaceSettings.settlement} м</b><input aria-label="Проектный отступ от населённых пунктов" type="range" min="0" max="1000" step="25" value={airspaceSettings.settlement} onChange={event=>onAirspaceSettings({...airspaceSettings,settlement:Number(event.target.value)})}/></label>
        </div>
        {airspace&&<div className="airspace-check-summary"><span><b>{airspace.conflicts.length}</b> конфликтов</span><span><b>{airspace.authorities.length}</b> зон ОрВД</span><span><b>{result?.plans.find(plan=>plan.id===selectedPlan)?.airspace_avoidance?.detours||0}</b> обходов</span></div>}
        {settlementAssessment&&<p>Границы населённых пунктов: {settlementAssessment.tiles_fresh} из {settlementAssessment.tiles_total} участков проверено · пересечений {settlementAssessment.intersections.length}. {settlementDataIncomplete?'Данные неполные: календарь будет только предварительной симуляцией, не разрешением на вылет.':'Источник OSM — предварительный, не юридическое подтверждение.'}</p>}
        <p>{airspace?.messages[0]||'Учитываются высота, время действия и буферы. Расчёт не заменяет разрешение на полёт.'}</p>
        <p>Отступ от населённых пунктов — проектный запас от загруженных границ OSM, а не установленная законом дистанция.</p>
      </section>

      <section className="control-link-card">
        <header><Radio size={16}/><strong>Канал управления</strong></header>
        <label>Тип связи<select aria-label="Тип канала управления" value={controlLinkSettings.mode} onChange={event=>onControlLinkSettings({...controlLinkSettings,mode:event.target.value as ControlLinkSettings['mode']})}><option value="radio">Прямая радиосвязь</option><option value="external">Сотовый / внешний канал</option></select></label>
        {controlLinkSettings.mode==='radio'?<><div className="control-link-fields"><label>Дальность оборудования, км<input type="number" min="1" max="500" value={controlLinkSettings.equipmentRangeKm} onChange={event=>onControlLinkSettings({...controlLinkSettings,equipmentRangeKm:Math.min(500,Math.max(1,Number(event.target.value)||1))})}/></label><label>Антенна на земле, м<input type="number" min="0" max="100" value={controlLinkSettings.groundAntennaHeightM} onChange={event=>onControlLinkSettings({...controlLinkSettings,groundAntennaHeightM:Math.min(100,Math.max(0,Number(event.target.value)||0))})}/></label></div><p>Расчётный предел: оборудование ∩ радиогоризонт с запасом 20%. Рельеф, помехи и паспорт канала проверяются отдельно.</p></>:<p>Маршрут не ограничивается прямой видимостью радиостанции. Покрытие сотового или иного канала пока не подтверждено и требует проверки по всей траектории.</p>}
        {selectedPlanResult?.link_assessment&&<div className="control-link-result">Удаление до {selectedPlanResult.link_assessment.max_distance_km.toLocaleString('ru-RU')} км · {selectedPlanResult.link_assessment.mode==='radio'?`расчётный предел ${selectedPlanResult.link_assessment.planning_limit_km?.toLocaleString('ru-RU')} км`:'покрытие не подтверждено'}</div>}
      </section>

      <section className="schedule-card">
        <div className="schedule-heading"><span><CalendarClock size={16}/> Дата и доступность</span><em>{result ? `${plannedDuration.toFixed(1)} ч по расчёту` : 'оценка 3,0 ч'}</em></div>
        {result&&<div className="schedule-plan-confirmation"><span>{confirmedPlan===selectedPlan?`Для календаря подтверждён: ${selectedPlanResult?.label}`:`На карте предпросмотр: ${selectedPlanResult?.label}. Для календаря подтвердите вариант в результатах.`}</span>{confirmedPlan!==selectedPlan&&<button type="button" onClick={onOpenResults}>Выбрать вариант</button>}</div>}
        <div className="schedule-fields"><label>Дата<input type="date" min={localDateIso()} value={scheduledDate} onChange={event=>onScheduledDate(event.target.value)}/></label><label>Старт<input type="time" step="900" value={scheduledTime} onChange={event=>onScheduledTime(event.target.value)}/></label></div>
        <div className="planning-constraints"><label>Доступно БВС<select aria-label="Ограничение числа БВС" value={maxUavs??'all'} onChange={event=>onMaxUavs(event.target.value==='all'?null:Number(event.target.value))}><option value="all">Весь совместимый флот</option>{[1,2,3,4].map(count=><option key={count} value={count}>Не более {count}</option>)}</select></label><label className="deadline-toggle"><input type="checkbox" checked={deadlineEnabled} onChange={event=>onDeadlineEnabled(event.target.checked)}/>Завершить к сроку</label>{deadlineEnabled&&<><label>Дата окончания<input aria-label="Дата окончания" type="date" min={scheduledDate} value={deadlineDate} onChange={event=>onDeadlineDate(event.target.value)}/></label><label>Время окончания<input aria-label="Время окончания" type="time" step="900" value={deadlineTime} onChange={event=>onDeadlineTime(event.target.value)}/></label></>}</div>
        <div className={`availability-state ${pastDate||(result?!fleetAvailable:!availableUavs.length)?'unavailable':'available'}`}><span className="pulse-dot"/><div><strong>{pastDate?'Прошедшая дата недоступна':result?(fleetAvailable?`Все ${plannedVehicles.length} БВС выбранного плана свободны`:`Заняты: ${busyPlanVehicles.map(vehicle=>vehicle.uav_name).join(', ')}`):availableUavs.length?`${availableUavs.length} из ${suitableUavs.length} совместимых бортов свободны`:'Все совместимые борта заняты'}</strong><small>{pastDate?'Выберите сегодня или будущую дату.':fleetAvailable||!result?`Окно ${scheduledTime}–${new Date(new Date(`2000-01-01T${scheduledTime}`).getTime()+plannedDuration*3_600_000).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'})}`:'Выберите другой сценарий, время или дату и пересчитайте план.'}</small></div></div>
        <div className={`planning-weather ${weatherScore===null?'unknown':weatherScore<55?'critical':weatherScore<78?'attention':'good'}`}><CloudSun size={16}/><div><strong>{weatherScore===null?'Прогноз пока недоступен':`Погодное окно ${weatherScore}/100`}</strong><small>{planningWeather?`ветер ${planningWeather.wind_speed_mps.toFixed(1)} м/с · осадки ${planningWeather.precipitation_probability}% · видимость ${(planningWeather.visibility_m/1000).toFixed(0)} км`:'Дата находится за пределами доступного прогноза; потребуется повторная проверка перед вылетом.'}</small></div></div>
        {!!plannedVehicles.length && <div className="available-uavs"><small>Борта выбранного плана</small><div>{plannedVehicles.map(vehicle=><span className="plan-assigned-uav" key={vehicle.uav_id}><Plane size={13}/>{vehicle.uav_name} · {vehicle.sorties||1} выл.</span>)}</div></div>}
        {selectedPlanResult&&selectedPlanResult.duration_min>720&&<p className="planner-campaign-warning">Расчёт занимает {(selectedPlanResult.duration_min/60).toFixed(1)} ч. Это кампания, а не один рабочий день: разбейте контур на дневные задания перед назначением в календарь. Сервис пока не назначает многодневную работу автоматически.</p>}
        {needsOperationalCheck&&<label className="operational-check"><input type="checkbox" checked={operationalChecksConfirmed} onChange={event=>setOperationalChecksConfirmed(event.target.checked)}/><span>{selectedPlanResult?.deployment?.required&&`Подтверждаю перебазирование БВС на ${selectedPlanResult.deployment.max_distance_km?.toLocaleString('ru-RU')} км. `}{selectedPlanResult?.link_assessment?.status==='coverage_unverified'&&'Покрытие внешнего канала по маршруту проверено. '}{settlementDataIncomplete&&'Понимаю: границы населённых пунктов загружены не полностью, запись в календаре — предварительная симуляция, не разрешение на вылет. '}Эти проверки и затраты на доставку не входят в расчёт.</span></label>}
        <button className="schedule-submit" disabled={!result||confirmedPlan!==selectedPlan||pastDate||!fleetAvailable||routeNeedsCorrection||(needsOperationalCheck&&!operationalChecksConfirmed)||Boolean(selectedPlanResult&&selectedPlanResult.duration_min>720)} onClick={onSchedule}><CalendarClock size={17}/>{!result?'Сначала рассчитайте план':confirmedPlan!==selectedPlan?'Подтвердите вариант в результатах':selectedPlanResult&&selectedPlanResult.duration_min>720?'Разделите кампанию по дням':routeNeedsCorrection?'Нужна корректировка маршрута':!fleetAvailable?'Один из бортов плана занят':needsOperationalCheck&&!operationalChecksConfirmed?'Подтвердите предварительные условия':`Назначить ${plannedVehicles.length} БВС · ${selectedPlanResult?.sorties||0} выл.`}</button>
      </section>
      </div></div>

      <div className="acceptance-card">
        <div className="acceptance-head"><span><b>03</b> Профиль приёмки</span><em>ПРОВЕРЯЕМЫЙ</em></div>
        <div><small>Технология</small><strong>{product.technology}</strong></div>
        <div><small>Критерий</small><strong>{product.acceptance}</strong></div>
        <div><small>Выдача</small><strong>{product.format}</strong></div>
      </div>

      <div className="field-grid deadline-row" hidden>
        <div className="field-group">
          <label>Срок результата</label>
          <button className="select-field"><CalendarClock size={16} /><span>Сегодня, 18:00</span></button>
        </div>
        <div className="field-group">
          <label>Качество</label>
          <button className="select-field compact"><span>{gsd.toFixed(1)} см/px</span></button>
        </div>
      </div>

      <div className="analysis-strip">
        <span><b>{result?.analysis?.available_uavs ?? catalogCounts.uavs}</b><small>доступно БВС</small></span>
        <span><b>{result?.analysis?.compatible_uavs ?? '—'}</b><small>совместимы</small></span>
        <span><b>{planningWeather?.wind_speed_mps.toFixed(1)??'—'}</b><small>ветер м/с · прогноз</small></span>
      </div>

      <button className="calculate-button" onClick={onCalculate} disabled={drawMode}>
        <Command size={18} />
        Рассчитать производственный план
        <span>⌘ ↵</span>
      </button>
      <button type="button" className="fleet-demo-button" onClick={onLoadEconomicsDemo}>Показать пример: 1 БВС / 3 вылета ↔ 3 БВС / по 1 вылету</button>
      <p className="fleet-demo-hint">Загрузит учебный контур и ВПП Лигачёво с курсом 180°. Затем нажмите «Рассчитать».</p>
      <div className="secure-note"><ShieldCheck size={14} /> {catalogCounts.payloads} нагрузок · проверка совместимости и экономики</div>
      <div className="wizard-stage-actions">{stage>1&&<button type="button" onClick={()=>setStage(stage-1)}>← Назад</button>}{stage<4&&<button type="button" className="primary" onClick={()=>setStage(stage+1)}>Далее →</button>}{stage===4&&result&&<button type="button" className="primary" onClick={onOpenResults}>Смотреть сценарии →</button>}</div>
    </aside>
  )
}

function FieldHelp({label,text}:{label:string;text:string}) {
  return <span className="field-help"><button type="button" aria-label={`Подсказка: ${label}`}><CircleHelp size={14}/></button><span role="tooltip">{text}</span></span>
}

function MethodologyModal({onClose}:{onClose:()=>void}){
  useEffect(()=>{const close=(event:KeyboardEvent)=>event.key==='Escape'&&onClose();window.addEventListener('keydown',close);return()=>window.removeEventListener('keydown',close)},[onClose])
  return <div className="methodology-backdrop" role="presentation" onMouseDown={event=>event.target===event.currentTarget&&onClose()}><section className="methodology-modal" role="dialog" aria-modal="true" aria-labelledby="methodology-title"><header><div><span>ИНЖЕНЕРНАЯ СПРАВКА</span><h2 id="methodology-title">Как рассчитывается миссия</h2><p>Ключевые этапы расчёта и проверок.</p></div><button onClick={onClose} aria-label="Закрыть"><X size={18}/></button></header><div className="methodology-flow"><span>Геометрия</span><span>Ограничения</span><span>Совместимость</span><span>Энергия</span><span>Распределение БВС</span><span>Оптимизация</span></div><p className="methodology-summary">Алгоритм строит покрытие территории, проверяет ограничения и допустимые комплекты БВС с сенсором, оценивает каждый вылет и сравнивает варианты по времени, налёту и затратам.</p><details className="methodology-details"><summary>Показать инженерные детали</summary><div className="methodology-grid"><article><h3>Геометрия съёмки</h3><p>Для площадного объекта рассчитываются захват кадра, шаг галсов и интервал срабатывания камеры. Для коридора и профильных методов используются ось объекта, ширина обследования и заданный шаг профилей.</p><code>H = (GSD / 100) × f × Nₓ / Sₓ<br/>W = H × Sₓ / f<br/>dгалс = W × (1 − pпопер.)<br/>dкадр = H × Sᵧ / f × (1 − pпрод.)</code></article><article><h3>Ветер и энергия</h3><p>Направление галсов сопоставляется с прогнозом ветра. Время и запас энергии оцениваются для неблагоприятной составляющей ветра, а частота съёмки — для максимальной путевой скорости.</p><code>Vземли = Vвозд ± Vветра<br/>Δt = dкадр / Vземли</code></article><article><h3>Воздушное пространство</h3><p>Запретные и опасные зоны, высотные препятствия и дистанции между БВС расширяются на заданный безопасный буфер. Для ОрВД определяется уведомительный или разрешительный порядок.</p></article><article><h3>Эксплуатационный цикл</h3><p>В маршрут входят взлёт, набор, перелёт, рабочие галсы, возврат, заход и посадка. Между повторными вылетами закладывается наземное обслуживание.</p></article></div></details><footer><span>Расчёт помогает планированию, но не заменяет разрешение на полёт, обследование площадки и требования РЛЭ.</span></footer></section></div>
}

function TopBar({ user, systemOk, demoMode, onDemoMode, onOpenCatalog, active, onView, onLogout }: { user: User; systemOk: boolean; demoMode:boolean; onDemoMode:(enabled:boolean)=>void; onOpenCatalog: (section: CatalogSection) => void; active: string; onView: (view: 'operations' | 'orders' | 'planner' | 'calendar' | 'airspace' | 'bases') => void; onLogout:()=>void }) {
  return (
    <header className="top-bar glass-panel">
      <Brand />
      <nav className="product-nav" aria-label="Основные разделы">
        <button className={`demo-mode-toggle ${demoMode?'enabled':''}`} aria-pressed={demoMode} onClick={()=>onDemoMode(!demoMode)} title={demoMode?'Показана витринная симуляция 10 БВС':'Показаны полёты из календаря'}><Play size={14}/><span>Демо</span><i><b/></i></button>
        <button className={active === 'operations' ? 'active' : ''} onClick={() => onView('operations')}><Route size={15} /> Центр полётов</button>
        <button className={active === 'orders' ? 'active' : ''} onClick={() => onView('orders')}><ClipboardList size={15} /> Заявки</button>
        <button className={active === 'planner' ? 'active' : ''} onClick={() => onView('planner')}><MapIcon size={15} /> Новое задание</button>
        <button className={active === 'calendar' ? 'active' : ''} onClick={() => onView('calendar')}><CalendarClock size={15} /> Планировщик</button>
        <button className={active === 'airspace' || active === 'bases' ? 'active' : ''} onClick={() => onView('airspace')}><MapPin size={15} /> Объекты</button>
        <button className={['uavs','payloads','technologies'].includes(active) ? 'active' : ''} onClick={() => onOpenCatalog('uavs')}><Plane size={15} /> Оборудование</button>
      </nav>
      <div className="top-actions">
        <div className={`system-status ${systemOk ? '' : 'degraded'}`}><span /> {systemOk ? 'СИСТЕМЫ В НОРМЕ' : 'АВТОНОМНЫЙ РЕЖИМ'}</div>
        <details className="account-menu"><summary className="user-button"><CircleUserRound size={19} /><div><strong>{user.full_name}</strong><small>{user.role === 'dispatcher' ? 'Диспетчер' : user.role === 'manager' ? 'Руководитель' : 'Администратор'}</small></div><ChevronDown size={15} /></summary><div className="account-popover"><div><strong>{user.full_name}</strong><small>Роль: {user.role === 'dispatcher' ? 'диспетчер' : user.role === 'manager' ? 'руководитель' : 'администратор'}</small></div><button onClick={onLogout}><LogOut size={15}/> Выйти из системы</button></div></details>
      </div>
    </header>
  )
}

function fleetPhoto(uav: CatalogUav) {
  if (uav.series?.includes('701')) return {src:'/fleet/geoscan-701.jpg',viewBox:'',width:768,height:768}
  if (uav.series?.includes('GEMINI')) return {src:'/fleet/geoscan-gemini-source.png',viewBox:'340 775 374 233',width:803,height:1219}
  if (uav.type === 'multirotor') return {src:'/fleet/geoscan-401-reference.png',viewBox:'72 69 391 280',width:1006,height:1051}
  return {src:'/fleet/geoscan-wing-source.png',viewBox:'339 28 413 285',width:785,height:1150}
}

function FleetPhoto({uav}: {uav: CatalogUav}) {
  const photo = fleetPhoto(uav)
  return photo.viewBox ? <svg className="reference-photo" viewBox={photo.viewBox} role="img" aria-label={uav.name} preserveAspectRatio="xMidYMid slice"><image href={photo.src} width={photo.width} height={photo.height}/></svg> : <img className="device-photo" src={photo.src} alt={uav.name} loading="lazy" decoding="async"/>
}

function CatalogWorkspace({ section, onSection, onClose, uavs, payloads, technologies, missions, onOpenSchedule, onPlanMaintenance }: {
  section: CatalogSection;
  onSection: (section: CatalogSection) => void;
  onClose: () => void;
  uavs: CatalogUav[];
  payloads: CatalogPayload[];
  technologies: TechnologyProfile[];
  missions: ScheduledMission[];
  onOpenSchedule: (uavId:string) => void;
  onPlanMaintenance: (uavId:string) => void;
}) {
  const [detail, setDetail] = useState<{kind:CatalogSection;id:string} | null>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (!detail) return
    const previous = document.activeElement as HTMLElement | null
    closeRef.current?.focus()
    const escape = (e:KeyboardEvent) => { if (e.key === 'Escape') setDetail(null) }
    window.addEventListener('keydown',escape)
    return () => { window.removeEventListener('keydown',escape); previous?.focus() }
  }, [detail])
  const detailUav = detail?.kind === 'uavs' ? uavs.find(v=>v.id===detail.id) : undefined
  const detailPayload = detail?.kind === 'payloads' ? payloads.find(v=>v.id===detail.id) : undefined
  const detailTech = detail?.kind === 'technologies' ? technologies.find(v=>v.id===detail.id) : undefined
  const detailResource = detailUav ? fleetResourceSnapshot(detailUav,missions) : undefined
  const reference = detailPayload ? payloadReferences[detailPayload.id] : undefined
  const techCopy = detailTech ? technologyContent[detailTech.result_type] || technologyContent[detailTech.survey_type] : undefined
  const statusLabel = (status: string) => status === 'ready' ? 'Готов' : status === 'charging' ? 'Заряжается' : status === 'maintenance' ? 'На ТО' : status
  return (
    <section className="catalog-workspace" aria-label="Справочники предприятия">
      <header className="catalog-header">
        <div><span>ЦИФРОВАЯ МОДЕЛЬ ПРЕДПРИЯТИЯ</span><h2>Оборудование</h2><p>БВС, полезные нагрузки и технологические профили. Совместимость и тарифы — учебная конфигурация предприятия.</p></div>
        <button onClick={onClose} aria-label="Закрыть справочники"><X size={21} /></button>
      </header>
      <div className="catalog-tabs">
        <button className={section === 'uavs' ? 'active' : ''} onClick={() => onSection('uavs')}><Plane size={16} /> БВС <b>{uavs.length}</b></button>
        <button className={section === 'payloads' ? 'active' : ''} onClick={() => onSection('payloads')}><Aperture size={16} /> Полезные нагрузки <b>{payloads.length}</b></button>
        <button className={section === 'technologies' ? 'active' : ''} onClick={() => onSection('technologies')}><Database size={16} /> Технологические профили <b>{technologies.length}</b></button>
      </div>

      {section === 'uavs' && <div className="catalog-grid uav-grid">
        {uavs.map((uav) => { const resource=fleetResourceSnapshot(uav,missions); return <article className="catalog-card" key={uav.id} role="button" tabIndex={0} aria-label={`Подробнее: ${uav.name}`} onClick={()=>setDetail({kind:'uavs',id:uav.id})} onKeyDown={e=>{if(e.key==='Enter'||e.key===' ') {e.preventDefault();setDetail({kind:'uavs',id:uav.id})}}}>
          <div className={`catalog-visual fleet-photo ${uav.type}`}><FleetPhoto uav={uav}/><AircraftGlyph type={uav.type} size={31} /><span>{uav.series || 'GEOSCAN'} · ИЛЛЮСТРАЦИЯ МОДЕЛИ</span></div>
          <div className="catalog-card-head"><div><small>{uav.modification || (uav.type === 'fixed_wing' ? 'САМОЛЁТНЫЙ' : 'МУЛЬТИРОТОРНЫЙ')}</small><h3>{uav.name}</h3></div><em className={`status-${uav.status}`}>{statusLabel(uav.status)}</em></div>
          <div className="spec-grid"><span><small>Скорость</small><b>{uav.cruise_speed_kmh} км/ч</b></span><span><small>Полёт</small><b>{Math.floor(uav.endurance_min / 60) ? `${Math.floor(uav.endurance_min / 60)} ч ` : ''}{uav.endurance_min % 60 ? `${uav.endurance_min % 60} мин` : ''}</b></span><span><small>Нагрузка</small><b>{uav.payload_kg} кг</b></span><span><small>Ветер</small><b>до {uav.max_wind_mps} м/с</b></span></div>
          <div className="catalog-next-events"><span><Plane size={13}/>{resource.nextFlight?`${resource.nextFlight.date.split('-').slice(1).reverse().join('.')} · ${resource.nextFlight.title}`:'Вылеты не назначены'}</span><span><Wrench size={13}/>{resource.nextMaintenance?`${resource.nextMaintenance.date.split('-').slice(1).reverse().join('.')} · плановое ТО`:uav.maintenance_metric==='flights'?'ТО каждые 80 полётов · журнал не загружен':uav.maintenance_metric==='engine_hours'?`ТО каждые ${uav.maintenance_interval} моточасов`:'Норма ТО не подтверждена'}</span></div>
          <div className="fleet-flight-hours"><span>Общий налёт</span><b>{uav.flight_hours.toLocaleString('ru-RU', {maximumFractionDigits:1})} ч</b></div>
        </article>})}
      </div>}

      {section === 'payloads' && <div className="catalog-grid payload-grid">
        {payloads.map((payload) => <article className="catalog-card payload-card" key={payload.id} role="button" tabIndex={0} aria-label={`Подробнее: ${payload.name}`} onClick={()=>setDetail({kind:'payloads',id:payload.id})} onKeyDown={e=>{if(e.key==='Enter'||e.key===' ') {e.preventDefault();setDetail({kind:'payloads',id:payload.id})}}}>
          <img className="device-photo" src={payloadReferences[payload.id]?.image} alt={payload.name} loading="lazy" decoding="async" />
          <small>{(payload.category || 'camera').toUpperCase()}</small><h3>{payload.name}</h3><p>{payload.sensor_type}</p>
          <div className="payload-tags">{payload.spectrums.map((spectrum) => <span key={spectrum}>{spectrum}</span>)}</div>
          {!!payload.resolution_width_px && <p>{payload.resolution_width_px} × {payload.resolution_height_px} пикс · f = {payload.focal_length_mm} мм</p>}
          <div className="payload-footer"><span>Масса <b>{payload.mass_kg} кг</b></span><span>Совместимо <b>{payload.compatible_uav_ids?.length || '—'} БВС</b></span></div>
        </article>)}
      </div>}

      {section === 'technologies' && <div className="technology-list">
        {technologies.map((technology, index) => <article key={technology.id} role="button" tabIndex={0} aria-label={`Подробнее: ${technology.name}`} onClick={()=>setDetail({kind:'technologies',id:technology.id})} onKeyDown={e=>{if(e.key==='Enter'||e.key===' ') {e.preventDefault();setDetail({kind:'technologies',id:technology.id})}}}>
          <div className="technology-number">{String(index + 1).padStart(2, '0')}</div>
          <div><small>{technology.survey_type}</small><h3>{technology.name}</h3><p>{technology.purpose}</p></div>
          <div><small>Критерии приёмки</small>{technology.acceptance.map((item) => <span key={item}><Check size={13} /> {item}</span>)}</div>
          <div><small>Комплект выдачи</small><p>{technology.deliverables.join(' · ')}</p></div>
        </article>)}
      </div>}
      {detail && <div className="detail-backdrop" onClick={()=>setDetail(null)}><section className="catalog-detail" role="dialog" aria-modal="true" aria-label="Подробная информация" onClick={e=>e.stopPropagation()}><button ref={closeRef} className="catalog-detail-close" onClick={()=>setDetail(null)} aria-label="Закрыть подробную карточку" title="Закрыть"><X size={20}/></button>
        <h2>{detailUav?.name || detailPayload?.name || detailTech?.name}</h2>
        {detailUav && detailResource && <><FleetPhoto uav={detailUav}/><section className="catalog-detail-resource"><header><div><small>ПРОГНОЗ РЕСУРСА · УЧЕБНАЯ ОЦЕНКА</small><strong>{detailUav.maintenance_metric==='flights'?'Регламент ТО: каждые 80 полётов':detailUav.maintenance_metric==='engine_hours'?`Регламент ТО: каждые ${detailUav.maintenance_interval} моточасов`:'Регламент ТО требует уточнения'}</strong></div><span className={detailResource.risk}>{detailResource.risk==='normal'?'Штатно':detailResource.risk==='attention'?'Внимание':'Критично'}</span></header><div>{([['Планер',detailResource.airframe],['Силовая установка',detailResource.propulsion],['Энергосистема',detailResource.power]] as const).map(([label,value])=><article key={label} className={value>=65?'critical':value>=40?'attention':'normal'}><span>{label}<b>{value}%</b></span><i><b style={{width:`${value}%`}}/></i></article>)}</div><p>Будущие задания: {detailResource.plannedHours.toFixed(1)} ч · {detailUav.maintenance_metric==='flights'?'счётчик полётов после последнего ТО не загружен; дата ТО по часам не определяется':`демонстрационный остаток ${detailResource.projectedRemainingHours.toFixed(1)} ч`}. Индексы узлов — демонстрационная модель, не регламент Геоскана.</p></section><section className="catalog-detail-schedule"><h3>Ближайшие события</h3>{missions.filter(m=>m.uavId===detailUav.id&&slotStart(m.date,m.time)>=Date.now()).sort((a,b)=>slotStart(a.date,a.time)-slotStart(b.date,b.time)).slice(0,4).map(m=><div key={m.id} className={m.status}><span>{m.status==='maintenance'?<Wrench/>:<Plane/>}</span><div><strong>{m.title}</strong><small>{m.date.split('-').reverse().join('.')} · {m.time} · {m.duration.toFixed(1)} ч</small></div></div>)}</section><h3>Назначение платформы</h3><p>{detailUav.type==='multirotor' ? 'Мультироторная платформа: вертикальный взлёт и посадка, работа с небольшими участками, деталями объектов и специализированными нагрузками.' : 'Самолётная платформа: продолжительная площадная и коридорная съёмка. Требует места для запуска, разворотов и посадки.'}</p><h3>Параметры учебного борта</h3><dl><dt>Крейсерская скорость</dt><dd>{detailUav.cruise_speed_kmh} км/ч</dd><dt>Автономность</dt><dd>{detailUav.endurance_min} мин</dd><dt>Масса нагрузки</dt><dd>до {detailUav.payload_kg} кг</dd></dl><div className="catalog-detail-actions"><button onClick={()=>{setDetail(null);onOpenSchedule(detailUav.id)}}><CalendarDays/>Открыть расписание</button><button className="primary" onClick={()=>{setDetail(null);onPlanMaintenance(detailUav.id)}}><Wrench/>Запланировать ТО</button></div></>}
        {detailPayload && reference && <><img className="device-photo" src={reference.image} alt={detailPayload.name} decoding="async"/><p>{reference.description}</p><h3>Что используется в расчёте</h3><dl><dt>Масса комплекта / допущение</dt><dd>{detailPayload.mass_kg} кг</dd>{!!detailPayload.focal_length_mm && <><dt>Физическое f</dt><dd>{detailPayload.focal_length_mm} мм</dd><dt>Матрица</dt><dd>{detailPayload.sensor_width_mm} × {detailPayload.sensor_height_mm} мм</dd><dt>Размер кадра</dt><dd>{detailPayload.resolution_width_px} × {detailPayload.resolution_height_px}</dd><dt>Лимит запуска / допущение</dt><dd>{detailPayload.fps} кадр/с</dd></>}</dl><p>{reference.notes}</p><a href={reference.source} target="_blank" rel="noreferrer">Характеристики и источник ↗</a>{reference.photoSource && <p><a href={reference.photoSource} target="_blank" rel="noreferrer">Источник фотографии ↗</a></p>}</>}
        {detailTech && techCopy && <><p>{detailTech.purpose}</p>{techCopy.paragraphs.map(p=><p key={p}>{p}</p>)}<h3>Рабочий процесс</h3><ol>{techCopy.workflow.map(p=><li key={p}>{p}</li>)}</ol><h3>Результаты</h3><p>{detailTech.deliverables.join(' · ')}</p><p>Числа в профиле приёмки — пример требований заказчика. Они должны подтверждаться контролем фактических данных, а не только планом маршрута.</p><a href={techCopy.source} target="_blank" rel="noreferrer">Технология подробнее ↗</a></>}
      </section></div>}
    </section>
  )
}

function weatherIcon(code=0,isDay=1){if(code===0)return isDay?'☀':'☾';if(code<=2)return'⛅';if(code===3)return'☁';if(code===45||code===48)return'≋';if(code>=51&&code<=67)return'🌧';if(code>=71&&code<=86)return'❄';if(code>=95)return'⚡';return'⛅'}
function windDirection(degrees=0){return ['С','СВ','В','ЮВ','Ю','ЮЗ','З','СЗ'][Math.round(((degrees%360)+360)%360/45)%8]}

type WeatherEffectMode={auto:boolean;rain:boolean;snow:boolean;wind:boolean}

function WeatherCard({data,radarEnabled,onRadar,effects,onEffects,radarTime,radarLoading}:{data:WeatherResponse|null;radarEnabled:boolean;onRadar:()=>void;effects:WeatherEffectMode;onEffects:(next:WeatherEffectMode)=>void;radarTime:string;radarLoading:boolean}) {
  const current=data?.current
  const pressureMmhg=current?.surface_pressure_hpa!=null&&Number.isFinite(current.surface_pressure_hpa)?Math.round(current.surface_pressure_hpa*0.750061683):null
  const setAuto=(checked:boolean)=>onEffects(checked?{auto:true,rain:false,snow:false,wind:false}:{...effects,auto:false})
  const setManual=(key:'rain'|'snow'|'wind',checked:boolean)=>onEffects({...effects,auto:false,[key]:checked})
  return (
    <section className="weather-card glass-panel" aria-label="Метеоусловия">
      <div className="weather-heading">
        <div className="weather-heading-copy"><h2 className="weather-card-title">Метеоусловия</h2><small className="weather-source">Open-Meteo · Москва{data?.status==='stale'?' · данные из кеша':''}</small></div>
        <span className="weather-rumb" title="Направление, откуда дует ветер"><Wind size={16}/>{current?`${windDirection(current.wind_direction_deg)} ${Math.round(current.wind_direction_deg)}°`:'—'}</span>
        <span className="weather-condition" aria-label="Текущее состояние атмосферы"><span>{current?weatherIcon(current.weather_code,current.is_day):'—'}</span><small>{data?.risk.score??'—'}/100</small></span>
      </div>
      <div className="weather-metrics" aria-label="Текущие метеопоказатели">
        <div title="Температура" aria-label={`Температура: ${current?`${Math.round(current.temperature_c)} градусов`:'нет данных'}`}><Thermometer size={17}/><strong>{current?`${current.temperature_c>0?'+':''}${Math.round(current.temperature_c)}°`:'—'}</strong></div>
        <div title="Скорость ветра" aria-label={`Скорость ветра: ${current?`${current.wind_speed_mps.toFixed(1)} метра в секунду`:'нет данных'}`}><Wind size={17}/><strong>{current?current.wind_speed_mps.toFixed(1):'—'}</strong><small>м/с</small></div>
        <div title="Влажность" aria-label={`Влажность: ${current?.relative_humidity_percent!=null?`${Math.round(current.relative_humidity_percent)} процентов`:'нет данных'}`}><Droplets size={17}/><strong>{current?.relative_humidity_percent!=null?`${Math.round(current.relative_humidity_percent)}%`:'—'}</strong></div>
        <div title="Видимость" aria-label={`Видимость: ${current?`${(current.visibility_m/1000).toFixed(0)} километров`:'нет данных'}`}><Eye size={17}/><strong>{current?(current.visibility_m/1000).toFixed(0):'—'}</strong><small>км</small></div>
        <div title="Атмосферное давление у поверхности" aria-label={`Атмосферное давление у поверхности: ${pressureMmhg!=null?`${pressureMmhg} миллиметров ртутного столба`:'нет данных'}`}><Gauge size={17}/><strong>{pressureMmhg??'—'}</strong><small>мм рт.ст.</small></div>
      </div>
      <fieldset className="weather-effect-controls"><legend>Эффекты</legend>
        <label title="Осадки по текущей метеосводке"><input type="checkbox" checked={effects.auto} onChange={event=>setAuto(event.target.checked)}/><span>Авто</span></label>
        <label><input type="checkbox" checked={effects.rain} onChange={event=>setManual('rain',event.target.checked)}/><span>Дождь</span></label>
        <label><input type="checkbox" checked={effects.snow} onChange={event=>setManual('snow',event.target.checked)}/><span>Снег</span></label>
        <label><input type="checkbox" checked={effects.wind} onChange={event=>setManual('wind',event.target.checked)}/><span>Ветер</span></label>
      </fieldset>
      <div className="weather-actions"><button className={radarEnabled?'active':''} onClick={onRadar}>{radarLoading?'Загрузка…':radarEnabled?`Радар осадков Росгидромет · ${radarTime}`:'Радар осадков Росгидромет'}</button></div>
    </section>
  )
}

function WindParticles({speed,direction}:{speed:number;direction:number}){
  const canvasRef=useRef<HTMLCanvasElement>(null)
  useEffect(()=>{
    const canvas=canvasRef.current
    const context=canvas?.getContext('2d')
    if(!canvas||!context||window.matchMedia('(prefers-reduced-motion: reduce)').matches)return
    type WindPoint={x:number;y:number}
    type WindParticle=WindPoint&{trail:WindPoint[];life:number}
    let width=0
    let height=0
    let animation=0
    let lastFrame=performance.now()
    let particles:WindParticle[]=[]
    const trailLength=7
    const makeParticle=():WindParticle=>{
      const x=Math.random()*width
      const y=Math.random()*height
      return{x,y,trail:[{x,y}],life:70+Math.random()*150}
    }
    const resize=()=>{
      // This layer covers the whole map. A 2x backing buffer on a 4K display
      // makes every clear/draw cycle unnecessarily expensive for a soft effect.
      const ratio=1
      width=canvas.clientWidth
      height=canvas.clientHeight
      canvas.width=Math.round(width*ratio)
      canvas.height=Math.round(height*ratio)
      context.setTransform(ratio,0,0,ratio,0,0)
      const target=Math.min(126,Math.max(70,Math.round(Math.sqrt(width*height)/15)))
      particles=Array.from({length:target},makeParticle)
    }
    const draw=(timestamp:number)=>{
      animation=requestAnimationFrame(draw)
      if(timestamp-lastFrame<40)return
      const delta=Math.min(.06,(timestamp-lastFrame)/1000||.025)
      lastFrame=timestamp
      context.clearRect(0,0,width,height)
      const meteorological=((direction+180)%360)*Math.PI/180
      const velocity={x:Math.sin(meteorological),y:-Math.cos(meteorological)}
      const baseAngle=Math.atan2(velocity.y,velocity.x||.001)
      const pixelSpeed=48+Math.min(Math.max(speed,0),25)*14
      const time=timestamp/1000
      const middle=Math.floor(trailLength/2)
      context.lineWidth=1.18
      context.lineCap='round'
      context.strokeStyle='rgba(48,112,158,.20)'
      context.beginPath()
      particles.forEach(particle=>{
        for(let index=1;index<Math.min(middle,particle.trail.length);index+=1){
          context.moveTo(particle.trail[index-1].x,particle.trail[index-1].y)
          context.lineTo(particle.trail[index].x,particle.trail[index].y)
        }
      })
      context.stroke()
      context.strokeStyle='rgba(35,104,158,.58)'
      context.beginPath()
      particles.forEach(particle=>{
        const from=Math.max(0,particle.trail.length-1-middle)
        for(let index=from+1;index<particle.trail.length;index+=1){
          context.moveTo(particle.trail[index-1].x,particle.trail[index-1].y)
          context.lineTo(particle.trail[index].x,particle.trail[index].y)
        }
      })
      context.stroke()
      particles.forEach((particle,index)=>{
        const angle=baseAngle+Math.sin(particle.x*.004+time*.9)*.55+Math.cos(particle.y*.003-time*.7)*.55
        particle.x+=Math.cos(angle)*pixelSpeed*delta
        particle.y+=Math.sin(angle)*pixelSpeed*delta
        particle.life-=1
        if(particle.life<=0||particle.x< -12||particle.x>width+12||particle.y< -12||particle.y>height+12){particles[index]=makeParticle();return}
        particle.trail.push({x:particle.x,y:particle.y})
        if(particle.trail.length>trailLength)particle.trail.shift()
      })
    }
    resize()
    window.addEventListener('resize',resize)
    animation=requestAnimationFrame(draw)
    return()=>{window.removeEventListener('resize',resize);cancelAnimationFrame(animation);context.clearRect(0,0,width,height)}
  },[speed,direction])
  return <canvas ref={canvasRef} className="weather-effect-layer wind-particles"/>
}

function WeatherEffects({weather,effects}:{weather:WeatherResponse|null;effects:WeatherEffectMode}){
  const current=weather?.current
  const code=current?.weather_code??0
  const reportedSnow=!!current&&(current.snowfall_cm>0||(code>=71&&code<=77)||(code>=85&&code<=86))
  const reportedRain=!!current&&!reportedSnow&&(current.rain_mm>0||current.precipitation_mm>0||(code>=51&&code<=67)||(code>=80&&code<=82)||code>=95)
  const rain=effects.auto?reportedRain:effects.rain
  const snow=effects.auto?reportedSnow:effects.snow
  const windy=!effects.auto&&effects.wind
  if(!rain&&!snow&&!windy)return null
  const particles=(kind:'rain'|'snow',count:number)=><div className={`weather-effect-layer ${kind}`}>{Array.from({length:count},(_,index)=><i key={index} style={{'--x':`${(index*37)%100}%`,'--delay':`${-(index%11)*.19}s`,'--duration':`${.8+(index%7)*.09}s`} as React.CSSProperties}/>)}</div>
  return <div className="weather-effects" aria-hidden="true">
    {rain&&particles('rain',38)}
    {snow&&particles('snow',42)}
    {windy&&<WindParticles speed={current?.wind_speed_mps??3} direction={current?.wind_direction_deg??315}/>} 
  </div>
}

function AirTrafficCard({ enabled, loading, data, error, onToggle, showGround, onGroundToggle }: {
  enabled: boolean; loading: boolean; data: AirTrafficResponse | null; error: string; onToggle: () => void; showGround:boolean; onGroundToggle:()=>void;
}) {
  const observed = data?.observed_at ? new Date(data.observed_at).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }) : '—'
  return (
    <section className={`air-traffic-card glass-panel ${data?.status || ''}`} aria-label="Внешнее воздушное движение">
      <div className="air-traffic-head"><span><Radio size={16} /><strong>Воздушная обстановка</strong></span><button type="button" aria-pressed={enabled} onClick={onToggle}>{enabled ? 'Скрыть' : 'Показать'}</button></div>
      {enabled ? <>
        <div className="air-traffic-value"><Plane size={23} /><strong>{loading && !data ? '…' : data?.airborne_count ?? 0}</strong><span>бортов<br />в воздухе</span></div>
        {!!data?.ground_count&&<button type="button" className={`ground-traffic-toggle ${showGround?'active':''}`} onClick={onGroundToggle}>{showGround?'Скрыть':'Показать'} наземные · {data.ground_count}</button>}
        <div className="air-traffic-meta"><span>{data?.source || 'источник недоступен'}</span><span>{data?.status === 'stale' ? `кэш · ${Math.round((data.age_seconds || 0) / 60)} мин` : observed}</span></div>
        {data&&<small className="air-refresh-note">Позиции: плавный прогноз · источник через {data.refresh_after_seconds<60?`${data.refresh_after_seconds} с`:`${Math.round(data.refresh_after_seconds/60)} мин`}</small>}
        {error && <small className="air-traffic-error">{error}</small>}
      </> : <small>ADS‑B/MLAT телеметрия авиабортов. Телеметрия собственных БВС работает независимо.</small>}
    </section>
  )
}

function FleetTelemetryPanel({ items, selectedId, onSelect, demoMode, contextLabel, simulationRate, onSimulationRate }: { items: Telemetry[]; selectedId: string | null; onSelect: (id: string) => void; demoMode:boolean; contextLabel:string; simulationRate:number; onSimulationRate:(rate:number)=>void }) {
  const selected = items.find((item) => item.uav_id === selectedId) || items[0]
  const airborneCount=items.filter(item=>item.status!=='servicing'&&item.altitude_m>.5).length
  const groundCount=items.length-airborneCount
  return (
    <section className="telemetry-panel glass-panel" aria-label="Телеметрия активного флота">
      <div className="telemetry-header"><div><span className="pulse-dot" /><strong>Флот в работе</strong></div><b title={`Всего ${items.length} БВС`}>{airborneCount} в полёте · {groundCount} на земле</b></div>
      {demoMode?<div className="demo-player" aria-label="Скорость демонстрации"><small>ДЕМО-ТЕМП</small><div>{[0,1,2,4].map(rate=><button type="button" key={rate} className={simulationRate===rate?'active':''} onClick={()=>onSimulationRate(rate)} aria-pressed={simulationRate===rate} title={rate===0?'Пауза':`Скорость демонстрации ×${rate}`}>{rate===0?'Ⅱ':`×${rate}`}</button>)}</div></div>:<div className="calendar-mode-note"><CalendarClock size={14}/><span><small>ОБСТАНОВКА ПО РАСПИСАНИЮ</small><b>{contextLabel}</b></span></div>}
      <div className="fleet-switcher">
        {items.map((item) => <button key={item.uav_id} className={item.uav_id === selected?.uav_id ? 'selected' : ''} onClick={() => onSelect(item.uav_id)} title={item.task_name}><AircraftGlyph type={item.uav_type} size={22} /><span>{item.name.replace('Геоскан ', '')}</span><i style={{ background: item.color }} /></button>)}
      </div>
      {selected ? <>
        <div className="instrument-panel">
          <div className="instrument-unit"><div className="attitude-bezel"><AttitudeIndicator pitch={selected.pitch_deg} roll={selected.roll_deg}/></div><small>АВИАГОРИЗОНТ</small><span>{selected.pitch_deg>0?'+':''}{selected.pitch_deg.toFixed(1)}° / {selected.roll_deg>0?'+':''}{selected.roll_deg.toFixed(1)}°</span></div>
          <div className="instrument-unit"><div className="heading-bezel"><HeadingIndicator heading={selected.heading_deg}/></div><small>ГИРОКОМПАС</small><span>Курс {selected.heading_deg.toFixed(0)}°</span></div>
          <div className="instrument-caption"><small>ПРОСТРАНСТВЕННОЕ ПОЛОЖЕНИЕ · {selected.uav_type === 'multirotor' ? 'МУЛЬТИРОТОР' : 'САМОЛЁТ'}</small><strong>{selected.name.replace('Геоскан ','')}</strong></div>
        </div>
        <div className="telemetry-mission"><small>АКТИВНОЕ ЗАДАНИЕ</small><strong>{selected.task_name}</strong><mark className={`flight-phase ${selected.status}`}>{selected.phase_label || selected.status}</mark><span><i style={{ width: `${selected.mission_progress_percent}%`, background: selected.color }} /></span><em>{selected.mission_progress_percent.toFixed(0)}%</em>{selected.compliance_note&&<p><ShieldCheck size={12}/>{selected.compliance_note}</p>}{selected.base_name&&<p><MapPin size={12}/>{selected.base_name}</p>}</div>
        <div className="telemetry-grid">
          <span><Gauge size={13} /><small>Скорость</small><b>{selected.speed_kmh.toFixed(1)} км/ч</b></span>
          <span><Navigation size={13} /><small>Высота</small><b>{selected.altitude_m.toFixed(1)} м</b></span>
          <span><Activity size={13} /><small>Тангаж / крен</small><b>{selected.pitch_deg.toFixed(1)}° / {selected.roll_deg.toFixed(1)}°</b></span>
          <span><Wind size={13} /><small>Вертикальная скорость</small><b>{selected.vertical_speed_mps > 0 ? '+' : ''}{selected.vertical_speed_mps.toFixed(1)} м/с</b></span>
          <span><BatteryMedium size={13} /><small>Батарея</small><b>{selected.battery_percent.toFixed(1)}%</b></span>
          <span><Signal size={13} /><small>Канал</small><b>{selected.link_quality_percent}%</b></span>
        </div>
      </> : <div className="telemetry-empty">Ожидание телеметрии…</div>}
    </section>
  )
}

function ReplayControls({mission,playback,contextCount,onChange,onClose}:{mission:ScheduledMission;playback:PlaybackState;contextCount:number;onChange:(next:PlaybackState)=>void;onClose:()=>void}){
  const duration=mission.simulation?.durationSeconds||1
  const sample=mission.simulation?sampleReplay(mission.simulation,playback.cursor):null
  const time=(seconds:number)=>`${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(Math.floor(seconds%60)).padStart(2,'0')}`
  return <section className={`replay-controls ${playback.mode}`} aria-label="Управление просмотром полёта">
    <header><div><small>{playback.mode==='live'?'ТЕКУЩИЙ ПОЛЁТ':playback.mode==='simulation'?'СИМУЛЯЦИЯ ПОЛЁТА':mission.simulation?.reconstructed?'РЕКОНСТРУКЦИЯ ПОЛЁТА':'ПОВТОР ПОЛЁТА'}</small><strong>{mission.title}</strong><span>{mission.uavName} · {sample?.phaseLabel} · в воздушной обстановке {contextCount} БВС</span></div><button onClick={onClose} aria-label="Закрыть просмотр"><X size={16}/></button></header>
    <div className="replay-timeline"><input aria-label="Позиция повтора" type="range" min={0} max={duration} step={.1} value={playback.cursor} onChange={event=>onChange({...playback,cursor:Number(event.target.value),playing:false})}/><div><b>{time(playback.cursor)}</b><span>Взлёт</span><span>Перелёт</span><span>Съёмка</span><span>Возврат</span><span>Посадка</span><b>{time(duration)}</b></div></div>
    <footer><button onClick={()=>onChange({...playback,cursor:0,playing:false})} title="В начало"><RotateCcw size={15}/></button><button className="replay-play" onClick={()=>onChange({...playback,playing:!playback.playing})}>{playback.playing?<Pause size={16}/>:<Play size={16}/>} {playback.playing?'Пауза':'Продолжить'}</button><div>{[1,2,4,8].map(rate=><button className={playback.rate===rate?'active':''} key={rate} onClick={()=>onChange({...playback,rate})}>×{rate}</button>)}</div><em>{Math.round(playback.cursor/duration*100)}%</em></footer>
  </section>
}

function PlanComparison({ selected, onSelect, calculating, result }: { selected: PlanId; onSelect: (id: PlanId) => void; calculating: boolean; result: OptimizationResult | null }) {
  if (!result) return <div className="plan-empty">{calculating ? 'Строим галсы, проверяем автономность и считаем затраты…' : 'Измените параметры и нажмите «Рассчитать производственный план». Сценарии появятся после расчёта.'}</div>
  const formatMinutes = (minutes: number) => `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`
  const planEntries = (Object.entries(plans) as [PlanId, typeof plans.fast][]).map(([id, fallback]) => {
    const live = result?.plans.find((plan) => plan.id === id)
    return [id, live ? {
      ...fallback,
      time: formatMinutes(live.duration_min),
      flight: formatMinutes(live.total_flight_time_min),
      cost: `${Math.round(live.cost_rub).toLocaleString('ru-RU')} ₽`,
      uavs: live.uav_count,
      sorties: live.sorties || live.vehicles.reduce((sum, vehicle) => sum + (vehicle.sorties || 1), 0),
      risk: `${live.reserve_percent || 22}%`,
      reason: live.selection_reason,
    } : {...fallback,time:'—',flight:'—',cost:'Недоступен',uavs:0,sorties:0,risk:'—',reason:''}] as const
  })
  const recommended = result?.plans.find((plan) => plan.id === result.recommended_plan_id)
  const fastest = result.plans.find((plan) => plan.id === 'fast')
  const savedFlight = Math.max(0, Math.round((fastest?.total_flight_time_min || 0) - (recommended?.total_flight_time_min || 0)))
  const extraTime = Math.max(0, Math.round((recommended?.duration_min || 0) - (fastest?.duration_min || 0)))
  const economy = result.plans.find(plan => plan.id === 'economy')
  const sameFlightPlan = (left: typeof economy, right: typeof economy) => Boolean(left && right && left.vehicles.length === right.vehicles.length && left.vehicles.every((vehicle, index) => {
    const other = right.vehicles[index]
    return vehicle.uav_id === other.uav_id && vehicle.sorties === other.sorties && JSON.stringify(vehicle.route.coordinates) === JSON.stringify(other.route.coordinates)
  }))
  return (
    <section className={`plan-dock glass-panel ${calculating ? 'calculating' : ''}`}>
      <div className="dock-summary">
        <div className="eyebrow"><Sparkles size={13} /> ИНТЕЛЛЕКТУАЛЬНЫЙ ОПТИМИЗАТОР</div>
        <strong>{recommended?.label || 'Минимум налёта'}</strong>
        <p>{savedFlight > 0 ? `На ${savedFlight} мин меньше суммарный налёт относительно быстрого варианта${extraTime ? `, завершение позже на ${extraTime} мин` : ''}.` : 'Быстрый и бережный варианты равны по суммарному налёту.'}</p>
      </div>
      <div className="plan-options">
        {planEntries.map(([id, plan]) => {
          const Icon = plan.icon
          const equivalentToEconomy = id === 'safe' && sameFlightPlan(economy, result.plans.find(item => item.id === id))
          return (
            <button key={id} disabled={!result.plans.some(p => p.id === id)} className={`plan-card ${selected === id ? 'selected' : ''}`} onClick={() => onSelect(id)} style={{ '--accent': plan.accent } as React.CSSProperties}>
              <div className="plan-title"><Icon size={17} /><span>{plan.label}</span>{selected === id ? <em>НА КАРТЕ</em> : result.recommended_plan_id === id ? <em>РЕКОМ.</em> : null}</div>
              <div className="plan-metrics"><div><small>Завершение</small><strong>{plan.time}</strong></div><div><small>Стоимость</small><strong>{plan.cost}</strong></div></div>
              <div className="plan-footer"><span>{plan.uavs} БВС · {plan.sorties} выл. · Σ налёт {plan.flight}</span><span>резерв {plan.risk}</span></div>
              <p className="scenario-criterion">{equivalentToEconomy ? 'Маршрут и состав БВС совпадают с «Минимумом налёта»: здесь отличается только требуемый резерв автономности.' : plan.reason || (id === 'fast' ? 'Минимум времени; несколько БВС могут работать параллельно.' : id === 'economy' ? 'Минимум суммарного налёта с подлётом и возвратом.' : 'Резерв автономности 30%.')}</p>
            </button>
          )
        })}
      </div>
      {calculating && <div className="calculation-sheen" />}
    </section>
  )
}

function FleetEconomics({result}:{result:OptimizationResult}){
  const variants=result.fleet_comparison||[]
  if(!variants.length)return null
  const minimum=Math.min(...variants.map(item=>item.total_flight_time_min))
  const winner=variants.find(item=>item.recommended)||variants.find(item=>item.total_flight_time_min===minimum)||variants[0]
  const money=(value:number)=>Math.round(value).toLocaleString('ru-RU')
  const time=(minutes:number)=>`${Math.floor(minutes/60)} ч ${minutes%60} мин`
  return <section className="fleet-economics" aria-label="Сравнение загрузки флота">
    <header><div><small>СРАВНЕНИЕ СЦЕНАРИЕВ</small><h3>Одна территория — разные составы флота</h3><p>Для каждого числа БВС показан вариант с минимальным суммарным налётом. Время завершения учитывает параллельную работу; цена — отдельная демонстрационная оценка.</p></div><span>{result.economics?.candidate_count||variants.length} сочетаний проверено</span></header>
    <div className="fleet-economics-table-wrap"><table><thead><tr><th>Состав и вылеты</th><th>Завершение</th><th>Суммарный налёт</th><th>Стоимость</th></tr></thead><tbody>{variants.map(item=><tr key={item.uav_count} className={item.recommended?'recommended':''}><td><strong>{item.uav_count} БВС · {item.sorties} выл.</strong><small>{item.vehicle_names.map((name,index)=>`${name} × ${item.vehicle_sorties[index]}`).join(' · ')}</small></td><td>{time(item.duration_min)}</td><td><b>{time(item.total_flight_time_min)}</b> {item.recommended?<em>МИНИМУМ</em>:`+${time(item.total_flight_time_min-minimum)}`}</td><td>{money(item.cost_rub)} ₽</td></tr>)}</tbody></table></div>
    <details className="fleet-cost-explainer"><summary>Как получилась цена {winner.uav_count} БВС / {winner.sorties} выл.</summary><div>{Object.entries(winner.cost_components).map(([name,value])=><span key={name}>{name}<b>{money(value)} ₽</b></span>)}</div></details>
    <p className="fleet-economics-note">Тарифы демонстрационные: подготовка {money(result.economics?.sortie_preparation_cost_rub||0)} ₽/вылет, мобилизация {money(result.economics?.uav_mobilization_cost_rub||0)} ₽/БВС. Доставка к удалённой площадке не включена.</p>
  </section>
}

function MissionResultsModal({ result, selected, confirmedPlan, onSelect, onConfirm, onClose }: { result: OptimizationResult; selected: PlanId; confirmedPlan:PlanId|null; onSelect: (id: PlanId) => void; onConfirm:(id:PlanId)=>void; onClose: () => void }) {
  useEffect(()=>{const close=(event:KeyboardEvent)=>{if(event.key==='Escape')onClose()};window.addEventListener('keydown',close);return()=>window.removeEventListener('keydown',close)},[onClose])
  const plan = result.plans.find(item => item.id === selected) || result.plans[0]
  const settlementAssessment=result.settlement_assessments?.[selected]||result.settlement_assessment
  const sortieCount = plan?.sorties || plan?.vehicles.reduce((sum, vehicle)=>sum+(vehicle.sorties||1),0) || 0
  const sortieLabel = sortieCount % 10 === 1 && sortieCount % 100 !== 11 ? 'вылет' : sortieCount % 10 >= 2 && sortieCount % 10 <= 4 && (sortieCount % 100 < 12 || sortieCount % 100 > 14) ? 'вылета' : 'вылетов'
  const routeUnsafe=Boolean(plan.airspace_avoidance?.unresolved)
  const airspaceLabel = routeUnsafe ? 'Нужна корректировка маршрута' : result.airspace?.status === 'clear' ? 'Конфликтов не найдено' : result.airspace?.status === 'notification_required' ? 'Уведомительный порядок' : result.airspace?.status === 'permission_required' ? 'Нужно разрешение' : 'Нужна корректировка'
  return <div className="mission-results-backdrop" role="presentation" onMouseDown={event=>{if(event.target===event.currentTarget)onClose()}}>
    <section className="mission-results-modal" role="dialog" aria-labelledby="mission-results-title">
      <header><div><span>РАСЧЁТ ЗАВЕРШЁН · {(result.calculation_ms/1000).toLocaleString('ru-RU',{maximumFractionDigits:1})} С</span><h2 id="mission-results-title">Сценарии выполнения задания</h2><p>Маршрут: {((result.timings_ms?.route_planning??result.calculation_ms)/1000).toLocaleString('ru-RU',{maximumFractionDigits:1})} с; локальная проверка границ: {((result.timings_ms?.settlement_lookup??0)/1000).toLocaleString('ru-RU',{maximumFractionDigits:1})} с. Догрузка Overpass выполняется отдельно и не задерживает расчёт.</p><p>Сценарии могут иметь один маршрут, если ограничения не меняют оптимальный состав флота. Для календаря подтвердите выбранный вариант отдельно.</p></div><div className="results-header-actions"><a href={`/api/missions/${encodeURIComponent(result.mission_id)}/export?plan=${encodeURIComponent(selected)}&format=kml`} download><Download size={15}/> Скачать KML</a><button type="button" onClick={onClose} aria-label="Закрыть результаты"><X size={18}/></button></div></header>
      <PlanComparison selected={selected} onSelect={onSelect} calculating={false} result={result}/>
      {plan&&<MissionExportActions missionId={result.mission_id} planId={selected}/>}
      <FleetEconomics result={result}/>
      {plan && <div className="mission-results-detail">
        <section className="result-vehicle-section"><div className="result-section-heading"><div><small>ВЫБРАННЫЙ СЦЕНАРИЙ</small><h3>{plan.label}</h3></div><div className="result-summary-pills"><span><b>{plan.duration_min} мин</b> завершение</span><span><b>{plan.total_flight_time_min} мин</b> Σ налёт</span><span><b>{Math.round(plan.cost_rub).toLocaleString('ru-RU')} ₽</b> стоимость</span><span><b>{plan.uav_count}</b> БВС</span><span><b>{sortieCount}</b> {sortieLabel}</span><span><b>{plan.reserve_percent || 0}%</b> резерв</span></div></div>
          {plan.selection_reason&&<p className="result-selection-reason">{plan.selection_reason}</p>}
          {plan.maintenance_policy&&<p className="result-selection-reason">ТО: {plan.maintenance_policy}</p>}
          <div className="result-operational-notes"><p><Radio size={15}/>{plan.link_assessment?.mode==='radio'?`Прямая радиосвязь: удаление до ${plan.link_assessment.max_distance_km.toLocaleString('ru-RU')} км при расчётном пределе ${plan.link_assessment.planning_limit_km?.toLocaleString('ru-RU')} км. Это не проверка рельефа и качества сигнала.`:'Внешний канал: покрытие по всей траектории не подтверждено; до назначения полёта нужна проверка оператора.'}</p>{plan.deployment?.required&&<p><MapPin size={15}/>Потребуется перебазировать БВС примерно на {plan.deployment.max_distance_km?.toLocaleString('ru-RU')} км. Доставка и её стоимость не включены в бюджет сценария.</p>}</div>
          <div className="result-vehicles">{plan.vehicles.map(vehicle=><article key={vehicle.uav_name}><header><strong>{vehicle.uav_name}</strong><span>{vehicle.sorties || 1} выл. · до {vehicle.max_sortie_min?.toFixed(0) || '—'} из {vehicle.usable_endurance_min?.toFixed(0) || '—'} мин доступной автономности · {vehicle.altitude_m} м AGL{vehicle.turn_radius_m?` · R ≥ ${vehicle.turn_radius_m} м при ${vehicle.turn_speed_kmh} км/ч, крен ≤ ${vehicle.turn_bank_deg}°`:''}{vehicle.runway_heading_deg!==null&&vehicle.runway_heading_deg!==undefined?` · ВПП ${vehicle.runway_length_m} м / ${vehicle.runway_heading_deg}°, взлёт ${vehicle.departure_heading_deg}° · ${vehicle.runway_headwind_mps&&vehicle.runway_headwind_mps<0?'попутный':'встречный'} ${Math.abs(vehicle.runway_headwind_mps||0)} м/с, боковой ${vehicle.runway_crosswind_mps||0} м/с`:''}{vehicle.departure_offset_min?` · старт +${vehicle.departure_offset_min} мин`:''}</span></header><div className="compact-phase-timeline">{vehicle.phases?.map((phase,index)=><span key={`${phase.name}-${index}`} style={{flex:Math.max(phase.minutes,2),'--phase-color':['#20b5e5','#71a2bd','#f15a32','#a47dea','#71a2bd','#20b5e5','#899398'][index%7]} as React.CSSProperties} title={`${phase.name}: ${phase.minutes.toFixed(1)} мин`}><b>{phase.minutes.toFixed(0)}</b><small>{phase.name}</small></span>)}</div></article>)}</div>
        </section>
        {result.airspace&&<aside className={`result-airspace ${routeUnsafe?'requires_correction':result.airspace.status}`}><div className="result-section-heading"><div><small>ВОЗДУШНОЕ ПРОСТРАНСТВО</small><h3>{airspaceLabel}</h3></div><ShieldAlert size={21}/></div><dl><div><dt>Зоны</dt><dd>{result.airspace.conflicts.length}</dd></div><div><dt>ОрВД</dt><dd>{result.airspace.authorities.length}</dd></div><div><dt>Обходы</dt><dd>{plan.airspace_avoidance?.detours||0}</dd></div><div><dt>Слоты</dt><dd>{plan.deconfliction?.departure_slots_applied||0}</dd></div></dl><ul>{routeUnsafe&&<li>Плавный обход с учётом радиуса разворота не найден; назначение заблокировано.</li>}{result.airspace.messages.slice(0,3).map(message=><li key={message}>{message}</li>)}</ul></aside>}
        {settlementAssessment&&<aside className={`result-airspace ${settlementAssessment.status==='COVERED'?'clear':'adjustment_required'}`}><div className="result-section-heading"><div><small>ГРАНИЦЫ НАСЕЛЁННЫХ ПУНКТОВ</small><h3>{settlementAssessment.status==='COVERED'?'Покрытие загружено':'Данные загружены не полностью'}</h3></div><MapPin size={21}/></div><dl><div><dt>Участки</dt><dd>{settlementAssessment.tiles_fresh} / {settlementAssessment.tiles_total}</dd></div><div><dt>Пересечения</dt><dd>{settlementAssessment.intersections.length}</dd></div><div><dt>Коридор</dt><dd>±{settlementAssessment.corridor_margin_m} м</dd></div></dl><ul><li>OSM используется для предварительной проверки; юридически значимую границу необходимо подтвердить.</li>{settlementAssessment.intersections.slice(0,5).map(item=><li key={`${item.osm_type}-${item.osm_id}`}>{item.name||'Без названия'}{item.region?` · ${item.region}`:''}{item.district?` · ${item.district}`:''}</li>)}</ul></aside>}
      </div>}
      <footer><p>{confirmedPlan===selected?`Для календаря подтверждён вариант «${plan.label}».`:'Предпросмотр не назначает вариант. Перед вылетом нужна проверка воздушной обстановки.'}</p><div className="result-footer-actions"><button type="button" className="result-back-button" onClick={onClose}>Вернуться без выбора</button><button type="button" onClick={()=>onConfirm(selected)}>{confirmedPlan===selected?'Выбор подтверждён':`Подтвердить «${plan.label}» для календаря`}</button></div></footer>
    </section>
  </div>
}

function LoginScreen({ onLogin }: { onLogin: (user: User) => void }) {
  const [username, setUsername] = useState('dispatcher')
  const [password, setPassword] = useState('dispatcher')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const response = await fetch('/api/auth/login', {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      })
      if (response.status === 401) throw new Error('Проверьте логин и пароль')
      if (!response.ok) throw new Error('Сервер авторизации недоступен. Попробуйте ещё раз позже')
      onLogin(await response.json())
    } catch (loginError) {
      setError(loginError instanceof TypeError ? 'Нет соединения с сервером авторизации' : loginError instanceof Error ? loginError.message : 'Не удалось войти')
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="login-layer">
      <div className="login-shell">
        <form className="login-card glass-panel" onSubmit={submit}>
          <Brand />
          <div className="login-copy">
            <div className="eyebrow"><ShieldCheck size={13} /> КОНКУРСНЫЙ ПРОТОТИП</div>
            <h2>Центр управления<br />воздушными миссиями</h2>
            <p>Планирование, экономическая оптимизация и контроль групповых полётов БВС.</p>
          </div>
          <label className="login-field"><span>Логин</span><div><CircleUserRound size={17} /><input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" /></div></label>
          <label className="login-field"><span>Пароль</span><div><KeyRound size={17} /><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" /></div></label>
          {error && <div className="login-error">{error}</div>}
          <button className="login-button" disabled={busy}>{busy ? 'Проверяю доступ…' : 'Войти в систему'} <Navigation size={17} /></button>
          <div className="demo-access"><span>Демонстрационный доступ</span><code>dispatcher / dispatcher</code></div>
        </form>
      </div>
    </div>
  )
}

export default function App() {
  const [view, setView] = useState<'operations' | 'orders' | 'planner' | 'calendar' | 'airspace' | 'bases'>('operations')
  const [maxAltitude, setMaxAltitude] = useState(150)
  const [lineSpacing, setLineSpacing] = useState(40)
  const [corridorWidth, setCorridorWidth] = useState(80)
  const [geometryMode, setGeometryMode] = useState<'area'|'corridor'>('area')
  const [launchPoint,setLaunchPoint] = useState<[number,number] | null>(null)
  const [selectedLaunchSiteId,setSelectedLaunchSiteId]=useState<string|null>(null)
  const [selectedProducts, setSelectedProducts] = useState<ProductId[]>(['orthophoto'])
  const [operationAreas, setOperationAreas] = useState<GeoJSON.FeatureCollection<GeoJSON.Polygon>>({type:'FeatureCollection',features:[]})
  const [operationRoutes, setOperationRoutes] = useState<GeoJSON.FeatureCollection<GeoJSON.LineString | GeoJSON.MultiLineString>>({type:'FeatureCollection',features:[]})
  const [operationalAirspace,setOperationalAirspace]=useState<GeoJSON.FeatureCollection>({type:'FeatureCollection',features:[]})
  const [airspaceVisibility,setAirspaceVisibility]=useState<OperationalAirspaceVisibility>({prohibited:true,danger:true,obstacle:true})
  const [settlementsVisible,setSettlementsVisible]=useState(true)
  const [settlementCount,setSettlementCount]=useState(0)
  const [selectedPlan, setSelectedPlan] = useState<PlanId>('economy')
  const [confirmedPlan,setConfirmedPlan]=useState<PlanId|null>(null)
  const [selectedProduct, setSelectedProduct] = useState<ProductId>('orthophoto')
  const [calculating, setCalculating] = useState(false)
  const [user, setUser] = useState<User | null | undefined>(undefined)
  const [systemOk, setSystemOk] = useState(false)
  const [result, setResult] = useState<OptimizationResult | null>(null)
  const [telemetry, setTelemetry] = useState<Telemetry[]>([])
  const [demoMode,setDemoMode]=useState(true)
  const [simulationRate,setSimulationRate]=useState(1)
  const telemetrySocketRef=useRef<WebSocket|null>(null)
  const simulationRateRef=useRef(1)
  const [selectedUavId, setSelectedUavId] = useState<string | null>(null)
  const [area, setArea] = useState<MissionGeometry>(initialMissionArea)
  const [drawMode, setDrawMode] = useState(false)
  const [notice, setNotice] = useState('')
  const [calculationError, setCalculationError] = useState('')
  const [methodologyOpen,setMethodologyOpen]=useState(false)
  const [resultsOpen,setResultsOpen]=useState(false)
  const [catalogSection, setCatalogSection] = useState<CatalogSection | null>(null)
  const [catalogUavs, setCatalogUavs] = useState<CatalogUav[]>([])
  const [catalogPayloads, setCatalogPayloads] = useState<CatalogPayload[]>([])
  const [technologyProfiles, setTechnologyProfiles] = useState<TechnologyProfile[]>([])
  const [launchSites,setLaunchSites]=useState<LaunchSite[]>([])
  const [selectedPayloadId, setSelectedPayloadId] = useState('')
  const [gsd, setGsd] = useState(missionProducts.orthophoto.gsd)
  const [sideOverlap, setSideOverlap] = useState(missionProducts.orthophoto.sideOverlap)
  const [forwardOverlap, setForwardOverlap] = useState(missionProducts.orthophoto.forwardOverlap)
  const [airspaceSettings,setAirspaceSettings]=useState<AirspaceSettings>({prohibited:300,danger:150,obstacle:50,settlement:100,separation:100})
  const [controlLinkSettings,setControlLinkSettings]=useState<ControlLinkSettings>({mode:'radio',equipmentRangeKm:50,groundAntennaHeightM:5})
  const [airTrafficEnabled, setAirTrafficEnabled] = useState(false)
  const [airTraffic, setAirTraffic] = useState<AirTrafficResponse | null>(null)
  const [airTrafficLoading, setAirTrafficLoading] = useState(false)
  const [airTrafficError, setAirTrafficError] = useState('')
  const [groundTrafficEnabled,setGroundTrafficEnabled]=useState(false)
  const [weather, setWeather] = useState<WeatherResponse|null>(null)
  const [weatherEffects, setWeatherEffects] = useState<WeatherEffectMode>({auto:true,rain:false,snow:false,wind:false})
  const [radarEnabled, setRadarEnabled] = useState(false)
  const [radar, setRadar] = useState<RadarManifest|null>(null)
  const [radarIndex, setRadarIndex] = useState(0)
  const [radarLoading, setRadarLoading] = useState(false)
  const [orders, setOrders] = useState<CustomerOrder[]>([])
  const [ordersLoadError,setOrdersLoadError]=useState('')
  const [activeOrderId, setActiveOrderId] = useState<string | null>(null)
  const [scheduledDate,setScheduledDate]=useState(()=>{const date=new Date();date.setDate(date.getDate()+1);return localDateIso(date)})
  const [scheduledTime,setScheduledTime]=useState('09:00')
  const [deadlineEnabled,setDeadlineEnabled]=useState(false)
  const [deadlineDate,setDeadlineDate]=useState(()=>{const date=new Date();date.setDate(date.getDate()+1);return localDateIso(date)})
  const [deadlineTime,setDeadlineTime]=useState('18:00')
  const [maxUavs,setMaxUavs]=useState<number|null>(null)
  const [scheduledMissions,setScheduledMissions]=useState<ScheduledMission[]>(()=>{
    try { const saved=localStorage.getItem('mission-control-schedule-v2'); return saved?JSON.parse(saved):initialScheduledMissions } catch { return initialScheduledMissions }
  })
  const [calendarMode,setCalendarMode]=useState<PlannerCalendarMode>('flights')
  const [calendarFocusUavId,setCalendarFocusUavId]=useState<string|null>(null)
  const [maintenanceDraftUavId,setMaintenanceDraftUavId]=useState<string|null>(null)
  const [playback,setPlayback]=useState<PlaybackState|null>(null)
  const [calendarClock,setCalendarClock]=useState(()=>Date.now())
  const completingMissionRef=useRef<string|null>(null)
  const scheduleLifecycleRef=useRef(new Set<string>())

  useEffect(()=>{localStorage.setItem('mission-control-schedule-v2',JSON.stringify(scheduledMissions))},[scheduledMissions])

  const areaMetrics = (() => {
    const ring = area.geometry.type === 'LineString' ? area.geometry.coordinates : area.geometry.coordinates[0]
    const meanLat = ring.reduce((sum, point) => sum + point[1], 0) / ring.length
    const lonScale = 111_320 * Math.cos(meanLat * Math.PI / 180)
    const latScale = 110_540
    let twiceArea = 0
    let perimeter = 0
    for (let index = 0; index < ring.length - 1; index += 1) {
      const [lon1, lat1] = ring[index]
      const [lon2, lat2] = ring[index + 1]
      const x1 = lon1 * lonScale; const y1 = lat1 * latScale
      const x2 = lon2 * lonScale; const y2 = lat2 * latScale
      twiceArea += x1 * y2 - x2 * y1
      perimeter += Math.hypot(x2 - x1, y2 - y1)
    }
    if (area.geometry.type === 'LineString') return {areaKm2:perimeter*corridorWidth/1_000_000,perimeterKm:(2*perimeter+2*corridorWidth)/1000,routeLengthKm:perimeter/1000}
    return { areaKm2: Math.abs(twiceArea) / 2_000_000, perimeterKm: perimeter / 1000, routeLengthKm:0 }
  })()

  const importMissionFile = async (file: File) => {
    try {
      const geometries=parseMissionFile(file.name,await file.text())
      if (geometries.length !== 1) throw new Error('Загрузите один объект на задание. Несколько объектов нужно разделить на файлы.')
      const line = geometries.find((geometry) => geometry.type === 'LineString')
      if (line) {
        if (!['powerline_report','thermal_map'].includes(selectedProduct)) throw new Error('Линейная геометрия доступна для ЛЭП и теплотрасс.')
        if (line.coordinates.length < 2) throw new Error('Трасса должна содержать минимум две точки.')
        if (line.coordinates.some(p=>!Array.isArray(p)||p.length<2||!Number.isFinite(p[0])||!Number.isFinite(p[1])||Math.abs(p[0])>180||Math.abs(p[1])>85)) throw new Error('Нужны координаты WGS 84: долгота, широта в градусах.')
        setGeometryMode('corridor'); setArea({type:'Feature',properties:{source:'import',filename:file.name},geometry:line}); setDrawMode(false);setResult(null);setNotice(`Трасса «${file.name}» загружена.`);return
      }
      const polygon = geometries.find((geometry) => geometry.type === 'Polygon' || geometry.type === 'MultiPolygon')
      if (!polygon) throw new Error('В файле нет полигона')
      if (polygon.type === 'MultiPolygon' && polygon.coordinates.length !== 1) throw new Error('Несколько контуров: создайте отдельное задание для каждого полигона.')
      const coordinates = polygon.type === 'Polygon' ? polygon.coordinates : polygon.coordinates[0]
      if (!coordinates[0] || coordinates[0].length < 4) throw new Error('Полигон содержит недостаточно вершин')
      if (coordinates.length !== 1) throw new Error('Редактор пока не поддерживает внутренние исключения: разделите участок на простые полигоны.')
      if (coordinates[0].some(p=>!Array.isArray(p)||p.length<2||!Number.isFinite(p[0])||!Number.isFinite(p[1])||Math.abs(p[0])>180||Math.abs(p[1])>85)) throw new Error('Нужны координаты WGS 84: долгота, широта в градусах.')
      if (JSON.stringify(coordinates[0][0]) !== JSON.stringify(coordinates[0][coordinates[0].length-1])) throw new Error('Контур не замкнут.')
      setArea({ type: 'Feature', properties: { source: 'import', filename: file.name }, geometry: { type: 'Polygon', coordinates } })
      setGeometryMode('area')
      setDrawMode(false)
      setResult(null)
      setNotice(`Контур «${file.name}» загружен; проверка самопересечений выполняется при расчёте.`)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Не удалось прочитать файл')
    }
  }

  const completeDrawing = useCallback(() => setDrawMode(false), [])

  useEffect(() => {
    Promise.all([
      fetch('/api/health').then((response) => response.ok ? response.json() : null).catch(() => null),
      fetch('/api/auth/me', { credentials: 'include' }).then((response) => response.ok ? response.json() : null).catch(() => null),
    ]).then(([health, authenticatedUser]) => {
      setSystemOk(health?.status === 'ok')
      setUser(authenticatedUser)
    })
  }, [])

  useEffect(() => {
    if (!user) return
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    let disposed=false,reconnectTimer=0
    const connect=()=>{
      if(disposed)return
      const socket = new WebSocket(`${protocol}//${window.location.host}/api/telemetry/ws`)
      telemetrySocketRef.current=socket
      socket.onopen=()=>socket.send(JSON.stringify({type:'set_simulation_rate',rate:simulationRateRef.current}))
      socket.onmessage = (event) => {
        const packet = JSON.parse(event.data) as Telemetry | { vehicles: Telemetry[]; simulation_rate?:number; areas?: GeoJSON.FeatureCollection<GeoJSON.Polygon>; routes?: GeoJSON.FeatureCollection<GeoJSON.LineString | GeoJSON.MultiLineString> }
        if ('areas' in packet && packet.areas) setOperationAreas(packet.areas)
        if ('routes' in packet && packet.routes) setOperationRoutes(packet.routes)
        const next = 'vehicles' in packet ? packet.vehicles : [packet]
        setTelemetry(next)
        setSelectedUavId((selected) => selected || next[0]?.uav_id || null)
      }
      socket.onerror=()=>socket.close()
      socket.onclose=()=>{if(!disposed)reconnectTimer=window.setTimeout(connect,1200)}
    }
    connect()
    return () => {disposed=true;window.clearTimeout(reconnectTimer);telemetrySocketRef.current?.close();telemetrySocketRef.current=null}
  }, [user])

  useEffect(()=>{
    simulationRateRef.current=simulationRate
    const socket=telemetrySocketRef.current
    if(socket?.readyState===WebSocket.OPEN)socket.send(JSON.stringify({type:'set_simulation_rate',rate:simulationRate}))
  },[simulationRate])

  useEffect(()=>{
    if(demoMode||playback)return
    const update=()=>setCalendarClock(Date.now())
    update();const timer=window.setInterval(update,1000)
    return()=>window.clearInterval(timer)
  },[demoMode,playback])

  useEffect(()=>{
    if(!user)return
    let cancelled=false
    const load=()=>Promise.all((['prohibited','danger','obstacle'] as OperationalAirspaceCategory[]).map(category=>fetch(`/api/airspace/zones?category=${category}&limit=5000`,{credentials:'include'}).then(response=>response.ok?response.json():Promise.reject()))).then(collections=>{if(!cancelled)setOperationalAirspace({type:'FeatureCollection',features:collections.flatMap(collection=>collection.features||[])})}).catch(()=>{if(!cancelled)setNotice('Слои воздушных ограничений временно недоступны на рабочих картах')})
    const refresh=()=>{void load()}
    void load();window.addEventListener('airspace-updated',refresh)
    return()=>{cancelled=true;window.removeEventListener('airspace-updated',refresh)}
  },[user])

  useEffect(()=>{
    if(!user)return
    fetch('/api/schedule',{credentials:'include'}).then(response=>response.ok?response.json():{items:[]}).then(data=>{const remote=(data.items||[]) as ScheduledMission[];if(!remote.length)return;setScheduledMissions(current=>{const byId=new Map(current.map(item=>[item.id,item]));remote.forEach(item=>byId.set(item.id,item));return [...byId.values()]})}).catch(()=>undefined)
  },[user])

  useEffect(()=>{
    if(!user)return
    let cancelled=false
    const refresh=()=>fetch('/api/weather/forecast?lat=55.7558&lon=37.6173',{credentials:'include'}).then(response=>response.ok?response.json():null).then(data=>{if(!cancelled&&data)setWeather(data)}).catch(()=>undefined)
    void refresh();const timer=window.setInterval(refresh,600_000)
    return()=>{cancelled=true;window.clearInterval(timer)}
  },[user])

  useEffect(()=>{
    if(!user||!radarEnabled||view!=='operations'||catalogSection)return
    let cancelled=false;setRadarLoading(true)
    fetch('/api/weather/radar',{credentials:'include'}).then(response=>response.ok?response.json():Promise.reject()).then((data:RadarManifest)=>{if(!cancelled){setRadar(data);setRadarIndex(Math.max(0,data.frames.length-1))}}).catch(()=>{if(!cancelled){setRadar(null);setNotice('Радар осадков временно недоступен; прогноз продолжает работать.')}}).finally(()=>{if(!cancelled)setRadarLoading(false)})
    return()=>{cancelled=true}
  },[user,radarEnabled,view,catalogSection])

  useEffect(()=>{
    if(!radarEnabled||!radar?.frames.length)return
    const timer=window.setInterval(()=>setRadarIndex(index=>(index+1)%radar.frames.length),1100)
    return()=>window.clearInterval(timer)
  },[radarEnabled,radar])

  useEffect(() => {
    if (!user) return
    setOrdersLoadError('')
    fetch('/api/orders', {credentials:'include'})
      .then(async response=>{if(!response.ok){const body=await response.json().catch(()=>null);throw new Error(body?.detail||`HTTP ${response.status}`)}return response.json()})
      .then(data=>setOrders(data.items||[]))
      .catch(error=>{setOrders([]);setOrdersLoadError(`Не удалось загрузить реестр: ${error instanceof Error?error.message:'ошибка API'}`)})
  }, [user])

  useEffect(() => {
    if (!user || !airTrafficEnabled || view !== 'operations' || catalogSection) return
    let cancelled = false
    let timer:number|undefined
    const refresh = async () => {
      let nextRefresh=30
      setAirTrafficLoading(true)
      try {
        const response = await fetch('/api/traffic/aircraft', { credentials: 'include' })
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        const next = await response.json() as AirTrafficResponse
        if (cancelled) return
        nextRefresh=Math.max(3,next.refresh_after_seconds||30)
        setAirTraffic(next)
        setAirTrafficError(next.status === 'unavailable' ? 'Внешние источники временно не отвечают' : '')
      } catch {
        if (!cancelled) setAirTrafficError('Не удалось обновить воздушную обстановку')
      } finally {
        if (!cancelled){setAirTrafficLoading(false);timer=window.setTimeout(refresh,nextRefresh*1000)}
      }
    }
    void refresh()
    return () => { cancelled = true;if(timer!==undefined)window.clearTimeout(timer) }
  }, [user, airTrafficEnabled, view, catalogSection])

  useEffect(() => {
    if (!user) return
    Promise.all([
      fetch('/api/catalog/uavs', { credentials: 'include' }).then((response) => response.ok ? response.json() : { items: [] }),
      fetch('/api/catalog/payloads', { credentials: 'include' }).then((response) => response.ok ? response.json() : { items: [] }),
      fetch('/api/catalog/technologies', { credentials: 'include' }).then((response) => response.ok ? response.json() : { items: [] }),
      fetch('/api/catalog/bases', { credentials: 'include' }).then((response) => response.ok ? response.json() : { items: [] }),
    ]).then(([uavs, payloads, technologies, baseCatalog]) => {
      setCatalogUavs(uavs.items || [])
      setCatalogPayloads(payloads.items || [])
      setTechnologyProfiles(technologies.items || [])
      const catalogSites:LaunchSite[]=baseCatalog.items || []
      setLaunchSites([...catalogSites,...centerLaunchSites.filter(site=>!catalogSites.some(item=>item.id===site.id))])
    }).catch(() => setNotice('Справочники временно работают в автономном режиме'))
  }, [user])

  useEffect(() => {
    const technology = technologyProfiles.find((item) => item.result_type === selectedProduct)
    const recommended = technology?.recommended_payload_ids.find((id) => catalogPayloads.some((payload) => payload.id === id))
    const compatible = catalogPayloads.find((payload) => payload.spectrums.includes(missionProducts[selectedProduct].surveyType))
    setSelectedPayloadId(recommended || compatible?.id || '')
  }, [selectedProduct, catalogPayloads, technologyProfiles])

  const calculationInput = JSON.stringify({area, selectedProduct, selectedProducts, selectedPayloadId, gsd, maxAltitude, sideOverlap, forwardOverlap, lineSpacing, corridorWidth, launchPoint,selectedLaunchSiteId,airspaceSettings,controlLinkSettings,scheduledDate,scheduledTime,deadlineEnabled,deadlineDate,deadlineTime,maxUavs})
  const currentInput = useRef(calculationInput)
  currentInput.current = calculationInput
  useEffect(() => { setCalculationError('') }, [calculationInput])
  const calculate = async () => {
    const submittedInput = currentInput.current
    setCalculating(true)
    setCalculationError('')
    setResult(null)
    setConfirmedPlan(null)
    const product = missionProducts[selectedProduct]
    const selectedPayload = catalogPayloads.find(item=>item.id===selectedPayloadId)
    const opticalSurvey = product.surveyType!=='lidar' && product.surveyType!=='geophysical'
    const calculatedOpticalAltitude = opticalAltitudeForGsd(gsd,selectedPayload)
    if (opticalSurvey && calculatedOpticalAltitude===null) {
      setCalculationError('Нет параметров выбранной камеры для расчёта высоты по GSD.')
      setCalculating(false)
      return
    }
    const flightAltitude = opticalSurvey ? Math.max(25,Math.min(5000,calculatedOpticalAltitude!)) : maxAltitude
    const targetTime=new Date(`${scheduledDate}T${scheduledTime}:00`).getTime()
    const forecastHour=weather?.hourly.reduce((best,item)=>Math.abs(new Date(item.time).getTime()-targetTime)<Math.abs(new Date(best.time).getTime()-targetTime)?item:best,weather.hourly[0])
    const weatherInput=forecastHour&&Math.abs(new Date(forecastHour.time).getTime()-targetTime)<4*3_600_000?forecastHour:null
    try {
      const response = await fetch('/api/missions/optimize', {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: `${product.title} · MC-2408`, area: area.geometry, result_type: selectedProduct,
          survey_type: product.surveyType, payload_id: selectedPayloadId || null, gsd_cm_px: gsd, max_flight_altitude_m: flightAltitude, result_types: selectedProducts,
          side_overlap: sideOverlap, forward_overlap: forwardOverlap, survey_line_spacing_m:lineSpacing, corridor_width_m:corridorWidth, launch_point:launchPoint, launch_site_id:selectedLaunchSiteId,
          control_link_mode:controlLinkSettings.mode,radio_equipment_range_km:controlLinkSettings.equipmentRangeKm,ground_antenna_height_m:controlLinkSettings.groundAntennaHeightM,
          wind_speed_mps: weatherInput?.wind_speed_mps ?? weather?.current.wind_speed_mps ?? 3.2, wind_direction_deg: weatherInput?.wind_direction_deg ?? weather?.current.wind_direction_deg ?? 315,
          precipitation_probability: (weatherInput?.precipitation_probability ?? 5)/100, visibility_m: weatherInput?.visibility_m ?? weather?.current.visibility_m ?? 18000,
          earliest_start:`${scheduledDate}T${scheduledTime}:00+03:00`,deadline:deadlineEnabled?`${deadlineDate}T${deadlineTime}:00+03:00`:null,max_uavs:maxUavs,airspace_check:true,
          prohibited_clearance_m:airspaceSettings.prohibited,danger_clearance_m:airspaceSettings.danger,
          obstacle_clearance_m:airspaceSettings.obstacle,settlement_clearance_m:airspaceSettings.settlement,vehicle_separation_m:airspaceSettings.separation,
        }),
      })
      if (!response.ok) { const error = await response.json(); throw new Error(typeof error.detail === 'string' ? error.detail : 'Проверьте исходные параметры задания') }
      const next = await response.json() as OptimizationResult
      if (submittedInput !== currentInput.current) { setNotice('Параметры изменены во время расчёта. Рассчитайте задание заново.'); return }
      setResult(next)
      setSelectedPlan(next.recommended_plan_id)
      setResultsOpen(true)
      setNotice(`Построено ${next.plans.length} сценария за ${next.calculation_ms} мс. Работа активного флота не изменена.`)
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Не удалось рассчитать миссию'
      setCalculationError(message)
      setNotice(message)
    } finally {
      setCalculating(false)
    }
  }

  const loadFleetEconomicsDemo=()=>{
    const demoDate=new Date()
    demoDate.setDate(demoDate.getDate()+3)
    setGeometryMode('area')
    setArea(fleetEconomicsDemoArea)
    setSelectedProduct('orthophoto')
    setSelectedProducts(['orthophoto'])
    setSelectedPayloadId('payload-sony-61')
    setGsd(5)
    setMaxAltitude(150)
    setSideOverlap(.6)
    setForwardOverlap(.7)
    setSelectedLaunchSiteId('base-demo-ligachevo')
    setLaunchPoint([37.25,55.975])
    setControlLinkSettings({mode:'radio',equipmentRangeKm:50,groundAntennaHeightM:5})
    setScheduledDate(localDateIso(demoDate))
    setScheduledTime('09:00')
    setDrawMode(false)
    setResultsOpen(false)
    setResult(null)
    setActiveOrderId(null)
    setConfirmedPlan(null)
    setNotice('Загружены учебный контур и учебная ВПП с курсом 180° и длиной 460 м. Нажмите «Рассчитать»; площадку нужно обследовать перед реальным полётом.')
  }

  const logout = async () => {
    try { await fetch('/api/auth/logout', {method:'POST', credentials:'include'}) } finally {
      setUser(null)
      setCatalogSection(null)
      setResult(null)
      setTelemetry([])
      setPlayback(null)
    }
  }

  const addToSchedule = async () => {
    if(!result||scheduledDate<localDateIso())return
    if(!confirmedPlan||confirmedPlan!==selectedPlan){setNotice('Сначала подтвердите выбранный вариант в результатах расчёта.');return}
    const plan=result.plans.find(item=>item.id===confirmedPlan)
    if(!plan?.vehicles.length)return
    if(plan.duration_min>720){setNotice('План длиннее рабочего дня: разделите кампанию на дневные задания перед назначением.');return}
    const product=missionProducts[selectedProduct]
    const linkedOrder=orders.find(order=>order.id===activeOrderId)
    const baseName=launchSites.find(site=>site.id===selectedLaunchSiteId)?.name||(launchPoint?'Полевая точка старта':'Автоматически выбранная площадка')
    const missionGroupId=crypto.randomUUID()
    const entries=plan.vehicles.map((vehicle,index)=>{
      const uav=catalogUavs.find(item=>item.id===vehicle.uav_id)
      const simulation={missionId:result.mission_id,planId:plan.id,route:vehicle.route,area:area.geometry.type==='Polygon'?area.geometry:undefined,uavType:(uav?.type==='fixed_wing'?'fixed_wing':'multirotor') as 'fixed_wing'|'multirotor',altitudeM:vehicle.altitude_m,color:vehicle.color,baseName,durationSeconds:150,actualDurationMin:vehicle.elapsed_time_min,settlementCoverage:result.settlement_assessments?.[plan.id]?.status}
      return {date:scheduledDate,time:scheduledTime,title:`${linkedOrder?.title||product.title}${plan.vehicles.length>1?` · сектор ${index+1}/${plan.vehicles.length}`:''}`,location:linkedOrder?.location||'Контур задания',uavId:vehicle.uav_id,uavName:vehicle.uav_name,duration:Number((vehicle.elapsed_time_min/60).toFixed(2)),status:'planned' as const,product:selectedProducts.map(id=>missionProducts[id].short).join(' + '),payloadId:selectedPayloadId,areaKm2:Math.max(0.01,Number((areaMetrics.areaKm2/plan.vehicles.length).toFixed(2))),costRub:Math.round(vehicle.cost_rub),source:'planner' as const,orderId:linkedOrder?.id,orderNumber:linkedOrder?.number,customerName:linkedOrder?.customer.name,missionGroupId,simulation}
    })
    let missions:ScheduledMission[]
    try{const response=await fetch('/api/schedule/batch',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({entries})});if(!response.ok){const body=await response.json();throw new Error(body.detail||'Не удалось назначить все борта плана')}missions=(await response.json()).items as ScheduledMission[]}catch(error){setNotice(error instanceof Error?error.message:'Не удалось добавить план в календарь');return}
    setScheduledMissions(items=>[...items,...missions])
    if(linkedOrder){setOrders(items=>items.map(item=>item.id===linkedOrder.id?{...item,status:'scheduled'}:item));setActiveOrderId(null)}
    setView('calendar');setCatalogSection(null)
    setNotice(`Задание запланировано на ${scheduledDate.split('-').reverse().join('.')} в ${scheduledTime}: ${missions.length} БВС и ${plan.sorties||missions.length} вылетов.`)
  }

  const addMaintenance = async (draft:MaintenanceDraft) => {
    const uav=catalogUavs.find(item=>item.id===draft.uavId)
    if(!uav)throw new Error('Выбранный борт не найден')
    const payload={...draft,uavName:uav.name,status:'maintenance' as const,product:'Техническое обслуживание',source:'planner' as const}
    const response=await fetch('/api/schedule',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
    if(!response.ok){const body=await response.json().catch(()=>null);throw new Error(body?.detail||'Не удалось запланировать обслуживание')}
    const mission=await response.json() as ScheduledMission
    setScheduledMissions(items=>[...items.filter(item=>item.id!==mission.id),mission])
    setNotice(`${uav.name}: обслуживание запланировано на ${draft.date.split('-').reverse().join('.')} в ${draft.time}. Борт заблокирован на ${draft.duration} ч.`)
    return mission
  }

  const updateScheduledMission=async(mission:ScheduledMission,updates:Pick<ScheduledMission,'status'|'demoStartedAt'|'demoCompletedAt'>)=>{
    const response=await fetch(`/api/schedule/${mission.id}`,{method:'PATCH',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify(updates)})
    if(!response.ok){const body=await response.json().catch(()=>null);throw new Error(body?.detail||'Не удалось изменить состояние задания')}
    const updated=await response.json() as ScheduledMission
    setScheduledMissions(items=>items.map(item=>item.id===updated.id?updated:item))
    return updated
  }

  const openMissionReplay=(mission:ScheduledMission)=>{
    if(!mission.simulation)return
    const existing=playback?.missionId===mission.id?playback.cursor:0
    const mode=mission.status==='active'?'live':mission.status==='planned'?'simulation':'replay'
    setPlayback({missionId:mission.id,mode,cursor:existing,playing:true,rate:mode==='live'?4:1})
    setDemoMode(false);setSelectedUavId(mission.uavId);setSimulationRate(0);setView('operations');setCatalogSection(null)
  }

  useEffect(()=>{
    if(!user)return
    const synchronize=()=>{
      const now=Date.now()
      scheduledMissions.filter(mission=>mission.source==='planner'&&mission.simulation).forEach(mission=>{
        if(scheduleLifecycleRef.current.has(mission.id))return
        const start=slotStart(mission.date,mission.time),end=start+mission.duration*3_600_000
        const next=mission.status==='planned'&&now>=start&&now<end?{status:'active' as const,demoStartedAt:new Date(start).toISOString(),demoCompletedAt:undefined}:mission.status==='active'&&now>=end&&!playback?{status:'completed' as const,demoStartedAt:mission.demoStartedAt,demoCompletedAt:new Date(end).toISOString()}:null
        if(!next)return
        scheduleLifecycleRef.current.add(mission.id)
        updateScheduledMission(mission,next).catch(()=>undefined).finally(()=>scheduleLifecycleRef.current.delete(mission.id))
      })
    }
    synchronize();const timer=window.setInterval(synchronize,5000);return()=>window.clearInterval(timer)
  },[user,scheduledMissions,playback])

  useEffect(()=>{
    if(!playback?.playing)return
    const timer=window.setInterval(()=>setPlayback(current=>{
      if(!current?.playing)return current
      const mission=scheduledMissions.find(item=>item.id===current.missionId)
      const duration=mission?.simulation?.durationSeconds||150
      const cursor=Math.min(duration,current.cursor+.2*current.rate)
      return{...current,cursor,playing:cursor<duration}
    }),200)
    return()=>window.clearInterval(timer)
  },[playback?.playing,scheduledMissions])

  useEffect(()=>{
    if(!playback||playback.mode!=='live')return
    const mission=scheduledMissions.find(item=>item.id===playback.missionId)
    if(!mission||mission.status!=='active'||playback.cursor<(mission.simulation?.durationSeconds||150)||completingMissionRef.current===mission.id)return
    completingMissionRef.current=mission.id
    updateScheduledMission(mission,{status:'completed',demoStartedAt:mission.demoStartedAt,demoCompletedAt:new Date().toISOString()}).then(()=>{
      setPlayback(current=>current?.missionId===mission.id?{...current,mode:'replay',playing:false}:current)
      setNotice(`${mission.uavName} завершил задание. Повтор полёта сохранён в календаре.`)
    }).catch(error=>setNotice(error instanceof Error?error.message:'Не удалось завершить задание')).finally(()=>{completingMissionRef.current=null})
  },[playback,scheduledMissions])

  const sendOrderToPlanner=(order:CustomerOrder)=>{
    const geometry=order.geometry
    if(geometry.type==='LineString'){setGeometryMode('corridor');setArea({type:'Feature',properties:{orderId:order.id},geometry})}
    else if(geometry.type==='Polygon'){setGeometryMode('area');setArea({type:'Feature',properties:{orderId:order.id},geometry})}
    const profile=missionProducts[order.result_type];const recommendedAltitude:Record<SurveyType,number>={rgb:150,ir:120,multispectral:150,lidar:100,geophysical:50}
    setSelectedProduct(order.result_type);setSelectedProducts([order.result_type]);setGsd(Number(order.requirements.gsd_cm_px)||profile.gsd);setSideOverlap(profile.sideOverlap);setForwardOverlap(profile.forwardOverlap);setMaxAltitude(recommendedAltitude[profile.surveyType]);setScheduledDate(order.desired_date<localDateIso()?localDateIso():order.desired_date);setScheduledTime(order.desired_time);setActiveOrderId(order.id);setResult(null);setCatalogSection(null);setView('planner');setNotice(`${order.number}: требования заказчика переданы в планировщик.`)
  }

  const selectedRoutes: GeoJSON.FeatureCollection<GeoJSON.LineString | GeoJSON.MultiLineString> = result ? {
    type: 'FeatureCollection',
    // The optimizer returns one ordered LineString containing transit, every
    // survey strip and its smooth connector. Rendering that single geometry
    // prevents turns from becoming detached when a dense strip set is reduced.
    features: (result.plans.find((plan) => plan.id === selectedPlan)?.vehicles || []).map((vehicle) => ({
      type: 'Feature', properties: { color: vehicle.color, name: vehicle.uav_name, kind: 'complete-route' }, geometry: vehicle.route,
    })),
  } : {type:'FeatureCollection',features:[]}
  const selectedSettlements:GeoJSON.FeatureCollection={type:'FeatureCollection',features:(result?.settlement_assessments?.[selectedPlan]?.intersections||[]).map(item=>({type:'Feature',properties:{name:item.name,place:item.place,region:item.region,district:item.district,source:item.source},geometry:item.geometry}))}
  const conflictRestrictionFeatures:GeoJSON.Feature[]=(result?.airspace?.conflicts||[]).map(conflict=>({type:'Feature',properties:{id:conflict.id,category:conflict.category,code:conflict.code,name:conflict.name,clearance_m:conflict.clearance_m},geometry:conflict.geometry}))
  const knownRestrictionIds=new Set(operationalAirspace.features.map(feature=>String(feature.properties?.id||feature.id||'')))
  const airspaceRestrictions:GeoJSON.FeatureCollection={type:'FeatureCollection',features:[...operationalAirspace.features,...conflictRestrictionFeatures.filter(feature=>!knownRestrictionIds.has(String(feature.properties?.id||feature.id||'')))]}
  const airspaceCounts=(['prohibited','danger','obstacle'] as OperationalAirspaceCategory[]).reduce((counts,category)=>({...counts,[category]:operationalAirspace.features.filter(feature=>feature.properties?.category===category).length}),{} as Record<OperationalAirspaceCategory,number>)
  const radarFrame=radarEnabled&&radar?.frames[radarIndex]?{url:radar.frames[radarIndex].url,coordinates:[[radar.bbox[0][0],radar.bbox[1][1]],[radar.bbox[1][0],radar.bbox[1][1]],[radar.bbox[1][0],radar.bbox[0][1]],[radar.bbox[0][0],radar.bbox[0][1]]] as [[number,number],[number,number],[number,number],[number,number]]}:null
  const radarTime=radar?.frames[radarIndex]?new Date(radar.frames[radarIndex].time*1000).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'}):'—'
  const selectedWeatherTarget=new Date(`${scheduledDate}T${scheduledTime}:00`).getTime()
  const selectedForecast=weather?.hourly.length?weather.hourly.reduce((best,item)=>Math.abs(new Date(item.time).getTime()-selectedWeatherTarget)<Math.abs(new Date(best.time).getTime()-selectedWeatherTarget)?item:best):undefined
  const planningWeather=selectedForecast&&Math.abs(new Date(selectedForecast.time).getTime()-selectedWeatherTarget)<4*3_600_000?selectedForecast:undefined
  const replayableMissions=scheduledMissions.map(mission=>{
    if(mission.simulation||mission.status==='maintenance')return mission
    const features=operationRoutes.features.filter(feature=>feature.properties?.uav_id===mission.uavId)
    const coordinates=features.flatMap(feature=>feature.geometry.type==='LineString'?feature.geometry.coordinates:feature.geometry.coordinates.flat())
    if(coordinates.length<2)return mission
    const uav=catalogUavs.find(item=>item.id===mission.uavId)
    return{...mission,simulation:{missionId:`reconstructed-${mission.id}`,planId:'historical',route:{type:'LineString' as const,coordinates},uavType:(uav?.type==='fixed_wing'?'fixed_wing':'multirotor') as 'fixed_wing'|'multirotor',altitudeM:uav?.type==='fixed_wing'?145:90,color:String(features[0]?.properties?.color||'#42bdd8'),baseName:mission.location,durationSeconds:150,actualDurationMin:Math.round(mission.duration*60),reconstructed:true}}
  })
  const playbackMission=playback?replayableMissions.find(item=>item.id===playback.missionId):undefined
  const playbackProgress=playback&&playbackMission?.simulation?Math.min(1,playback.cursor/playbackMission.simulation.durationSeconds):0
  const sceneTime=playback&&playbackMission?slotStart(playbackMission.date,playbackMission.time)+playbackProgress*playbackMission.duration*3_600_000:calendarClock
  const calendarScene=(!demoMode||playback)?replayableMissions.filter(mission=>{
    if(!mission.simulation||mission.status==='maintenance')return false
    const start=slotStart(mission.date,mission.time)
    return sceneTime>=start&&sceneTime<=start+mission.duration*3_600_000
  }):[]
  if(playbackMission?.simulation&&!calendarScene.some(mission=>mission.id===playbackMission.id))calendarScene.push(playbackMission)
  const sceneSamples=calendarScene.map(mission=>{
    const start=slotStart(mission.date,mission.time),duration=Math.max(1,mission.duration*3_600_000)
    const progress=Math.max(0,Math.min(1,(sceneTime-start)/duration))
    const cursor=progress*(mission.simulation?.durationSeconds||1)
    return{mission,progress,telemetry:replayTelemetry(mission,cursor,playback?.rate||1) as Telemetry}
  })
  const displayedTelemetry=demoMode&&!playback?telemetry:sceneSamples.map(item=>item.telemetry)
  const calendarRouteFeatures:GeoJSON.Feature<GeoJSON.LineString|GeoJSON.MultiLineString>[] = sceneSamples.flatMap(({mission,progress})=>mission.simulation?[
    {type:'Feature' as const,properties:{color:mission.simulation.color,name:mission.title,kind:'transit',direction:'planned',uav_id:mission.uavId},geometry:mission.simulation.route},
    {type:'Feature' as const,properties:{color:mission.simulation.color,name:mission.title,kind:'coverage',direction:'recorded',uav_id:mission.uavId},geometry:replayTrail(mission.simulation.route,progress)},
  ]:[])
  const displayedRoutes:GeoJSON.FeatureCollection<GeoJSON.LineString|GeoJSON.MultiLineString>=demoMode&&!playback?operationRoutes:{type:'FeatureCollection',features:calendarRouteFeatures}
  const calendarAreaFeatures:GeoJSON.Feature<GeoJSON.Polygon>[]=sceneSamples.flatMap(({mission})=>mission.simulation?.area?[{type:'Feature' as const,properties:{color:mission.simulation.color,name:mission.title},geometry:mission.simulation.area}]:[])
  const displayedAreas:GeoJSON.FeatureCollection<GeoJSON.Polygon>=demoMode&&!playback?operationAreas:{type:'FeatureCollection',features:calendarAreaFeatures}
  const sceneTimeLabel=new Date(sceneTime).toLocaleString('ru-RU',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'})

  return (
    <main className="app-shell">
      {user && <MapCanvas operationAreas={displayedAreas} routeData={displayedRoutes} areaData={area} drawMode={false} onAreaChange={setArea} onDrawingComplete={completeDrawing} telemetry={displayedTelemetry} selectedUavId={selectedUavId} onSelectUav={setSelectedUavId} externalAircraft={airTrafficEnabled ? (airTraffic?.aircraft || []).filter(item=>groundTrafficEnabled||!item.on_ground) : []} radarFrame={radarFrame} airspaceZones={operationalAirspace} airspaceVisibility={{prohibited:view==='operations'&&airspaceVisibility.prohibited,danger:view==='operations'&&airspaceVisibility.danger,obstacle:view==='operations'&&airspaceVisibility.obstacle}} settlementsVisible={view==='operations'&&settlementsVisible} onSettlementCount={setSettlementCount} showMapTools={view==='operations'&&!catalogSection} />}
      {user&&view==='operations'&&!catalogSection&&<WeatherEffects weather={weather} effects={weatherEffects}/>} 
      {user && <>
        <TopBar user={user} systemOk={systemOk} demoMode={demoMode} onDemoMode={enabled=>{setDemoMode(enabled);setPlayback(null);setSimulationRate(enabled?1:0);setCalendarClock(Date.now());setSelectedUavId(null)}} active={catalogSection || view} onView={(value) => {if(value==='calendar'){setCalendarMode('flights');setCalendarFocusUavId(null);setMaintenanceDraftUavId(null)}setView(value); setCatalogSection(null)}} onOpenCatalog={setCatalogSection} onLogout={logout} />
        {view === 'orders' && !catalogSection && <OrdersWorkspace orders={orders} loadError={ordersLoadError} onOrdersChange={setOrders} onSendToPlanner={sendOrderToPlanner}/>} 
        {view === 'planner' && !catalogSection && <section className="planner-workspace" aria-label="Планировщик заданий">
          <div className="planner-title"><div><span>ПЛАНИРОВАНИЕ / ПРЕДВАРИТЕЛЬНЫЙ РАСЧЁТ</span><h1>Конструктор полётного задания</h1><p>Геометрия → оптика и качество → этапы полёта → сравнение ресурсов.</p></div><div className="planner-title-actions">{result&&<button type="button" className="results-button" onClick={()=>setResultsOpen(true)}><Sparkles size={15}/> Результаты</button>}<button type="button" onClick={()=>setMethodologyOpen(true)}><CircleHelp size={16}/> Методика расчёта</button></div></div>
          {calculationError&&<div className="planner-calculation-error" role="alert"><strong>Маршрут не построен</strong><span>{calculationError}</span></div>}
          {activeOrderId&&orders.find(order=>order.id===activeOrderId)&&<div className="linked-order"><ClipboardList size={16}/><span><small>ЗАЯВКА ЗАКАЗЧИКА</small><strong>{orders.find(order=>order.id===activeOrderId)!.number} · {orders.find(order=>order.id===activeOrderId)!.customer.name}</strong></span><button onClick={()=>{setActiveOrderId(null);setView('orders')}}>Отвязать</button></div>}
          <div className="planner-columns"><div>
        <MissionPanel missions={scheduledMissions} scheduledDate={scheduledDate} onScheduledDate={value=>{setScheduledDate(value);setResult(null)}} scheduledTime={scheduledTime} onScheduledTime={value=>{setScheduledTime(value);setResult(null)}} deadlineEnabled={deadlineEnabled} onDeadlineEnabled={value=>{setDeadlineEnabled(value);setResult(null)}} deadlineDate={deadlineDate} onDeadlineDate={value=>{setDeadlineDate(value);setResult(null)}} deadlineTime={deadlineTime} onDeadlineTime={value=>{setDeadlineTime(value);setResult(null)}} maxUavs={maxUavs} onMaxUavs={value=>{setMaxUavs(value);setResult(null)}} selectedPlan={selectedPlan} confirmedPlan={confirmedPlan} onOpenResults={()=>setResultsOpen(true)} onSchedule={addToSchedule} lineSpacing={lineSpacing} onLineSpacing={value=>{setLineSpacing(value);setResult(null)}} selectedProducts={selectedProducts} maxAltitude={maxAltitude} onMaxAltitude={value => {setMaxAltitude(value); setResult(null)}} onCalculate={calculate} onLoadEconomicsDemo={loadFleetEconomicsDemo} drawMode={drawMode || calculating} selectedProduct={selectedProduct} planningWeather={planningWeather} onSelectProduct={(product) => {
          const profile = missionProducts[product]
          const same = profile.surveyType === missionProducts[selectedProduct].surveyType
          const corridorProduct = product === 'powerline_report'
          const next = corridorProduct ? [product] : same ? (selectedProducts.includes(product) ? selectedProducts.filter(p => p !== product) : [...selectedProducts,product]) : [product]
          if (!next.length) return
          const current = next.includes(product) ? product : next[0]
          if (corridorProduct) {setGeometryMode('corridor');setArea(initialMissionCorridor)}
          else if (!['powerline_report','thermal_map'].includes(current) && geometryMode==='corridor') {setGeometryMode('area');setArea(initialMissionArea)}
          const recommendedAltitude:Record<SurveyType,number>={rgb:150,ir:120,multispectral:150,lidar:100,geophysical:50}
          setSelectedProducts(next); setSelectedProduct(current); setMaxAltitude(recommendedAltitude[missionProducts[current].surveyType]); setGsd(Math.min(...next.map(p => missionProducts[p].gsd))); setSideOverlap(Math.max(...next.map(p => missionProducts[p].sideOverlap))); setForwardOverlap(Math.max(...next.map(p => missionProducts[p].forwardOverlap))); setResult(null)
        }} result={result} airspaceSettings={airspaceSettings} onAirspaceSettings={value=>{setAirspaceSettings(value);setResult(null)}} controlLinkSettings={controlLinkSettings} onControlLinkSettings={value=>{setControlLinkSettings(value);setResult(null)}} catalogCounts={{ uavs: catalogUavs.filter((item) => item.status === 'ready').length, payloads: catalogPayloads.length }} payloads={catalogPayloads} uavs={catalogUavs} selectedPayloadId={selectedPayloadId} onPayloadChange={(id) => { setSelectedPayloadId(id); setResult(null) }} gsd={gsd} onGsdChange={(value) => { setGsd(value); setResult(null) }} sideOverlap={sideOverlap} onSideOverlapChange={(value) => { setSideOverlap(value); setResult(null) }} forwardOverlap={forwardOverlap} onForwardOverlapChange={(value) => { setForwardOverlap(value); setResult(null) }} launchSites={launchSites} launchPoint={launchPoint} selectedLaunchSiteId={selectedLaunchSiteId} onLaunchSite={id=>{if(id==='auto'){setSelectedLaunchSiteId(null);setLaunchPoint(null)}else if(id==='custom'){setSelectedLaunchSiteId(null);setLaunchPoint(null)}else{const site=launchSites.find(item=>item.id===id);setSelectedLaunchSiteId(id);if(site)setLaunchPoint([site.lon,site.lat])}setResult(null)}} />
          </div><div className="planner-map-column"><PolygonEditor restrictions={airspaceRestrictions} settlements={selectedSettlements} launchSites={launchSites} selectedLaunchSiteId={selectedLaunchSiteId} resultsOverlay={Boolean(result&&resultsOpen)} onLaunchSite={id=>{if(id==='auto'){setSelectedLaunchSiteId(null);setLaunchPoint(null)}else if(id==='custom'){setSelectedLaunchSiteId(null);setLaunchPoint(null)}else{const site=launchSites.find(item=>item.id===id);setSelectedLaunchSiteId(id);if(site)setLaunchPoint([site.lon,site.lat])}setResult(null)}} launch={launchPoint} onLaunch={p=>{setSelectedLaunchSiteId(null);setLaunchPoint(p);setResult(null)}} area={area} onEditing={setDrawMode} onChange={value => {setArea(value); setResult(null)}} routes={selectedRoutes} mode={geometryMode} corridorAllowed={['powerline_report','thermal_map'].includes(selectedProduct)&&selectedProducts.length===1} corridorWidth={corridorWidth} onCorridorWidth={value=>{setCorridorWidth(value);setResult(null)}} onModeChange={mode=>{setGeometryMode(mode);setArea(mode==='corridor'?initialMissionCorridor:initialMissionArea);setResult(null)}} onImport={importMissionFile} onReset={()=>{setGeometryMode('area');setArea(initialMissionArea);setDrawMode(false);setResult(null)}} areaSummary={geometryMode==='corridor'?`${areaMetrics.routeLengthKm.toLocaleString('ru-RU',{maximumFractionDigits:2})} км трассы · ${areaMetrics.areaKm2.toLocaleString('ru-RU',{maximumFractionDigits:2})} км² коридора`:`${areaMetrics.areaKm2.toLocaleString('ru-RU',{maximumFractionDigits:2})} км² · ${areaMetrics.perimeterKm.toLocaleString('ru-RU',{maximumFractionDigits:1})} км периметр`} />
          </div></div>
        {methodologyOpen&&<MethodologyModal onClose={()=>setMethodologyOpen(false)}/>} 
        {result&&resultsOpen&&<MissionResultsModal result={result} selected={selectedPlan} confirmedPlan={confirmedPlan} onSelect={id=>{setSelectedPlan(id);if(id!==confirmedPlan)setConfirmedPlan(null)}} onConfirm={id=>{setConfirmedPlan(id);setResultsOpen(false);setNotice(`Для календаря подтверждён вариант «${result.plans.find(plan=>plan.id===id)?.label||id}».`)}} onClose={()=>setResultsOpen(false)}/>} 
        </section>}
        {view === 'calendar' && !catalogSection && <CalendarDashboard uavs={catalogUavs} missions={replayableMissions} onReplay={openMissionReplay} onCreateMaintenance={addMaintenance} initialMode={calendarMode} focusUavId={calendarFocusUavId} maintenanceDraftUavId={maintenanceDraftUavId} onMaintenanceDraftConsumed={()=>setMaintenanceDraftUavId(null)} />}
        {(view === 'airspace'||view==='bases') && !catalogSection && <ObjectsWorkspace sites={launchSites} onSitesChange={items=>{setLaunchSites(items);const selected=items.find(item=>item.id===selectedLaunchSiteId);if(selected)setLaunchPoint([selected.lon,selected.lat])}} />}
        {view === 'operations' && !catalogSection && <>
          <div className="operations-left-stack"><WeatherCard data={weather} radarEnabled={radarEnabled} onRadar={()=>setRadarEnabled(value=>!value)} effects={weatherEffects} onEffects={setWeatherEffects} radarTime={radarTime} radarLoading={radarLoading}/><AirTrafficCard enabled={airTrafficEnabled} loading={airTrafficLoading} data={airTraffic} error={airTrafficError} onToggle={() => setAirTrafficEnabled(value => !value)} showGround={groundTrafficEnabled} onGroundToggle={()=>setGroundTrafficEnabled(value=>!value)}/></div>
          <div className="airspace-layer-controls" aria-label="Слои воздушных ограничений">{([['prohibited','Запретные зоны'],['danger','Опасные зоны'],['obstacle','Высотные объекты']] as [OperationalAirspaceCategory,string][]).map(([category,label])=><button key={category} className={`airspace-layer-toggle ${category} ${airspaceVisibility[category]?'active':''}`} onClick={()=>setAirspaceVisibility(value=>({...value,[category]:!value[category]}))} aria-pressed={airspaceVisibility[category]}><ShieldAlert size={15}/><span>{label}</span><b>{airspaceCounts[category]}</b></button>)}<button className={`airspace-layer-toggle settlements ${settlementsVisible?'active':''}`} onClick={()=>setSettlementsVisible(value=>!value)} aria-pressed={settlementsVisible} title="Границы населённых пунктов из локального справочника OSM; число показывает объекты в видимой области карты"><MapPin size={15}/><span>Населённые пункты</span><b>{settlementCount}</b></button></div>
          <FleetTelemetryPanel items={displayedTelemetry} selectedId={selectedUavId} onSelect={setSelectedUavId} demoMode={Boolean(demoMode&&!playback)} contextLabel={playback?`${sceneTimeLabel} · ${sceneSamples.length} БВС`:`Сейчас · ${sceneSamples.length} БВС`} simulationRate={simulationRate} onSimulationRate={setSimulationRate}/>
          {playback&&playbackMission?.simulation&&<ReplayControls mission={playbackMission} playback={playback} contextCount={sceneSamples.length} onChange={setPlayback} onClose={()=>{setPlayback(null);setCalendarClock(Date.now());setView('calendar')}}/>}
        </>}
        {notice && <button className="app-notice" onClick={() => setNotice('')}><Check size={15} /> {notice}</button>}
        <div className="map-credit">OPENFREEMAP · OPENSTREETMAP · БЕЗ API-КЛЮЧА</div>
        {catalogSection && <CatalogWorkspace section={catalogSection} onSection={setCatalogSection} onClose={() => setCatalogSection(null)} uavs={catalogUavs} payloads={catalogPayloads} technologies={technologyProfiles} missions={replayableMissions} onOpenSchedule={uavId=>{setCalendarMode('combined');setCalendarFocusUavId(uavId);setMaintenanceDraftUavId(null);setCatalogSection(null);setView('calendar')}} onPlanMaintenance={uavId=>{setCalendarMode('maintenance');setCalendarFocusUavId(uavId);setMaintenanceDraftUavId(uavId);setCatalogSection(null);setView('calendar')}} />}
      </>}
      {user === null && <LoginScreen onLogin={setUser} />}
    </main>
  )
}
