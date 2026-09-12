/* Real browser sink and local onset guard. No network/provider dependency for stopping. */
class IagoAudio extends AudioWorkletProcessor {
  constructor() {
    super(); this.queue=[]; this.buffered=0; this.epoch=-1; this.stopGeneration=0;
    this.latched=true; this.lastHeartbeat=currentTime; this.sequence=-1;
    this.input=[]; this.inputPhase=0; this.recording=false; this.muted=false;
    this.volume=.8; this.threshold=.018; this.speaking=false; this.high=0; this.low=0;
    this.patient=false; this.hasSpeech=false; this.outputPhase=0; this.speechStarted=null;
    this.port.onmessage=({data:m})=>{
      if(m.type==='finish'&&this.recording&&!this.muted)this.finish();
      if(m.type==='heartbeat') this.lastHeartbeat=currentTime;
      if(m.type==='stop') this.stop(true);
      if(m.type==='remote_stop') {this.queue=[];this.buffered=0;this.latched=true;}
      if(m.type==='authorize'&&m.acknowledged_stop===this.stopGeneration&&m.epoch>this.epoch){
        this.queue=[];this.buffered=0;
        this.epoch=m.epoch;this.sequence=-1;this.latched=false;this.outputPhase=0;
      }
      if(m.type==='settings'){
        for(const key of ['recording','muted','patient'])if(key in m&&typeof m[key]!=='boolean')return;
        if('volume' in m&&(!Number.isFinite(m.volume)||m.volume<0||m.volume>1))return;
        const recording=m.recording??this.recording,muted=m.muted??this.muted;
        if(recording!==this.recording||muted!==this.muted){this.input=[];this.inputPhase=0;this.speechStarted=null;this.speaking=this.hasSpeech=false;this.high=this.low=0;}
        this.recording=recording;this.muted=muted;
        if('patient' in m)this.patient=m.patient;
        if('volume' in m)this.volume=m.volume;
      }
      if(m.type==='segment_end'&&m.epoch===this.epoch&&!this.latched){
        if(typeof m.segment!=='string'||!m.segment.length||m.segment.length>128){this.stop(true);this.port.postMessage({type:'invalid_audio'});return;}
        if(this.queue.length>=1024){this.stop(true);this.port.postMessage({type:'overflow'});return;}
        this.queue.push({end:m.segment});
      }
      if(m.type==='audio'&&!this.latched&&m.epoch===this.epoch&&m.sequence>this.sequence){
        if(!(m.pcm instanceof Int16Array)||!m.pcm.length){this.stop(true);this.port.postMessage({type:'invalid_audio'});return;}
        const pcm=m.pcm;const ratio=24000/sampleRate;const count=Math.max(0,Math.ceil((pcm.length-this.outputPhase)/ratio));
        if(this.queue.length>=1024||this.buffered+count>sampleRate*10){this.stop(true);this.port.postMessage({type:'overflow'});return;}
        const out=new Float32Array(count);
        for(let i=0;i<count;i++){const p=this.outputPhase+i*ratio,j=Math.floor(p);out[i]=pcm[Math.min(j,pcm.length-1)]/32768;}
        this.outputPhase+=count*ratio-pcm.length;
        this.sequence=m.sequence;this.queue.push({samples:out,index:0,sequence:m.sequence});this.buffered+=count;
      }
    };
  }
  stop(notify){
    this.queue=[];this.buffered=0;this.latched=true;this.stopGeneration++;
    if(notify)this.port.postMessage({type:'local_stop',generation:this.stopGeneration});
  }
  finish(end=currentTime){
    if(this.input.length){const packet=new Int16Array(this.input.splice(0));this.port.postMessage({type:'capture',pcm:packet},[packet.buffer]);}
    this.port.postMessage({type:'commit',start:this.speechStarted,end});this.speechStarted=null;this.hasSpeech=false;this.speaking=false;this.high=this.low=0;
  }
  process(inputs,outputs){
    const output=outputs[0][0];if(!output)return true;
    if(currentTime-this.lastHeartbeat>1&&!this.latched)this.stop(true);
    const input=inputs[0]?.[0];
    if(input&&this.recording&&!this.muted){
      let energy=0;for(const x of input)energy+=x*x;const rms=Math.sqrt(energy/input.length);
      const duration=input.length/sampleRate;
      if(rms>this.threshold){this.high+=duration;this.low=0;}
      else {this.low+=duration;this.high=0;}
      if(!this.speaking&&this.high>=.08){this.speaking=true;this.hasSpeech=true;this.speechStarted=currentTime+duration-this.high;this.stop(true);this.port.postMessage({type:'speech_start',at:this.speechStarted});}
      const finish=this.speaking&&this.low>=(this.patient?1.2:.7)&&this.hasSpeech;
      // Continuous processed capture; server commits locally owned turns only.
      const step=sampleRate/24000;
      for(let p=this.inputPhase;p<input.length;p+=step){this.input.push(Math.max(-32768,Math.min(32767,Math.round(input[Math.floor(p)]*32767))));this.inputPhase=p+step-input.length;}
      if(this.input.length>=480){const packet=new Int16Array(this.input.splice(0,480));this.port.postMessage({type:'capture',pcm:packet},[packet.buffer]);}
      if(finish)this.finish(currentTime+duration);
    }
    let at=0;
    while(at<output.length&&this.queue.length&&!this.latched){
      const head=this.queue[0];
      if(head.end){this.queue.shift();this.port.postMessage({type:'consumed',epoch:this.epoch,segment:head.end});continue;}
      const take=Math.min(output.length-at,head.samples.length-head.index);
      for(let j=0;j<take;j++)output[at+j]=head.samples[head.index+j]*this.volume;
      head.index+=take;at+=take;this.buffered-=take;
      if(head.index===head.samples.length){this.queue.shift();this.port.postMessage({type:'played',epoch:this.epoch,sequence:head.sequence});}
    }
    return true;
  }
}
registerProcessor('iago-audio',IagoAudio);
