'use strict';
const $ = id => document.getElementById(id);
const labels = {paid:'支付成功订单实付金额',count:'支付成功订单数',net:'实付减这些订单全部已完成退款（含跨月）',shipped_orders:'已发货订单数',stock_qty:'指定日期库存余额'};
const dimensions = {none:'合计（不分组）',day:'按天',month:'按月',category:'按品类'};
const allowed = {paid:['none','day','month','category'],count:['none','day','month'],net:['none','day','month'],shipped_orders:['none','day','month'],stock_qty:['none','category']};
let session = null, busy = false;
function dayOffset(value, days) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || '')) throw new Error('请选择完整日期。');
  const date = new Date(value + 'T00:00:00Z');
  if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0,10)!==value) throw new Error('日期无效。');
  date.setUTCDate(date.getUTCDate()+days); return date.toISOString().slice(0,10);
}
function cnDate(value) { const [y,m,d]=value.split('-').map(Number);return `${y}年${m}月${d}日`; }
function notice(text) { $('notice').textContent=text;$('notice').hidden=!text; }
function setBusy(value) { busy=value;document.querySelectorAll('button').forEach(b=>b.disabled=value);$('connection-note').textContent=value?'正在处理，请稍候…':'确认前不会执行 SQL'; }
async function request(path, body) {
  const response=await fetch(path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
  let data;try{data=await response.json();}catch{data={};}
  if (!response.ok) { const error=new Error(response.status===409?'这张确认卡已失效，已刷新最新状态。':response.status===400?'当前操作不适用，请核对输入和会话状态。':'服务暂时不可用，请检查后端日志后重试。');error.status=response.status;throw error; }
  return data;
}
async function perform(action) {
  if (busy) return;setBusy(true);notice('');
  try { await action(); }
  catch(error) {
    if(error.status===409 && session) {try{session=await request('/sessions/'+encodeURIComponent(session.id));render();}catch{/* 保留原错误供用户处理。 */}}
    notice(error.message);
  } finally {setBusy(false);}
}
function updateDimensions() {
  const metric=$('metric').value, previous=$('dimension').value;
  $('dimension').replaceChildren(...allowed[metric].map(key=>new Option(dimensions[key],key)));
  $('dimension').value=allowed[metric].includes(previous)?previous:allowed[metric][0];
  if(metric==='stock_qty') {$('start').value='2026-10-31';$('end').value='2026-10-31';}
}
function buildQuestion() {
  const metric=$('metric').value, start=$('start').value, end=$('end').value, group=$('dimension').value;
  dayOffset(start,0);dayOffset(end,0);if(start>end) throw new Error('结束日期不能早于开始日期。');
  if(metric==='stock_qty' && (start!=='2026-10-31'||end!=='2026-10-31')) throw new Error('课程库存使用 2026-10-31 快照，请选择这一天。');
  const period=start===end?cnDate(start):`${cnDate(start)}至${cnDate(end)}`;
  const phrasing={paid:'支付成功订单实付金额',count:'成功付款订单去重数量',net:'支付订单净额，采用订单实付减这些订单全部已完成退款，含跨月退款且不限制退款时间',shipped_orders:'已发货订单数，按发货日期归属，已发货和已送达都计，同订单去重',stock_qty:'库存余额'};
  $('question').value=`${period}，${group==='none'?'汇总':dimensions[group]+'统计'}${phrasing[metric]}。`;
}
function entry(key,value) {const row=document.createElement('div'),dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=key;dd.textContent=value;row.append(dt,dd);return row;}
function render() {
  tracePanel.sync(session);
  $('empty-state').hidden=!!session;$('session-view').hidden=!session;$('result-panel').hidden=true;
  if(!session){$('stage-badge').textContent='等待提问';return;}
  localStorage.setItem('xiaozhe.session',session.id);
  const stages={confirm:'待确认',clarify:'待补充',done:'已完成',cancelled:'已取消',reject:'不支持',error:'处理失败'};
  $('stage-badge').textContent=stages[session.stage]||'处理中';$('reason').textContent=session.reason||'';
  const plan=session.decision?.plan, card=$('plan-card');card.replaceChildren();
  if(plan){
    let end=plan.end;if(end && plan.end_inclusive===false) end=dayOffset(end,-1);
    card.append(entry('指标口径',labels[plan.metric]||'待选择'),entry('时间范围',plan.start&&end?`${plan.start} 至 ${end}（含末日）`:'待补充'),entry('分组',dimensions[plan.dimension]||'待补充'),entry('单位',['paid','net'].includes(plan.metric)?'分':plan.metric==='stock_qty'?'件':'单'));
    if(plan.category)card.append(entry('品类',plan.category));
  }
  $('warnings').replaceChildren(...(session.warnings||[]).map(text=>{const li=document.createElement('li');li.textContent=text;return li;}));
  $('confirm-actions').hidden=!['confirm','clarify'].includes(session.stage);$('confirm').hidden=session.stage!=='confirm';$('clarify-form').hidden=session.stage!=='clarify';
  $('edit-panel').hidden=!['confirm','clarify'].includes(session.stage);
  const missing=session.decision?.missing_fields||[];
  $('choice-metric-label').hidden=!missing.includes('metric');$('choice-dates').hidden=!missing.includes('date');
  $('choice-start').required=missing.includes('date');$('choice-end').required=missing.includes('date');
  if(session.stage==='clarify') {
    $('choice-metric').value=plan?.metric||'paid';
    $('choice-start').value=plan?.start||$('start').value;
    $('choice-end').value=plan?.end?(plan.end_inclusive===false?dayOffset(plan.end,-1):plan.end):$('end').value;
    if(missing.some(key=>!['metric','date'].includes(key))) $('edit-panel').open=true;
  }
  $('session-meta').textContent=`会话 ${session.id.slice(0,8)} · 确认卡版本 ${session.revision}`;
  if(session.stage==='done') renderResults();
}
function renderResults() {
  $('result-panel').hidden=false;
  const plan=session.decision.plan, rows=session.rows||[], grouped=plan.dimension!=='none';
  $('result-unit').textContent=['paid','net'].includes(plan.metric)?'金额单位：分':plan.metric==='stock_qty'?'数量单位：件':'数量单位：单';
  const host=$('results');host.replaceChildren();
  if(!rows.length){const p=document.createElement('p');p.textContent='这个时间范围内没有记录。';host.append(p);return;}
  const table=document.createElement('table'),head=document.createElement('thead'),tr=document.createElement('tr');
  for(const text of grouped?[dimensions[plan.dimension],'数值']:['数值']){const th=document.createElement('th');th.scope='col';th.textContent=text;tr.append(th);}head.append(tr);table.append(head);
  const body=document.createElement('tbody');
  for(const row of rows){const tr=document.createElement('tr');for(const value of row){const td=document.createElement('td');td.textContent=value===null?'—':String(value);tr.append(td);}body.append(tr);}table.append(body);host.append(table);
}
async function respond(payload) {const snapshot=session;if(!snapshot)throw new Error('请先提问。');session=await request('/sessions/'+encodeURIComponent(snapshot.id)+'/respond',{revision:snapshot.revision,...payload});render();}
$('metric').addEventListener('change',updateDimensions);
$('build-question').addEventListener('click',()=>{try{buildQuestion();notice('');}catch(e){notice(e.message);}});
$('question-form').addEventListener('submit',event=>{event.preventDefault();perform(async()=>{const question=$('question').value.trim();if(!question)throw new Error('请填写问题。');session=await request('/sessions',{question});render();});});
$('confirm').addEventListener('click',()=>perform(()=>respond({action:'confirm'})));
$('cancel').addEventListener('click',()=>perform(()=>respond({action:'cancel'})));
$('reply-form').addEventListener('submit',event=>{event.preventDefault();perform(async()=>{const text=$('reply').value.trim();if(!text)throw new Error('请填写补充内容。');await respond({action:'reply',text});$('reply').value='';});});
$('clarify-form').addEventListener('submit',event=>{event.preventDefault();perform(async()=>{
  const missing=session.decision.missing_fields,choice={};
  if(missing.includes('metric')){if($('choice-metric').value==='other'){$('edit-panel').open=true;$('reply').focus();throw new Error('请在“重新描述”中填写你的口径。');}choice.metric=$('choice-metric').value;}
  if(missing.includes('date')){const start=$('choice-start').value,end=$('choice-end').value;dayOffset(start,0);if(start>end)throw new Error('结束日期不能早于开始日期。');choice.start=start;choice.end=dayOffset(end,1);}
  if(!Object.keys(choice).length)throw new Error('请在“重新描述”中补充所需条件。');await respond({action:'choose',choice});
});});
$('new-session').addEventListener('click',()=>{session=null;localStorage.removeItem('xiaozhe.session');notice('');render();});
const examples={paid:['paid','day','2026-09-01','2026-09-30'],count:['count','month','2026-07-01','2026-09-30'],stock_qty:['stock_qty','category','2026-10-31','2026-10-31']};
document.querySelectorAll('[data-example]').forEach(button=>button.addEventListener('click',()=>{const[m,d,s,e]=examples[button.dataset.example];$('metric').value=m;updateDimensions();$('dimension').value=d;$('start').value=s;$('end').value=e;buildQuestion();$('question').focus();}));
$('download').addEventListener('click',()=>{
  const escape=value=>'"'+String(value??'').replace(/^[=+@-]/,"'$&").replaceAll('"','""')+'"';
  const headings=session.decision.plan.dimension==='none'?['数值']:['分组','数值'];
  const csv='\ufeff'+[headings,...session.rows].map(row=>row.map(escape).join(',')).join('\r\n');
  const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'})),link=document.createElement('a');link.href=url;link.download='小哲问数结果.csv';link.click();URL.revokeObjectURL(url);
});
(async()=>{updateDimensions();$('dimension').value='day';
  try{const choices=await request('/choices');$('choice-metric').replaceChildren(...Object.entries(choices).map(([key,value])=>new Option(value,key)));const id=localStorage.getItem('xiaozhe.session');if(id){session=await request('/sessions/'+encodeURIComponent(id));render();}}
  catch{notice('暂时无法恢复会话。请确认后端已启动，或点击“新建会话”重新开始。');}
})();
