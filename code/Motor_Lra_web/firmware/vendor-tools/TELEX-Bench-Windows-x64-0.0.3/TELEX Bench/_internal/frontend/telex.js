'use strict';
(() => {
const $=id=>document.getElementById(id),roles={1:'左手',2:'右手',3:'接收器'},fingers=['小指','无名指','中指','食指','拇指'],keys=['little','ring','middle','index','thumb'],refs=['U10','U11','U16','U17','U18'];
const names={idle:'尚未开始',active:'运行中',running:'运行中',starting:'等待开始确认',stopping:'等待停止确认',stopped:'已收到停止确认',accepted:'请求已接收',applied:'设备已确认',rejected:'请求被拒绝',unknown:'状态未知',failed:'失败',complete:'流程完成 · 效果未验证',aborted:'已撤销',cancelled:'已取消'};
let s={},selected=null,unknown=null,loaded=false,polling=false,deviceKey='',portKey='',pairKey='',busy=false,hadSelection=false;
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const selectionLocked=()=>busy||s.download?.state==='running'||s.haptics?.active||['starting','stopping','unknown'].includes(s.haptics?.state)||s.calibration?.inhibit;
const current=()=>s.devices?.find(v=>v.descriptor.uid===selected),label=v=>names[v]||v||'未知',fmt=(v,d=1)=>v==null?'未知':Number(v).toFixed(d);
function notice(text,error=false){$('notice').textContent=text;$('notice').classList.toggle('error',error);}
async function call(name,payload){if(!window.pywebview?.api?.[name])return notice('桌面连接尚未就绪',true);try{const r=await (payload===undefined?window.pywebview.api[name]():window.pywebview.api[name](payload));notice(r.reason||label(r.state),['rejected','unknown','failed'].includes(r.state));return r;}catch(e){notice(String(e),true);}}
function chooseDevice(uid){
 if(selectionLocked()){notice('请先停止当前联动或校准，再切换设备。',true);render();return;}
 if(uid&&!(s.devices||[]).some(v=>v.descriptor.uid===uid&&!v.conflicts?.length)){notice('该设备已离线或身份冲突，请重新选择。',true);return;}
 selected=uid||null;hadSelection=true;unknown=null;deviceKey='';pairKey='';render();
}
function routeText(v){const r=v.preferred_route;if(!r)return '暂无可用连接';return r.kind==='radio'?`无线 · 经接收器 ${r.receiver_uid||''} · ${r.port||''}`:`USB 直连 · ${r.port||''}`;}
function renderTargets(){
 const devices=s.devices||[],v=current(),locked=selectionLocked();
 const options='<option value="">请选择设备</option>'+devices.map(v=>`<option value="${esc(v.descriptor.uid)}" ${v.conflicts?.length?'disabled':''}>${roles[v.descriptor.role]||'未知'} · ${esc(v.descriptor.uid)} · ${esc(routeText(v))}</option>`).join('');
 for(const el of document.querySelectorAll('[data-device-target]')){
  if(el.dataset.options!==options){el.innerHTML=options;el.dataset.options=options;}
  el.value=selected||'';el.disabled=locked||!devices.length;
 }
 const text=v?`${roles[v.descriptor.role]} · ${v.descriptor.uid} ｜ ${routeText(v)}${v.routes?.length>1?' ｜ 有线与无线均在线，当前优先使用 USB':''}${locked?' ｜ 操作进行中，目标已锁定':v.descriptor.role===3?' ｜ 接收器可测通信；手部数据、振动及校准请选择左手或右手':''}`:devices.length?'请选择本页要查看或操作的设备；其他栏目会同步此选择。':'尚未发现设备，插入后会自动识别。';
 for(const el of document.querySelectorAll('[data-target-note]'))el.textContent=text;
}
for(const el of document.querySelectorAll('[data-device-target]'))el.onchange=()=>chooseDevice(el.value);
function config(){const old=$('mode').value==='0';return {protocol_mode:old?0:1,imu_profile:old?0:Number($('imu-profile').value),diagnostics_enabled:$('diag-enabled').checked,haptics_enabled:!old&&$('haptics-enabled').checked,group_rate_hz:old?30:Number($('group-rate').value)};}
function trigger(){return {...s.settings?.trigger,preset:Number($('trigger-preset').value),strength:Number($('trigger-strength').value),duration_ms:Number($('trigger-duration').value)};}
function modeChanged(){const old=$('mode').value==='0';for(const id of ['haptics-enabled','imu-profile','group-rate'])$(id).disabled=old;if(old){$('haptics-enabled').checked=false;$('imu-profile').value='0';$('group-rate').value='30';}}
$('mode').onchange=modeChanged;$('legacy-imu-side').onchange=()=>renderData();
for(const tab of document.querySelectorAll('[data-tab]'))tab.onclick=()=>{for(const v of document.querySelectorAll('.view'))v.hidden=v.id!==`view-${tab.dataset.tab}`;for(const b of document.querySelectorAll('[data-tab]'))b.classList.toggle('selected',b===tab);$('page-title').textContent=tab.querySelector('span').textContent;};
function renderDevices(){
 const checkedPeers=new Set([...document.querySelectorAll('[data-peer]:checked')].map(e=>e.dataset.peer));
 const devices=s.devices||[];
 if(selected&&!devices.some(v=>v.descriptor.uid===selected)){selected=null;hadSelection=true;deviceKey='';notice('设备连接已变化；振动不会自动恢复。',true);}
 if(!selected&&!unknown&&!hadSelection){const hands=devices.filter(v=>v.descriptor.role!==3&&!v.conflicts?.length);const candidate=hands.length===1?hands[0]:devices.length===1&&!devices[0].conflicts?.length?devices[0]:null;if(candidate){selected=candidate.descriptor.uid;hadSelection=true;}}
 const next=JSON.stringify([devices,selected]);
 if(deviceKey!==next){deviceKey=next;let html='';for(const role of [1,2,3]){const matches=devices.filter(v=>v.descriptor.role===role);if(!matches.length)html+=`<div class="device-card empty"><strong>${roles[role]}</strong><small>尚未连接</small></div>`;for(const v of matches){const d=v.descriptor;html+=`<button class="device-card ${selected===d.uid?'active':''}" data-uid="${esc(d.uid)}"><strong>${roles[role]}</strong><span class="tag">${v.preferred_route?.kind==='radio'?'经接收器无线':'USB 直连'}</span><span class="tag">${esc(d.firmware)}</span><div class="uid">${esc(d.uid)} · ${esc(v.preferred_route?.port)}</div>${v.conflicts?.length?'<span class="bad">身份冲突，禁止控制</span>':''}</button>`;}}
 $('device-cards').innerHTML=html;for(const card of document.querySelectorAll('[data-uid]'))card.onclick=()=>chooseDevice(card.dataset.uid);}
 for(const card of document.querySelectorAll('[data-uid]'))card.disabled=Boolean(selectionLocked());
 const v=current(),d=v?.descriptor;
 $('device-detail').textContent=d?`${roles[d.role]}已选择 · ${d.config?.protocol_mode===0?'旧协议：振动禁用':'新协议'} · ${d.config?.imu_profile===1?'瑞芬框架：实际解析未实现；UART1 为00–04，UART0 为05–10':'维特 JY901P / I²C'}`:unknown?'已选择待初始化端口：下载时自动备份、探测和识别。':'选择设备卡片开始操作。';
 $('connection-status').textContent=s.offline?'离线 / 模拟':`${devices.length} 台设备在线`;
 const ports=s.ports||[],pk=JSON.stringify(ports);
 if(pk!==portKey){portKey=pk;const held=$('source-port').value;$('source-port').innerHTML='<option value="">请选择触觉设备串口</option>'+ports.filter(p=>p.state!=='identified').map(p=>`<option value="${esc(p.port_instance)}">${esc(p.port)}</option>`).join('');if([...$('source-port').options].some(o=>o.value===held))$('source-port').value=held;
 $('unknown-devices').innerHTML=ports.filter(p=>p.state==='needs_initialization').map(p=>`<button data-unknown="${esc(p.port_instance)}">待初始化：${esc(p.port)}</button>`).join('');for(const b of document.querySelectorAll('[data-unknown]'))b.onclick=()=>{if(selectionLocked())return;unknown=b.dataset.unknown;selected=null;hadSelection=true;deviceKey='';render();};}
 const peers=devices.filter(o=>d&&o.descriptor.uid!==d.uid&&((d.role===3)!==(o.descriptor.role===3))&&!o.conflicts?.length),pairs=JSON.stringify([selected,peers.map(o=>o.descriptor.uid)]);
 if(pairs!==pairKey){pairKey=pairs;$('pairing-options').innerHTML=peers.length?peers.map(o=>`<label class="check"><input type="checkbox" data-peer="${esc(o.descriptor.uid)}" ${checkedPeers.has(o.descriptor.uid)?'checked':''}>${roles[o.descriptor.role]} · ${esc(o.descriptor.uid)}</label>`).join(''):'暂无可选成员；可以只测试直连设备。';}
 const editing=$('change-pairing').checked||!d?.capabilities?.configured;$('suite').disabled=!editing;for(const box of document.querySelectorAll('[data-peer]'))box.disabled=!editing;
 $('download-button').disabled=busy||s.download?.state==='running'||(!d&&!unknown)||Boolean(v?.conflicts?.length)||v?.preferred_route?.kind==='radio';
 const stepNames={port_handoff:'移交下载串口',boot_identity:'等待设备重启与身份确认',diagnostics:'读取外设状态',stop_playback:'停止应用控制',chip_identity:'芯片与硬件身份',backup:'原固件完整备份',ram_probe:'身份脚读取',flash_identity_probe:'最小识别程序',write_and_verify:'镜像写入与回读',pairing_preserved:'保留原有套装与配对',configuration_readback:'配置保存与回读',wireless:'无线链路'};
 const dl=s.download||{},running=dl.state==='running',cancelled=running&&dl.cancel_requested;
 const duration=v=>{const n=Math.max(0,Math.floor(v||0));return `${Math.floor(n/60)}分${n%60}秒`;};
 const opNames={'read-mac':'确认芯片身份','flash-id':'读取存储容量','read-flash':'读取原固件','read-mem':'读取身份引脚','write-mem':'设置 / 恢复身份引脚','write-flash':'写入固件','verify-flash':'校验固件'};
 const detail=[];
 if(running){
  detail.push('当前步骤：'+(stepNames[dl.stage]||'准备下载'));
  if(dl.stage==='backup'&&dl.backup_total){
   detail.push(`原固件备份：${fmt(dl.backup_done/1048576)} / ${fmt(dl.backup_total/1048576)} MiB（${fmt(100*dl.backup_done/dl.backup_total,0)}%）`);
   if(dl.backup_chunk)detail.push(`第 ${dl.backup_chunk} / ${dl.backup_chunks} 段 · 本段第 ${dl.backup_attempt} / 3 次尝试`);
  }
  if(dl.operation_active)detail.push(`${opNames[dl.operation]||'当前操作'} · 已等待 ${duration(dl.operation_elapsed_s)}${dl.operation_timeout_s?' · 单次超时上限 '+duration(dl.operation_timeout_s):''}`);
  if(cancelled)detail.push(dl.stage==='backup'?'已收到取消请求；当前分段结束后停止，不再读取剩余分段。':'已收到取消请求；等待当前安全步骤结束。已开始的写入会先完成写入和校验。');
  if(dl.cancel_pending)detail.push('正在提交取消请求…');
  if(dl.last_error)detail.push('最近一次错误：'+dl.last_error);
 }
 if(dl.elapsed_s!=null&&dl.state!=='idle')detail.push('本次用时：'+duration(dl.elapsed_s));
 if(dl.cancel_error)detail.push(dl.cancel_error);
 $('download-result').textContent=dl.state==='idle'?'尚未下载':[cancelled?'正在取消 · 请保持设备连接':label(dl.state),...detail,...(dl.steps||[]).map(x=>(stepNames[x.name]||x.name)+'：'+(x.state==='pending'?'待实测':label(x.state))),dl.error||'',dl.backup?'原固件备份：'+dl.backup:''].filter(Boolean).join('\n');
 const bar=$('backup-progress');bar.hidden=!(running&&dl.stage==='backup'&&dl.backup_total);if(!bar.hidden){bar.max=dl.backup_total;bar.value=dl.backup_done||0;}
 $('cancel-download').disabled=!running||Boolean(dl.cancel_requested||dl.cancel_pending);
 $('cancel-download').textContent=cancelled?'等待安全停止…':'取消后续步骤';
 $('save-settings').disabled=busy||running;
}
function metric(title,value,unit,note=''){return `<div class="metric"><span class="muted">${title}</span><strong>${esc(value)} <small>${unit}</small></strong><small>${esc(note)}</small></div>`;}
function renderData(){
 const v=current(),d=v?.descriptor,old=d?.config?.protocol_mode===0;
 $('legacy-imu-side-label').hidden=!(old&&d.role===3);
 const legacyKey=`legacy:${v?.preferred_route?.port}`+(d?.role===3?':'+$('legacy-imu-side').value:'');
 const t=s.telemetry?.[old?legacyKey:selected]||{},g=t.pose;
 $('imu-target').textContent=d?`${roles[d.role]} · ${d.uid}`:'请先选择设备';
 $('imu-metrics').innerHTML=metric('收到的整手组率',fmt(t.received_groups_hz),'组 / 秒')+metric('完整读取组率',fmt(t.complete_read_groups_hz),'组 / 秒')+metric('IMU 真实采样率','尚未证实','不由接收率推断')+metric('电池电量',g?.battery_percent==null?'未知':g.battery_percent+'%','',g?.voltage_mv==null?'无有效电压读数':`${g.voltage_mv} mV · 电池接入待验证`);
 $('imu-rows').innerHTML=(t.imus||[]).map(i=>`<tr><td>${esc(i.id)} · ${esc(i.name)}</td><td class="${i.valid===false?'bad':i.valid===true?'ok':'pending'}">${i.valid==null?'有效性未知':i.valid?'读取有效':'未读到 / 无效'}</td><td>${i.valid===false?'—':i.euler_deg.map(v=>fmt(v,2)).join(' / ')}</td><td>${i.valid===false?'—':i.quaternion.map(v=>fmt(v,4)).join(' / ')}</td></tr>`).join('')||'<tr><td colspan="4">尚无完整姿态数据。瑞芬框架不会生成模拟姿态。</td></tr>';
 $('communication-metrics').innerHTML=metric('有效接收组数',t.groups??'—','组')+metric('序号缺失',t.sequence_missing??'未知','',t.loss_percent==null?'无可验证计数':`${fmt(t.loss_percent,2)}% · 最近4096序号可回补乱序`)+metric('重复 / 乱序',`${t.duplicates??0} / ${t.reordered??0}`,'组')+metric('最近数据',t.stale?'已过期':t.last_seen_us?'已收到':'等待','');
 $('communication-details').textContent=JSON.stringify({device:d?.uid,route:current()?.preferred_route,status:t.status||{},test:s.communication_test?.uid===selected?s.communication_test:'所选设备尚未测试',errors:s.errors||{}},null,2);
 $('communication-test').disabled=!d||busy||!d.config?.diagnostics_enabled||d.config?.protocol_mode===0;
}
function renderHaptics(){
 const v=current(),d=v?.descriptor,allowed=d&&d.role!==3&&d.config?.protocol_mode===1&&d.config?.haptics_enabled&&!v.conflicts?.length&&!s.calibration?.inhibit&&!busy&&s.download?.state!=='running',mask=d?.capabilities?.motors||0,active=s.haptics?.active;
 $('haptic-state').textContent=label(s.haptics?.state)+(s.haptics?.state==='unknown'?' · 实际停机未确认':'');
 $('motor-cards').innerHTML=fingers.map((name,ch)=>`<div class="motor-card" id="motor-${ch}"><small>CH${ch} / ${refs[ch]}</small><strong>${name}</strong><small class="${mask&(1<<ch)?'muted':'pending'}">${ch===3?'U17 实物验收暂缓':mask&(1<<ch)?'驱动在线 · 机械响应待测':'驱动未在线 / 未测试'}</small><button data-motor="${ch}" ${allowed&&!active&&(mask&(1<<ch))?'':'disabled'}>短振测试</button></div>`).join('');
 for(const b of document.querySelectorAll('[data-motor]'))b.onclick=()=>call('motor_test',{uid:selected,channels:[Number(b.dataset.motor)],preset:Number($('trigger-preset').value),strength:24,duration_ms:100});
 $('tactile-start').disabled=!allowed||active;$('tactile-stop').disabled=!active;$('source-state').textContent=label(s.source?.state)+(s.source?.reason?' · '+s.source.reason:'');
}
function renderCal(){const d=current()?.descriptor,c=s.calibration||{},remoteOld=d?.role===3&&d?.config?.protocol_mode===0;$('legacy-cal-side-label').hidden=!remoteOld;$('calibration-state').textContent=label(c.state);$('calibration-detail').textContent=[label(c.state),c.reason||'',c.event?'在线IMU：'+Array.from({length:11},(_,i)=>c.event.active_mask&(1<<i)?String(i).padStart(2,'0'):null).filter(x=>x!==null).join('、'):'',c.event?'退出写入：'+(c.event.switch_ok?'已确认':'未确认')+' / 保存：'+(c.event.save_ok?'已确认':'未确认'):'',c.event?.mag?'磁场原始值：\n'+c.event.mag.map((v,i)=>String(i).padStart(2,'0')+'  '+v.join(' / ')).join('\n'):'',c.mag_line||'',c.last_line||''].filter(Boolean).join('\n');$('cal-start').disabled=!d||(d.role===3&&!remoteOld)||d.config?.imu_profile!==0||c.inhibit||busy;$('cal-stop').disabled=!c.uid;$('cal-query').disabled=!d;$('cal-abort').disabled=!c.uid||(!remoteOld&&c.route!=='radio');$('footer-status').textContent=c.inhibit?'校准尚未确认退出；关闭窗口前请停止并确认。':'实物效果、100 Hz、矩侨侧别与 U17 分项验收，软件通过不代替实测。';}
function render(){renderDevices();renderTargets();renderData();renderHaptics();renderCal();}
async function poll(){if(polling||!window.pywebview?.api)return;polling=true;try{s=await window.pywebview.api.snapshot();if(!loaded&&s.settings){loaded=true;const c=s.settings.config,t=s.settings.trigger;$('mode').value=c.protocol_mode;$('imu-profile').value=c.imu_profile;$('group-rate').value=c.group_rate_hz;$('diag-enabled').checked=c.diagnostics_enabled;$('haptics-enabled').checked=c.haptics_enabled;$('trigger-preset').value=t.preset;$('trigger-strength').value=t.strength;$('trigger-duration').value=t.duration_ms;modeChanged();}if(s.hardware_warning)$('hardware-warning').textContent=s.hardware_warning;render();}catch(e){notice('无法读取工作台状态：'+e,true);}finally{polling=false;}}
$('change-pairing').onchange=()=>renderDevices();
$('save-settings').onclick=()=>call('configure',{config:config(),trigger:trigger()});
$('download-button').onclick=async()=>{busy=true;render();try{const c=config();if(current()?.descriptor.role===3)c.haptics_enabled=false;await call('download',{uid:selected||undefined,port_instance:unknown||undefined,config:c,trigger:trigger(),mode:$('download-mode').value,suite:$('suite').value,change_pairing:$('change-pairing').checked,peer_uids:[...document.querySelectorAll('[data-peer]:checked')].map(e=>e.dataset.peer)});}finally{busy=false;render();}};
$('cancel-download').onclick=async()=>{
 if(s.download?.state!=='running')return;
 s.download.cancel_pending=true;renderDevices();
 try{const r=await call('cancel_download');if(r?.state==='accepted')s.download.cancel_requested=true;else s.download.cancel_error=r?.reason||'取消请求未确认，请查看连接状态';}
 finally{s.download.cancel_pending=false;renderDevices();}
};$('emergency-stop').onclick=()=>call('stop',{reason:'emergency'});$('tactile-stop').onclick=()=>call('stop',{reason:'normal'});
$('cal-start').onclick=()=>call('calibrate',{operation:'start',uid:selected,side:$('legacy-cal-side').value});for(const [id,operation] of [['cal-stop','stop'],['cal-query','query'],['cal-abort','abort']])$(id).onclick=()=>call('calibrate',{operation,uid:selected,side:$('legacy-cal-side').value});
$('communication-test').onclick=()=>call('communication_test',{uid:selected});$('export-button').onclick=async()=>{const r=await call('export_report');if(r?.path)notice('测试记录已导出：'+r.path);};
function mappingRows(){const adc=$('source-kind').value==='juqiao';$('mapping-rows').innerHTML=fingers.map((f,i)=>`<tr data-map="${i}"><td><label><input type="checkbox" class="enabled" ${i===4?'checked':''}>${f}</label></td><td><input class="points" placeholder="${adc?'0,1,2':'合力 / 区域'}" aria-label="${f}原始点"></td><td><input type="number" class="baseline" value="${adc?'':'0'}" step="any" aria-label="${f}零点"></td><td><input type="number" class="fullscale" value="${adc?'':'10'}" step="any" aria-label="${f}满量程"></td></tr>`).join('');$('juqiao-side-label').hidden=!adc;$('paxini-address-label').hidden=adc;$('paxini-compat-label').hidden=adc;$('source-help').textContent=adc?'仅沿用矩侨原 USB 采集板。必须填写实测零点、满量程、触点区域及原始侧别；ADC 不标注为 N。':'Paxini 使用力单位 N。多个手指须分别配置不重叠的触点区域。';}
$('source-kind').onchange=mappingRows;mappingRows();
$('tactile-start').onclick=async()=>{try{const d=current()?.descriptor;if(!d)throw Error('请先选择手板');const kind=$('source-kind').value,adc=kind==='juqiao',port=$('source-port').value;if(!port)throw Error('请选择触觉采集串口');const side=d.role===1?'left':'right',source_id=kind+':'+port;
 const routes=[...document.querySelectorAll('[data-map]')].filter(row=>row.querySelector('.enabled').checked).map(row=>{const zero=row.querySelector('.baseline').value,full=row.querySelector('.fullscale').value;if(zero===''||full==='')throw Error('请填写零点和满量程');const text=row.querySelector('.points').value.trim();return {finger:keys[Number(row.dataset.map)],indices:text?text.split(',').map(x=>Number(x.trim())):[],baseline:Number(zero),full_scale:Number(full)};});
 const options=adc?{}:{device_id:Number($('paxini-address').value),allow_err_len:$('paxini-compat').checked};if(adc){const raw=$('juqiao-side').value;if(!raw)throw Error('请按实物确认矩侨原始侧别');options.side_map={[raw]:side};}
 await call('tactile_start',{uid:selected,source:kind,port_instance:port,options,mapping:{source_id,side,unit:adc?'ADC':'N',routes,pattern:$('feedback-pattern').value,max_level:Number($('level-cap').value)}});
 }catch(e){notice(e.message,true);}};
window.addEventListener('pywebviewready',poll);window.TELEX={refresh:poll};poll();setInterval(poll,400);
})();
