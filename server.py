#!/usr/bin/env python3
"""周末见独立服务：SQLite 持久化 + Cookie 会话 + JSON API。
部署目录应位于 /var/www/yangyuan/ 下的新子目录，不依赖原养元账号或数据。
"""
from __future__ import annotations
import base64, hashlib, hmac, json, os, secrets, sqlite3, time
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "weekend_jian.sqlite3"
SESSION_DAYS = 14


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310000)
    return base64.b64encode(salt + derived).decode()


def verify_password(password: str, stored: str) -> bool:
    raw = base64.b64decode(stored)
    return hmac.compare_digest(raw[16:], hashlib.pbkdf2_hmac("sha256", password.encode(), raw[:16], 310000))


def row_dict(row):
    return dict(row) if row else None


def init_db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
          nickname TEXT NOT NULL, area TEXT DEFAULT '北京', bio TEXT DEFAULT '', availability TEXT DEFAULT '周六、周日都可以安排',
          reach TEXT DEFAULT '可接受 40 分钟通勤', budget TEXT DEFAULT '人均 ¥80–150', interests TEXT DEFAULT '逛展 · citywalk · 好好吃饭',
          style TEXT DEFAULT '1–3 人 · 慢热 · 不赶时间', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
          token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS routes (
          id INTEGER PRIMARY KEY, author_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
          title TEXT NOT NULL, description TEXT NOT NULL, district TEXT NOT NULL DEFAULT '北京', duration TEXT NOT NULL,
          budget TEXT NOT NULL, visibility TEXT NOT NULL DEFAULT 'public', route_type TEXT NOT NULL DEFAULT 'community',
          stops_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS saved_routes (
          user_id INTEGER REFERENCES users(id) ON DELETE CASCADE, route_id INTEGER REFERENCES routes(id) ON DELETE CASCADE,
          created_at TEXT NOT NULL, PRIMARY KEY(user_id, route_id)
        );
        CREATE TABLE IF NOT EXISTS invitations (
          id INTEGER PRIMARY KEY, route_id INTEGER REFERENCES routes(id) ON DELETE CASCADE,
          creator_id INTEGER REFERENCES users(id) ON DELETE CASCADE, status TEXT DEFAULT 'open', max_people INTEGER DEFAULT 2,
          note TEXT DEFAULT '', created_at TEXT NOT NULL
        );
        """)
        existing = db.execute("SELECT count(*) FROM routes").fetchone()[0]
        if not existing:
            seed = [
              ("一个人逛完美术馆，再去吃碗热汤面", "不用赶时间的西城午后。逛一场展，再给自己留一碗热汤面的时间。", "西城", "4 小时", "¥60–120", "community", [["14:00","北海附近看展"],["15:40","旧书店坐坐"],["17:30","吃碗热汤面"]]),
              ("下午打球，晚上和朋友吃火锅", "把身体动起来，再用一顿晚饭把这一晚过得有点热闹。", "朝阳", "3 小时", "¥70–130", "community", [["17:00","就近球馆集合"],["17:30","轻松打一场球"],["19:00","附近晚饭"]]),
              ("雨天也不窝在家：电影和书店", "下雨天也可以把自己带出门，先看电影，再在书店慢慢待一会。", "海淀", "4 小时", "¥100–180", "community", [["14:00","看一场电影"],["16:20","附近书店"],["18:00","吃一顿晚饭"]]),
            ]
            for title, desc, district, duration, budget, typ, stops in seed:
                db.execute("INSERT INTO routes(author_id,title,description,district,duration,budget,route_type,stops_json,created_at) VALUES(NULL,?,?,?,?,?,?,?,?)", (title,desc,district,duration,budget,typ,json.dumps(stops,ensure_ascii=False),now()))


def user_from_request(handler):
    cookie = SimpleCookie(handler.headers.get("Cookie"))
    token = cookie.get("wj_session")
    if not token: return None
    with connect() as db:
        r = db.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires_at>?", (token.value, int(time.time()))).fetchone()
    return row_dict(r)


def public_user(user):
    if not user: return None
    return {k:user[k] for k in ("id","email","nickname","area","bio","availability","reach","budget","interests","style","created_at")}


def plan_for(payload):
    time_slot = payload.get("time", "周六下午")
    area = payload.get("area", "朝阳 / 望京")
    budget = payload.get("budget", "100–200 元")
    interests = payload.get("interests", [])
    together = payload.get("together", "我自己去也好")
    social = "认识些人" in interests or together == "想约人一起"
    sunny = "晒晒太阳" in interests
    active = "动一动" in interests
    if active:
        return {"title":f"{area} 的轻运动傍晚", "time":time_slot, "duration":"约 3 小时", "budget":budget, "social":social, "description":"不必把一天排满，轻松动一动，再吃点好的。", "stops":[["17:00","在就近球馆集合 / 出发","优先选通勤方便、能预订的场馆。"],["17:30","轻松打一场球","60–90 分钟，按自己的节奏来。"],["19:00","在附近吃顿晚饭","聊得来就多坐会，累了就各自回家。"]]}
    if sunny:
        return {"title":f"{area} 的晒太阳午后", "time":time_slot, "duration":"约 3–4 小时", "budget":budget, "social":social, "description":"留一点可以慢慢走和晒到太阳的空白，不用赶场。", "stops":[["14:00","从附近公园出发","先走一段舒服的路。"],["15:20","找家咖啡店坐坐","让下午慢下来。"],["17:00","按当下胃口吃点好的","可以在这里结束，也可以再散步一会。"]]}
    return {"title":f"{area} 的轻松午后", "time":time_slot, "duration":"约 4 小时", "budget":budget, "social":social, "description":"一场不赶时间的展、一杯咖啡和一点可以按自己节奏走的路。", "stops":[["14:00","去一场不必赶时间的展","预留 90 分钟，看到想看的就停下来。"],["15:50","沿街散步，找一间咖啡店","步行 10–15 分钟，留一点空白。"],["17:30","吃一顿舒服的晚饭","按当下胃口决定，也可以在这里结束。"]]}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs): super().__init__(*args, directory=str(ROOT), **kwargs)
    def log_message(self, fmt, *args): print("[%s] %s" % (self.log_date_time_string(), fmt % args))
    def body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length).decode() or "{}")
        except (ValueError, UnicodeDecodeError): return None
    def send_json(self, data, status=200, cookie=None):
        raw=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Content-Length",str(len(raw))); self.send_header("Cache-Control","no-store")
        if cookie: self.send_header("Set-Cookie",cookie)
        self.end_headers(); self.wfile.write(raw)
    def error_json(self, msg, status=400): self.send_json({"error":msg},status)
    def require_user(self):
        u=user_from_request(self)
        if not u: self.error_json("请先登录",401)
        return u
    def do_GET(self):
        path=urlparse(self.path).path
        if path=="/api/health": return self.send_json({"ok":True,"service":"weekend-jian","time":now()})
        if path=="/api/me": return self.send_json({"user":public_user(user_from_request(self))})
        if path=="/api/routes":
            with connect() as db:
                rows=db.execute("SELECT r.*,COALESCE(u.nickname,'周末见用户') author, (SELECT count(*) FROM saved_routes sr WHERE sr.route_id=r.id) saves FROM routes r LEFT JOIN users u ON u.id=r.author_id WHERE r.visibility='public' ORDER BY r.id DESC").fetchall()
            return self.send_json({"routes":[{**row_dict(r),"stops":json.loads(r["stops_json"])} for r in rows]})
        if path=="/api/plans":
            u=self.require_user()
            if not u:return
            with connect() as db: rows=db.execute("SELECT r.* FROM saved_routes s JOIN routes r ON r.id=s.route_id WHERE s.user_id=? ORDER BY s.created_at DESC",(u["id"],)).fetchall()
            return self.send_json({"plans":[{**row_dict(r),"stops":json.loads(r["stops_json"])} for r in rows]})
        return super().do_GET()
    def do_POST(self):
        path=urlparse(self.path).path; payload=self.body()
        if payload is None:return self.error_json("请求格式错误")
        if path=="/api/auth/register":
            email=str(payload.get("email","")).strip().lower(); password=str(payload.get("password", "")); nickname=str(payload.get("nickname","")).strip()
            if "@" not in email or len(password)<8 or not nickname:return self.error_json("请填写昵称、有效邮箱和至少 8 位密码")
            try:
                with connect() as db:
                    cur=db.execute("INSERT INTO users(email,password_hash,nickname,created_at) VALUES(?,?,?,?)",(email,hash_password(password),nickname,now())); uid=cur.lastrowid
                    token=secrets.token_urlsafe(32); db.execute("INSERT INTO sessions(token,user_id,expires_at) VALUES(?,?,?)",(token,uid,int(time.time())+SESSION_DAYS*86400)); u=db.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
                return self.send_json({"user":public_user(row_dict(u))},201, f"wj_session={token}; Path=/; Max-Age={SESSION_DAYS*86400}; HttpOnly; SameSite=Lax")
            except sqlite3.IntegrityError:return self.error_json("这个邮箱已经注册过了",409)
        if path=="/api/auth/login":
            with connect() as db: u=db.execute("SELECT * FROM users WHERE email=?",(str(payload.get("email","")).strip().lower(),)).fetchone()
            if not u or not verify_password(str(payload.get("password","")),u["password_hash"]):return self.error_json("邮箱或密码不正确",401)
            token=secrets.token_urlsafe(32)
            with connect() as db: db.execute("INSERT INTO sessions(token,user_id,expires_at) VALUES(?,?,?)",(token,u["id"],int(time.time())+SESSION_DAYS*86400))
            return self.send_json({"user":public_user(row_dict(u))},200,f"wj_session={token}; Path=/; Max-Age={SESSION_DAYS*86400}; HttpOnly; SameSite=Lax")
        if path=="/api/auth/logout": return self.send_json({"ok":True},cookie="wj_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
        if path=="/api/ai/plan":
            u=self.require_user()
            if not u:return
            return self.send_json({"plan":plan_for(payload)})
        u=self.require_user()
        if not u:return
        if path=="/api/me":
            fields={k:str(payload[k]).strip()[:500] for k in ("nickname","area","bio","availability","reach","budget","interests","style") if k in payload}
            if not fields:return self.error_json("没有需要保存的资料")
            with connect() as db:
                db.execute("UPDATE users SET "+",".join(k+"=?" for k in fields)+" WHERE id=?",(*fields.values(),u["id"])); updated=db.execute("SELECT * FROM users WHERE id=?",(u["id"],)).fetchone()
            return self.send_json({"user":public_user(row_dict(updated))})
        if path=="/api/routes":
            title=str(payload.get("title","")).strip(); desc=str(payload.get("description","")).strip(); stops=payload.get("stops",[])
            if not title or not desc or not isinstance(stops,list) or not stops:return self.error_json("路线名称、说明和至少一站行程不能为空")
            with connect() as db:
                cur=db.execute("INSERT INTO routes(author_id,title,description,district,duration,budget,visibility,route_type,stops_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(u["id"],title[:100],desc[:800],str(payload.get("district","北京"))[:50],str(payload.get("duration","半天"))[:30],str(payload.get("budget","预算待定"))[:30],("private" if payload.get("visibility")=="private" else "public"),str(payload.get("route_type","community"))[:30],json.dumps(stops,ensure_ascii=False),now()))
                route_id=cur.lastrowid
                if payload.get("visibility")=="private":
                    db.execute("INSERT OR IGNORE INTO saved_routes(user_id,route_id,created_at) VALUES(?,?,?)",(u["id"],route_id,now()))
            return self.send_json({"route_id":route_id},201)
        if path.startswith("/api/routes/") and path.endswith("/save"): 
            try:rid=int(path.split("/")[3])
            except:return self.error_json("路线不存在",404)
            with connect() as db:
                if not db.execute("SELECT 1 FROM routes WHERE id=?",(rid,)).fetchone():return self.error_json("路线不存在",404)
                db.execute("INSERT OR IGNORE INTO saved_routes(user_id,route_id,created_at) VALUES(?,?,?)",(u["id"],rid,now()))
            return self.send_json({"ok":True})
        if path=="/api/invitations":
            try: rid=int(payload.get("route_id"))
            except:return self.error_json("请选择有效路线")
            with connect() as db:
                cur=db.execute("INSERT INTO invitations(route_id,creator_id,max_people,note,created_at) VALUES(?,?,?,?,?)",(rid,u["id"],min(max(int(payload.get("max_people",2)),1),6),str(payload.get("note",""))[:300],now()))
            return self.send_json({"invitation_id":cur.lastrowid},201)
        return self.error_json("接口不存在",404)

if __name__ == "__main__":
    init_db()
    port=int(os.getenv("PORT","8787")); host=os.getenv("HOST","127.0.0.1")
    print(f"周末见服务 http://{host}:{port}")
    ThreadingHTTPServer((host,port),Handler).serve_forever()
