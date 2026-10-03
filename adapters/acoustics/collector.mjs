import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {ENDPOINT,projectSnapshot,failSnapshot} from './model.mjs';

export async function collect(fetchImpl=fetch,previous=null,now=Date.now()){
 try{
  const response=await fetchImpl(ENDPOINT,{method:'GET',redirect:'error',headers:{accept:'application/json'},signal:AbortSignal.timeout(8000)});
  if(!response.ok)throw Error(`UPSTREAM_HTTP_${response.status}`);
  // Bound both declared and streamed payloads; never persist the raw multimodal data.
  if(Number(response.headers.get('content-length'))>8*1024*1024)throw Error('UPSTREAM_TOO_LARGE');
  let size=0;const chunks=[];
  for await(const chunk of response.body){size+=chunk.length;if(size>8*1024*1024)throw Error('UPSTREAM_TOO_LARGE');chunks.push(chunk)}
  return projectSnapshot(JSON.parse(Buffer.concat(chunks).toString('utf8')),now);
 }catch(error){return failSnapshot(previous,/^UPSTREAM_/.test(error.message)||error.message==='INVALID_UPSTREAM_SCHEMA'||error.message==='INVALID_SOURCE_ID'?error.message:'UPSTREAM_UNAVAILABLE',now)}
}
export async function writeSnapshot(file,snapshot){
 await fs.mkdir(path.dirname(file),{recursive:true});
 const temp=`${file}.${process.pid}.tmp`;
 await fs.writeFile(temp,JSON.stringify(snapshot)+'\n',{mode:0o644});await fs.rename(temp,file);
}
async function main(){
 const once=process.argv.includes('--once'),arg=process.argv.indexOf('--output');
 const output=arg>=0?path.resolve(process.argv[arg+1]):fileURLToPath(new URL('./live.json',import.meta.url));
 let previous=null,stopped=false;
 process.on('SIGTERM',()=>{stopped=true});process.on('SIGINT',()=>{stopped=true});
 do{
  const start=Date.now();previous=await collect(fetch,previous);
  // Receipt time is recorded after the bounded request, not before it.
  previous.collectedAt=new Date().toISOString();
  if(previous.transport.status==='OK')previous.transport.lastSuccessAt=previous.collectedAt;
  await writeSnapshot(output,previous);
  if(once||stopped)break;
  await new Promise(resolve=>setTimeout(resolve,Math.max(200,2000-(Date.now()-start))));
 }while(!stopped);
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url))main().catch(()=>{console.error('ACOUSTICS_COLLECTOR_FAILED');process.exitCode=1});
