import {BrowserClock, audioTiming} from './clock.js';
import {captureFrame} from './capture.js';
import {sendControl, controlDeliverySnapshot} from './control_delivery.js';
import {evidenceView} from './evidence.js';
import {installHistory} from './history.js';
const $=id=>document.getElementById(id);
const token=new URLSearchParams(location.hash.slice(1)).get('token')||sessionStorage.getItem('iago-token');
if(token)sessionStorage.setItem('iago-token',token);history.replaceState(null,'',location.pathname);
let control,audio,ctx,worklet,mic,mode='idle',activeAnswer=null,speechFailed=false,lastHeartbeat=performance.now(),perceptionEnabled=false,deployment='desktop';
const captureClock=new BrowserClock();
const sources=new Map(),heardTimers=new Set(),objectURLs=new Set();
const notice=text=>{$('notice').textContent=text;};
const send=m=>{if(m.type==='robot_camera'&&m.enabled===false)window.dispatchEvent(new Event('iago-visual-change'));sendControl(control,m);};
async function api(path,options={}){
 const r=await fetch(path,{...options,headers:{Authorization:`Bearer ${token}`,...options.headers}});
 if(!r.ok){let message;try{message=await r.json();}catch{message={detail:r.statusText};}throw Error(message.detail||message.error||`HTTP ${r.status}`);}
 return r;
}
let visualClearVersion=0;
const post=async(path,data)=>{if(path==='/api/visual'&&data.action==='clear')visualClearVersion++;if(path==='/api/visual'&&['clear','off'].includes(data.action))window.dispatchEvent(new Event('iago-visual-change'));const result=await (await api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})).json();if(path==='/api/visual'&&['pin','unpin'].includes(data.action))window.dispatchEvent(new Event('iago-visual-pin-change'));return result;};
let spokenReferenceRequest=null,spokenReferenceToken=null;
function clearSpokenReference(){
 if(spokenReferenceRequest||spokenReferenceToken){spokenReferenceRequest=crypto.randomUUID();send({type:'speech_reference',frame:null,request:spokenReferenceRequest});}
 spokenReferenceToken=null;$('spokenHistorySelection').textContent='';$('forgetSpokenHistory').hidden=true;
}
$('forgetSpokenHistory').onclick=clearSpokenReference;
window.addEventListener('iago-history-unselected',clearSpokenReference);
window.addEventListener('iago-history-spoken',event=>{
 if(mode!=='conversation'){notice('Start Conversation, then choose an image before speaking.');return;}
 forgetHistorySelection();spokenReferenceRequest=crypto.randomUUID();spokenReferenceToken=null;
 $('spokenHistorySelection').textContent='Selecting image for your next spoken question…';$('forgetSpokenHistory').hidden=false;
 send({type:'speech_reference',frame:event.detail.id,region:event.detail.region,request:spokenReferenceRequest});
});
let selectedHistory=null;
function forgetHistorySelection(){selectedHistory=null;$('historySelection').textContent='';$('forgetHistorySelection').hidden=true;}
window.addEventListener('iago-history-unselected',forgetHistorySelection);
window.addEventListener('iago-history-selected',event=>{clearSpokenReference();selectedHistory=event.detail;$('historySelection').textContent=`For your next typed question: ${selectedHistory.label}`;$('forgetHistorySelection').hidden=false;});
$('forgetHistorySelection').onclick=forgetHistorySelection;
installHistory(api,post);
function turn(who,text){const el=document.createElement('div');el.className=`turn ${who}`;const label=document.createElement('span');label.className='who';label.textContent=who==='user'?'You':'Iago';const content=document.createElement('span');content.textContent=text;el.append(label,content);$('transcript').append(el);el.scrollIntoView({block:'nearest'});return content;}
function clearHeard(){for(const t of heardTimers)clearTimeout(t);heardTimers.clear();}
function stop(){clearHeard();worklet?.port.postMessage({type:'stop'});if(!worklet)send({type:'stop'});}
let devicePreferences={};
function loadDevicePreferences(){
 try{const raw=localStorage.getItem('iago-device-preferences');const saved=raw&&raw.length<=4096?JSON.parse(raw):{};
  if(saved&&typeof saved==='object'){
   for(const id of ['microphone','cameraDevice','speaker'])if(typeof saved[id]==='string'&&saved[id].length<=512)devicePreferences[id]=saved[id];
   for(const id of ['mute','patient'])if(typeof saved[id]==='boolean')$(id).checked=saved[id];
   if(typeof saved.volume==='number'&&Number.isFinite(saved.volume)&&saved.volume>=0&&saved.volume<=1)$('volume').value=saved.volume;
  }
 }catch{devicePreferences={};}
}
function saveDevicePreferences(){
 for(const id of ['microphone','cameraDevice','speaker'])if($(id).options.length)devicePreferences[id]=$(id).value;
 if(deployment==='desktop'){devicePreferences.mute=$('mute').checked;devicePreferences.patient=$('patient').checked;devicePreferences.volume=Number($('volume').value);}
 try{localStorage.setItem('iago-device-preferences',JSON.stringify(devicePreferences));}catch{notice('This browser could not save device choices. Current controls still work.');}
}
let deviceEnumeration=0;
async function enumerate(){
 const request=++deviceEnumeration,devices=await navigator.mediaDevices.enumerateDevices();
 if(request!==deviceEnumeration)return;
 for(const [id,kind] of [['microphone','audioinput'],['cameraDevice','videoinput'],['speaker','audiooutput']]){
  const selected=devicePreferences[id]??$(id).value;$(id).replaceChildren(new Option('System default',''));
  for(const d of devices.filter(x=>x.kind===kind)){if(d.deviceId)$(id).add(new Option(d.label||`${kind} ${$(id).length}`,d.deviceId));}
  if(selected&&![...$(id).options].some(option=>option.value===selected)){const missing=new Option('Saved device unavailable — choose another',selected);missing.dataset.unavailable='true';$(id).add(missing);}
  $(id).value=selected;
 }
}

let audioSetup=null,pendingAudio=null,audioGeneration=0,modeRequest=0;
function closeAudioResources(owned){
 if(!owned)return;
 owned.mic?.getTracks().forEach(track=>{track.onended=null;track.stop();});
 if(owned.worklet){owned.worklet.port.onmessage=null;owned.worklet.disconnect();}
 if(owned.audio){owned.audio.onclose=null;owned.audio.onopen=null;owned.audio.close();}
 if(owned.ctx&&owned.ctx.state!=='closed')owned.ctx.close().catch(()=>{});
}
function releaseAudio(){
 audioGeneration++;
 $('audioSetupStatus').textContent='Audio capture is off.';
 closeAudioResources(pendingAudio);pendingAudio=null;
 closeAudioResources({mic,ctx,worklet,audio});mic=ctx=worklet=audio=null;
}
async function setupAudio(){
 if(audioSetup)return audioSetup;
 if(ctx&&worklet&&audio?.readyState===WebSocket.OPEN)return;
 if(ctx||mic||audio)releaseAudio();
 const generation=audioGeneration,owned={};pendingAudio=owned;
 $('audioSetupStatus').textContent='Preparing microphone and speaker…';
 const current=()=>{if(generation!==audioGeneration)throw Error('Audio setup canceled.');};
 const operation=(async()=>{
 try{
 await captureClock.refresh(api);current();
 for(const id of ['microphone','speaker'])if($(id).selectedOptions[0]?.dataset.unavailable)throw Error('A saved audio device is unavailable. Choose another device before starting.');
 owned.mic=await navigator.mediaDevices.getUserMedia({audio:{deviceId:$('microphone').value?{exact:$('microphone').value}:undefined,echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});current();
 owned.mic.getTracks().forEach(track=>{track.onended=()=>{
  if(generation!==audioGeneration)return;
  stop();releaseAudio();
  $('audioSetupStatus').textContent='Audio setup failed: Microphone disconnected. Choose an available device and restart conversation.';
  notice('Microphone disconnected. Choose an available device and restart conversation.');
 };});
 owned.ctx=new AudioContext();await owned.ctx.resume();current();
 if(owned.ctx.setSinkId&&$('speaker').value){await owned.ctx.setSinkId($('speaker').value);current();}
 await owned.ctx.audioWorklet.addModule('/static/audio-worklet.js');current();
 owned.worklet=new AudioWorkletNode(owned.ctx,'iago-audio');
 owned.ctx.createMediaStreamSource(owned.mic).connect(owned.worklet);owned.worklet.connect(owned.ctx.destination);
 owned.worklet.port.onmessage=({data:m})=>{
  if(generation!==audioGeneration||audio!==owned.audio)return;
  if(m.type==='capture'&&mode==='conversation'&&audio?.readyState===1){if(audio.bufferedAmount>12000){stop();notice('Audio connection is too slow; restart conversation.');return;}audio.send(m.pcm.buffer);}
  if(m.type==='local_stop'){clearHeard();send({type:'stop',generation:m.generation});}
  if(m.type==='commit'&&mode==='conversation'&&audio?.readyState===1){const marker={type:'commit'},timing=audioTiming(captureClock,owned.ctx,m.start,m.end);if(timing){marker.capture_start=timing.capture_start;marker.capture_end=timing.capture_end;marker.capture_clock_uncertainty=timing.clock_uncertainty;}audio.send(JSON.stringify(marker));}
  if(m.type==='speech_start'){const timing=audioTiming(captureClock,owned.ctx,m.at);send(timing?{type:'speech_start',captured:timing.capture_start,clock_uncertainty:timing.clock_uncertainty}:{type:'speech_start',timing_unknown:true});}
  if(m.type==='played')send(m);
  if(m.type==='overflow')notice('Playback buffering exceeded its limit; speech stopped.');
  if(m.type==='invalid_audio')notice('Invalid playback audio received; speech stopped.');
  if(m.type==='consumed'){const timer=setTimeout(()=>{heardTimers.delete(timer);send({type:'heard',epoch:m.epoch,segment:m.segment});},1000*((owned.ctx.outputLatency||.1)+(owned.ctx.baseLatency||.01)+.05));heardTimers.add(timer);}
 };

 owned.audio=new WebSocket(`${location.origin.replace('http','ws')}/audio`);
 owned.audio.onopen=()=>{if(generation===audioGeneration)owned.audio.send(token);};
 owned.audio.onclose=()=>{if(generation===audioGeneration){stop();releaseAudio();$('audioSetupStatus').textContent='Audio setup failed: microphone connection closed. Restart conversation.';notice('Microphone connection closed. Restart conversation.');}};
 await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error('Microphone channel did not become ready.')),5000);
  const failed=()=>{clearTimeout(timer);reject(Error('Microphone channel failed.'));};
  owned.audio.onmessage=event=>{try{if(JSON.parse(event.data).type==='audio_ready'){clearTimeout(timer);resolve();}}catch{failed();}};
  owned.audio.addEventListener('error',failed,{once:true});owned.audio.addEventListener('close',failed,{once:true});
 });current();
 await enumerate();current();
 ({mic,ctx,worklet,audio}=owned);pendingAudio=null;settingsAudio();$('audioSetupStatus').textContent='Microphone and speaker are ready.';
 }catch(error){closeAudioResources(owned);if(pendingAudio===owned)pendingAudio=null;if(generation===audioGeneration)$('audioSetupStatus').textContent=`Audio setup failed: ${error.message}`;throw error;}
 })();
 audioSetup=operation;
 try{return await operation;}finally{if(audioSetup===operation)audioSetup=null;}
}

function settingsAudio(){if(deployment!=='desktop')send({type:'audio_settings',muted:$('mute').checked,patient:$('patient').checked,volume:Number($('volume').value)});worklet?.port.postMessage({type:'settings',recording:mode==='conversation',muted:$('mute').checked,patient:$('patient').checked,volume:Number($('volume').value)});}
async function setMode(next){
 const request=++modeRequest;
 try{
  if(next==='idle'){stop();releaseAudio();for(const id of [...sources.keys()])await stopSource(id);}
  if(next==='conversation'&&deployment==='desktop')await setupAudio();
  if(next==='aware'&&deployment==='desktop'){try{await setupAudio();}catch(error){if(request===modeRequest)notice('Awareness can run, but microphone setup is needed for greetings.');}}
  if(request!==modeRequest)return;
  send({type:'mode',mode:next});notice(next==='conversation'?'Checking speech connections…':'Updating session…');
 }catch(error){if(request===modeRequest)notice(error.message);}
}

function connect(){
 if(!token){notice('Launch Iago with its local launcher to authorize this page.');return;}
 control=new WebSocket(`${location.origin.replace('http','ws')}/control`);
 control.onopen=()=>control.send(token);
 control.onclose=()=>{captureClock.reset();window.dispatchEvent(new Event('iago-visual-change'));stop();modeRequest++;releaseAudio();mode='idle';settingsAudio();for(const s of sources.values())s.stream?.getTracks().forEach(t=>t.stop());notice('Local session disconnected. Relaunch or reload to reconnect.');};
 control.onmessage=({data})=>{const m=JSON.parse(data);
  if(m.type==='robot_connection')notice(m.status==='connected_idle'?'Robot reconnected in Idle. Choose Aware or Conversation.':`Robot connection: ${m.status}`);
  if(m.type==='robot_motion')$('robotMotion').checked=m.enabled;
  if(m.type==='robot_camera'){$('robotCamera').dataset.enabled=String(m.enabled);$('robotCamera').textContent=m.enabled?'Reachy camera off':'Reachy camera on';refresh();}
  if(m.type==='heartbeat'){lastHeartbeat=performance.now();worklet?.port.postMessage({type:'heartbeat'});}
  if(m.type==='ready'){deployment=m.deployment||'desktop';$('projectFolderLabel').textContent=deployment==='reachy_local'?'Folder on the robot':'Folder on this PC';$('projectRoot').placeholder=deployment==='reachy_local'?'/home/reachy/projects/my-project':'C:\\Projects\\my-project';$('robotMotionPanel').hidden=deployment==='desktop';$('robotMotion').checked=!!m.robot_motion_enabled;$('robotCamera').hidden=deployment==='desktop';$('robotLookNow').hidden=deployment==='desktop';$('robotCamera').dataset.enabled=String(m.robot_camera_enabled!==false);$('robotCamera').textContent=m.robot_camera_enabled===false?'Reachy camera on':'Reachy camera off';if(m.audio_settings){$('mute').checked=m.audio_settings.muted;$('patient').checked=m.audio_settings.patient;$('volume').value=m.audio_settings.volume;}notice('Ready. Start a conversation or enable local awareness.');refresh();}
  if(m.type==='state'){if(m.mode!=='conversation')window.dispatchEvent(new Event('iago-history-unselected'));refreshVoice();mode=m.mode;$('state').textContent=`${mode} · ${m.voice}`;settingsAudio();notice($('audioSetupStatus').textContent.startsWith('Audio setup failed')?$('audioSetupStatus').textContent:m.voice_reason||'');}
  if(m.type==='stop'){clearHeard();worklet?.port.postMessage({type:'remote_stop'});}
  if(m.type==='authorize')worklet?.port.postMessage(m);
  if(m.type==='audio'){const binary=atob(m.pcm),buf=new ArrayBuffer(binary.length),bytes=new Uint8Array(buf);for(let i=0;i<binary.length;i++)bytes[i]=binary.charCodeAt(i);worklet?.port.postMessage({...m,pcm:new Int16Array(buf)},[buf]);}
  if(m.type==='segment_end')worklet?.port.postMessage(m);
  if(m.type==='transcript'){const entry=turn('user',m.text);if(m.gesture)entry.parentElement.dataset.gestureSlot=m.gesture.slot;activeAnswer=null;}
  if(m.type==='thinking'){speechFailed=false;activeAnswer=null;notice('Thinking…');}
  if(m.type==='memory_status')notice(m.status==='summarized'?'Older discussion condensed; recent turns retained.':'Could not condense older discussion. Recent history remains available.');
  if(m.type==='answer_partial'){if(!activeAnswer)activeAnswer=turn('assistant','');activeAnswer.textContent+=m.text;}
  if(m.type==='answer'){if(!activeAnswer)activeAnswer=turn('assistant',m.text);else activeAnswer.textContent=m.text;notice(speechFailed?'Speech unavailable. Answer shown as text.':'You can interrupt at any time.');}
  if(m.type==='transcript_partial')notice(`Hearing: ${m.text}`);
  if(m.type==='speech_error')speechFailed=true;
  if(m.type==='error'||m.type==='speech_error')notice(m.message);
  if(m.type==='error'&&spokenReferenceRequest&&!spokenReferenceToken){spokenReferenceRequest=null;$('spokenHistorySelection').textContent='Spoken reference was not selected. Please try again.';$('forgetSpokenHistory').hidden=true;}
  if(m.type==='voice_preview')$('voiceStatus').textContent=m.status==='failed'?'Voice preview failed. No other voice was substituted.':`${m.provider} preview: ${m.status==='queued'?'queued for playback':'preparing speech'}`;
  if(m.type==='active_question')$('thumbQuestion').textContent=m.text;
  if(m.type==='stop')$('thumbQuestion').textContent='No active question';
  if(m.type==='gesture_superseded'){for(const el of document.querySelectorAll('[data-gesture-slot]'))if(el.dataset.gestureSlot===m.slot)el.remove();}
  if(m.type==='confirmation')showConfirmation(m.proposal);
  if(m.type==='stop')$('confirmations').replaceChildren();
  if(m.type==='speech_reference'){
   if(m.status==='bound'&&m.token===spokenReferenceToken){spokenReferenceToken=null;spokenReferenceRequest=null;$('spokenHistorySelection').textContent='Selected image attached to this spoken turn.';$('forgetSpokenHistory').hidden=true;}
   else if(m.request===spokenReferenceRequest){
    if(m.status==='armed'){spokenReferenceToken=m.token;$('spokenHistorySelection').textContent=`Ready for your next spoken question: ${m.frame.source_label}. Start speaking after this message.`;}
    else{spokenReferenceToken=null;spokenReferenceRequest=null;$('spokenHistorySelection').textContent='';$('forgetSpokenHistory').hidden=true;}
   }
  }
  if(m.type==='evidence_cleared')window.dispatchEvent(new Event('iago-visual-change'));
  if(m.type==='evidence')showEvidence(m.frames);
 };
}
function sourceOptions(){const chosen=$('source').value;$('source').replaceChildren(new Option('No selected source',''));for(const s of sources.values())$('source').add(new Option(`${s.kind}: ${s.label}`,s.id));if(sources.has(chosen))$('source').value=chosen;else if(sources.size===1)$('source').value=sources.keys().next().value;}
const startingSources=new Set();
async function startSource(kind){
 if(mode==='idle'){notice('Enable Aware or start a conversation before sharing.');return;}
 if(startingSources.has(kind))return;
 startingSources.add(kind);
 const request=modeRequest;
 let stream,source;
 const valid=()=>request===modeRequest&&mode!=='idle'&&control?.readyState===1;
 try{
  const existing=[...sources.values()].find(s=>s.kind===kind);if(existing){await stopSource(existing.id);return;}
  stream=kind==='screen'?await navigator.mediaDevices.getDisplayMedia({video:true,audio:false}):await navigator.mediaDevices.getUserMedia({video:{deviceId:$('cameraDevice').value?{exact:$('cameraDevice').value}:undefined,width:{ideal:1920},height:{ideal:1080}},audio:false});
  if(!valid())throw new Error('Capture start canceled.');
  stream.getAudioTracks().forEach(t=>{t.stop();stream.removeTrack(t);});
  source=await post('/api/source',{kind,label:Array.from(stream.getVideoTracks()[0].label||kind).slice(0,120).join('')});source.stream=stream;sources.set(source.id,source);
  if(!valid())throw new Error('Capture start canceled.');
  const figure=document.createElement('figure'),video=document.createElement('video'),caption=document.createElement('figcaption'),off=document.createElement('button');video.autoplay=true;video.muted=true;video.playsInline=true;video.srcObject=stream;off.textContent='Stop';off.onclick=()=>stopSource(source.id);caption.append(document.createTextNode(`${kind}: ${source.label}`),off);figure.append(video,caption);source.figure=figure;$('previews').querySelector('.empty')?.remove();$('previews').append(figure);await video.play();
  if(!valid()||!sources.has(source.id)||stream.getVideoTracks()[0].readyState!=='live')throw new Error('Capture start canceled.');
  source.captureStatus=document.createElement('p');source.captureStatus.className='muted';figure.append(source.captureStatus);source.video=video;source.canvas=document.createElement('canvas');source.busy=false;source.timer=setInterval(()=>capture(source),1000);source.detectorTimer=kind==='camera'?setInterval(()=>detect(source),100):null;stream.getVideoTracks()[0].onended=()=>stopSource(source.id);sourceOptions();capture(source);
 }catch(e){
  stream?.getTracks().forEach(t=>{t.onended=null;t.stop();});
  if(source)await stopSource(source.id);
  if(request===modeRequest)notice(`${kind}: ${e.message}`);
 }finally{startingSources.delete(kind);}
}
async function capture(source){return captureFrame(source,sources,api,notice,captureClock);}
$('robotLookNow').onclick=async()=>{
 const request=modeRequest,visualVersion=visualClearVersion;
 const valid=()=>request===modeRequest&&visualVersion===visualClearVersion&&mode!=='idle';
 if(!valid()){notice('Enable Aware or Conversation before capture.');return;}
 $('robotLookNow').disabled=true;
 try{
  const frame=await post('/api/capture',{});
  if(!valid())return;
  window.dispatchEvent(new CustomEvent('iago-history-selected',{detail:{id:frame.id,label:`${frame.source_label} · new capture`}}));
  notice('Reachy image selected for your next typed question.');refresh();
 }catch(error){if(valid())notice(error.message);}
 finally{$('robotLookNow').disabled=false;}
};
$('lookNow').onclick=async()=>{
 const source=sources.get($('source').value);
 if(!source?.video||!['camera','screen'].includes(source.kind)){notice('Select a live PC camera or screen for Look now.');return;}
 const request=modeRequest,visualVersion=visualClearVersion,generation=source.generation;
 const valid=()=>request===modeRequest&&visualVersion===visualClearVersion&&sources.get(source.id)===source&&source.generation===generation&&mode!=='idle';
 $('lookNow').disabled=true;
 try{
  const deadline=performance.now()+2000;
  while(source.busy&&valid()&&performance.now()<deadline)await new Promise(resolve=>setTimeout(resolve,25));
  if(!valid())return;
  const frame=await capture(source);
  if(!valid())return;
  if(!frame){notice('Fresh capture unavailable. Try Look now again when the source is ready.');return;}
  window.dispatchEvent(new CustomEvent('iago-history-selected',{detail:{id:frame.id,label:`${source.label} · fresh capture`}}));
  notice('Fresh image selected for your next typed question.');refresh();
 }catch(error){if(valid())notice(error.message);}
 finally{$('lookNow').disabled=false;}
};
async function stopSource(id){const source=sources.get(id);if(!source)return;sources.delete(id);source.captureAbort?.abort();source.detectorAbort?.abort();clearInterval(source.timer);clearInterval(source.detectorTimer);source.stream?.getTracks().forEach(t=>{t.onended=null;t.stop();});source.figure?.remove();sourceOptions();try{await post('/api/visual',{action:'off',source:id});}catch(e){notice(e.message);}refresh();}
let refreshVersion=0,refreshAbort;
const recentExpiryTimers=new Set();
function clearRecentImages(){
 refreshVersion++;refreshAbort?.abort();
 for(const timer of recentExpiryTimers)clearTimeout(timer);recentExpiryTimers.clear();
 for(const url of objectURLs)URL.revokeObjectURL(url);
 objectURLs.clear();$('history').replaceChildren();
}
window.addEventListener('iago-visual-change',clearRecentImages);
window.addEventListener('iago-visual-pin-change',()=>{clearRecentImages();refresh();});
window.addEventListener('pagehide',clearRecentImages);
$('end').addEventListener('click',clearRecentImages);
async function refresh(){const version=++refreshVersion;refreshAbort?.abort();const pending=new AbortController();refreshAbort=pending;try{const data=await(await api('/api/status',{signal:pending.signal})).json();if(version!==refreshVersion)return;perceptionEnabled=data.perception.enabled;$('thumbEnabled').checked=data.thumbs.enabled;$('thumbFeedback').textContent=data.thumbs.feedback.at(-1)?.reason||'';if(!data.thumbs.question)$('thumbQuestion').textContent='No active question';$('perception').textContent=data.perception.error?`Perception unavailable: ${data.perception.error}`:data.perception.latest?`${data.perception.latest.effective_fps.toFixed(1)} analyzed fps - ${data.perception.latest.objects.map(o=>o.label).join(', ')||'No supported objects'} - ${data.perception.latest.hands.map(h=>h.gesture).join(', ')||'No hands'}`:perceptionEnabled?'Local perception ready for camera frames':'Local perception disabled';$('diagnostics').textContent=JSON.stringify({brain:data.settings.brain_model,voice:data.settings.tts_provider,limits:data.provider_limits,storage:data.storage,resources:data.resources,perception_health:data.perception.worker,detector_browser:detectorSnapshot(),control_delivery:controlDeliverySnapshot(),project_timings:data.project_timings,response_timings:data.response_timings,costs:data.costs},null,2);$('storage').textContent=`${data.storage.frames} frames · ${(data.storage.rolling_bytes/1048576).toFixed(1)} MiB rolling · ${data.storage.pins} pins · ${data.storage.earliest_rolling_capture==null?'No rolling images retained':`Retained ${new Date(data.storage.earliest_rolling_capture*1000).toLocaleTimeString()} – ${new Date(data.storage.latest_rolling_capture*1000).toLocaleTimeString()} (gaps possible)`}`;for(const timer of recentExpiryTimers)clearTimeout(timer);recentExpiryTimers.clear();for(const url of objectURLs)URL.revokeObjectURL(url);objectURLs.clear();$('history').replaceChildren();for(const f of data.history.frames.slice(0,12)){const tile=document.createElement('div');tile.className='frame';const img=document.createElement('img');img.alt=`${f.source_label} at ${new Date(f.captured*1000).toLocaleTimeString()}`;const started=performance.now(),response=await api(`/api/frame/${f.id}?thumbnail=true`,{signal:pending.signal}),expiry=response.headers.get('X-Iago-Expires-In');if(expiry!=='pinned'&&(expiry==null||!Number.isFinite(Number(expiry))))throw Error('Image expiry unavailable');const blob=await response.blob();if(version!==refreshVersion)return;const remaining=expiry==='pinned'?null:Number(expiry)*1000-(performance.now()-started);if(remaining!==null&&remaining<=0)continue;const url=URL.createObjectURL(blob);objectURLs.add(url);img.src=url;const label=document.createElement('div');label.textContent=`${f.source_kind} · ${new Date(f.captured*1000).toLocaleTimeString()}`;const pin=document.createElement('button');pin.textContent=f.pin?'Unpin':'Keep';pin.onclick=async()=>{try{await post('/api/visual',{action:f.pin?'unpin':'pin',frame:f.id,label:'Reference'});refresh();}catch(e){notice(e.message);}};tile.append(img,label,pin);$('history').append(tile);if(remaining!==null){const timer=setTimeout(()=>{recentExpiryTimers.delete(timer);objectURLs.delete(url);URL.revokeObjectURL(url);tile.remove();},Math.min(remaining,2147483647));recentExpiryTimers.add(timer);}}}catch(e){if(version===refreshVersion&&e.name!=='AbortError')notice(e.message);}}
$('start').onclick=()=>setMode('conversation');$('aware').onclick=()=>setMode('aware');$('end').onclick=()=>setMode('idle');$('stop').onclick=stop;$('commit').onclick=()=>{if(deployment==='desktop')worklet?.port.postMessage({type:'finish'});else send({type:'commit'});};
for(const id of ['mute','patient','volume'])$(id).oninput=()=>{saveDevicePreferences();settingsAudio();};
for(const id of ['microphone','cameraDevice','speaker'])$(id).onchange=saveDevicePreferences;
$('camera').onclick=()=>startSource('camera');$('screen').onclick=()=>startSource('screen');
$('ask').onsubmit=e=>{e.preventDefault();if(mode!=='conversation'){notice('Start conversation first.');return;}const text=$('question').value;if(text.trim()){send({type:'user',text,source:$('source').value,frame:selectedHistory?.id,region:selectedHistory?.region});forgetHistorySelection();$('question').value='';}};
$('clear').onclick=async()=>{for(const source of sources.values()){source.captureAbort?.abort();source.detectorAbort?.abort();}try{const data=await post('/api/visual',{action:'clear'});for(const s of data.sources)if(sources.has(s.id))sources.get(s.id).generation=s.generation;refresh();}catch(e){notice(e.message);}};
let uploadPending=false;
async function upload(file){
 if(mode==='idle'){notice('Enable Aware or Conversation before adding an image.');return;}
 if(uploadPending){notice('An image is already being added. Please wait.');return;}
 uploadPending=true;
 const request=modeRequest,visualVersion=visualClearVersion;
 let source;
 try{
  source=await post('/api/source',{kind:'upload',label:Array.from(file.name||'Pasted image').slice(0,120).join('')});
  sources.set(source.id,source);
  if(request!==modeRequest||visualVersion!==visualClearVersion||mode==='idle')throw new Error('Image upload canceled.');
  await api(`/api/frame/${source.id}/${source.generation}`,{method:'POST',headers:{'X-Captured-At':String(Date.now()/1000)},body:file});
  if(request!==modeRequest||visualVersion!==visualClearVersion||mode==='idle'||!sources.has(source.id))throw new Error('Image upload canceled.');
  sourceOptions();refresh();
 }catch(e){
  if(source)await stopSource(source.id);
  if(request===modeRequest)notice(e.message);
 }finally{uploadPending=false;}
}
$('upload').onchange=e=>{const file=e.target.files[0];e.target.value='';if(file)upload(file);};document.addEventListener('paste',e=>{const file=[...e.clipboardData.items].find(i=>i.type.startsWith('image/'))?.getAsFile();if(file)upload(file);});
setInterval(()=>{send({type:'heartbeat'});if(performance.now()-lastHeartbeat>1500)worklet?.port.postMessage({type:'remote_stop'});},250);
setInterval(()=>{if(control?.readyState===1){refresh();if(ctx&&mode==='conversation')captureClock.refresh(api).catch(()=>{});}},5000);
window.addEventListener('pagehide',()=>{stop();modeRequest++;releaseAudio();control?.close();});
let activeProject=null,projectViewVersion=0,projectRefreshRequest=0,documentRequest=0;
const showEvidence=evidenceView(api);
function showConfirmation(p){const card=document.createElement('div'),label=document.createElement('p'),payload=document.createElement('pre');card.className='panel';label.textContent=`Allow this action? ${p.tool} · account ${p.account}`;payload.textContent=JSON.stringify(p.payload,null,2);card.append(label,payload);for(const [text,approved] of [['Confirm this action',true],['Cancel',false]]){const button=document.createElement('button');button.textContent=text;button.onclick=()=>{send({type:'confirmation',operation_id:p.operation_id,binding:p.binding,approved});card.remove();};card.append(button);}$('confirmations').append(card);setTimeout(()=>card.remove(),Math.max(0,p.expires*1000-Date.now()));}
function projectError(error){
  const messages={
    project_removed_storage_cleanup_incomplete:'Project removed from the index. Storage cleanup is incomplete; use Retry storage cleanup after resolving disk access or space issues.',
    index_storage_cleanup_failed:'Storage cleanup could not finish. Check disk access and free space, then retry.'
  };
  notice(messages[error.message]||error.message);
}
$('compactProjects').onclick=async()=>{
  const button=$('compactProjects');button.disabled=true;
  try{await post('/api/projects',{action:'compact'});notice('Project index storage cleanup completed.');}
  catch(e){projectError(e);}finally{button.disabled=false;}
};
async function refreshProjects(){
  const request=++projectRefreshRequest;
  try{
    const data=await(await api('/api/status')).json();
    if(request!==projectRefreshRequest)return;
    if(activeProject!==data.active_project){projectViewVersion++;documentRequest++;$('documentResults').replaceChildren();}
    const expanded=new Set([...$('projects').children].filter(row=>row.querySelector('details')?.open).map(row=>row.dataset.projectId));
    activeProject=data.active_project;$('projects').replaceChildren();
    for(const p of data.projects){
      const row=document.createElement('div'),label=document.createElement('p');row.dataset.project=p.name;row.dataset.projectId=p.id;
      label.textContent=`${p.id===activeProject?'● ':''}${p.name} · ${p.indexing?'Indexing':p.error?`Indexing failed: ${p.error}`:JSON.stringify(p.coverage||p.statuses||{})}`;row.append(label);
      for(const [text,action] of [['Use','activate'],['Reindex','refresh'],['Remove index','remove']]){
        const button=document.createElement('button');button.textContent=text;
        button.onclick=async()=>{
          projectViewVersion++;documentRequest++;projectRefreshRequest++;$('documentResults').replaceChildren();
          try{await post('/api/projects',{action,project:p.id});}catch(e){projectError(e);}
          await refreshProjects();
        };row.append(button);
      }
      const coverage=document.createElement('details'),title=document.createElement('summary');title.textContent=p.files_truncated?`File coverage · showing ${(p.files||[]).length} of ${p.files_total} files`:'File coverage';coverage.open=expanded.has(p.id);coverage.append(title);
      for(const file of p.files||[]){const line=document.createElement('p');line.textContent=`${file.relative}: ${file.status}`;coverage.append(line);}row.append(coverage);$('projects').append(row);
    }
  }catch(e){if(request===projectRefreshRequest)notice(e.message);}
}
async function refreshNotes(offset=0){try{const data=await(await api(`/api/notes?offset=${offset}`)).json();if(!offset)$('notes').replaceChildren();$('notesMore')?.remove();for(const n of data.notes){const row=document.createElement('div'),text=document.createElement('p'),del=document.createElement('button'),download=document.createElement('button');text.textContent=n.text;del.textContent='Delete';del.onclick=async()=>{await post('/api/notes',{action:'delete',id:n.id});refreshNotes();};download.textContent='Export text';download.onclick=async()=>{try{const response=await api(`/api/notes/${n.id}/export`),url=URL.createObjectURL(await response.blob()),a=document.createElement('a');a.href=url;a.download=`iago-note-${n.id}.txt`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){notice(e.message);}};row.append(text,download,del);$('notes').append(row);}if(data.next_offset!==null){const more=document.createElement('button');more.id='notesMore';more.textContent='More saved ideas';more.onclick=()=>refreshNotes(data.next_offset);$('notes').append(more);}}catch(e){notice(e.message);}}
$('addProject').onsubmit=async e=>{e.preventDefault();try{await post('/api/projects',{action:'add',name:$('projectName').value,root:$('projectRoot').value});await refreshProjects();}catch(err){notice(err.message);}};
$('searchProject').onsubmit=async e=>{
  e.preventDefault();if(!activeProject){notice('Choose a project with Use first.');return;}
  const project=activeProject,version=projectViewVersion,request=++documentRequest;
  try{
    const data=await post('/api/projects',{action:'search',project,query:$('documentQuery').value});
    if(version!==projectViewVersion||project!==activeProject||request!==documentRequest)return;
    $('documentResults').replaceChildren();
    for(const row of data.passages){const citation=document.createElement('p'),text=document.createElement('blockquote');citation.textContent=`${row.project} · ${row.path} · ${row.locator_kind||'locator'} ${row.locator} · ${row.revision.slice(0,12)}`;text.textContent=row.text;$('documentResults').append(citation,text);}
    if(!data.passages.length)$('documentResults').textContent='No current matching passages. Check indexing coverage or narrow the query.';
  }catch(err){if(request===documentRequest)notice(err.message);}
};
$('saveNote').onsubmit=async e=>{e.preventDefault();try{await post('/api/notes',{action:'save',text:$('noteText').value});$('noteText').value='';refreshNotes();}catch(err){notice(err.message);}};
$('source').onchange=()=>send({type:'source',source:$('source').value});
setInterval(refreshProjects,5000);if(token){refreshProjects();refreshNotes();}
async function refreshIntegrations(){try{const data=await(await api('/api/integrations')).json();$('integrations').replaceChildren();for(const m of data.modules){const label=document.createElement('label'),toggle=document.createElement('input');toggle.type='checkbox';toggle.checked=m.enabled;toggle.setAttribute('aria-label',`Enable module ${m.module} / ${m.account}`);toggle.onchange=async()=>{toggle.disabled=true;try{await post('/api/integrations',{action:'module_enabled',module:m.module,account:m.account,enabled:toggle.checked});}catch(error){notice(error.message);}finally{await refreshIntegrations();}};label.append(toggle,document.createTextNode(`${m.module} / ${m.account} · ${m.restart_required?'Restart application to connect':m.enabled?'Enabled':'Disabled'}`));$('integrations').append(label);}for(const t of data.tools){const label=document.createElement('label'),box=document.createElement('input');box.type='checkbox';box.checked=t.enabled;box.onchange=async()=>{try{await post('/api/integrations',{action:'tool_enabled',tool:t.key,enabled:box.checked});refreshIntegrations();}catch(e){notice(e.message);}};label.append(box,document.createTextNode(` ${t.key} · ${t.action} `));const permission=document.createElement('select');permission.setAttribute('aria-label',`Permission for ${t.key}`);for(const [value,text] of [['deny','Deny'],['confirm','Confirm each action'],['allow','Allow within configured limits']]){const option=document.createElement('option');option.value=value;option.textContent=text;permission.append(option);}permission.value=t.policy;permission.onchange=async()=>{permission.disabled=true;try{await post('/api/integrations',{action:'tool_policy',tool:t.key,policy:permission.value});}catch(error){notice(error.message);}finally{await refreshIntegrations();}};label.append(permission);$('integrations').append(label);}for(const c of data.connections.filter(c=>!['visual','documents','notes','workflows'].includes(c.module))){const row=document.createElement('p'),off=document.createElement('button');row.textContent=`${c.module} / ${c.account} · ${c.enabled?'Connected':'Disconnected'} `;off.textContent='Disconnect';off.disabled=!c.enabled;off.onclick=async()=>{await post('/api/integrations',{action:'disconnect',module:c.module,account:c.account});refreshIntegrations();};row.append(off);$('integrations').append(row);}for(const diagnostic of data.diagnostics){const p=document.createElement('p');const status=diagnostic.status==='local_credentials_cleared_remote_revocation_not_configured'?'Local credentials cleared. Remote revocation is not configured.':diagnostic.status;p.textContent=`${diagnostic.module||''} ${diagnostic.account||''} · ${status||''}`;$('integrations').append(p);}for(const w of data.workflows){const p=document.createElement('label'),toggle=document.createElement('input');toggle.type='checkbox';toggle.checked=w.enabled;toggle.setAttribute('aria-label',`Enable workflow ${w.id}`);toggle.onchange=async()=>{try{await post('/api/integrations',{action:'skill_enabled',skill:w.id,enabled:toggle.checked});}catch(error){notice(error.message);}finally{await refreshIntegrations();}};p.append(toggle,document.createTextNode(`${w.description} · ${w.available?'Available':`Missing: ${w.missing.join(', ')}`}`));$('integrations').append(p);}}catch(e){notice(e.message);}}
if(token)refreshIntegrations();
loadDevicePreferences();enumerate().catch(()=>{});navigator.mediaDevices.addEventListener('devicechange',()=>enumerate().catch(()=>{}));connect();

export function detectorSnapshot(){
 const now=performance.now();
 return {enabled:perceptionEnabled,sources:[...sources.values()].filter(source=>source.kind==='camera').map(source=>({
  source:source.id,generation:source.generation,busy:!!source.detectBusy,
  width:source.video?.videoWidth||0,height:source.video?.videoHeight||0,
  ready_state:source.video?.readyState??null,track_state:source.stream?.getVideoTracks()[0]?.readyState??null,
  phase:source.detectorHealth?.phase||'not_started',
  phase_seconds:source.detectorHealth?Math.max(0,(now-source.detectorHealth.phaseAt)/1000):null,
  ticks:source.detectorHealth?.ticks||0,busy_skips:source.detectorHealth?.busySkips||0,
  submitted:source.detectorHealth?.submitted||0,completed:source.detectorHealth?.completed||0,
  errors:source.detectorHealth?.errors||0,timeouts:source.detectorHealth?.timeouts||0
 }))};
}
async function detect(source){
 const health=source.detectorHealth||(source.detectorHealth={phase:'not_started',phaseAt:performance.now(),ticks:0,busySkips:0,submitted:0,completed:0,errors:0,timeouts:0});
 health.ticks++;
 const phase=value=>{if(health.phase!==value){health.phase=value;health.phaseAt=performance.now();}};
 if(!sources.has(source.id)){phase('removed');return;}
 if(!perceptionEnabled){phase('disabled');return;}
 if(source.detectBusy){health.busySkips++;return;}
 if(!source.video.videoWidth||!source.video.videoHeight){phase('video_unavailable');return;}
 source.detectBusy=true;
 const generation=source.generation,controller=new AbortController();source.detectorAbort=controller;
 const valid=()=>sources.get(source.id)===source&&source.generation===generation;
 const deadline=setTimeout(()=>{if(valid())health.timeouts++;controller.abort();},1000);
 try{
  phase('clock');
  if(!captureClock.map()||captureClock.now()-captureClock.sample.received>5)await captureClock.refresh(api);
  if(!valid()||controller.signal.aborted)return;
  const clockGeneration=captureClock.generation;
  if(source.detectorSequenceGeneration!==generation){source.detectorSequenceGeneration=generation;source.detectorSequence=0;}
  const sequence=++source.detectorSequence;
  phase('drawing');
  const v=source.video,c=source.detectorCanvas||(source.detectorCanvas=document.createElement('canvas'));
  const ratio=Math.min(1,640/Math.max(v.videoWidth,v.videoHeight));
  c.width=Math.max(1,Math.round(v.videoWidth*ratio));c.height=Math.max(1,Math.round(v.videoHeight*ratio));
  c.getContext('2d').drawImage(v,0,0,c.width,c.height);
  const observed=captureClock.now(),mapped=captureClock.map(observed);
  if(!mapped)throw Error('Detector clock unavailable; waiting for synchronization.');
  const at=mapped.time;
  phase('encoding');
  const blob=await new Promise(resolve=>c.toBlob(resolve,'image/jpeg',.8));
  if(blob&&valid()&&!controller.signal.aborted&&captureClock.generation===clockGeneration){
   phase('uploading');health.submitted++;
   await api(`/api/perception/${source.id}/${generation}`,{method:'POST',headers:{'X-Captured-At':String(at),'X-Frame-Sequence':String(sequence),'X-Clock-Owner':mapped.owner,'X-Source-Monotonic':String(observed),'X-Clock-Uncertainty':String(mapped.uncertainty)},body:blob,signal:controller.signal});
   if(valid()&&!controller.signal.aborted)health.completed++;
  }
 }catch(e){if(valid()&&!controller.signal.aborted){health.errors++;notice(e.message);}}finally{clearTimeout(deadline);if(source.detectorAbort===controller)source.detectorAbort=null;source.detectBusy=false;phase('idle');}
}

$('thumbEnabled').onchange=async()=>{try{await post('/api/thumbs',{enabled:$('thumbEnabled').checked});}catch(e){notice(e.message);}};

let behaviorDraft;
async function loadBehaviors(){
 try{
  const data=await (await api('/api/behaviors')).json();behaviorDraft=data.configuration;
  const root=$('behaviorFields');root.replaceChildren();
  function field(parent,object,key,label,choices){
   const row=document.createElement('label'),input=document.createElement(choices?'select':'input');
   row.append(document.createTextNode(label+' '));
   if(choices){for(const value of choices){const option=document.createElement('option');option.value=value;option.textContent=value;input.append(option);}input.value=object[key];}
   else if(typeof object[key]==='boolean'){input.type='checkbox';input.checked=object[key];}
   else {input.type=typeof object[key]==='number'?'number':'text';if(input.type==='number')input.step='any';input.value=Array.isArray(object[key])?object[key].join(', '):object[key];}
   input.onchange=()=>{object[key]=input.type==='checkbox'?input.checked:input.type==='number'?Number(input.value):Array.isArray(object[key])?input.value.split(',').map(x=>x.trim()).filter(Boolean):input.value;};
   row.append(input);parent.append(row);
  }
  field(root,behaviorDraft,'spontaneous','Allow spontaneous interaction');
  field(root,behaviorDraft,'presence_confirmation','Confirm presence (seconds)');field(root,behaviorDraft,'presence_absence','Confirm absence (seconds)');field(root,behaviorDraft,'presence_rearm','Absence before a new entry greeting (seconds)');
  field(root,behaviorDraft,'quiet_start','Quiet from (HH:MM)');field(root,behaviorDraft,'quiet_end','Quiet until (HH:MM)');
  field(root,behaviorDraft,'interruption_backoff','Pause after interruption (seconds)');field(root,behaviorDraft,'conversation_window','Wait for a reply (seconds)');
  for(const rule of behaviorDraft.rules){const group=document.createElement('fieldset'),title=document.createElement('legend');title.textContent=rule.id;group.append(title);
   for(const [key,label] of Object.entries({enabled:'Enabled',event:'Event',source:'Source filter',label:'Object label',confidence:'Minimum confidence',persistence:'Stable for (seconds)',cooldown:'Global cooldown (seconds)',track_cooldown:'Per-person cooldown (seconds)',expiry:'Expires after (seconds)',modes:'Modes (aware, conversation)',prompt:'Prompt',allow_startup:'Allow startup presence'}))field(group,rule,key,label);
   field(group,rule,'action','Action',['greet','log','bookmark']);
   const test=document.createElement('button');test.type='button';test.textContent='Run test event (may speak)';test.onclick=async()=>{try{const source=$('source').value;if(!source)throw Error('Select an active camera source first.');const result=await post('/api/behaviors/test',{rule:rule.id,source});notice('Synthetic test event: '+result.decision);}catch(e){notice(e.message);}};group.append(test);root.append(group);
  }
  const log=$('behaviorEvents');log.replaceChildren();for(const event of data.events.slice(-20)){const row=document.createElement('p');row.textContent=`${event.kind}: ${event.decision}`;log.append(row);}
 }catch(e){notice(e.message);}
}
$('behaviorPanel').addEventListener('toggle',()=>{if($('behaviorPanel').open)loadBehaviors();});
$('behaviorSettings').onsubmit=async e=>{e.preventDefault();try{await post('/api/behaviors',behaviorDraft);notice('Behavior settings saved.');await loadBehaviors();}catch(error){notice(error.message);}};

$('robotCamera').onclick=()=>send({type:'robot_camera',enabled:$('robotCamera').dataset.enabled!=='true'});

$('robotMotion').onchange=()=>send({type:'robot_motion',enabled:$('robotMotion').checked});
$('robotCue').onclick=()=>send({type:'robot_cue'});

$('robotReconnect').onclick=()=>send({type:'robot_reconnect'});

async function refreshTranscripts(offset=0){
  try {
    const data=await(await api(`/api/transcripts?offset=${offset}`)).json();
    $('saveTranscripts').checked=data.enabled;
    $('transcriptStatus').textContent=`${data.entries}/${data.max_entries} saved records · ${(data.payload_bytes/1048576).toFixed(2)}/${(data.max_payload_bytes/1048576).toFixed(2)} MiB text and metadata · ${data.pending} queued · ${data.dropped} unsaved${data.at_capacity?' · Storage limit reached':''}${data.error?' · '+data.error:''}`;
    if(!offset)$('transcripts').replaceChildren();
    $('transcriptsMore')?.remove();
    for(const session of data.sessions){
      const row=document.createElement('p'),download=document.createElement('button'),del=document.createElement('button');
      row.textContent=`${new Date(session.started*1000).toLocaleString()} · ${session.entries} entries `;
      download.textContent='Export text';
      download.onclick=async()=>{try{const response=await api(`/api/transcripts/${session.session}/export`),url=URL.createObjectURL(await response.blob()),a=document.createElement('a');a.href=url;a.download=`iago-transcript-${session.session}.txt`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){notice(e.message);}};
      del.textContent='Delete';del.onclick=async()=>{try{await post('/api/transcripts',{action:'delete',session:session.session});await refreshTranscripts();}catch(e){notice(e.message);}};
      const structured=document.createElement('button');
      structured.textContent='Export JSON';
      structured.onclick=async()=>{try{const response=await api(`/api/transcripts/${session.session}/export?format=json`),url=URL.createObjectURL(await response.blob()),a=document.createElement('a');a.href=url;a.download=`iago-transcript-${session.session}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){notice(e.message);}};
      row.append(download,structured,del);$('transcripts').append(row);
    }
    if(data.next_offset!==null){const more=document.createElement('button');more.id='transcriptsMore';more.textContent='More sessions';more.onclick=()=>refreshTranscripts(data.next_offset);$('transcripts').append(more);}
  }catch(e){notice(e.message);}
}
$('saveTranscripts').onchange=async()=>{try{await post('/api/transcripts',{action:'enabled',enabled:$('saveTranscripts').checked});}catch(e){notice(e.message);}await refreshTranscripts();};
$('refreshTranscripts').onclick=()=>refreshTranscripts();
$('deleteTranscripts').onclick=async()=>{try{await post('/api/transcripts',{action:'delete_all'});await refreshTranscripts();}catch(e){notice(e.message);}};
if(token)refreshTranscripts();

let operationRefreshVersion=0;
async function refreshOperations(){
 const version=++operationRefreshVersion;
 try{
  const data=await(await api('/api/operations')).json();
  if(version!==operationRefreshVersion)return;
  $('operations').replaceChildren();
  const storage=document.createElement('p');
  storage.textContent=`Operation storage: ${data.storage.bytes} / ${data.storage.max_bytes} bytes · ${data.storage.retention_days} days for completed records · ${data.storage.unresolved} unresolved. ${data.storage.accepting_new_writes?'':'Storage limit reached: new writes are blocked.'}`;
  $('operationStorage').replaceChildren(storage);
  if(!data.operations.length)$('operations').textContent='No recorded operations.';
  for(const op of data.operations){
   const row=document.createElement('div'),label=document.createElement('p');
   row.dataset.operation=op.id;
   label.textContent=`${op.tool} · ${op.id} · ${op.status} · ${new Date(op.updated*1000).toLocaleString()}`;
   row.append(label);
   if(op.provider_cancel_available){
    const cancel=document.createElement('button');cancel.textContent='Request provider cancellation';
    cancel.onclick=async()=>{cancel.disabled=true;try{
     let result=await post('/api/operations/cancel',{operation_id:op.id});
     if(result.confirmation){const p=result.confirmation;
      if(!window.confirm(`Approve cancellation request?\nTool: ${p.tool}\nAccount: ${p.account}\nPayload: ${JSON.stringify(p.payload)}`)){
       await post('/api/operations',{action:'cancel_undispatched',operation_id:p.operation_id});return;
      }
      result=await post('/api/operations/cancel',{operation_id:op.id,request_id:p.operation_id,binding:p.binding});
     }
     const receipt=JSON.stringify(result.request.result||{});
     notice(`Cancellation request: ${result.request.status}. Provider response: ${receipt.slice(0,1000)}${receipt.length>1000?'…':''}. ${result.original_outcome}`);
    }catch(error){notice(error.message);}finally{await refreshOperations();}};
    row.append(cancel);
   }
   const queued=['proposed','awaiting-confirmation','queued'].includes(op.status);
   if(queued||op.status==='uncertain'){
    const button=document.createElement('button');
    button.textContent=queued?'Cancel before dispatch':'Check outcome';
    button.onclick=async()=>{
     button.disabled=true;
     try{
      const result=await post('/api/operations',{action:queued?'cancel_undispatched':'reconcile',operation_id:op.id});
      if(queued&&!result.canceled)notice('The operation was already dispatched. Cancellation was not confirmed.');
     }catch(error){notice(error.message);}finally{await refreshOperations();}
    };
    row.append(button);
   }
   $('operations').append(row);
  }
 }catch(error){notice(error.message);}
}
$('refreshOperations').onclick=refreshOperations;
if(token)refreshOperations();

let defaultPersonality='';
async function refreshPersonality(){try{const data=await(await api('/api/personality')).json();$('personalityText').value=data.instructions;defaultPersonality=data.default;}catch(error){notice(error.message);}}
$('personalityForm').onsubmit=async event=>{event.preventDefault();try{await post('/api/personality',{instructions:$('personalityText').value});$('personalityStatus').textContent='Saved for future replies.';}catch(error){$('personalityStatus').textContent=error.message;}};
$('resetPersonality').onclick=()=>{$('personalityText').value=defaultPersonality;$('personalityStatus').textContent='Default selected. Save to apply.';};
if(token)refreshPersonality();

$('voicePreview').onclick=()=>{if(mode!=='conversation'){notice('Start Conversation before previewing the active voice.');return;}send({type:'voice_preview'});};
async function refreshVoice(){try{const data=await(await api('/api/voice')).json();$('voiceProvider').value=data.selected;$('voiceStatus').textContent=data.active?`Active: ${data.active} · ${data.reason}`:'Voice is validated when the next conversation starts.';}catch(error){notice(error.message);}}
$('voiceProvider').onchange=async()=>{try{await post('/api/voice',{provider:$('voiceProvider').value});$('voiceStatus').textContent='Saved. Applies at the next conversation start.';}catch(error){$('voiceStatus').textContent=error.message;}};
if(token)refreshVoice();

$("exportResources").onclick=async()=>{try{const response=await api("/api/resources"),url=URL.createObjectURL(await response.blob()),link=document.createElement("a");link.href=url;link.download="iago-resource-samples.json";link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){notice(error.message);}};

async function refreshVoiceRecovery(){try{const data=await(await api("/api/voice")).json();$("voiceRecovery").textContent=Object.entries(data.speech_recovery||{}).filter(([,v])=>v.state!=="closed").map(([name,v])=>`${name}: ${v.state==="probe_in_progress"?"checking recovery":v.retry_after_seconds>0?`paused after failures; retry in ${Math.ceil(v.retry_after_seconds)}s`:"ready for a recovery attempt on the next speech request"}`).join(" · ");}catch{}}
setInterval(()=>{if(token&&$("voiceRecovery").closest("details").open)refreshVoiceRecovery();},5000);

$("exportProjectTimings").onclick=async()=>{try{const response=await api("/api/project-timings"),url=URL.createObjectURL(await response.blob()),link=document.createElement("a");link.href=url;link.download="iago-project-timings.json";link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){notice(error.message);}};

$("exportResponseTimings").onclick=async()=>{try{const response=await api("/api/response-timings"),url=URL.createObjectURL(await response.blob()),link=document.createElement("a");link.href=url;link.download="iago-response-timings.json";link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){notice(error.message);}};
$("exportSpeechActivity").onclick=async()=>{try{const response=await api("/api/speech-activity"),url=URL.createObjectURL(await response.blob()),link=document.createElement("a");link.href=url;link.download="iago-speech-activity.json";link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){notice(error.message);}};
