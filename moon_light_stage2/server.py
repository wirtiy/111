from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from datetime import date, datetime
import sqlite3, json, uuid, hashlib, secrets, csv, io, re

ROOT = Path(__file__).parent
DB = ROOT / "moon_light.db"
RATES = {"Moon Dorm": 24, "Moon Double": 58, "Moon Family": 82}
TOKENS = {}  # demo sessions; persistent accounts live in SQLite

def connect():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db

def init_db():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
          password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'guest',
          active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS rooms(
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, description TEXT DEFAULT '',
          capacity INTEGER NOT NULL, price REAL NOT NULL, quantity INTEGER NOT NULL DEFAULT 1,
          active INTEGER NOT NULL DEFAULT 1, amenities TEXT DEFAULT '[]');
        CREATE TABLE IF NOT EXISTS bookings(
          id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL, user_id INTEGER,
          guest_name TEXT NOT NULL, email TEXT NOT NULL, room_id INTEGER,
          room_name TEXT NOT NULL, checkin TEXT NOT NULL, checkout TEXT NOT NULL,
          guests INTEGER NOT NULL, nights INTEGER NOT NULL, total REAL NOT NULL,
          status TEXT NOT NULL DEFAULT 'new', note TEXT DEFAULT '', created_at TEXT NOT NULL,
          FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(room_id) REFERENCES rooms(id));
        CREATE TABLE IF NOT EXISTS messages(
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT NOT NULL,
          message TEXT NOT NULL, status TEXT DEFAULT 'new', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reviews(
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, rating INTEGER NOT NULL,
          body TEXT NOT NULL, approved INTEGER DEFAULT 0, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS coupons(
          id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL, percent INTEGER NOT NULL,
          active INTEGER DEFAULT 1, expires TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS faqs(
          id INTEGER PRIMARY KEY AUTOINCREMENT, question TEXT NOT NULL, answer TEXT NOT NULL,
          active INTEGER DEFAULT 1, sort_order INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tasks(
          id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, details TEXT DEFAULT '',
          status TEXT DEFAULT 'open', assigned_to TEXT DEFAULT '', due_date TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS audit(
          id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL, entity TEXT NOT NULL,
          entity_id TEXT DEFAULT '', details TEXT DEFAULT '', created_at TEXT NOT NULL);
        """)
        if db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] == 0:
            db.executemany("INSERT INTO rooms(name,description,capacity,price,quantity,amenities) VALUES(?,?,?,?,?,?)", [
              ("Moon Dorm","Общий номер с уютными спальными местами",4,24,5,'["Wi-Fi","Общая ванная"]'),
              ("Moon Double","Двухместный номер",2,58,4,'["Wi-Fi","Душ","Полотенца"]'),
              ("Moon Family","Семейный номер",4,82,2,'["Wi-Fi","Душ","Зона отдыха"]')])
        if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            db.execute("INSERT INTO users(name,email,password_hash,role,created_at) VALUES(?,?,?,?,?)",
              ("Администратор","admin@moonlight.local",pw("MoonLight2026!"),"admin",now()))
        if db.execute("SELECT COUNT(*) FROM faqs").fetchone()[0] == 0:
            db.executemany("INSERT INTO faqs(question,answer,sort_order) VALUES(?,?,?)", [
              ("Во сколько заезд и выезд?","Заезд после 14:00, выезд до 11:00.",1),
              ("Есть ли Wi-Fi?","Да, Wi-Fi доступен гостям.",2),
              ("Можно ли отменить бронь?","Свяжитесь с администрацией для изменения заявки.",3)])
        db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('hotel_name','Moon Light')")
        db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('currency','RUB')")
        # Миграции для существующих баз: безопасно добавляем новые поля.
        user_cols={r["name"] for r in db.execute("PRAGMA table_info(users)")}
        if "phone" not in user_cols: db.execute("ALTER TABLE users ADD COLUMN phone TEXT DEFAULT ''")
        booking_cols={r["name"] for r in db.execute("PRAGMA table_info(bookings)")}
        if "updated_at" not in booking_cols: db.execute("ALTER TABLE bookings ADD COLUMN updated_at TEXT DEFAULT ''")
        if "cancel_reason" not in booking_cols: db.execute("ALTER TABLE bookings ADD COLUMN cancel_reason TEXT DEFAULT ''")
        if "payment_status" not in booking_cols: db.execute("ALTER TABLE bookings ADD COLUMN payment_status TEXT DEFAULT 'unpaid'")
        db.execute("CREATE INDEX IF NOT EXISTS idx_bookings_dates ON bookings(room_id,checkin,checkout,status)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_bookings_user ON bookings(user_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_reviews_approved ON reviews(approved)")

def now(): return datetime.now().isoformat(timespec="seconds")
def pw(value): return hashlib.sha256(value.encode()).hexdigest()
def rowdict(row): return dict(row) if row else None

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*a,**kw): super().__init__(*a,directory=str(ROOT/"static"),**kw)
    def json(self,code,obj):
        b=json.dumps(obj,ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(b))); self.send_header("Access-Control-Allow-Origin","*")
        self.end_headers(); self.wfile.write(b)
    def body(self):
        n=int(self.headers.get("Content-Length","0"))
        if n>200000: raise ValueError("Слишком большой запрос")
        return json.loads(self.rfile.read(n).decode() or "{}")
    def actor(self):
        token=self.headers.get("Authorization","").replace("Bearer ","")
        return TOKENS.get(token)
    def admin(self):
        a=self.actor()
        return bool(a and a["role"]=="admin")
    def audit(self,action,entity,eid="",details=""):
        with connect() as db: db.execute("INSERT INTO audit(action,entity,entity_id,details,created_at) VALUES(?,?,?,?,?)",(action,entity,str(eid),details,now()))
    def do_OPTIONS(self):
        self.send_response(204); self.send_header("Access-Control-Allow-Headers","Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods","GET,POST,PATCH,DELETE,OPTIONS"); self.end_headers()
    def do_GET(self):
        path=urlparse(self.path).path; q=parse_qs(urlparse(self.path).query)
        try:
            with connect() as db:
                if path=="/api/health": return self.json(200,{"status":"ok","stage":2,"database":"SQLite"})
                if path=="/api/my-bookings":
                    actor=self.actor()
                    if not actor: return self.json(401,{"error":"Войдите в аккаунт"})
                    rows=db.execute("SELECT id,code,room_name,checkin,checkout,guests,nights,total,status,payment_status,created_at FROM bookings WHERE user_id=? ORDER BY id DESC",(actor["id"],)).fetchall()
                    return self.json(200,{"items":[dict(x) for x in rows]})
                if path=="/api/profile":
                    actor=self.actor()
                    if not actor: return self.json(401,{"error":"Войдите в аккаунт"})
                    u=db.execute("SELECT id,name,email,phone,role,created_at FROM users WHERE id=?",(actor["id"],)).fetchone()
                    return self.json(200,{"user":dict(u) if u else None})
                if path=="/api/export/bookings.csv":
                    if not self.admin(): return self.json(401,{"error":"Требуется вход администратора"})
                    rows=db.execute("SELECT code,guest_name,email,room_name,checkin,checkout,guests,nights,total,status,payment_status,created_at FROM bookings ORDER BY id DESC").fetchall()
                    out=io.StringIO(); w=csv.writer(out,delimiter=";")
                    w.writerow(["Код","Гость","Email","Номер","Заезд","Выезд","Гостей","Ночей","Сумма","Статус","Оплата","Создано"])
                    w.writerows([tuple(r) for r in rows])
                    data=("\\ufeff"+out.getvalue()).encode("utf-8")
                    self.send_response(200); self.send_header("Content-Type","text/csv; charset=utf-8")
                    self.send_header("Content-Disposition",'attachment; filename="moon_light_bookings.csv"')
                    self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data); return
                if path=="/api/reports/monthly":
                    if not self.admin(): return self.json(401,{"error":"Требуется вход администратора"})
                    rows=db.execute("""SELECT substr(created_at,1,7) month,COUNT(*) bookings,
                      SUM(CASE WHEN status='cancelled' THEN 1 ELSE 0 END) cancelled,
                      ROUND(SUM(CASE WHEN status IN ('confirmed','checked_in','checked_out') THEN total ELSE 0 END),2) revenue
                      FROM bookings GROUP BY substr(created_at,1,7) ORDER BY month DESC LIMIT 12""").fetchall()
                    return self.json(200,{"items":[dict(x) for x in rows]})
                if path=="/api/rooms":
                    rows=db.execute("SELECT * FROM rooms WHERE active=1 ORDER BY price").fetchall()
                    return self.json(200,{"items":[dict(x) for x in rows]})
                if path=="/api/faqs":
                    rows=db.execute("SELECT * FROM faqs WHERE active=1 ORDER BY sort_order").fetchall()
                    return self.json(200,{"items":[dict(x) for x in rows]})
                if path=="/api/reviews":
                    rows=db.execute("SELECT name,rating,body,created_at FROM reviews WHERE approved=1 ORDER BY id DESC").fetchall()
                    return self.json(200,{"items":[dict(x) for x in rows]})
                if path=="/api/availability":
                    ci=q.get("checkin",[""])[0]; co=q.get("checkout",[""])[0]
                    if not ci or not co or co<=ci: return self.json(400,{"error":"Укажите корректные даты"})
                    items=[]
                    for room in db.execute("SELECT * FROM rooms WHERE active=1"):
                        booked=db.execute("""SELECT COALESCE(SUM(1),0) FROM bookings WHERE room_id=? AND status IN ('new','confirmed','checked_in')
                          AND checkin < ? AND checkout > ?""",(room["id"],co,ci)).fetchone()[0]
                        available=max(0,room["quantity"]-booked)
                        items.append({"room":room["name"],"available":available,"price":room["price"]})
                    return self.json(200,{"items":items})
                if path=="/api/me":
                    return self.json(200,{"user":self.actor()})
                if path=="/api/bookings" and not self.admin():
                    return self.json(401,{"error":"Требуется вход администратора"})
                if path=="/api/bookings":
                    rows=db.execute("SELECT * FROM bookings ORDER BY id DESC").fetchall()
                    return self.json(200,{"items":[dict(x) for x in rows]})
                if path=="/api/messages":
                    if not self.admin(): return self.json(401,{"error":"Требуется вход администратора"})
                    return self.json(200,{"items":[dict(x) for x in db.execute("SELECT * FROM messages ORDER BY id DESC")]})
                if path=="/api/users":
                    if not self.admin(): return self.json(401,{"error":"Требуется вход администратора"})
                    return self.json(200,{"items":[dict(x) for x in db.execute("SELECT id,name,email,role,active,created_at FROM users ORDER BY id DESC")]})
                if path=="/api/reviews/all":
                    if not self.admin(): return self.json(401,{"error":"Требуется вход администратора"})
                    return self.json(200,{"items":[dict(x) for x in db.execute("SELECT * FROM reviews ORDER BY id DESC")]})
                if path=="/api/coupons":
                    if not self.admin(): return self.json(401,{"error":"Требуется вход администратора"})
                    return self.json(200,{"items":[dict(x) for x in db.execute("SELECT * FROM coupons ORDER BY id DESC")]})
                if path=="/api/tasks":
                    if not self.admin(): return self.json(401,{"error":"Требуется вход администратора"})
                    return self.json(200,{"items":[dict(x) for x in db.execute("SELECT * FROM tasks ORDER BY id DESC")]})
                if path=="/api/settings":
                    if not self.admin(): return self.json(401,{"error":"Требуется вход администратора"})
                    return self.json(200,{"items":{x["key"]:x["value"] for x in db.execute("SELECT * FROM settings")}})
                if path=="/api/stats":
                    if not self.admin(): return self.json(401,{"error":"Требуется вход администратора"})
                    stats={k:db.execute(sql).fetchone()[0] for k,sql in {
                      "bookings":"SELECT COUNT(*) FROM bookings","new_bookings":"SELECT COUNT(*) FROM bookings WHERE status='new'",
                      "confirmed":"SELECT COUNT(*) FROM bookings WHERE status='confirmed'","messages":"SELECT COUNT(*) FROM messages",
                      "users":"SELECT COUNT(*) FROM users","revenue":"SELECT COALESCE(SUM(total),0) FROM bookings WHERE status IN ('confirmed','checked_in','checked_out')",
                      "reviews_pending":"SELECT COUNT(*) FROM reviews WHERE approved=0"}.items()}
                    return self.json(200,stats)
                if path=="/api/audit":
                    if not self.admin(): return self.json(401,{"error":"Требуется вход администратора"})
                    return self.json(200,{"items":[dict(x) for x in db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 100")]})
            if path=="/" : self.path="/index.html"
            if path=="/admin": self.path="/admin.html"
            return super().do_GET()
        except Exception as e: return self.json(500,{"error":str(e)})
    def do_POST(self):
        path=urlparse(self.path).path
        try: d=self.body()
        except Exception: return self.json(400,{"error":"Некорректные данные"})
        try:
            with connect() as db:
                if path=="/api/register":
                    name=str(d.get("name","")).strip(); email=str(d.get("email","")).strip().lower(); password=str(d.get("password",""))
                    if len(name)<2 or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$",email) or len(password)<8: return self.json(400,{"error":"Проверьте имя, email и пароль (минимум 8 символов)"})
                    try: cur=db.execute("INSERT INTO users(name,email,password_hash,created_at) VALUES(?,?,?,?)",(name,email,pw(password),now()))
                    except sqlite3.IntegrityError: return self.json(409,{"error":"Такой email уже зарегистрирован"})
                    db.execute("INSERT INTO audit(action,entity,entity_id,created_at) VALUES(?,?,?,?)",("register","user",str(cur.lastrowid),now()))
                    return self.json(201,{"id":cur.lastrowid,"message":"Аккаунт создан"})
                if path=="/api/login":
                    email=str(d.get("email","")).lower().strip(); password=str(d.get("password",""))
                    u=db.execute("SELECT id,name,email,role,active FROM users WHERE email=? AND password_hash=?",(email,pw(password))).fetchone()
                    if not u or not u["active"]: return self.json(401,{"error":"Неверный логин или пароль"})
                    token=secrets.token_urlsafe(32); TOKENS[token]=dict(u)
                    return self.json(200,{"token":token,"user":dict(u)})
                if path=="/api/logout":
                    token=self.headers.get("Authorization","").replace("Bearer ",""); TOKENS.pop(token,None)
                    return self.json(200,{"message":"Вы вышли из системы"})
                if path=="/api/bookings":
                    required=("checkin","checkout","room","name","email")
                    if any(not str(d.get(k,"")).strip() for k in required): return self.json(400,{"error":"Заполните обязательные поля"})
                    ci=date.fromisoformat(d["checkin"]); co=date.fromisoformat(d["checkout"]); guests=int(d.get("guests",1))
                    if co<=ci or ci<date.today(): return self.json(400,{"error":"Проверьте даты заезда и выезда"})
                    room=db.execute("SELECT * FROM rooms WHERE name=? AND active=1",(d["room"],)).fetchone()
                    if not room or guests<1 or guests>room["capacity"]: return self.json(400,{"error":"Номер не найден или превышена вместимость"})
                    conflicts=db.execute("""SELECT COUNT(*) FROM bookings WHERE room_id=? AND status IN ('new','confirmed','checked_in') AND checkin<? AND checkout>?""",(room["id"],co.isoformat(),ci.isoformat())).fetchone()[0]
                    if conflicts>=room["quantity"]: return self.json(409,{"error":"На выбранные даты нет свободных номеров"})
                    nights=(co-ci).days; total=nights*room["price"]
                    coupon=str(d.get("coupon","")).upper().strip()
                    if coupon:
                        cp=db.execute("SELECT * FROM coupons WHERE code=? AND active=1 AND (expires='' OR expires>=?)",(coupon,ci.isoformat())).fetchone()
                        if cp: total=round(total*(100-cp["percent"])/100,2)
                    code=uuid.uuid4().hex[:8].upper()
                    actor=self.actor()
                    cur=db.execute("""INSERT INTO bookings(code,user_id,guest_name,email,room_id,room_name,checkin,checkout,guests,nights,total,note,created_at,updated_at)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(code,actor["id"] if actor else None,str(d["name"]).strip(),str(d["email"]).strip(),room["id"],room["name"],ci.isoformat(),co.isoformat(),guests,nights,total,str(d.get("note","")),now(),now()))
                    db.execute("INSERT INTO audit(action,entity,entity_id,details,created_at) VALUES(?,?,?,?,?)",("create","booking",str(cur.lastrowid),code,now()))
                    return self.json(201,{"booking":{"id":cur.lastrowid,"code":code,"room":room["name"],"checkin":ci.isoformat(),"checkout":co.isoformat(),"nights":nights,"guests":guests,"total":total}})
                if path=="/api/messages":
                    if not all(str(d.get(k,"")).strip() for k in ("name","email","message")): return self.json(400,{"error":"Заполните все поля"})
                    cur=db.execute("INSERT INTO messages(name,email,message,created_at) VALUES(?,?,?,?)",(d["name"],d["email"],d["message"],now()))
                    return self.json(201,{"id":cur.lastrowid,"message":"Сообщение сохранено"})
                if path=="/api/reviews":
                    rating=int(d.get("rating",0)); body=str(d.get("body","")).strip(); name=str(d.get("name","Гость")).strip()
                    if rating not in range(1,6) or len(body)<5: return self.json(400,{"error":"Оценка 1–5, отзыв минимум 5 символов"})
                    cur=db.execute("INSERT INTO reviews(name,rating,body,created_at) VALUES(?,?,?,?)",(name,rating,body,now()))
                    return self.json(201,{"id":cur.lastrowid,"message":"Отзыв отправлен на модерацию"})
                if path=="/api/change-password":
                    actor=self.actor()
                    if not actor: return self.json(401,{"error":"Войдите в аккаунт"})
                    old=str(d.get("current_password","")); new=str(d.get("new_password",""))
                    if len(new)<8: return self.json(400,{"error":"Новый пароль должен содержать минимум 8 символов"})
                    cur=db.execute("UPDATE users SET password_hash=? WHERE id=? AND password_hash=?",(pw(new),actor["id"],pw(old)))
                    if not cur.rowcount: return self.json(400,{"error":"Текущий пароль указан неверно"})
                    TOKENS.clear()
                    return self.json(200,{"message":"Пароль изменён. Войдите снова."})
                if not self.admin(): return self.json(401,{"error":"Требуется вход администратора"})
                if path=="/api/rooms":
                    cur=db.execute("INSERT INTO rooms(name,description,capacity,price,quantity,amenities) VALUES(?,?,?,?,?,?)",(d["name"],d.get("description",""),int(d.get("capacity",2)),float(d.get("price",0)),int(d.get("quantity",1)),json.dumps(d.get("amenities",[]),ensure_ascii=False)))
                    db.execute("INSERT INTO audit(action,entity,entity_id,created_at) VALUES(?,?,?,?)",("create","room",str(cur.lastrowid),now())); return self.json(201,{"id":cur.lastrowid})
                if path=="/api/coupons":
                    code=str(d["code"]).upper()
                    cur=db.execute("INSERT INTO coupons(code,percent,expires) VALUES(?,?,?)",(code,int(d["percent"]),d.get("expires","")))
                    return self.json(201,{"id":cur.lastrowid})
                if path=="/api/tasks":
                    cur=db.execute("INSERT INTO tasks(title,details,assigned_to,due_date) VALUES(?,?,?,?)",(d["title"],d.get("details",""),d.get("assigned_to",""),d.get("due_date","")))
                    return self.json(201,{"id":cur.lastrowid})
                if path=="/api/faqs":
                    cur=db.execute("INSERT INTO faqs(question,answer,sort_order) VALUES(?,?,?)",(d["question"],d["answer"],int(d.get("sort_order",0))))
                    return self.json(201,{"id":cur.lastrowid})
                return self.json(404,{"error":"Маршрут не найден"})
        except (ValueError,KeyError,TypeError,sqlite3.IntegrityError) as e: return self.json(400,{"error":str(e)})
        except Exception as e: return self.json(500,{"error":str(e)})
    def do_PATCH(self):
        path=urlparse(self.path).path
        try: d=self.body()
        except Exception: return self.json(400,{"error":"Некорректные данные"})
        if path=="/api/profile":
            actor=self.actor()
            if not actor: return self.json(401,{"error":"Войдите в аккаунт"})
            name=str(d.get("name","")).strip(); phone=str(d.get("phone","")).strip()
            if len(name)<2: return self.json(400,{"error":"Имя должно содержать минимум 2 символа"})
            with connect() as db:
                db.execute("UPDATE users SET name=?,phone=? WHERE id=?",(name,phone,actor["id"]))
            actor["name"]=name
            return self.json(200,{"message":"Профиль обновлён"})
        if not self.admin(): return self.json(401,{"error":"Требуется вход администратора"})
        try:
            with connect() as db:
                m=re.match(r"/api/(bookings|messages|reviews|rooms|users|tasks|coupons|faqs)/(\\d+)",path)
                if not m: return self.json(404,{"error":"Маршрут не найден"})
                table,eid=m.group(1),int(m.group(2))
                allowed={
                 "bookings":{"status":["new","confirmed","cancelled","checked_in","checked_out"],"payment_status":["unpaid","paid","refunded"]},
                 "messages":{"status":["new","read","replied","archived"]},
                 "reviews":{"approved":[0,1]}, "rooms":{"active":[0,1]},
                 "users":{"active":[0,1],"role":["guest","admin"]},
                 "tasks":{"status":["open","in_progress","done"]},
                 "coupons":{"active":[0,1]}, "faqs":{"active":[0,1]}}
                sets=[]; vals=[]
                for k,v in d.items():
                    if k in allowed.get(table,{}) and v in allowed[table][k]: sets.append(k+"=?"); vals.append(v)
                if not sets: return self.json(400,{"error":"Нет допустимых полей для изменения"})
                vals.append(eid); cur=db.execute(f"UPDATE {table} SET {','.join(sets)} WHERE id=?",vals)
                if not cur.rowcount: return self.json(404,{"error":"Запись не найдена"})
                if table=="bookings": db.execute("UPDATE bookings SET updated_at=? WHERE id=?",(now(),eid))
                db.execute("INSERT INTO audit(action,entity,entity_id,details,created_at) VALUES(?,?,?,?,?)",("update",table,str(eid),json.dumps(d,ensure_ascii=False),now()))
                return self.json(200,{"message":"Изменено"})
        except Exception as e: return self.json(400,{"error":str(e)})
    def do_DELETE(self):
        path=urlparse(self.path).path
        if not self.admin(): return self.json(401,{"error":"Требуется вход администратора"})
        m=re.match(r"/api/(rooms|users|tasks|coupons|faqs)/(\\d+)",path)
        if not m: return self.json(404,{"error":"Маршрут не найден"})
        table,eid=m.group(1),int(m.group(2))
        with connect() as db:
            cur=db.execute(f"DELETE FROM {table} WHERE id=?",(eid,))
            if not cur.rowcount: return self.json(404,{"error":"Запись не найдена"})
            db.execute("INSERT INTO audit(action,entity,entity_id,created_at) VALUES(?,?,?,?)",("delete",table,str(eid),now()))
        return self.json(200,{"message":"Удалено"})
    def log_message(self,fmt,*args): print("[Moon Light] "+fmt%args)

if __name__=="__main__":
    init_db()
    print("Moon Light Stage 2: http://127.0.0.1:8000")
    print("Администратор: admin@moonlight.local / MoonLight2026!")
    print("Остановка: Ctrl+C")
    ThreadingHTTPServer(("127.0.0.1",8000),Handler).serve_forever()
