/* Shared Mac/Windows presentation layer. All hardware actions use the same API. */
let workbenchPage = 'production', preparedFlash = null, lastCaptureResult = null, appliedSerial = null;
function hasFlashTargets(state, mode) {
  const available=new Set((state.ports||[]).map(p=>p.device));
  const assigned=Object.entries(state.assignments||{}).filter(([role,port])=>port&&available.has(rawAssignment(port)));
  if(mode==='single')return assigned.length===1;
  const roles=['receiver','left','right'].filter(role=>{
    const port=rawAssignment(state.assignments?.[role])||state.detected_identities?.[role]?.port;
    return !!port&&available.has(port);
  });
  return roles.includes('receiver')&&(roles.includes('left')||roles.includes('right'));
}
function navigateWorkbench(page) {
  workbenchPage = page;
  switchWorkspace(page === 'production' ? 'production' : 'diagnostics');
  for (const name of ['monitor','calibration','reports','signal']) {
    document.getElementById(name+'-view').classList.toggle('hidden', page !== name);
  }
  document.querySelectorAll('.nav-item').forEach(b => {
    const active = b.dataset.page === page;
    b.classList.toggle('active',active); b.setAttribute('aria-current',active?'page':'false');
  });
  if(page==='reports') refreshReports();
  window.scrollTo(0,0);
}
function showGuide(){
  showInformation('快速开始','1. 连接设备与绑定角色\n接收器和手套使用 USB 数据线；Mac 串口显示 /dev/cu.…。\n\n2. 选择固件和设备编号\nver.3 / ver.5 同为 S3，请核对设备 ID 或人工选择。套装提供完整与快速两种方式；完整模式全擦除，快速模式只擦写目标分区。返修默认保留其他分区。\n\n3. 确认后执行\n逐项核对端口、配对地址和固件哈希。执行中保持供电，安全停止会在当前设备操作结束后生效。\n\n4. 校准与测试\n校准前预热约 5 分钟；旋转至少约 35 秒。保存需收到 COMPLETE。信号测试先开始监控，再采集并保存真实报告。');
}
function showInformation(title,text){
  const dialog=document.getElementById('information-modal');
  document.getElementById('information-title').textContent=title;
  document.getElementById('information-body').textContent=text;
  dialog.classList.remove('hidden'); dialog.querySelector('button').focus();
}
async function openReportsFolder(){
  try{await postBridgeJson('/api/reports/open',{});}catch(e){toast('打开目录失败：'+e.message);}
}
async function exportSession(){
  try{const result=await postBridgeJson('/api/reports/export',{});await refreshReports();showInformation('会话快照已保存',result.directory);}catch(e){toast('保存失败：'+e.message);}
}
async function refreshReports(){
  try{
    const data=await fetchBridgeJson('/api/reports'),list=document.getElementById('report-list');
    list.replaceChildren(); document.getElementById('report-count').textContent='最近 '+data.reports.length+' 条真实记录';
    if(!data.reports.length){list.innerHTML='<p class="empty-state">暂无记录。完成烧录、数据采集或保存快照后，会在这里显示。</p>';return;}
    const labels={signal_capture:'信号采集',flash:'烧录任务',session_snapshot:'会话快照',calibration:'磁力计校准'};
    const statuses={completed:'已完成',exported:'已保存',success:'成功',failed:'失败',running:'进行中',interrupted:'已中断',no_data:'未收到数据',save_failed:'保存失败'};
    data.reports.forEach(report=>{
      const row=document.createElement('div');row.className='report-row';
      row.innerHTML='<span class="report-icon">'+(report.type==='flash'?'FW':'DATA')+'</span><div><b>'+esc(labels[report.type]||report.type)+'</b><small>'+esc(report.started_at)+' · '+esc(report.device_id||report.task_id)+'</small></div><span class="badge">'+esc(statuses[report.status]||report.status)+'</span>';
      const button=document.createElement('button');button.className='btn small';button.textContent='查看';button.onclick=async()=>{try{const r=await postBridgeJson('/api/reports/read',{id:report.id});showInformation(labels[report.type]||'任务报告',JSON.stringify(r,null,2));}catch(e){toast(e.message);}};
      row.append(button);list.append(row);
    });
  }catch(e){document.getElementById('report-count').textContent='读取失败：'+e.message;}
}
async function startCapture(){
  try{applyBackendState(await postBridgeJson('/api/capture/start',{seconds:Number(document.getElementById('capture-seconds').value)}));}catch(e){toast('无法开始记录：'+e.message);}
}
async function stopCapture(){
  try{applyBackendState(await postBridgeJson('/api/capture/stop',{}));}catch(e){toast(e.message);}
}
function renderCapture(capture){
  if(!capture)return;
  document.getElementById('capture-start-btn').disabled=!!capture.active;
  document.getElementById('capture-stop-btn').classList.toggle('hidden',!capture.active);
  document.getElementById('capture-count').textContent=capture.frames+' 帧';
  document.getElementById('capture-progress').style.width=(capture.active?Math.min(100,capture.elapsed_s/capture.seconds*100):capture.result?100:0)+'%';
  document.getElementById('capture-status').textContent=capture.active?'正在记录 · '+Math.floor(capture.elapsed_s)+' / '+capture.seconds+' 秒':capture.error?'保存失败':capture.result?capture.result.status==='no_data'?'记录结束 · 未收到数据':'记录已结束':'等待开始';
  if(capture.error)document.getElementById('capture-detail').textContent=capture.error;
  if(capture.result && capture.result.task_id!==lastCaptureResult){
    lastCaptureResult=capture.result.task_id;
    const r=capture.result,rows=[['包数','packet_count'],['实际速率 / Hz','actual_rate_hz'],['丢包率','loss_rate'],['最大连续丢包','max_burst_loss'],['重复 / 乱序','duplicates'],['批间隔 P95 / ms','p95_ms'],['批间抖动 / ms','jitter_ms'],['断连次数','disconnect_count'],['电量变化 / %','battery_delta']];
    document.getElementById('quality-result').innerHTML='<table class="device-table"><thead><tr><th>指标</th><th>左手</th><th>右手</th></tr></thead><tbody>'+rows.map(([label,key])=>'<tr><td>'+label+'</td>'+['left','right'].map(side=>{const h=r.hands[side],v=h[key];return '<td>'+(v==null?'—（未提供）':key==='loss_rate'?(v*100).toFixed(2)+'%':typeof v==='number'?Number(v.toFixed(3)):esc(v))+'</td>';}).join('')+'</tr>').join('')+'</tbody></table>';
    document.getElementById('capture-detail').textContent=r.directory?'已保存：'+r.directory:(capture.error||'数据尚未保存');
  }
}
function profileState(state){
  (state.firmware_profiles||[]).forEach(p=>{
    if(profiles[p.generation])profiles[p.generation].manifest=p.status==='available'?'固件已校验 · 可用':'资源未就绪';
  });
}
document.addEventListener('DOMContentLoaded',()=>{
  switchDiagnostic=navigateWorkbench;
  scanSerialPorts=async()=>{try{applyBackendState(await postBridgeJson('/api/scanner/start',{}));toast('自动扫描已开启，正在识别设备');}catch(e){toast('扫描失败：'+e.message);}};
  document.getElementById('scan-btn').textContent='重新检测';
  const main=document.querySelector('#production-workspace > main');
  const panels=[...main.children].filter(e=>e.classList.contains('panel'));
  const device=panels.find(e=>e.textContent.includes('设备扫描结果'));
  const binding=panels.find(e=>e.textContent.includes('1. 连接设备'));
  const firmware=panels.find(e=>e.textContent.includes('2. 选择固件'));
  const config=main.querySelector('.config-grid');
  const execution=panels.find(e=>e.textContent.includes('烧录执行'));
  device.after(binding);binding.id='step-connect';firmware.id='step-firmware';config.id='step-task';execution.id='step-execute';
  [binding,firmware,config,execution].forEach(e=>e.classList.add('section-anchor'));
  const strip=document.createElement('div');strip.className='step-strip';strip.innerHTML='<button onclick="document.getElementById(\'step-connect\').scrollIntoView({behavior:\'smooth\'})">01 连接设备</button><button onclick="document.getElementById(\'step-firmware\').scrollIntoView({behavior:\'smooth\'})">02 选择固件</button><button onclick="document.getElementById(\'step-task\').scrollIntoView({behavior:\'smooth\'})">03 确认与烧录</button>';
  main.querySelector('.page-head').after(strip);
  const advanced=document.createElement('details');advanced.className='manual-config';advanced.innerHTML='<summary>返修与高级设置 · 手动配对 MAC / 接收器芯片</summary><p>通常使用自动读取；只在单设备返修或无法识别时手动指定。</p><div class="fields">'+['receiver','left','right'].map(role=>'<div class="field"><label for="mac-'+role+'">'+roleName(role)+' MAC</label><input id="mac-'+role+'" placeholder="自动读取或 AA:BB:CC:DD:EE:FF"><button class="btn small" data-mac="'+role+'">保存地址</button></div>').join('')+'</div><div class="field"><label for="receiver-mcu-select">接收器芯片</label><select id="receiver-mcu-select"><option value="">自动识别</option><option value="esp32s3">ESP32-S3</option><option value="esp32c6">ESP32-C6</option></select><button id="receiver-mcu-save" class="btn small">保存芯片选择</button></div>';
  binding.append(advanced);
  advanced.querySelectorAll('[data-mac]').forEach(b=>b.onclick=async()=>{try{applyBackendState(await postBridgeJson('/api/config/manual-mac',{role:b.dataset.mac,mac:document.getElementById('mac-'+b.dataset.mac).value}));toast('配对地址已保存');}catch(e){toast(e.message);}});
  document.getElementById('receiver-mcu-save').onclick=async()=>{const mcu=document.getElementById('receiver-mcu-select').value;if(!mcu){toast('保留自动识别结果');return;}try{applyBackendState(await postBridgeJson('/api/mcu/select',{mcu}));}catch(e){toast(e.message);}};
  document.querySelectorAll('.version-card').forEach(card=>{card.tabIndex=0;card.setAttribute('role','button');card.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();card.click();}};});
  const speed=document.createElement('div');speed.className='field';speed.innerHTML='<label for="flash-speed">执行方式</label><select id="flash-speed"><option value="full">完整校验 · 默认</option><option value="fast">快速套装 · 写入校验 + 无线配对</option></select><p id="flash-speed-help">完整模式保留整片擦除、独立摘要及身份检查；两种套装模式均需通过无线配对并确认退出维护模式。</p>';
  config.querySelector('.panel').append(speed);
  document.getElementById('flash-speed').onchange=async e=>{try{applyBackendState(await postBridgeJson('/api/config/fast-mode',{enabled:e.target.value==='fast'}));}catch(error){toast(error.message);await refreshBackendState();}};
  const oldApply=applyBackendState;
  applyBackendState=function(state){
    lastBackendState=state;profileState(state);oldApply(state);renderCapture(state.capture);
    if(state.calibration?.status==='aborted'&&state.calibration?.mode==='wired')document.getElementById('result-text').textContent='本机校准会话已取消，未发送保存命令。有线协议没有 ABORT；请确认手套已按固件规则退出校准模式。';
    if(state.calibration?.report_error)document.getElementById('result-text').textContent+=' 报告保存失败：'+state.calibration.report_error;
    const ports=state.ports||[];
    document.getElementById('scan-btn').textContent=state.scanner_active?'重新检测':'开始检测';
    document.getElementById('scan-summary').textContent=(state.scanner_active?'自动检测 · ':'检测已暂停 · ')+(ports.length?ports.length+' 个串口':'等待设备');
    if(!ports.length)document.getElementById('device-body').innerHTML='<tr><td colspan="7"><div class="empty-state">尚未发现串口设备<br>'+(state.scanner_active?'工作台已自动检测串口。连接 USB 数据线后会自动显示；若未出现，请检查线缆和下载模式。':'检测已暂停，点击“开始检测”以识别已连接设备。')+'</div></td></tr>';
    document.getElementById('top-status').textContent=ports.length?ports.length+' 个端口 · 已连接工作台':'工作台已连接 · 等待 USB 设备';
    for(const side of ['left','right']){
      const select=document.getElementById('cal-hand'),hand=state.monitor?.hands?.[side];
      select.querySelector('[value="'+side+'"]').textContent=roleName(side)+(hand?.connected?' · 在线':'');
    }
    const busy=!['idle','done','error'].includes(state.phase),capture=!!state.capture?.active,cal=!!state.calibration?.active;
    document.querySelectorAll('#step-connect select,#step-connect input,#step-connect button,#step-firmware button,#step-task input,#step-task select,#step-task button').forEach(e=>{e.disabled=busy;});
    const speedSelect=document.getElementById('flash-speed');if(document.activeElement!==speedSelect||busy||state.mode==='single')speedSelect.value=state.fast_mode?'fast':'full';speedSelect.disabled=busy||state.mode==='single';
    document.getElementById('flash-speed-help').textContent=state.mode==='single'?'单设备修复保留摘要及身份检查，不递增套装编号。':state.fast_mode?'快速模式保留写入校验和实际参与手套的无线配对；省去整片擦除、额外摘要及 USB 身份回读。':'完整模式保留整片擦除、独立摘要及身份检查；套装完成还需无线配对与退出维护确认。';
    const ready=hasFlashTargets(state,flashMode);
    const flash=document.getElementById('flash-btn');flash.disabled=busy||cal||capture||!ready||!version;
    flash.textContent=busy?'任务进行中':!version?'先选择固件版本':!ready?'先绑定目标设备':'查看并确认烧录';
    if(!busy&&(!ready||cal||capture))document.getElementById('compat').textContent=cal?'先结束校准':capture?'先结束数据记录':!ready?'请完成目标角色的绑定':'';
    document.getElementById('capture-start-btn').disabled=capture||cal||busy||!state.monitor?.active;
    const calSide=document.getElementById('cal-hand').value,calMode=document.getElementById('cal-mode').value;
    const calPort=calMode==='wireless'?(state.monitor?.receiver_port||receiverPortFromState()):rawAssignment(state.assignments?.[calSide]);
    const canCal=!!calPort&&ports.some(p=>p.device===calPort);
    document.querySelector('[onclick="startCalibration()"]').disabled=busy||cal||capture||!canCal;
    document.querySelector('#cal-ready h2').textContent=canCal?'准备开始校准':'等待目标设备';
    document.querySelector('#cal-ready p').textContent=canCal?'确认目标手套，预热后旋转覆盖多个方向。':'先连接并绑定'+(calMode==='wireless'?'接收器':'目标手套')+'；再选择目标、方式和时长。';
    const serialKey=String(state.device_id_kind)+':'+String(state.device_id_number);
    if(appliedSerial!==serialKey){document.getElementById('serial').value=state.device_id_number||1;document.getElementById('kind').value=state.device_id_kind||'T';appliedSerial=serialKey;updateSelectAccent('kind');}
    const draft=Number(document.getElementById('serial').value)!==state.device_id_number||document.getElementById('kind').value!==state.device_id_kind;
    if(draft){flash.disabled=true;document.getElementById('compat').textContent='编号已修改，请先点击“应用设备 ID”';}
  };
  // Bind final confirmation to the validated backend request, not a stale UI.
  const oldConfirm=openFlashConfirm;
  openFlashConfirm=async()=>{
    try{
      const prepared=await postBridgeJson('/api/flash/prepare',{});applyBackendState(prepared.state);preparedFlash=prepared.confirmation;oldConfirm();
      const state=prepared.state,p=(state.firmware_profiles||[]).find(x=>x.generation===state.selected_generation);
      document.getElementById('confirm-body').insertAdjacentHTML('beforeend','<div class="summary-line"><span>擦除策略</span><b>'+esc(state.mode==='full_set'?(state.fast_mode?'仅擦除写入分区 · 快速套装':'全部目标设备全擦除'):state.full_erase_single?'目标设备全擦除':'仅覆盖固件清单分区')+'</b></div><div class="summary-line"><span>固件清单 SHA-256</span><b>'+esc(p?.manifest_sha256||'任务开始前再次校验')+'</b></div>');
      document.getElementById('confirm-body').insertAdjacentHTML('beforeend','<div class="summary-line"><span>执行方式</span><b>'+esc(state.fast_mode?'快速 · 写入校验 + 无线配对 + 维护退出确认':'完整 · 摘要与身份检查')+'</b></div>');
      document.getElementById('confirm-ack').checked=false;document.getElementById('confirm-flash-btn').disabled=true;document.getElementById('confirm-ack').focus();
    }catch(e){toast('无法准备烧录：'+e.message);}
  };
  startFlash=async()=>{
    if(!preparedFlash||!document.getElementById('confirm-ack').checked)return;
    document.getElementById('confirm-flash-btn').disabled=true;
    try{await postBridgeJson('/api/flash/start',{confirmation:preparedFlash});cancelFlashConfirm();await refreshBackendState();document.getElementById('step-execute').scrollIntoView({behavior:'smooth'});}
    catch(e){toast('烧录未启动：'+e.message);cancelFlashConfirm();preparedFlash=null;}
  };
  // A readable persistent error message, including failures before any hardware write.
  toast=function(message){const el=document.getElementById('toast');el.textContent=message;el.classList.add('show');clearTimeout(el._timer);el._timer=setTimeout(()=>el.classList.remove('show'),7000);};
  document.addEventListener('keydown',e=>{if(e.key==='Escape'){cancelFlashConfirm();document.getElementById('information-modal').classList.add('hidden');}});
  document.querySelector('#confirm-modal .modal-actions').before(Object.assign(document.createElement('label'),{className:'confirm-ack',innerHTML:'<input type="checkbox" id="confirm-ack"><span>已核对目标端口、固件与擦除策略。执行期间保持供电和 USB 连接。</span>'}));
  document.body.insertAdjacentHTML('beforeend','<div id="information-modal" class="modal-bg hidden" role="dialog" aria-modal="true"><div class="modal wide"><h2 id="information-title"></h2><pre id="information-body" class="report-json"></pre><div class="modal-actions"><button class="btn primary" onclick="document.getElementById(\'information-modal\').classList.add(\'hidden\')">关闭</button></div></div></div>');
  // The acknowledgement node is inserted after the handler setup below.
  document.getElementById('confirm-ack').onchange=e=>document.getElementById('confirm-flash-btn').disabled=!e.target.checked;
  document.querySelectorAll('.chart-legend').forEach(e=>e.innerHTML='<span>采样率 / Hz</span><span>丢包率 / %</span><span class="chart-note">最近时间窗 · 真实数据</span>');
  navigateWorkbench('production');
  if(lastBackendState)applyBackendState(lastBackendState);
});
