const CARDINALS:Record<number,string>={0:'N',90:'E',180:'S',270:'W'}

export function HeadingIndicator({heading}:{heading:number}) {
  const normalized=((heading%360)+360)%360
  const ticks=Array.from({length:36},(_,index)=>index*10)
  return <svg className="heading-svg" viewBox="0 0 180 180" role="img" aria-label={`Гироскопический компас: курс ${normalized.toFixed(0)}°`}>
    <defs><radialGradient id="heading-face"><stop offset="0" stopColor="#34454c"/><stop offset=".72" stopColor="#17242a"/><stop offset="1" stopColor="#080e11"/></radialGradient><radialGradient id="heading-glass"><stop offset="55%" stopColor="#d6f5ff" stopOpacity=".04"/><stop offset="100%" stopColor="#000" stopOpacity=".36"/></radialGradient></defs>
    <circle cx="90" cy="90" r="87" fill="#182126" stroke="#758087" strokeWidth="2"/><circle cx="90" cy="90" r="78" fill="url(#heading-face)" stroke="#05090b" strokeWidth="5"/>
    <g className="heading-card" transform={`rotate(${-normalized} 90 90)`}>
      {ticks.map(value=>{const cardinal=CARDINALS[value];const major=value%30===0;return <g key={value} transform={`rotate(${value} 90 90)`}><line x1="90" y1={major?24:29} x2="90" y2="39" stroke={cardinal?'#68dbef':'#e9f0ed'} strokeWidth={major?2.5:1.4}/>{major&&<text x="90" y="52" fill={cardinal?'#68dbef':'#f1f3ed'} fontSize={cardinal?15:10} fontWeight="800" textAnchor="middle" transform={`rotate(${-value} 90 47)`}>{cardinal||value/10}</text>}</g>})}
      <circle cx="90" cy="90" r="48" fill="none" stroke="rgba(215,230,233,.16)" strokeWidth="1"/>
    </g>
    <path d="M83 25 90 15 97 25Z" fill="#ffc44f"/><path d="M90 58v64M65 91h50M75 108l15 14 15-14" fill="none" stroke="#ffc44f" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round"/>
    <rect x="66" y="72" width="48" height="25" rx="6" fill="rgba(5,12,15,.76)" stroke="rgba(120,217,196,.5)"/><text x="90" y="90" fill="#f4f7f4" fontSize="15" fontWeight="800" textAnchor="middle">{String(Math.round(normalized)).padStart(3,'0')}°</text>
    <circle cx="90" cy="90" r="72" fill="url(#heading-glass)" stroke="#aab3b7" strokeWidth="2"/>
  </svg>
}
