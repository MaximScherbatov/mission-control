import { Download, FileCode2, FileJson, Route } from 'lucide-react'

type Props = {
  missionId: string
  planId: string
  compact?: boolean
}

export function MissionExportActions({ missionId, planId, compact = false }: Props) {
  const endpoint = `/api/missions/${encodeURIComponent(missionId)}/export?plan=${encodeURIComponent(planId)}&format=`
  return <section className={`mission-export-actions ${compact ? 'compact' : ''}`} aria-label="Скачать полётное задание">
    <div className="mission-export-heading"><Download size={17}/><span><strong>Полётное задание</strong><small>Выбранный вариант · маршруты всех БВС и вылетов</small></span></div>
    <div className="mission-export-links">
      <a className="primary" href={`${endpoint}kml`} download><Route size={16}/> Скачать KML</a>
      <a href={`${endpoint}geojson`} download><FileJson size={16}/> GeoJSON</a>
      <a href={`${endpoint}gpx`} download><FileCode2 size={16}/> GPX</a>
    </div>
  </section>
}
