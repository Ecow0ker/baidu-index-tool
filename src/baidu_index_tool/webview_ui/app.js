const $=id=>document.getElementById(id);
let indexMode='search', regions=[], lastRunDir='', running=false, paused=false, completedRows=0, demoMode=false, resumeKey='';
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const iso=date=>{const copy=new Date(date.getTime()-date.getTimezoneOffset()*60000);return copy.toISOString().slice(0,10)};
const clock=()=>new Date().toLocaleTimeString('zh-CN',{hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'});
const yesterday=new Date();yesterday.setDate(yesterday.getDate()-1);const monthAgo=new Date(yesterday);monthAgo.setMonth(monthAgo.getMonth()-1);
$('startDate').value=iso(monthAgo);$('endDate').value=iso(yesterday);

function api(){return window.pywebview.api}
function keywords(){return $('keywords').value.split(/\r?\n/).map(x=>x.trim()).filter((x,i,a)=>x&&a.indexOf(x)===i)}
function selectedIds(){return [...document.querySelectorAll('.regionCheck:checked')].map(x=>Number(x.value))}
function selectedChannels(group){return [...document.querySelectorAll(`#${group} input:checked`)].map(x=>x.value)}
function requestCount(){const tasks=Math.max(1,keywords().length)*Math.max(1,selectedIds().length);const days=Math.max(1,Math.round((new Date($('endDate').value+'T00:00:00Z')-new Date($('startDate').value+'T00:00:00Z'))/86400000)+1)||1;return indexMode==='brand'?Math.max(1,keywords().length)+tasks*selectedChannels('brandChannels').length:2*Math.ceil(days/365)*tasks*(1+(selectedChannels('searchChannels').includes('news')?1:0))}
function queryConfig(){return {keywords:keywords(),regionIds:selectedIds(),startDate:$('startDate').value,endDate:$('endDate').value,period:$('period').value,interval:Number($('interval').value||0),outputDir:$('outputDir').value,indexMode,searchChannels:selectedChannels('searchChannels'),brandChannels:selectedChannels('brandChannels')}}
function queryKey(config){const {interval,resume,...fields}=config;return JSON.stringify(fields)}
function updateSummary(){
  const tasks=keywords().length*selectedIds().length;
  $('taskTotal').textContent=String(tasks);
  const seconds=Math.max(0,Number($('interval').value||0))*requestCount();
  $('estimatedTime').textContent=seconds<60?`${seconds} 秒`:`约 ${Math.ceil(seconds/60)} 分钟`;
  if(!running)$('startBtn').textContent=resumeKey&&resumeKey===queryKey(queryConfig())?'继续查询':'开始查询';
}
function updateCounts(){
  $('keywordCount').textContent=`${keywords().length} 个`;
  const ids=selectedIds();$('regionCount').textContent=`${ids.length} 个`;
  const names=ids.map(id=>(regions.find(r=>Number(r.area_id)===id)||{}).display_name||id);
  $('selectedRegions').textContent=names.join('、')||'尚未选择地区';
  $('regionSummary').textContent=ids.length?`${names.slice(0,2).join('、')}${names.length>2?'…':''} · ${ids.length} 个地区`:'尚未选择地区';
  updateSummary();
}
function renderRegions(items){
  regions=items;
  $('regions').innerHTML=items.map((r,i)=>`<label class="regionRow ${r.kind==='city'?'city':''}" data-search="${esc(`${r.name} ${r.display_name} ${r.area_id}`.toLowerCase())}"><input class="regionCheck" type="checkbox" value="${r.area_id}" ${i===0?'checked':''}> <span>${esc(r.display_name)} (${r.area_id})</span></label>`).join('');
  document.querySelectorAll('.regionCheck').forEach(x=>x.onchange=updateCounts);updateCounts();
}
function switchMode(mode){
  indexMode=mode;$('searchTab').classList.toggle('active',mode==='search');$('brandTab').classList.toggle('active',mode==='brand');
  $('searchChannels').classList.toggle('hidden',mode!=='search');$('brandChannels').classList.toggle('hidden',mode!=='brand');$('brandHelp').classList.toggle('hidden',mode!=='brand');renderHead();updateSummary();
}
function renderHead(){
  const labels=indexMode==='brand'?['关键词','日期','省份','城市','品牌指数','品牌搜索指数','品牌资讯指数','品牌互动指数']:['关键词','日期','省份','城市','PC+移动','PC端','移动端','资讯指数'];
  $('tableHead').innerHTML=labels.map(x=>`<th>${x}</th>`).join('');
}
function cell(value,numeric){const missing=value===null||value===undefined||value==='';return `<td${numeric?' class="number'+(missing?' missing':'')+'"':''}>${missing?'—':esc(value)}</td>`}
function rowHtml(row){
  const values=indexMode==='brand'?[row.keyword,row.date,row.province,row.city,row.brand_index,row.brand_search,row.brand_news,row.brand_interaction]:[row.keyword,row.date,row.province,row.city,row.overall,row.pc,row.mobile,row.news];
  return `<tr>${values.map((x,i)=>cell(x,i>=4)).join('')}</tr>`;
}
function setRows(rows,replace=true,totalRows=null){
  const html=rows.slice(-2000).map(rowHtml).join('');
  if(replace)$('tableBody').innerHTML=html||'<tr><td colspan="8" class="empty"><span class="emptyIcon">⌕</span><b>当前指标没有数据</b><small>继续检查下一项指标</small></td></tr>';else $('tableBody').insertAdjacentHTML('beforeend',html);
  while($('tableBody').children.length>2000)$('tableBody').firstElementChild.remove();
  completedRows=replace?(totalRows??rows.length):completedRows+(totalRows??rows.length);$('rowCount').textContent=`共 ${completedRows} 条${completedRows>2000?' · 显示最近 2000 条':''}`;
}
function addLog(title,detail='',kind='info'){
  const item=document.createElement('article');item.className=`logItem ${kind}`;
  item.innerHTML=`<i></i><time>${clock()}</time><b>${esc(title)}</b>${detail?`<p>${esc(detail)}</p>`:''}`;
  $('activityLog').prepend(item);while($('activityLog').children.length>80)$('activityLog').lastElementChild.remove();
}
function updateStatus(title,detail){$('statusTitle').textContent=title;$('status').textContent=detail||title}
function setRunning(value){running=value;document.querySelectorAll('.configPanel input, .configPanel textarea, .configPanel select, .configPanel button').forEach(control=>control.disabled=value);$('startBtn').disabled=value;$('stopBtn').disabled=!value;$('pauseBtn').disabled=!value;if(!value){paused=false;$('pauseBtn').textContent='Ⅱ 暂停'}}
function setAccounts(value){
  $('accounts').value=value.text||'';$('strategy').value=value.strategy||$('strategy').value;
  $('accountCount').textContent=`已保存 ${value.count||0} 个账号`;
  $('accountState').innerHTML=`<i></i>${value.count?`已保存账号 · ${value.count}`:'未保存账号'}`;$('accountState').classList.toggle('connected',Boolean(value.count));
}
function showQueryResult(result){
  const rowTotal=result.rowCount??result.rows.length;
  setRows(result.rows,true,rowTotal);lastRunDir=result.runDir||'';$('openExport').disabled=!lastRunDir;
  const done=result.completed||0,total=result.total||1,p=Math.round(done/total*100);
  const noData=result.noDataCount||0,withData=result.dataCount??(done-noData);
  const counts=`有数据 ${withData} · 无数据 ${noData}`;
  $('bar').style.width=`${p}%`;$('percent').textContent=`${p}%`;$('successTotal').textContent=String(withData);$('noDataTotal').textContent=String(noData);
  $('failureTotal').textContent=result.incomplete&&!result.cancelled?'1':'0';
  if(result.incomplete){
    const title=result.blocked?'请求被拦截':result.cancelled?'任务已停止':'查询中断';
    const saved=`已处理 ${done}/${total} · ${counts} · 已保留 ${rowTotal} 行`;
    updateStatus(title,result.message||saved);$('resultMeta').textContent=saved;
    addLog(title,result.message||'',result.cancelled?'warn':'error');addLog('当前结果已保留',saved,'info');
  }else{
    updateStatus('查询完成',`${counts} · ${rowTotal} 行`);$('resultMeta').textContent=`已处理 ${done}/${total} · ${counts} · ${rowTotal} 行`;addLog('查询完成',`${counts} · ${rowTotal} 行 · ${result.outputs.length} 个文件`,'success');
    if(noData)addLog('无数据任务已记录','查看导出目录中的查询进度.json；缺失不等于 0','warn');
  }
}
async function syncCookies(){
  const button=$('syncCookies');button.disabled=true;$('syncState').textContent='正在提取 Cookie…';
  try{const value=await api().sync_login_cookies();setAccounts({...value,strategy:value.strategy||$('strategy').value});$('syncState').textContent=value.detected?'✓ Cookie 已提取并保存':(value.message||'尚未检测到登录 Cookie');}
  catch(error){$('syncState').textContent=`提取失败：${error}`}
  finally{button.disabled=false}
}
async function startQuery(){
  const config=queryConfig(),key=queryKey(config);config.resume=Boolean(resumeKey&&resumeKey===key);
  if(!config.resume){resumeKey='';lastRunDir='';$('tableBody').innerHTML='';completedRows=0;$('rowCount').textContent='共 0 条';$('bar').style.width='0';$('percent').textContent='0%';$('successTotal').textContent='0';$('noDataTotal').textContent='0'}
  setRunning(true);$('openExport').disabled=true;$('failureTotal').textContent='0';$('countdownValue').textContent='—';updateStatus(config.resume?'正在继续任务':'正在创建任务','校验关键词、地区、日期与账号状态');addLog(config.resume?'继续未完成的任务':'任务已创建',`${config.keywords.length} 个关键词 · ${config.regionIds.length} 个地区`);
  try{
    const result=await api().query(config);
    resumeKey=result.canResume?key:'';showQueryResult(result);
  }catch(error){$('failureTotal').textContent='1';updateStatus('查询失败',String(error));addLog('查询失败',String(error),'error')}
  finally{setRunning(false);updateSummary();$('openExport').disabled=!lastRunDir;$('countdownBadge').classList.remove('active');$('countdownValue').textContent='—'}
}
window.__pythonEvent=(name,data)=>{
  if(name==='countdown'){
    $('countdownValue').textContent=data.seconds||'0';$('countdownBadge').classList.toggle('active',Boolean(data.seconds));
    updateStatus(data.seconds?`距下次请求 ${data.seconds} 秒`:'正在发送请求',data.label);
    if(data.seconds===Number($('interval').value||0)||data.seconds===0)addLog(data.seconds?`等待 ${data.seconds} 秒`:'开始发送请求',data.label);
  }
  if(name==='progress'){const p=Math.round(data.done/data.total*100);$('bar').style.width=`${p}%`;$('percent').textContent=`${p}%`;$('successTotal').textContent=String(data.dataCount??data.completedTasks??(indexMode==='search'?data.done:0));$('noDataTotal').textContent=String(data.noDataCount||0);$('resultMeta').textContent=`进行中 · ${data.done}/${data.total} 个请求`}
  if(name==='batch')setRows(data.rows,false,data.rowCount??null);
  if(name==='snapshot')setRows(data.rows,true,data.rowCount??null);
  if(name==='log'){updateStatus(data.text,data.detail||data.text);addLog(data.text,data.detail||'',data.kind||'info')}
};

$('keywords').oninput=updateCounts;$('interval').oninput=updateSummary;$('searchTab').onclick=()=>switchMode('search');$('brandTab').onclick=()=>switchMode('brand');
document.querySelectorAll('.checks input').forEach(x=>x.onchange=updateSummary);
$('regionSearch').oninput=event=>{const needle=event.target.value.trim().toLowerCase();document.querySelectorAll('.regionRow').forEach(row=>row.classList.toggle('hiddenRegion',Boolean(needle)&&!row.dataset.search.includes(needle)))};
$('selectProvinces').onclick=()=>{document.querySelectorAll('.regionCheck').forEach(box=>{const r=regions.find(x=>Number(x.area_id)===Number(box.value));box.checked=Boolean(r&&r.kind==='province')});updateCounts()};
$('selectCities').onclick=()=>{document.querySelectorAll('.regionCheck').forEach(box=>{const r=regions.find(x=>Number(x.area_id)===Number(box.value));box.checked=Boolean(r&&r.kind==='city')});updateCounts()};
$('clearRegions').onclick=()=>{document.querySelectorAll('.regionCheck').forEach(x=>x.checked=false);updateCounts()};
$('resetConfig').onclick=()=>{resumeKey='';$('keywords').value='华为';$('interval').value='4';switchMode('search');document.querySelectorAll('.regionCheck').forEach((x,i)=>x.checked=i===0);updateCounts();addLog('已恢复默认配置')};
function openAccounts(){$('accountModal').classList.remove('hidden')}
function openAbout(){$('aboutModal').classList.remove('hidden')}
$('loginBtn').onclick=openAccounts;$('closeModal').onclick=()=>$('accountModal').classList.add('hidden');
$('accountModal').onclick=event=>{if(event.target===$('accountModal'))$('accountModal').classList.add('hidden')};
$('aboutButton').onclick=openAbout;$('closeAbout').onclick=()=>$('aboutModal').classList.add('hidden');
$('aboutModal').onclick=event=>{if(event.target===$('aboutModal'))$('aboutModal').classList.add('hidden')};
document.addEventListener('keydown',event=>{if(event.key==='Escape'){$('accountModal').classList.add('hidden');$('aboutModal').classList.add('hidden')}});
$('openLogin').onclick=async()=>{const button=$('openLogin');button.disabled=true;try{const value=await api().open_login();$('syncState').textContent=value.opened?'登录窗口已打开；完成登录后点击同步 Cookie':(value.message||'登录窗口启动失败')}catch(error){$('syncState').textContent=`登录窗口启动失败：${error}`}finally{button.disabled=false}};
$('syncCookies').onclick=syncCookies;
$('saveAccounts').onclick=async()=>{const value=await api().save_accounts($('accounts').value,$('strategy').value);setAccounts(value);$('syncState').textContent=`已保存 ${value.count} 个账号`;addLog('账号设置已保存',`${value.count} 个账号`,'success')};
$('accounts').oninput=()=>{$('accountCount').textContent=`状态：${$('accounts').value.split(/\r?\n/).filter(Boolean).length} 行`};
$('startBtn').onclick=startQuery;$('stopBtn').onclick=async()=>{await api().cancel();updateStatus('正在停止','等待当前请求结束')};
$('pauseBtn').onclick=async()=>{const value=await api().toggle_pause();paused=Boolean(value.paused);$('pauseBtn').textContent=paused?'▶ 继续':'Ⅱ 暂停';updateStatus(paused?'任务已暂停':'任务已继续',paused?'倒计时和后续请求已暂停':'继续执行查询任务');addLog(paused?'任务已暂停':'任务已继续','','warn')};
$('openExport').onclick=()=>lastRunDir&&api().open_path(lastRunDir);
$('chooseOutput').onclick=async()=>{$('chooseOutput').disabled=true;try{const value=await api().choose_output_directory($('outputDir').value);if(value.selected){$('outputDir').value=value.path;updateStatus('导出目录已更新',value.path);addLog('导出目录已更新',value.path)}}catch(error){updateStatus('选择目录失败',String(error))}finally{$('chooseOutput').disabled=running}};
$('clearLog').onclick=()=>{$('activityLog').innerHTML='';addLog('日志已清空')};
$('collapseLog').onclick=()=>{document.body.classList.toggle('logCollapsed');$('collapseLog').textContent=document.body.classList.contains('logCollapsed')?'展开':'收起'};
document.querySelectorAll('.navItem').forEach(button=>button.onclick=()=>{document.querySelectorAll('.navItem').forEach(x=>x.classList.remove('active'));button.classList.add('active');const page=button.dataset.page;if(page==='account')openAccounts();if(page==='export'){if(lastRunDir)api().open_path(lastRunDir);else addLog('暂无导出记录','完成一次查询后可打开导出目录','warn')}if(page!=='task')setTimeout(()=>{button.classList.remove('active');document.querySelector('[data-page="task"]').classList.add('active')},350)});
window.addEventListener('pywebviewready',async()=>{
  const value=await api().bootstrap();demoMode=Boolean(value.demo);$('outputDir').value=value.outputDir;renderRegions(value.regions);setAccounts({text:value.accounts,count:value.accounts.split(/\r?\n/).filter(Boolean).length,strategy:value.strategy});renderHead();addLog('应用已启动','','success');
  if(value.demo){
    $('keywords').value='华为';switchMode('brand');setAccounts({text:'BDUSS=DEMO_ACCOUNT',count:1,strategy:'round_robin'});
    const rows=[
      {keyword:'华为',date:'2026-08-07',province:'全国',city:'全国',brand_index:5050874,brand_search:910929,brand_news:4988476,brand_interaction:370472},
      {keyword:'华为',date:'2026-08-08',province:'全国',city:'全国',brand_index:5124630,brand_search:928114,brand_news:null,brand_interaction:382901},
      {keyword:'华为',date:'2026-08-09',province:'全国',city:'全国',brand_index:5089212,brand_search:917806,brand_news:5014438,brand_interaction:376244}
    ];
    setRows(rows);$('successTotal').textContent='1';$('bar').style.width='60%';$('percent').textContent='60%';$('countdownValue').textContent='4';$('countdownBadge').classList.add('active');updateStatus('正在获取品牌指数 · 3/5','当前步骤：品牌资讯指数 · 华为 · 全国');$('resultMeta').textContent='进行中 · 3/5 个请求';addLog('等待 4 秒','品牌资讯指数 · 华为 · 全国');addLog('品牌搜索指数获取成功','3/5','success');addLog('品牌已匹配','品牌名称：华为','success');updateCounts();
  }
  window.__appReady=true;
});
