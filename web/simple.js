"use strict";

const $ = (id) => document.getElementById(id);
const state = {file:null, token:"", ready:false, busy:false, preview:null, result:null, showOriginal:false};

function message(text, error=false){
  $("error").hidden = !error;
  $(error ? "error" : "status").textContent = text;
}
function controls(){
  $("detect-button").disabled = !state.file || !state.ready || state.busy;
  $("file-input").disabled = state.busy;
  $("detect-button").textContent = state.busy ? "识别中…" : "识别病斑";
}
async function api(path, body){
  const options={cache:"no-store",credentials:"same-origin",headers:{Accept:"application/json"}};
  if(body!==undefined){options.method="POST";options.headers["Content-Type"]="application/json";options.headers["X-CSRF-Token"]=state.token;options.body=JSON.stringify(body);}
  let response;
  try{response=await fetch(path,options);}catch{throw new Error("本地服务未连接，请重新打开工作台。");}
  const data=await response.json();
  if(!response.ok)throw new Error(data.error || "操作未完成，请重试。");
  return data;
}
function fileUrl(runId,path){
  if(typeof path!=="string"||path.split("/").some(part=>part===".."||part==="."))throw new Error("结果地址无效");
  return `/files/${encodeURIComponent(runId)}/${path.split("/").map(encodeURIComponent).join("/")}`;
}
function choose(files){
  if(state.busy)return;
  if(files.length!==1){message("每次请选择一张叶片照片。",true);return;}
  const file=files[0];
  if(!/\.(jpe?g|png|webp)$/i.test(file.name)||file.size===0||file.size>10*1024*1024){message("请选择 10 MB 以内的 JPG、PNG 或 WebP 图片。",true);return;}
  if(state.preview)URL.revokeObjectURL(state.preview);
  state.file=file;state.preview=URL.createObjectURL(file);state.result=null;state.showOriginal=false;
  $("file-label").textContent=file.name;
  $("result-image").src=state.preview;$("result-image").alt="待识别的叶片照片";$("result-image").hidden=false;
  $("placeholder").hidden=true;$("result-info").hidden=true;$("result-actions").hidden=true;
  message("照片已就绪，点击“识别病斑”。");controls();
}
function base64(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(",")[1]);reader.onerror=()=>reject(new Error("照片读取失败，请重新选择。"));reader.readAsDataURL(file);});}
function render(run){
  const item=run.cases?.[0];if(!item)throw new Error("没有得到图片结果，请重试。");
  state.result={original:fileUrl(run.id,item.original),annotated:fileUrl(run.id,item.annotated)};state.showOriginal=false;
  $("result-image").src=state.result.annotated;$("result-image").alt="疑似柑橘溃疡病斑的框选结果";
  $("original-button").textContent="查看原图";$("original-button").setAttribute("aria-pressed","false");
  $("download").href=state.result.annotated;$("download").download="柑橘病斑识别结果.jpg";
  $("lesion-count").textContent=String(item.count);$("result-info").hidden=false;$("result-actions").hidden=false;
}
async function detect(){
  if(state.busy||!state.file||!state.ready)return;
  state.busy=true;controls();message("正在识别，首次加载模型会稍慢一些…");
  try{
    const request=await api("/api/predict",{files:[{name:state.file.name,data:await base64(state.file)}],settings:{}});
    let failures=0;
    for(;;){
      let job;
      try{job=await api(`/api/jobs/${encodeURIComponent(request.job_id)}`);failures=0;}
      catch(error){if(++failures>=3)throw error;await new Promise(resolve=>setTimeout(resolve,1000));continue;}
      if(job.status==="failed")throw new Error(job.error||"识别未完成。");
      if(job.status==="completed"){render(await api(`/api/runs/${encodeURIComponent(job.run_id)}`));message("识别完成。可以保存结果，或换一张照片。");break;}
      await new Promise(resolve=>setTimeout(resolve,700));
    }
  }catch(error){message(error.message,true);$("status").textContent="本次识别未完成。";}
  finally{state.busy=false;controls();}
}
$("file-input").addEventListener("change",event=>{choose(Array.from(event.target.files));event.target.value="";});
$("detect-button").addEventListener("click",detect);
$("original-button").addEventListener("click",()=>{if(!state.result)return;state.showOriginal=!state.showOriginal;$("result-image").src=state.result[state.showOriginal?"original":"annotated"];$("original-button").textContent=state.showOriginal?"查看框选":"查看原图";$("original-button").setAttribute("aria-pressed",String(state.showOriginal));});
for(const name of ["dragenter","dragover"]){$("drop-zone").addEventListener(name,event=>{event.preventDefault();if(!state.busy)$("drop-zone").classList.add("over");});}
for(const name of ["dragleave","drop"]){$("drop-zone").addEventListener(name,event=>{event.preventDefault();$("drop-zone").classList.remove("over");});}
$("drop-zone").addEventListener("drop",event=>choose(Array.from(event.dataTransfer.files)));
api("/api/status").then(data=>{state.token=data.csrf_token;state.ready=Boolean(data.model_ready&&state.token);message(state.ready?"模型已就绪，照片仅在本机处理。":"找不到本地模型，请检查权重文件。",!state.ready);controls();}).catch(error=>message(error.message,true));
