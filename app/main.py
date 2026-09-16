import base64, hashlib, hmac, json, os, sqlite3, time, uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

DB_PATH = os.getenv("DATABASE_URL", "sqlite:///./lead_capture.db").replace("sqlite:///./", "")
SECRET = os.getenv("APP_SECRET", "dev-secret")
RATE_MAX = int(os.getenv("RATE_LIMIT_REQUESTS", "5"))
RATE_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))

app = FastAPI(title="Lead Capture Platform", version="1.0.0")
origins = [x.strip() for x in os.getenv("CORS_ORIGINS", "*").split(",")]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

limiter: dict[str, list[float]] = defaultdict(list)


def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS widgets(id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, type TEXT NOT NULL, title TEXT NOT NULL, description TEXT, fields TEXT NOT NULL, button_text TEXT NOT NULL, display_options TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS submissions(id TEXT PRIMARY KEY, widget_id TEXT NOT NULL, owner_id TEXT NOT NULL, data TEXT NOT NULL, ip TEXT, country TEXT, city TEXT, created_at TEXT NOT NULL);
    """)
    if not c.execute("SELECT 1 FROM users WHERE email='owner@example.com'").fetchone():
        for email, password in [("owner@example.com", "owner-password"), ("other@example.com", "other-password")]:
            c.execute("INSERT INTO users VALUES(?,?,?)", (uuid.uuid4().hex, email, password_hash(password)))
    owner = c.execute("SELECT id FROM users WHERE email='owner@example.com'").fetchone()[0]
    if not c.execute("SELECT 1 FROM widgets WHERE id='demo'").fetchone():
        now = iso_now()
        c.execute("INSERT INTO widgets VALUES(?,?,?,?,?,?,?,?,?,?,?)", ("demo", owner, "signup", "Join our newsletter", "Get product updates and helpful ideas.", json.dumps([{"name":"name","label":"Your name","type":"text","required":True},{"name":"email","label":"Email address","type":"email","required":True}]), "Subscribe", json.dumps({"position":"inline"}), 1, now, now))
    c.commit(); c.close()


def iso_now(): return datetime.now(timezone.utc).isoformat()
def password_hash(value): return hashlib.sha256((SECRET + value).encode()).hexdigest()
def token_for(uid):
    raw = f"{uid}.{int(time.time())}"; sig = hmac.new(SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{raw}.{sig}".encode()).decode()
def current_user(auth: str | None):
    if not auth or not auth.startswith("Bearer "): raise HTTPException(401, "Valid bearer authentication required")
    try: raw = base64.urlsafe_b64decode(auth[7:].encode()).decode(); uid, ts, sig = raw.split(".")
    except Exception: raise HTTPException(401, "Invalid token")
    expected = hmac.new(SECRET.encode(), f"{uid}.{ts}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected) or time.time() - int(ts) > 86400: raise HTTPException(401, "Expired token")
    row = db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not row: raise HTTPException(401, "Unknown user")
    return row

class Login(BaseModel): email: str; password: str
class WidgetIn(BaseModel):
    type: str = Field("signup", pattern="^(signup|contact|cta|popover)$")
    title: str = Field(..., min_length=1, max_length=120)
    description: str | None = Field(None, max_length=500)
    fields: list[dict[str, Any]] = Field(default_factory=list, max_length=12)
    button_text: str = Field("Submit", min_length=1, max_length=40)
    display_options: dict[str, Any] = Field(default_factory=dict)
class SubmissionIn(BaseModel):
    widget_id: str = Field(..., min_length=1, max_length=80)
    data: dict[str, Any] = Field(..., max_length=20)
    website: str = Field("", max_length=1)  # honeypot; must remain empty
    @field_validator("data")
    @classmethod
    def bounded(cls, value):
        encoded = json.dumps(value)
        if len(encoded) > 10000: raise ValueError("submission payload is too large")
        for k, v in value.items():
            if len(str(k)) > 80 or len(str(v)) > 2000: raise ValueError("field is too large")
        return value

@app.on_event("startup")
def startup(): init_db()

@app.get("/health")
def health(): return {"status":"ok", "service":"lead-capture-platform"}

@app.post("/auth/login")
def login(payload: Login):
    row = db().execute("SELECT * FROM users WHERE email=? AND password_hash=?", (payload.email, password_hash(payload.password))).fetchone()
    if not row: raise HTTPException(401, "Invalid email or password")
    return {"access_token": token_for(row["id"]), "token_type":"bearer"}

def widget_dict(row):
    d = dict(row); d["fields"] = json.loads(d["fields"]); d["display_options"] = json.loads(d["display_options"]); return d

@app.post("/widgets", status_code=201)
def create_widget(payload: WidgetIn, authorization: str | None = Header(None)):
    owner = current_user(authorization); wid = uuid.uuid4().hex[:12]; now = iso_now(); c = db()
    c.execute("INSERT INTO widgets VALUES(?,?,?,?,?,?,?,?,?,?,?)", (wid, owner["id"], payload.type, payload.title, payload.description, json.dumps(payload.fields), payload.button_text, json.dumps(payload.display_options), 1, now, now)); c.commit()
    return widget_dict(c.execute("SELECT * FROM widgets WHERE id=?", (wid,)).fetchone())

@app.get("/widgets")
def list_widgets(authorization: str | None = Header(None)):
    owner=current_user(authorization); return [widget_dict(x) for x in db().execute("SELECT * FROM widgets WHERE owner_id=? ORDER BY created_at DESC", (owner["id"],)).fetchall()]

@app.get("/widgets/{wid}")
def get_widget(wid: str, authorization: str | None = Header(None)):
    owner=current_user(authorization); row=db().execute("SELECT * FROM widgets WHERE id=? AND owner_id=?", (wid, owner["id"])).fetchone()
    if not row:
        raise HTTPException(404, "Widget not found")
    return widget_dict(row)

@app.put("/widgets/{wid}")
def update_widget(wid: str, payload: WidgetIn, authorization: str | None = Header(None)):
    owner=current_user(authorization); c=db(); row=c.execute("SELECT * FROM widgets WHERE id=? AND owner_id=?", (wid,owner["id"])).fetchone()
    if not row: raise HTTPException(404,"Widget not found")
    c.execute("UPDATE widgets SET type=?,title=?,description=?,fields=?,button_text=?,display_options=?,version=version+1,updated_at=? WHERE id=?", (payload.type,payload.title,payload.description,json.dumps(payload.fields),payload.button_text,json.dumps(payload.display_options),iso_now(),wid)); c.commit()
    return widget_dict(c.execute("SELECT * FROM widgets WHERE id=?",(wid,)).fetchone())

@app.delete("/widgets/{wid}", status_code=204)
def delete_widget(wid: str, authorization: str | None = Header(None)):
    owner=current_user(authorization); c=db(); cur=c.execute("DELETE FROM widgets WHERE id=? AND owner_id=?",(wid,owner["id"])); c.commit()
    if cur.rowcount == 0: raise HTTPException(404,"Widget not found")
    return Response(status_code=204)

@app.get("/widgets/{wid}/embed")
def embed(wid: str, authorization: str | None = Header(None)):
    owner=current_user(authorization); row=db().execute("SELECT id,version FROM widgets WHERE id=? AND owner_id=?",(wid,owner["id"])).fetchone()
    if not row: raise HTTPException(404,"Widget not found")
    return {"snippet": f'<script src="http://localhost:8000/widget.js?v={row["version"]}&id={wid}"></script>'}

@app.get("/widgets/{wid}/config")
def public_config(wid: str):
    row=db().execute("SELECT id,type,title,description,fields,button_text,display_options,version FROM widgets WHERE id=?",(wid,)).fetchone()
    if not row: raise HTTPException(404,"Widget not found")
    response=JSONResponse(widget_dict(row)); response.headers["Cache-Control"]="public, max-age=60"; return response

@app.get("/widget.js", response_class=HTMLResponse)
def widget_js():
    js="""(async()=>{const s=document.currentScript,u=new URL(s.src),id=u.searchParams.get('id'),api=u.origin;const c=await fetch(api+'/widgets/'+id+'/config').then(r=>r.json());const root=document.getElementById('lead-widget')||document.body;root.innerHTML='<h2>'+c.title+'</h2><p>'+ (c.description||'')+'</p><form>'+c.fields.map(f=>`<label>${f.label}<input name="${f.name}" type="${f.type||'text'}" ${f.required?'required':''}></label>`).join('')+'<input name="website" tabindex="-1" autocomplete="off" style="display:none"><button>'+c.button_text+'</button><output></output></form>';root.querySelector('form').onsubmit=async e=>{e.preventDefault();const f=new FormData(e.target),data={};f.forEach((v,k)=>{if(k!=='website')data[k]=v});const r=await fetch(api+'/submissions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({widget_id:id,data,website:f.get('website')||''})});e.target.querySelector('output').textContent=r.ok?'Thanks — you are on the list.':(await r.json()).detail||'Unable to submit.'}})();"""
    response=HTMLResponse(js, media_type="application/javascript"); response.headers["Cache-Control"]="public, max-age=31536000, immutable"; return response

def enrich(ip):
    mode=os.getenv("GEO_PROVIDER_MODE","mock")
    if mode == "all_down": return None
    if mode == "provider_a_down": return {"country":"US","city":"Fallback City","provider":"B"}
    return {"country":"US","city":"Primary City","provider":"A"}

def check_limit(key):
    now=time.time(); recent=[t for t in limiter[key] if now-t < RATE_WINDOW]; limiter[key]=recent
    if len(recent)>=RATE_MAX: return False
    recent.append(now); return True

@app.post("/submissions", status_code=201)
def submit(payload: SubmissionIn, request: Request):
    if payload.website: raise HTTPException(422,"spam detected")
    ip=request.client.host if request.client else "unknown"
    if not check_limit(f"ip:{ip}") or not check_limit(f"widget:{payload.widget_id}"): raise HTTPException(429,"rate limit exceeded; try again later")
    c=db(); widget=c.execute("SELECT * FROM widgets WHERE id=?",(payload.widget_id,)).fetchone()
    if not widget: raise HTTPException(404,"Widget not found")
    geo=enrich(ip); sid=uuid.uuid4().hex; c.execute("INSERT INTO submissions VALUES(?,?,?,?,?,?,?,?)",(sid,widget["id"],widget["owner_id"],json.dumps(payload.data),ip,geo.get("country") if geo else None,geo.get("city") if geo else None,iso_now())); c.commit()
    try:
        if os.getenv("SIDE_EFFECT_MODE") == "fail": raise RuntimeError("simulated notification outage")
        print(f"notification queued for submission {sid}")
    except Exception as exc: print(f"non-critical notification failed: {exc}")
    return {"id":sid,"status":"accepted","geo":geo}

@app.get("/dashboard/submissions")
def dashboard_submissions(authorization: str | None = Header(None), widget_id: str | None = None):
    owner=current_user(authorization); c=db(); q="SELECT * FROM submissions WHERE owner_id=?"; args=[owner["id"]]
    if widget_id: q+=" AND widget_id=?"; args.append(widget_id)
    q+=" ORDER BY created_at DESC"; rows=[dict(x) for x in c.execute(q,args).fetchall()]
    for x in rows: x["data"]=json.loads(x["data"])
    return {"count":len(rows),"submissions":rows}

@app.get("/dashboard/stats")
def dashboard_stats(authorization: str | None = Header(None)):
    owner=current_user(authorization); c=db(); total=c.execute("SELECT COUNT(*) FROM submissions WHERE owner_id=?",(owner["id"],)).fetchone()[0]
    widgets=[dict(x) for x in c.execute("SELECT w.id,w.title,COUNT(s.id) count FROM widgets w LEFT JOIN submissions s ON w.id=s.widget_id WHERE w.owner_id=? GROUP BY w.id",(owner["id"],)).fetchall()]
    geo=[dict(x) for x in c.execute("SELECT COALESCE(country,'unknown') country,COUNT(*) count FROM submissions WHERE owner_id=? GROUP BY country ORDER BY count DESC",(owner["id"],)).fetchall()]
    return {"total_submissions":total,"per_widget":widgets,"geo_breakdown":geo}

@app.get("/docs-quick", include_in_schema=False)
def docs_quick(): return {"message":"See /docs for interactive OpenAPI documentation"}

@app.get("/")
def root():
    return {
        "message": "Lead Capture Platform API is running"
    }

if __name__ == "__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=8000)
