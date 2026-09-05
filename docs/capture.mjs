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
const hist=(openai)=>Object.fromEntries(['dia','semana','mes','geral'].map((k,i)=>[k,{total_tokens:(openai?24:52)*1e6*(i+1),total_cost:150*(i+1),equivalent_usd:36*(i+1),unpriced_tokens:0,calls:150*(i+1),total_turns:150*(i+1),first_event:iso(-240),by_model:[]} ]));
const limit={id:'codex',name:'Codex',snapshot_ts:iso(),primary:{used_percent:23,window_minutes:10080,resets_at:iso(120)}};
const state={generated_at:iso(),snapshot_ts:iso(),auth_connected:true,windows:{five_hour:{utilization:42,resets_at:iso(3),rate:5,projected:57},seven_day:{utilization:31,resets_at:iso(98),rate:.3,projected:60}},config:{usd_brl:5,subscription_brl:550,chatgpt_subscription_brl:550,chatgpt_extra_brl:0,intended_hours:2},history:hist(false),burn_tokph:{'claude-opus-4-6':14e6,'claude-sonnet-4-6':7e6},extra_usage:{used:0,burning:false},chatgpt:{limits:[limit],history:hist(true),burn_tokph:12e6,burn_by_model:{'gpt-5.4':12e6},daily:daily(6e5),heatmap:heatmap(5e5),limit_history:Array.from({length:12},(_,i)=>({ts:iso(i-11),limits:[{...limit,primary:{...limit.primary,used_percent:12+i}}]}))}};
const history={daily:daily(1e6),heatmap:heatmap(7e5),snapshots:Array.from({length:12},(_,i)=>({ts:iso(i-11),window:'seven_day',utilization:20+i}))};
const server=createServer(async(req,res)=>{try{if(req.url.startsWith('/api/')){res.setHeader('Content-Type','application/json');res.end(JSON.stringify(req.url==='/api/state'?state:req.url==='/api/history'?history:{total_tokens:304e6}));return;}const url=req.url==='/'?'index.html':req.url.slice(1);res.setHeader('Content-Type',url.endsWith('.js')?'text/javascript':url.endsWith('.css')?'text/css':'text/html');res.end(await readFile(new URL('../web/dist/'+url,import.meta.url)));}catch{res.writeHead(404);res.end();}});
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
const views=[['CLAUDE 5H','42%','reset 3h 0m',42],['CLAUDE 7D','31%','reset 4d 2h',31],['CODEX 7D','23%','reset 5d 0h',23],['FONTE','LICENÇA','Claude'],['CLAUDE /H','21M','tokens/h · 2h'],['CODEX /H','12M','tokens/h · 2h'],['CLAUDE ROI','0.8x','API / gasto'],['CODEX ROI','1.0x','API / gasto']].map(([label,value,sub,pct])=>({label,value,sub,pct,level:'safe',accent:label.includes('CODEX')?'#45d6aa':'#ef985d'}));
await page.setViewportSize({width:880,height:540});
await page.setContent(`<body style="margin:0;background:#080b10;padding:24px;font-family:Arial;color:white"><div style="display:grid;grid-template-columns:repeat(4,144px);justify-content:space-between;gap:20px">${views.map(v=>R.clean(v)).join('')}</div><div style="display:flex;justify-content:space-between;margin-top:24px">${[views[0],views[5],{...views[0],label:'CLAUDE DIA',value:'R$ 150',sub:'API estimada',pct:undefined},views[7]].map(v=>R.clean(v,true)).join('')}</div><p style="color:#acb8c8;font-size:13px">Consumo de IA · 8 teclas + 4 dials · dados demonstrativos</p>`);
await page.screenshot({path:new URL('streamdeck.png',import.meta.url).pathname});
if(errors.length)throw new Error(errors.join('\n'));
console.log('Three screenshots generated; no browser errors.');
}finally{await browser.close();server.close();}
