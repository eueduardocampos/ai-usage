// Run after npm --prefix web install and npm --prefix web run build.
// Screenshots use synthetic data only; no account or local usage is read.
import {createRequire} from 'node:module';
import {readFile,writeFile} from 'node:fs/promises';
import {createServer} from 'node:http';
const require=createRequire(new URL('../web/package.json',import.meta.url));
const {chromium}=require('playwright');
const R=require('../streamdeck/digital.astronauta.claudeusage.sdPlugin/bin/render.js');
const now=Date.now(),iso=(h=0)=>new Date(now+h*3600000).toISOString();
const heatmap=(scale)=>Array.from({length:7*24},(_,i)=>({dow:Math.floor(i/24),hour:i%24,avg_tokens:(i%24>=8&&i%24<=20)?(1+(i*7%13))*scale:0,avg_cost:2,models:[]}));
const daily=(scale)=>Array.from({length:30},(_,i)=>({day:new Date(now-(29-i)*86400000).toISOString().slice(0,10),tokens:(10+i*7%21)*scale}));
// total_cost e' USD; total_cost_brl vem convertido do backend (a UI nao
// multiplica por cambio por conta propria).
// Escopos dia < semana < mes < geral. O mês fecha em ~R$ 4.100 de API
// equivalente no Claude e ~R$ 1.900 no Codex contra R$ 1.100 de assinatura:
// a conta que o painel existe para mostrar.
const hist=(openai)=>Object.fromEntries(['dia','semana','mes','geral'].map((k,i)=>[k,{
  total_tokens:(openai?38:96)*1e6*(i+1),
  total_cost:(openai?0:[62,205,820,2140][i]),
  total_cost_brl:(openai?0:[310,1025,4100,10700][i]),
  equivalent_usd:(openai?[28,95,380,910][i]:0),
  unpriced_tokens:0,calls:(openai?90:260)*(i+1),total_turns:(openai?90:260)*(i+1),
  first_event:iso(-240),by_model:[]}]));
const limit={id:'codex',name:'Codex',snapshot_ts:iso(),primary:{used_percent:23,window_minutes:10080,resets_at:iso(120)}};
const spark={id:'codex_bengalfox',name:'GPT-5.3-Codex-Spark',snapshot_ts:iso(),primary:{used_percent:8,window_minutes:300,resets_at:iso(4)},secondary:{used_percent:14,window_minutes:10080,resets_at:iso(120)}};
// quotas[]: contrato unico Claude + Codex (mesma forma, projecao e semaforo
// prontos). Inclui o teto semanal por modelo, que so existe nesse formato.
const quota=(provider,key,label,util,proj,resets,hours,product=null)=>{const htr=hours===5?3:98;return{provider,key:`${provider}:${key}`,source_key:key,label,product,window_hours:hours,utilization:util,resets_at:resets,hours_to_reset:htr,projected:proj,projected_linear:proj,status:proj>=100?'RISCO':proj>=80?'ATENCAO':'SEGURO',rate:(proj-util)/htr,eta_100:null,smart:true,snapshot_ts:iso()};};
// Números de demonstração (nenhum dado de conta real entra aqui): uma
// semana de uso intenso, com a sessão apertando e a semana em ritmo
// saudável, que é o caso em que o painel tem o que dizer.
const quotas=[
  quota('claude','five_hour','Sessão · 5 horas',68,86,iso(3),5),
  quota('claude','seven_day','Semana · 7 dias',44,58,iso(98),168),
  quota('claude','seven_day_model:fable','Fable · 7 dias',17,23,iso(98),168,'Fable'),
  quota('codex','codex:primary','Codex · 7 dias',31,39,iso(120),168,'Codex'),
  quota('codex','codex_bengalfox:primary','Spark · 5 horas',12,16,iso(4),5,'GPT-5.3-Codex-Spark'),
  quota('codex','codex_bengalfox:secondary','Spark · 7 dias',22,27,iso(120),168,'GPT-5.3-Codex-Spark'),
];
const state={generated_at:iso(),snapshot_ts:iso(),auth_connected:true,rate_limited:false,last_error:null,
  quotas,
  windows:{five_hour:{utilization:42,resets_at:iso(3),rate:5,projected:57,label:'Sessão (5h)',window:'five_hour'},seven_day:{utilization:31,resets_at:iso(98),rate:.3,projected:60,label:'Semana (7d)',window:'seven_day'}},
  switch:{verdict:'ATENCAO',message:'No ritmo atual você chega a 86% no reset. Vale reservar o modelo caro para o que importa.',message_id:8,tightest_window:'five_hour',factor:1.67,factor_estimated:false,dominant_model:'claude-sonnet-5',target:'opus',target_label:'Opus',intended_hours:2,windows:{}},
  update:{current:'4.2.0',latest:'4.2.0',url:'https://github.com/astronauta-martech/ai-usage/releases/latest',available:false},
  config:{usd_brl:5,subscription_brl:550,chatgpt_subscription_brl:550,chatgpt_extra_brl:0,intended_hours:2},
  history:hist(false),burn_tokph:{'claude-opus-5':18e6,'claude-sonnet-5':9e6,'claude-fable-5-1':4e6},
  extra_usage:{used:0,limit:20,used_brl:0,limit_brl:100,currency:'USD',burning:false},
  chatgpt:{available:true,limits:[limit,spark],history:hist(true),burn_tokph:12e6,burn_by_model:{'gpt-5.5':12e6},daily:daily(6e5),heatmap:heatmap(5e5),hour_of_day:[],limits_error:null,
    limit_history:Array.from({length:12},(_,i)=>({ts:iso(i-11),limits:[{...limit,primary:{...limit.primary,used_percent:12+i}}]}))}};
const history={daily:daily(1e6),heatmap:heatmap(7e5),snapshots:Array.from({length:12},(_,i)=>({ts:iso(i-11),window:'seven_day',utilization:20+i}))};
const server=createServer(async(req,res)=>{try{if(req.url.startsWith('/api/')){res.setHeader('Content-Type','application/json');res.end(JSON.stringify(req.url==='/api/state'?state:req.url==='/api/history'?history:{total_tokens:304e6}));return;}const pathname=req.url.split('?')[0];const url=['/','/widget'].includes(pathname)?'index.html':pathname.slice(1);res.setHeader('Content-Type',url.endsWith('.js')?'text/javascript':url.endsWith('.css')?'text/css':url.endsWith('.svg')?'image/svg+xml':'text/html');res.end(await readFile(new URL('../web/dist/'+url,import.meta.url)));}catch{res.writeHead(404);res.end();}});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const browser=await chromium.launch({headless:true,...(process.env.CHROME_CHANNEL?{channel:process.env.CHROME_CHANNEL}:{})});
try{
const page=await browser.newPage({viewport:{width:1440,height:1080},deviceScaleFactor:1});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.goto(`http://127.0.0.1:${server.address().port}`);await page.getByText('Janelas em uso',{exact:true}).waitFor();await page.waitForTimeout(800);
const comparison=await page.getByText('Comparativo de consumo',{exact:true}).locator('xpath=ancestor::section').boundingBox();
const evolution=await page.getByText('Evolução das cotas',{exact:true}).locator('xpath=ancestor::section').boundingBox();
await page.screenshot({path:new URL('painel.png',import.meta.url).pathname,fullPage:true,clip:{x:0,y:0,width:1440,height:comparison.y-16}});
await page.screenshot({path:new URL('comparativo.png',import.meta.url).pathname,fullPage:true,clip:{x:0,y:comparison.y-16,width:1440,height:evolution.y-comparison.y}});
await page.setViewportSize({width:640,height:350});
await page.addInitScript(()=>{window.__opened=[];window.__TAURI__={core:{invoke:async command=>{window.__opened.push(command);}}};});
await page.goto(`http://127.0.0.1:${server.address().port}/widget?native=1`);
await page.getByRole('button',{name:'Claude',exact:false}).first().waitFor();
await page.waitForTimeout(300);
await page.screenshot({path:new URL('widget-mac.png',import.meta.url).pathname});
for(const selector of ['.aw-brand','.aw-account:first-child','.aw-account:nth-child(2)','.aw-footer button']) await page.locator(selector).click();
const opened=await page.evaluate(()=>window.__opened);
if(opened.length!==4||opened.some(c=>c!=='open_dashboard')) throw new Error('Widget links did not invoke system browser');
if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth||document.documentElement.scrollHeight>innerHeight)) throw new Error('Widget overflow');
await page.setViewportSize({width:440,height:350});
await page.screenshot({path:new URL('widget-compact.png',import.meta.url).pathname});
if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth||document.documentElement.scrollHeight>innerHeight)) throw new Error('Compact widget overflow');
const views=[['CLAUDE 5H','42%','reset 3h 0m',42],['CLAUDE 7D','31%','reset 4d 2h',31],['CODEX 7D','23%','reset 5d 0h',23],['FONTE','LICENÇA','Claude'],['CLAUDE /H','21M','tokens/h · 2h'],['CODEX /H','12M','tokens/h · 2h'],['CLAUDE ROI','0.8x','API / gasto'],['CODEX ROI','1.0x','API / gasto']].map(([label,value,sub,pct])=>({label,value,sub,pct,level:'safe',accent:label.includes('CODEX')?'#45d6aa':'#ef985d'}));
await page.setViewportSize({width:880,height:540});
await page.setContent(`<body style="margin:0;background:#080b10;padding:24px;font-family:Arial;color:white"><div style="display:grid;grid-template-columns:repeat(4,144px);justify-content:space-between;gap:20px">${views.map(v=>R.clean(v)).join('')}</div><div style="display:flex;justify-content:space-between;margin-top:24px">${[views[0],views[5],{...views[0],label:'CLAUDE DIA',value:'R$ 150',sub:'API estimada',pct:undefined},views[7]].map(v=>R.clean(v,true)).join('')}</div><p style="color:#acb8c8;font-size:13px">Consumo de IA · 8 teclas + 4 dials · dados demonstrativos</p>`);
await page.screenshot({path:new URL('streamdeck.png',import.meta.url).pathname});
if(errors.length)throw new Error(errors.join('\n'));
console.log('Screenshots generated; widget browser links and responsive layout passed; no browser errors.');
}finally{await browser.close();server.close();}
