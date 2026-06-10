import { useState, useEffect } from "react";
import {
  LineChart, Line, AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, ReferenceLine
} from "recharts";

// ─── Constants ────────────────────────────────────────────────────────────────
const SERVICES = ["frontend", "checkout", "payment", "cart", "catalog", "shipping"];
const EDGES = [
  ["frontend","checkout"],["frontend","catalog"],
  ["checkout","payment"],["checkout","cart"],["cart","shipping"]
];
const NODE_POS = {
  frontend:[48,95], catalog:[130,38], checkout:[130,152],
  cart:[215,72], payment:[215,135], shipping:[215,195]
};

// ─── Colours — clean light palette ──────────────────────────────────────────
const C = {
  blue:    "#2563EB",
  indigo:  "#4F46E5",
  green:   "#16A34A",
  amber:   "#D97706",
  red:     "#DC2626",
  slate:   "#475569",
  sky:     "#0EA5E9",
  bg:      "#F8FAFC",
  surface: "#FFFFFF",
  border:  "#E2E8F0",
  text:    "#1E293B",
  text2:   "#64748B",
  text3:   "#94A3B8",
};

// ─── Mock data ────────────────────────────────────────────────────────────────
const genHistory = () => Array.from({length:30},(_,i)=>({
  t:`${i*2}s`,
  predFrontend: Math.round(120 + Math.sin(i/3)*30),
  actFrontend:  Math.round(118 + Math.sin(i/3)*30 + Math.random()*10 - 5),
  predCheckout: Math.round(60  + Math.cos(i/4)*18),
  actCheckout:  Math.round(58  + Math.cos(i/4)*18 + Math.random()*8  - 4),
}));

const genServices = (chaos) => SERVICES.map(s => ({
  name: s,
  predRPS:  Math.round(40  + Math.random()*180),
  actRPS:   Math.round(38  + Math.random()*185 + (chaos && s==="checkout" ? 120 : 0)),
  predRep:  Math.round(2   + Math.random()*8),
  actRep:   Math.round(2   + Math.random()*8),
  p99:      Math.round(chaos && s==="checkout" ? 380+Math.random()*150 : 45+Math.random()*140),
  conf:     parseFloat((0.65+Math.random()*0.33).toFixed(3)),
  mape:     parseFloat((5   + Math.random()*20).toFixed(1)),
  phase:    ["Stable","Stable","Predicting","Scaling","Stable","Predicting"][Math.floor(Math.random()*6)],
}));

const genConf  = () => Array.from({length:30},(_,i)=>({
  t:`${i*2}s`, v: parseFloat((0.78+Math.sin(i/6)*0.12+Math.random()*0.04).toFixed(3))
}));
const genRepl  = () => Array.from({length:30},(_,i)=>({
  t:`${i*2}s`,
  phantom: Math.round(3+Math.sin(i/5)*2),
  hpa:     Math.round(3+Math.sin(i/5)*2 + (Math.random()>0.7 ? 2 : 0)),
}));

const CMP_DATA = [
  {name:"Spike",   PHANTOM:87,  HPA:248, KEDA:192},
  {name:"Ramp",    PHANTOM:64,  HPA:181, KEDA:143},
  {name:"Periodic",PHANTOM:58,  HPA:162, KEDA:129},
];

// ─── Helpers ──────────────────────────────────────────────────────────────────
const phaseBadge = (phase) => {
  const map = {
    Stable:    {bg:"#DCFCE7",color:"#16A34A"},
    Scaling:   {bg:"#FEF3C7",color:"#D97706"},
    Predicting:{bg:"#DBEAFE",color:"#2563EB"},
    Error:     {bg:"#FEE2E2",color:"#DC2626"},
  };
  const s = map[phase] || {bg:"#F1F5F9",color:"#64748B"};
  return (
    <span style={{
      background:s.bg, color:s.color,
      padding:"2px 8px", borderRadius:99,
      fontSize:10, fontWeight:600, letterSpacing:.5
    }}>{phase}</span>
  );
};

const ConfBar = ({v}) => {
  const color = v>=.82 ? C.green : v>=.68 ? C.amber : C.red;
  return (
    <div style={{display:"flex",alignItems:"center",gap:6}}>
      <div style={{width:56,height:5,background:"#E2E8F0",borderRadius:99,overflow:"hidden"}}>
        <div style={{width:`${v*100}%`,height:"100%",background:color,borderRadius:99,transition:"width .5s"}}/>
      </div>
      <span style={{fontSize:11,color,fontWeight:600,minWidth:34}}>{(v*100).toFixed(0)}%</span>
    </div>
  );
};

const Stat = ({label,value,sub,color,icon}) => (
  <div style={{
    background:C.surface, border:`1px solid ${C.border}`,
    borderRadius:12, padding:"16px 20px",
    borderTop:`3px solid ${color||C.blue}`
  }}>
    <div style={{fontSize:11,color:C.text2,fontWeight:500,textTransform:"uppercase",letterSpacing:.8,marginBottom:6}}>
      {icon} {label}
    </div>
    <div style={{fontSize:28,fontWeight:700,color:color||C.text,lineHeight:1}}>{value}</div>
    {sub && <div style={{fontSize:11,color:C.text3,marginTop:4}}>{sub}</div>}
  </div>
);

const Panel = ({title,children,color}) => (
  <div style={{
    background:C.surface, border:`1px solid ${C.border}`,
    borderRadius:12, padding:"18px 20px",
    borderLeft:`4px solid ${color||C.blue}`
  }}>
    <div style={{fontSize:11,fontWeight:600,textTransform:"uppercase",
      letterSpacing:1,color:C.text2,marginBottom:14}}>{title}</div>
    {children}
  </div>
);

// ─── Call graph SVG ──────────────────────────────────────────────────────────
function CallGraph({chaos,graph}) {
  return (
    <svg viewBox="0 0 280 230" style={{width:"100%",height:220}}>
      {EDGES.map(([s,t],i)=>{
        const sp=NODE_POS[s], tp=NODE_POS[t];
        const hot = chaos&&(s==="checkout"||t==="checkout");
        return <line key={i} x1={sp[0]} y1={sp[1]} x2={tp[0]} y2={tp[1]}
          stroke={hot?"#FCA5A5":"#CBD5E1"} strokeWidth={hot?2:1.5} strokeDasharray={hot?"5 3":"none"}/>;
      })}
      {SERVICES.map(svc=>{
        const p=NODE_POS[svc]; if(!p) return null;
        const hot=chaos&&svc==="checkout";
        const isFrontend=svc==="frontend";
        const n=graph.nodes?.find(nd=>nd.id===svc)||{rps:Math.round(30+Math.random()*100)};
        return (
          <g key={svc}>
            <circle cx={p[0]} cy={p[1]} r={18}
              fill={hot?"#FEF2F2":isFrontend?"#EFF6FF":"#F8FAFC"}
              stroke={hot?C.red:isFrontend?C.blue:C.slate}
              strokeWidth={isFrontend?2:1.5}/>
            <text x={p[0]} y={p[1]-3} textAnchor="middle" dominantBaseline="central"
              fill={hot?C.red:isFrontend?C.blue:C.slate}
              fontSize={8} fontFamily="Inter,sans-serif" fontWeight={600}>
              {svc.slice(0,5)}
            </text>
            <text x={p[0]} y={p[1]+9} textAnchor="middle"
              fill={C.text3} fontSize={7} fontFamily="Inter,sans-serif">
              {n.rps||Math.round(30+Math.random()*80)}rps
            </text>
          </g>
        );
      })}
    </svg>
  );
}

// ─── Main App ─────────────────────────────────────────────────────────────────
export default function App() {
  const [chaos,   setChaos]   = useState(false);
  const [phantom, setPhantom] = useState(true);
  const [mode,    setMode]    = useState("PHANTOM");
  const [rpsHist, setRpsHist] = useState(genHistory);
  const [confHist,setConfHist]= useState(genConf);
  const [replHist,setReplHist]= useState(genRepl);
  const [services,setServices]= useState(()=>genServices(false));
  const [events,  setEvents]  = useState([
    {t:"09:41:02", type:"scale", msg:"Pre-scaled checkout 3→7 (conf 0.91)"},
    {t:"09:40:31", type:"info",  msg:"Graph rebuilt: 6 nodes, 5 edges"},
    {t:"09:39:58", type:"model", msg:"MAPE improved 18.2% → 10.7%"},
    {t:"09:39:12", type:"scale", msg:"Pre-scaled frontend 4→8 (conf 0.88)"},
  ]);
  const [graph,   setGraph]   = useState({nodes:[],edges:[]});
  const [scaleCount,setScaleCount] = useState(0);
  const [clock,   setClock]   = useState("");

  useEffect(()=>{
    const id = setInterval(()=>{
      const t = new Date().toLocaleTimeString("en",{hour12:false});
      setClock(t);
      const spk = chaos ? 1.6 : 1;
      const t0 = Date.now();
      setRpsHist(h=>[...h.slice(-29),{
        t, predFrontend:Math.round(120+Math.sin(t0/5000)*30*spk),
        actFrontend:  Math.round(118+Math.sin(t0/5000)*30*spk+Math.random()*10-5),
        predCheckout: Math.round(60+Math.cos(t0/6000)*18*spk),
        actCheckout:  Math.round(58+Math.cos(t0/6000)*18*spk+Math.random()*8-4+(chaos?25:0)),
      }]);
      setConfHist(h=>[...h.slice(-29),{
        t, v:parseFloat((Math.max(.45,Math.min(.97,.80+Math.sin(t0/7000)*.12+(chaos?-.12:0)+Math.random()*.04))).toFixed(3))
      }]);
      setReplHist(h=>[...h.slice(-29),{
        t,
        phantom: Math.round(3+Math.sin(t0/8000)*2+(chaos?2:0)),
        hpa:     Math.round(3+Math.sin(t0/8000)*2+(chaos?2:0)+(Math.random()>.6?2:0)),
      }]);
      setServices(genServices(chaos));
      setGraph({
        nodes: SERVICES.map(s=>({id:s,rps:Math.round(30+Math.random()*120)})),
        edges: EDGES.map(([s,t])=>({source:s,target:t})),
      });
      if(Math.random()>.78){
        const pool = chaos
          ? [{type:"warn",msg:"⚠ Chaos: elevated latency on checkout"},
             {type:"scale",msg:`Pre-scaled checkout 3→${5+Math.floor(Math.random()*4)} (cascade detected)`}]
          : [{type:"scale",msg:`Pre-scaled ${SERVICES[Math.floor(Math.random()*SERVICES.length)]} 3→${4+Math.floor(Math.random()*5)} (conf ${(0.78+Math.random()*.15).toFixed(2)})`},
             {type:"info", msg:"Graph rebuilt: 6 nodes, 5 edges"},
             {type:"model",msg:`MAPE: ${(7+Math.random()*8).toFixed(1)}%`},
             {type:"info", msg:"Scale-down: cart 5→2 (cooldown cleared)"}];
        const ev = pool[Math.floor(Math.random()*pool.length)];
        setEvents(es=>[{t:new Date().toLocaleTimeString("en",{hour12:false}),...ev},...es.slice(0,9)]);
        if(ev.type==="scale") setScaleCount(c=>c+1);
      }
    }, 2000);
    return ()=>clearInterval(id);
  },[chaos]);

  const avgConf = (services.reduce((a,s)=>a+s.conf,0)/services.length*100).toFixed(1);
  const avgMAPE = (services.reduce((a,s)=>a+s.mape,0)/services.length).toFixed(1);
  const avgP99  = Math.round(services.reduce((a,s)=>a+s.p99,0)/services.length);
  const podsSaved = Math.max(0, Math.round(3 + Math.sin(Date.now()/12000)*2));

  const TICK = {style:{fontSize:10,fill:C.text3}};
  const GRID = {stroke:C.border,strokeDasharray:"3 3"};
  const TT   = {contentStyle:{background:C.surface,border:`1px solid ${C.border}`,
                               borderRadius:8,fontSize:11,boxShadow:"0 4px 12px rgba(0,0,0,.08)"},
                 labelStyle:{color:C.text,fontWeight:600}};

  return (
    <div style={{background:C.bg,minHeight:"100vh",fontFamily:"Inter,-apple-system,sans-serif",color:C.text}}>

      {/* ── Top bar ── */}
      <div style={{
        background:C.surface, borderBottom:`1px solid ${C.border}`,
        padding:"0 28px", display:"flex", alignItems:"center",
        justifyContent:"space-between", height:60,
        boxShadow:"0 1px 3px rgba(0,0,0,.06)"
      }}>
        <div style={{display:"flex",alignItems:"center",gap:12}}>
          <div style={{
            width:32,height:32,background:`linear-gradient(135deg,${C.blue},${C.indigo})`,
            borderRadius:8,display:"flex",alignItems:"center",justifyContent:"center",
            color:"white",fontWeight:700,fontSize:14
          }}>P</div>
          <div>
            <div style={{fontWeight:700,fontSize:16,letterSpacing:-.3}}>PHANTOM</div>
            <div style={{fontSize:10,color:C.text2,letterSpacing:.5}}>PREDICTIVE AUTOSCALER · GNN+LSTM</div>
          </div>
        </div>
        <div style={{display:"flex",alignItems:"center",gap:10}}>
          <span style={{fontSize:12,color:C.text2,marginRight:4}}>{clock}</span>

          <button onClick={()=>setPhantom(p=>!p)} style={{
            padding:"6px 14px", borderRadius:8, fontSize:12, fontWeight:600,
            cursor:"pointer", border:`1.5px solid ${phantom?C.blue:C.border}`,
            background:phantom?"#EFF6FF":C.surface, color:phantom?C.blue:C.text2,
            transition:"all .15s"
          }}>{phantom?"◉ PHANTOM ON":"○ PHANTOM OFF"}</button>

          <button onClick={()=>{
            setChaos(c=>!c);
            setEvents(es=>[{
              t:new Date().toLocaleTimeString("en",{hour12:false}),
              type:chaos?"info":"warn",
              msg:chaos?"Chaos cleared — system recovering":"⚠ Chaos injected: pod kill on checkout"
            },...es.slice(0,9)]);
          }} style={{
            padding:"6px 14px", borderRadius:8, fontSize:12, fontWeight:600,
            cursor:"pointer", border:`1.5px solid ${chaos?C.red:C.border}`,
            background:chaos?"#FEF2F2":C.surface, color:chaos?C.red:C.text2,
            transition:"all .15s"
          }}>{chaos?"✕ CHAOS ACTIVE":"⚡ Inject Chaos"}</button>

          <button onClick={()=>setMode(m=>m==="PHANTOM"?"HPA":m==="HPA"?"KEDA":"PHANTOM")} style={{
            padding:"6px 14px", borderRadius:8, fontSize:12, fontWeight:600,
            cursor:"pointer", border:`1.5px solid ${C.border}`,
            background:C.surface, color:C.amber, transition:"all .15s"
          }}>A/B: {mode}</button>
        </div>
      </div>

      <div style={{padding:"24px 28px", maxWidth:1440, margin:"0 auto"}}>

        {/* ── KPI row ── */}
        <div style={{display:"grid",gridTemplateColumns:"repeat(5,1fr)",gap:14,marginBottom:20}}>
          <Stat label="Avg Confidence" value={`${avgConf}%`}
            color={parseFloat(avgConf)>=80?C.green:C.amber}
            sub="ensemble agreement" icon="◎"/>
          <Stat label="Prediction MAPE" value={`${avgMAPE}%`}
            color={parseFloat(avgMAPE)<15?C.green:C.amber}
            sub="lower is better" icon="◈"/>
          <Stat label="P99 Latency" value={`${avgP99}ms`}
            color={avgP99<200?C.green:avgP99<350?C.amber:C.red}
            sub="SLO target: 200ms" icon="⏱"/>
          <Stat label="Pods Saved" value={podsSaved}
            color={C.blue} sub="vs reactive HPA" icon="⬇"/>
          <Stat label="Scale Events" value={scaleCount}
            color={C.indigo} sub="last 60 min" icon="↕"/>
        </div>

        {/* ── Row 1: RPS chart + Call graph ── */}
        <div style={{display:"grid",gridTemplateColumns:"2fr 1fr",gap:14,marginBottom:14}}>
          <Panel title="Predicted vs Actual RPS — frontend & checkout" color={C.blue}>
            <ResponsiveContainer width="100%" height={200}>
              <LineChart data={rpsHist}>
                <CartesianGrid {...GRID}/>
                <XAxis dataKey="t" {...TICK} interval={4}/>
                <YAxis {...TICK}/>
                <Tooltip {...TT}/>
                <Legend wrapperStyle={{fontSize:11}}/>
                <Line dataKey="predFrontend" stroke={C.blue}    strokeWidth={2} strokeDasharray="6 3" dot={false} name="Pred: frontend"/>
                <Line dataKey="actFrontend"  stroke="#93C5FD"   strokeWidth={1.5} dot={false} name="Actual: frontend"/>
                <Line dataKey="predCheckout" stroke={C.indigo}  strokeWidth={2} strokeDasharray="6 3" dot={false} name="Pred: checkout"/>
                <Line dataKey="actCheckout"  stroke="#A5B4FC"   strokeWidth={1.5} dot={false} name="Actual: checkout"/>
              </LineChart>
            </ResponsiveContainer>
          </Panel>

          <Panel title="Live Service Call Graph" color={C.sky}>
            {chaos && (
              <div style={{
                background:"#FEF2F2",border:`1px solid #FCA5A5`,borderRadius:8,
                padding:"6px 12px",fontSize:11,color:C.red,fontWeight:500,
                marginBottom:10
              }}>⚠ Chaos active — pod kill injected on checkout</div>
            )}
            <CallGraph chaos={chaos} graph={graph}/>
            <div style={{fontSize:10,color:C.text3,marginTop:4}}>
              {graph.nodes.length||6} nodes · {graph.edges.length||5} edges · rebuilt every 60s
            </div>
          </Panel>
        </div>

        {/* ── Row 2: Replicas + Confidence + Events ── */}
        <div style={{display:"grid",gridTemplateColumns:"1fr 1fr 1fr",gap:14,marginBottom:14}}>
          <Panel title="Replica Count — PHANTOM vs HPA" color={C.indigo}>
            <ResponsiveContainer width="100%" height={160}>
              <AreaChart data={replHist}>
                <CartesianGrid {...GRID}/>
                <XAxis dataKey="t" {...TICK} interval={4}/>
                <YAxis {...TICK} allowDecimals={false}/>
                <Tooltip {...TT}/>
                <Legend wrapperStyle={{fontSize:10}}/>
                <Area dataKey="hpa"     stroke="#CBD5E1" fill="#F1F5F9" strokeWidth={1.5} name="HPA (reactive)" dot={false}/>
                <Area dataKey="phantom" stroke={C.blue}  fill="#DBEAFE" strokeWidth={2}   name="PHANTOM (predictive)" dot={false} strokeDasharray="5 2"/>
              </AreaChart>
            </ResponsiveContainer>
          </Panel>

          <Panel title="Model Confidence" color={C.green}>
            <ResponsiveContainer width="100%" height={160}>
              <AreaChart data={confHist}>
                <CartesianGrid {...GRID}/>
                <XAxis dataKey="t" {...TICK} interval={4}/>
                <YAxis {...TICK} domain={[0,1]}/>
                <Tooltip {...TT}/>
                <ReferenceLine y={0.75} stroke={C.amber} strokeDasharray="4 3"
                  label={{value:"threshold",position:"insideTopRight",fontSize:9,fill:C.amber}}/>
                <Area dataKey="v" stroke={C.green} fill="#DCFCE7" strokeWidth={2} name="Confidence" dot={false}/>
              </AreaChart>
            </ResponsiveContainer>
          </Panel>

          <Panel title="Controller Event Log" color={C.slate}>
            <div style={{maxHeight:160,overflowY:"auto"}}>
              {events.map((e,i)=>(
                <div key={i} style={{
                  display:"flex",gap:10,padding:"5px 0",
                  borderBottom:`1px solid ${C.border}`,fontSize:11
                }}>
                  <span style={{color:C.text3,minWidth:58,flexShrink:0}}>{e.t}</span>
                  <span style={{
                    color:e.type==="warn"?C.red:e.type==="scale"?C.blue:e.type==="model"?C.indigo:C.text
                  }}>{e.msg}</span>
                </div>
              ))}
            </div>
          </Panel>
        </div>

        {/* ── Row 3: Service table + Comparison ── */}
        <div style={{display:"grid",gridTemplateColumns:"3fr 2fr",gap:14}}>
          <Panel title="Per-Service Status" color={C.blue}>
            <table style={{width:"100%",borderCollapse:"collapse"}}>
              <thead>
                <tr>{["Service","Pred RPS","Act RPS","Replicas","P99 ms","Confidence","Phase"].map(h=>(
                  <th key={h} style={{
                    fontSize:10,fontWeight:600,color:C.text2,
                    padding:"0 8px 8px",textAlign:"left",
                    borderBottom:`2px solid ${C.border}`,
                    textTransform:"uppercase",letterSpacing:.5
                  }}>{h}</th>
                ))}</tr>
              </thead>
              <tbody>
                {services.map(s=>(
                  <tr key={s.name} style={{borderBottom:`1px solid ${C.border}`}}>
                    <td style={{padding:"7px 8px",fontWeight:600,color:C.blue}}>{s.name}</td>
                    <td style={{padding:"7px 8px",fontSize:12}}>{s.predRPS}</td>
                    <td style={{padding:"7px 8px",fontSize:12,
                      color:s.actRPS>s.predRPS*1.2?C.red:C.text}}>{s.actRPS}</td>
                    <td style={{padding:"7px 8px",fontSize:12,color:C.amber,fontWeight:600}}>{s.predRep}</td>
                    <td style={{padding:"7px 8px",fontSize:12,
                      color:s.p99>200?C.red:s.p99>150?C.amber:C.green,fontWeight:600}}>{s.p99}</td>
                    <td style={{padding:"7px 8px"}}><ConfBar v={s.conf}/></td>
                    <td style={{padding:"7px 8px"}}>{phaseBadge(s.phase)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>

          <Panel title="P99 Latency: PHANTOM vs Baselines (ms)" color={C.indigo}>
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={CMP_DATA} barCategoryGap="30%">
                <CartesianGrid {...GRID} vertical={false}/>
                <XAxis dataKey="name" {...TICK}/>
                <YAxis {...TICK}/>
                <Tooltip {...TT}/>
                <Legend wrapperStyle={{fontSize:11}}/>
                <ReferenceLine y={200} stroke={C.red} strokeDasharray="4 3"
                  label={{value:"SLO 200ms",position:"insideTopRight",fontSize:9,fill:C.red}}/>
                <Bar dataKey="PHANTOM" fill={C.blue}   radius={[4,4,0,0]} name="PHANTOM (ours)"/>
                <Bar dataKey="HPA"     fill="#CBD5E1"  radius={[4,4,0,0]} name="HPA baseline"/>
                <Bar dataKey="KEDA"    fill="#E0E7FF"  radius={[4,4,0,0]} name="KEDA baseline"/>
              </BarChart>
            </ResponsiveContainer>
          </Panel>
        </div>

        {/* ── Footer ── */}
        <div style={{
          textAlign:"center",marginTop:24,
          fontSize:11,color:C.text3,
          borderTop:`1px solid ${C.border}`,paddingTop:16
        }}>
          PHANTOM · Topology-Aware Predictive Autoscaler ·
          GraphSAGE + LSTM · 5-Model Ensemble ·
          <span style={{color:C.blue,fontWeight:600}}> open-source research</span>
        </div>
      </div>
    </div>
  );
}
