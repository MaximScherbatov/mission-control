export function AttitudeIndicator({pitch,roll}:{pitch:number;roll:number}) {
  const safePitch=Math.max(-25,Math.min(25,pitch));const safeRoll=Math.max(-60,Math.min(60,roll))
  const ladder=[-20,-10,10,20]
  return <svg className="attitude-svg" viewBox="0 0 180 180" role="img" aria-label={`Авиагоризонт: тангаж ${pitch.toFixed(1)}°, крен ${roll.toFixed(1)}°`}>
    <defs><clipPath id="attitude-clip"><circle cx="90" cy="90" r="72"/></clipPath><radialGradient id="attitude-glass"><stop offset="70%" stopColor="#fff" stopOpacity="0"/><stop offset="100%" stopColor="#061014" stopOpacity=".42"/></radialGradient></defs>
    <circle cx="90" cy="90" r="87" fill="#182126" stroke="#758087" strokeWidth="2"/><circle cx="90" cy="90" r="78" fill="#10171b" stroke="#05090b" strokeWidth="5"/>
    <g clipPath="url(#attitude-clip)"><g transform={`rotate(${-safeRoll} 90 90) translate(0 ${safePitch*2.35})`} className="attitude-world"><rect x="-70" y="-100" width="320" height="190" fill="#2582a3"/><rect x="-70" y="90" width="320" height="190" fill="#8a6742"/><line x1="-60" y1="90" x2="240" y2="90" stroke="#f5f0d9" strokeWidth="3"/>{ladder.map(value=>{const y=90-value*2.35;const width=Math.abs(value)===20?44:31;return <g key={value}><line x1={90-width} y1={y} x2={90+width} y2={y} stroke="#f3eee0" strokeWidth="2"/><text x={84-width} y={y+4} fill="#fff" fontSize="9" textAnchor="end">{Math.abs(value)}</text><text x={96+width} y={y+4} fill="#fff" fontSize="9">{Math.abs(value)}</text></g>})}</g><circle cx="90" cy="90" r="72" fill="url(#attitude-glass)"/></g>
    <g className="bank-scale" fill="none" stroke="#ecf1ee" strokeWidth="2">{[-60,-45,-30,-20,-10,0,10,20,30,45,60].map(value=><line key={value} x1="90" y1={value%30===0?20:24} x2="90" y2="31" transform={`rotate(${value} 90 90)`}/>)}</g><path d="M84 31 90 22 96 31Z" fill="#f4c35c"/>
    <g className="fixed-aircraft" fill="none" stroke="#ffc44f" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round"><path d="M39 89h35l8 8h16l8-8h35"/><circle cx="90" cy="90" r="4" fill="#ffc44f" stroke="none"/><path d="M90 97v13"/></g>
    <circle cx="90" cy="90" r="72" fill="none" stroke="#aab3b7" strokeWidth="2"/>
  </svg>
}
