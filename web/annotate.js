"use strict";
const $=id=>document.getElementById(id);
const s={token:"",samples:[],id:null,image:null,shapes:[],points:[],zoom:1,dirty:false,busy:false};
const canvas=$("canvas"),ctx=canvas.getContext("2d");
function lockForm(value){["save","sample","leaf-id","complete","kind"].forEach(id=>$(id).disabled=value);}
function message(text,error=false){$("message").textContent=text;$("message").classList.toggle("error",error);}
async function api(path,body){const options={cache:"no-store",headers:{Accept:"application/json"}};if(body!==undefined){options.method="POST";options.headers["Content-Type"]="application/json";options.headers["X-CSRF-Token"]=s.token;options.body=JSON.stringify(body);}const r=await fetch(path,options);const data=await r.json();if(!r.ok)throw new Error(data.error||"请求失败");return data;}
function dirty(){s.dirty=true;$("complete").checked=false;}
function draw(){
  if(!s.image)return;
  ctx.clearRect(0,0,canvas.width,canvas.height);ctx.drawImage(s.image,0,0);
  const scale=canvas.width/canvas.getBoundingClientRect().width;
  function polygon(points,label,closed){if(!points.length)return;ctx.beginPath();points.forEach((p,i)=>i?ctx.lineTo(...p):ctx.moveTo(...p));if(closed)ctx.closePath();ctx.strokeStyle=label==="leaf"?"#25a55a":"#ed861a";ctx.lineWidth=2*scale;ctx.stroke();if(closed){ctx.fillStyle=label==="leaf"?"#25a55a18":"#ed861a55";ctx.fill();}if(!closed){for(const p of points){ctx.beginPath();ctx.arc(...p,3*scale,0,Math.PI*2);ctx.fillStyle="#ef891a";ctx.fill();}}}
  s.shapes.forEach(shape=>polygon(shape.points,shape.label,true));polygon(s.points,$("kind").value,false);
  $("shapes").replaceChildren();s.shapes.forEach((shape,index)=>{const li=document.createElement("li");const text=document.createElement("span");text.textContent=`${shape.label==="leaf"?"整叶":"病斑"} · ${shape.points.length} 个点`;const button=document.createElement("button");button.textContent="移除";button.disabled=s.busy;button.onclick=()=>{s.shapes.splice(index,1);dirty();draw();};li.append(text,button);$("shapes").append(li);});
}
function resize(){if(!s.image)return;const fit=Math.min($("viewport").clientWidth-2,($("viewport").clientHeight-2)*s.image.width/s.image.height,s.image.width);canvas.style.width=`${fit*s.zoom}px`;canvas.style.height="auto";$("zoom-label").textContent=`${s.zoom}×`;draw();}
function finish(){if(s.busy)return;if(s.points.length<3){message("至少点 3 个位置才能闭合轮廓。",true);return;}if($("kind").value==="leaf"&&s.shapes.some(x=>x.label==="leaf")){message("整叶轮廓已有一个；要重画请先移除旧轮廓。",true);return;}s.shapes.push({label:$("kind").value,points:s.points,shape_type:"polygon"});s.points=[];dirty();draw();message("轮廓已闭合。可继续描病斑或保存。");}
async function load(id){
  s.busy=true;lockForm(true);
  try{const row=s.samples.find(r=>r.id===id);const data=await api(`/api/annotation/${id}`);const image=new Image();await new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=()=>reject(new Error("图片读取失败，请检查试标图片副本。"));image.src=row.image_url;});
    s.id=id;s.image=image;s.shapes=data.annotation?.shapes||[];s.points=[];s.zoom=1;s.dirty=false;canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;$("leaf-id").value=data.annotation?.leaf_id||"";$("complete").checked=Boolean(data.annotation?.flags?.complete);$("kind").value=s.shapes.some(x=>x.label==="leaf")?"canker":"leaf";resize();message("逐点描轮廓，按 Enter 闭合。可以先保存草稿。");
  }catch(error){message(error.message,true);$("sample").value=s.id||"";}
  finally{s.busy=false;lockForm(false);$("save").disabled=!s.image;draw();}
}
canvas.addEventListener("click",event=>{if(!s.image||s.busy)return;const rect=canvas.getBoundingClientRect();s.points.push([Math.max(0,Math.min(canvas.width-1,(event.clientX-rect.left)*canvas.width/rect.width)),Math.max(0,Math.min(canvas.height-1,(event.clientY-rect.top)*canvas.height/rect.height))]);dirty();draw();});
$("finish").onclick=finish;$("undo").onclick=()=>{if(!s.busy){s.points.pop();dirty();draw();}};
$("kind").onchange=()=>{if(s.points.length){message("请先闭合当前轮廓，再切换类别。",true);$("kind").value=$("kind").value==="leaf"?"canker":"leaf";}draw();};
$("zoom-in").onclick=()=>{s.zoom=Math.min(5,s.zoom+.5);resize();};$("zoom-out").onclick=()=>{s.zoom=Math.max(1,s.zoom-.5);resize();};
$("sample").onchange=()=>{if(s.dirty&&!confirm("当前修改尚未保存，要放弃这些未保存的修改并换图吗？")){$("sample").value=s.id;return;}load($("sample").value);};
$("leaf-id").oninput=()=>{s.dirty=true;};$("complete").onchange=()=>{s.dirty=true;};
$("save").onclick=async()=>{if(s.busy||!s.id)return;if(s.points.length){message("请先闭合正在画的轮廓，再保存。",true);return;}s.busy=true;lockForm(true);draw();try{const result=await api("/api/annotation",{id:s.id,shapes:s.shapes,leaf_id:$("leaf-id").value,complete:$("complete").checked});s.dirty=false;const row=s.samples.find(x=>x.id===s.id);row.complete=result.annotation.flags.complete;refreshOptions();message(row.complete?"已保存为完成标注。":"草稿已保存，稍后可以继续描绘。");}catch(error){message(error.message,true);}finally{s.busy=false;lockForm(false);draw();}};
function refreshOptions(){$("sample").replaceChildren();s.samples.forEach((row,index)=>{const option=new Option(`${index+1}. ${row.name}${row.complete?" ✓":""}`,row.id);$("sample").add(option);});if(s.id)$("sample").value=s.id;$("completion").textContent=`完成 ${s.samples.filter(x=>x.complete).length}/${s.samples.length}`;}
document.addEventListener("keydown",event=>{if(["INPUT","SELECT","TEXTAREA"].includes(event.target.tagName)||s.busy)return;if(event.key==="Enter"){event.preventDefault();finish();}else if(event.key==="Backspace"){event.preventDefault();s.points.pop();dirty();draw();}});
window.addEventListener("beforeunload",event=>{if(s.dirty){event.preventDefault();event.returnValue="";}});window.addEventListener("resize",resize);
(async()=>{try{s.token=(await api("/api/status")).csrf_token;s.samples=(await api("/api/annotations")).samples;refreshOptions();if(!s.samples.length)throw new Error("没有试标清单，请先准备样本。");await load(s.samples[0].id);}catch(error){message(error.message,true);}})();
