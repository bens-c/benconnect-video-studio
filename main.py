import os,uuid,time,json,hmac,sqlite3,asyncio,subprocess
from pathlib import Path
from fastapi import FastAPI,Request,Header,HTTPException,UploadFile,File
from fastapi.responses import FileResponse,JSONResponse
from pydantic import BaseModel,Field
from itsdangerous import URLSafeTimedSerializer,BadSignature
import httpx
from dotenv import load_dotenv
load_dotenv()
ROOT=Path(__file__).parent; DATA=ROOT/"data";DATA.mkdir(exist_ok=True)
PASSWORD=os.getenv("ADMIN_PASSWORD",""); SECRET=os.getenv("SESSION_SECRET","")
if not PASSWORD or PASSWORD.startswith("REPLACE_") or not SECRET or SECRET.startswith("REPLACE_"): raise RuntimeError("Set ADMIN_PASSWORD and SESSION_SECRET")
TOKEN=os.getenv("REPLICATE_API_TOKEN",""); MODEL=os.getenv("REPLICATE_MODEL","")
ORIGIN=os.getenv("PUBLIC_ORIGIN","http://localhost:8000").rstrip("/")
signer=URLSafeTimedSerializer(SECRET,salt="studio");app=FastAPI(docs_url=None,redoc_url=None)
DB=DATA/"studio.db"
with sqlite3.connect(DB) as db:
 db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY,status TEXT,prompt TEXT,clip TEXT,error TEXT)")
 db.execute("CREATE TABLE IF NOT EXISTS clips (id TEXT PRIMARY KEY,name TEXT)")
 db.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY,title TEXT,timeline TEXT)")
def sql(q,args=(),one=False):
 with sqlite3.connect(DB) as db:
  db.row_factory=sqlite3.Row
  r=db.execute(q,args)
  if q.lstrip().upper().startswith("SELECT"): return [dict(x) for x in r.fetchall()]
  db.commit()
def auth(req,write=False):
 try:
  if signer.loads(req.cookies.get("bc_session",""),max_age=86400)!="admin": raise ValueError()
 except (BadSignature,ValueError): raise HTTPException(401,"Bitte anmelden")
 if write:
  if req.headers.get("x-studio-request")!="1": raise HTTPException(403,"Header fehlt")
  origin=req.headers.get("origin")
  if origin and origin.rstrip("/") not in (ORIGIN,"http://localhost:8000","http://127.0.0.1:8000"):raise HTTPException(403,"Origin blockiert")
class Login(BaseModel):password:str
class Generate(BaseModel):prompt:str=Field(min_length=5,max_length=3000)
class Project(BaseModel):id:str|None=None;title:str;timeline:list[dict]
class Export(BaseModel):timeline:list[dict]
@app.get("/")
def home():return FileResponse(ROOT/"static"/"index.html")
@app.get("/health")
def health():return {"ok":True}
@app.get("/api/session")
def session(req:Request):
 try:auth(req);ok=True
 except HTTPException:ok=False
 return {"authenticated":ok,"configured":bool(TOKEN and MODEL)}
@app.post("/api/login")
def login(data:Login,req:Request):
 if req.headers.get("x-studio-request")!="1":raise HTTPException(403)
 if req.headers.get("origin") and req.headers["origin"].rstrip("/") not in (ORIGIN,"http://localhost:8000","http://127.0.0.1:8000"):raise HTTPException(403)
 if not hmac.compare_digest(data.password.encode(),PASSWORD.encode()):raise HTTPException(401,"Falsches Passwort")
 res=JSONResponse({"ok":True});res.set_cookie("bc_session",signer.dumps("admin"),httponly=True,secure=os.getenv("COOKIE_SECURE","true")=="true",samesite="strict",max_age=86400);return res
@app.post("/api/logout")
def logout(req:Request):
 auth(req,True);r=JSONResponse({"ok":True});r.delete_cookie("bc_session");return r
async def worker(jid,prompt):
 try:
  extra=json.loads(os.getenv("REPLICATE_INPUT_JSON","{}"))
  if not isinstance(extra,dict):raise ValueError("Invalid model input")
  headers={"Authorization":"Bearer "+TOKEN,"Content-Type":"application/json"}
  async with httpx.AsyncClient(timeout=60,follow_redirects=False) as c:
   r=await c.post("https://api.replicate.com/v1/models/"+MODEL+"/predictions",headers=headers,json={"input":{**extra,"prompt":prompt}})
   r.raise_for_status();pred=r.json()
   sql("UPDATE jobs SET status=? WHERE id=?",("processing",jid))
   for _ in range(180):
    if pred.get("status") in ("succeeded","failed","canceled"):break
    await asyncio.sleep(5)
    r=await c.get("https://api.replicate.com/v1/predictions/"+pred["id"],headers=headers);r.raise_for_status();pred=r.json()
   if pred.get("status")!="succeeded":raise ValueError(str(pred.get("error") or "Generation timed out"))
   output=pred.get("output")
   if isinstance(output,list):output=next((x for x in output if isinstance(x,str)),None)
   if isinstance(output,dict):output=output.get("video") or output.get("url")
   if not isinstance(output,str):raise ValueError("Model returned no video URL")
   from urllib.parse import urlparse
   u=urlparse(output);host=(u.hostname or "").lower()
   allowed=[x.strip().lower() for x in os.getenv("ALLOWED_VIDEO_HOSTS","replicate.delivery,*.replicate.delivery").split(",")]
   if u.scheme!="https" or not any(host==a or (a.startswith("*.") and host.endswith(a[1:])) for a in allowed):raise ValueError("Untrusted video host")
   # Remote output is fetched from a strict allowlist; disable redirects and proxy use.
   async with httpx.AsyncClient(timeout=120,follow_redirects=False,trust_env=False) as downloader:
    r=await downloader.get(output);r.raise_for_status()
    if len(r.content)>int(os.getenv("MAX_UPLOAD_MB","100"))*1048576:raise ValueError("Video too large")
   cid=uuid.uuid4().hex;path=DATA/(cid+".mp4");path.write_bytes(r.content)
   probe=subprocess.run(["ffprobe","-v","error","-select_streams","v:0","-show_entries","stream=codec_type","-of","csv=p=0",str(path)],capture_output=True,text=True,timeout=20)
   if probe.returncode or "video" not in probe.stdout:path.unlink(missing_ok=True);raise ValueError("Invalid video output")
   sql("INSERT INTO clips VALUES (?,?)",(cid,prompt[:80]));sql("UPDATE jobs SET status=?,clip=? WHERE id=?",("succeeded",cid,jid))
 except Exception as e:sql("UPDATE jobs SET status=?,error=? WHERE id=?",("failed",str(e)[:350],jid))
@app.post("/api/generate")
async def generate(data:Generate,req:Request):
 auth(req,True)
 if not TOKEN or not MODEL:raise HTTPException(503,"Replicate nicht konfiguriert")
 jid=uuid.uuid4().hex;sql("INSERT INTO jobs VALUES (?,?,?,?,?)",(jid,"starting",data.prompt,"",""))
 asyncio.create_task(worker(jid,data.prompt));return {"id":jid}
@app.get("/api/jobs/{jid}")
def job(jid:str,req:Request):
 auth(req);rows=sql("SELECT * FROM jobs WHERE id=?",(jid,));return rows[0] if rows else JSONResponse({"error":"Not found"},404)
@app.get("/api/clips")
def clips(req:Request):auth(req);return sql("SELECT * FROM clips ORDER BY rowid DESC")
@app.get("/api/clips/{cid}/video")
def clip(cid:str,req:Request):
 auth(req);rows=sql("SELECT * FROM clips WHERE id=?",(cid,))
 if not rows:raise HTTPException(404)
 return FileResponse(DATA/(cid+".mp4"),media_type="video/mp4")
@app.post("/api/upload")
async def upload(req:Request,file:UploadFile=File(...)):
 auth(req,True);cid=uuid.uuid4().hex;dest=DATA/(cid+".mp4");limit=int(os.getenv("MAX_UPLOAD_MB","100"))*1048576;size=0
 try:
  with dest.open("wb") as f:
   while chunk:=await file.read(1024*1024):
    size+=len(chunk)
    if size>limit:raise HTTPException(413,"Datei zu groß")
    f.write(chunk)
  p=subprocess.run(["ffprobe","-v","error","-select_streams","v:0","-show_entries","stream=codec_type","-of","csv=p=0",str(dest)],capture_output=True,text=True,timeout=20)
  if p.returncode or "video" not in p.stdout:raise HTTPException(400,"Ungültiges Video")
 except Exception:dest.unlink(missing_ok=True);raise
 sql("INSERT INTO clips VALUES (?,?)",(cid,(file.filename or "Upload")[:100]));return {"id":cid}
@app.get("/api/projects")
def projects(req:Request):auth(req);return sql("SELECT * FROM projects")
@app.post("/api/projects")
def save(data:Project,req:Request):
 auth(req,True);pid=data.id or uuid.uuid4().hex
 if len(data.timeline)>100:raise HTTPException(400,"Zu viele Clips")
 sql("INSERT OR REPLACE INTO projects VALUES (?,?,?)",(pid,data.title,json.dumps(data.timeline)));return {"id":pid}
@app.post("/api/export")
def export(data:Export,req:Request):
 auth(req,True)
 if not 1<=len(data.timeline)<=40:raise HTTPException(400,"Timeline leer oder zu lang")
 inputs=[];filters=[];concat=[]
 for i,item in enumerate(data.timeline):
  cid=str(item.get("id",""))
  if not sql("SELECT id FROM clips WHERE id=?",(cid,)):raise HTTPException(400,"Clip unbekannt")
  try:start=float(item.get("start",0));duration=float(item.get("duration",5))
  except (ValueError,TypeError):raise HTTPException(400,"Ungültige Zeiten")
  if start<0 or duration<=0 or duration>120:raise HTTPException(400,"Ungültige Zeiten")
  inputs+=["-ss",str(start),"-t",str(duration),"-i",str(DATA/(cid+".mp4"))]
  filters.append(f"[{i}:v]fps=24,scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[v{i}]")
  concat.append(f"[v{i}]")
 out=uuid.uuid4().hex;dest=DATA/(out+".mp4")
 cmd=["ffmpeg","-hide_banner","-loglevel","error","-y",*inputs,"-filter_complex",";".join(filters)+";"+"".join(concat)+f"concat=n={len(concat)}:v=1:a=0[out]","-map","[out]","-c:v","libx264","-preset","veryfast","-crf","23",str(dest)]
 try:subprocess.run(cmd,check=True,timeout=240)
 except Exception:raise HTTPException(500,"Videoexport fehlgeschlagen")
 return FileResponse(dest,media_type="video/mp4",filename="benconnect-video.mp4")
