import os
from dotenv import load_dotenv
load_dotenv()

import secrets
from datetime import datetime, timedelta, timezone
from io import BytesIO

import qrcode

from fastapi import FastAPI, Request, Depends, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from sqlalchemy.orm import Session
from sqlalchemy import desc

from .database import Base, engine, get_db, SessionLocal
from .models import User, ClassSession, Attendance
from .auth import verify_password, create_access_token, get_user_from_cookie, COOKIE_NAME
from .seed import seed_users

from zoneinfo import ZoneInfo

TR_TZ = ZoneInfo("Europe/Istanbul")


def utc_to_tr(dt: datetime) -> datetime | None:
    """
    DB'deki naive datetime'ı UTC kabul eder, TR saatine çevirir.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TR_TZ)


def fmt_tr(dt: datetime | None) -> str:
    d = utc_to_tr(dt)
    return d.strftime("%d.%m.%Y %H:%M:%S") if d else ""


def utcnow() -> datetime:
    return datetime.utcnow()


app = FastAPI(title="QR Yoklama Sistemi")

BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
LATE_MINUTES_DEFAULT = int(os.getenv("LATE_MINUTES_DEFAULT", "10"))

# Paths
APP_DIR = os.path.dirname(os.path.abspath(__file__))   # backend/app
BACKEND_DIR = os.path.dirname(APP_DIR)                 # backend

templates = Jinja2Templates(directory=os.path.join(BACKEND_DIR, "templates"))
static_dir = os.path.join(BACKEND_DIR, "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# DB init
Base.metadata.create_all(bind=engine)

# Seed users
db_seed = SessionLocal()
try:
    seed_users(db_seed)
finally:
    db_seed.close()


# ---------------- WebSocket manager ----------------
class WSManager:
    def __init__(self):
        self.active = {}  # session_id -> set(ws)

    async def connect(self, session_id: int, ws: WebSocket):
        await ws.accept()
        self.active.setdefault(session_id, set()).add(ws)

    def disconnect(self, session_id: int, ws: WebSocket):
        if session_id in self.active and ws in self.active[session_id]:
            self.active[session_id].remove(ws)

    async def broadcast(self, session_id: int, message: dict):
        conns = list(self.active.get(session_id, []))
        for ws in conns:
            try:
                await ws.send_json(message)
            except Exception:
                self.disconnect(session_id, ws)


ws_manager = WSManager()


# ---------------- Helpers ----------------
def require_login(request: Request):
    return get_user_from_cookie(request)


def require_teacher(request: Request):
    payload = require_login(request)
    if not payload or payload.get("role") != "teacher":
        return None
    return payload


def require_student(request: Request):
    payload = require_login(request)
    if not payload or payload.get("role") != "student":
        return None
    return payload


def compute_status(session: ClassSession, att: Attendance | None, late_minutes: int = LATE_MINUTES_DEFAULT) -> str:
    """
    Status hesabı: started_at'tan itibaren late_minutes dakika sonrası GEÇ.
    DB UTC olsa bile fark hesaplandığı için doğru.
    """
    if not att:
        return "YOK"
    diff_min = (att.timestamp - session.started_at).total_seconds() / 60.0
    return "GEÇ" if diff_min > late_minutes else "ZAMANINDA"


def safe_next(n: str | None) -> str | None:
    """
    Open-redirect olmasın diye sadece site içi path kabul ediyoruz.
    """
    if not n:
        return None
    n = n.strip()
    if n.startswith("/") and not n.startswith("//"):
        return n
    return None


# ---------------- Routes ----------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    payload = get_user_from_cookie(request)
    if not payload:
        return RedirectResponse("/login", status_code=302)

    # Hoca -> panel
    if payload["role"] == "teacher":
        return RedirectResponse("/teacher", status_code=302)

    # ✅ Öğrenci QR olmadan derse girmesin (dersi QR belirler)
    return HTMLResponse(
        f"<html><body style='font-family:Arial;padding:24px'>"
        f"<h3>Öğrenci</h3>"
        f"Giriş yaptın: {payload.get('name')}<br/><br/>"
        f"Yoklama almak için hocanın oluşturduğu QR kodunu okut.<br/><br/>"
        f"<a href='/logout'>Çıkış</a>"
        f"</body></html>"
    )


# ---- Register kapalı ----
@app.get("/register", response_class=HTMLResponse)
def register_closed():
    return HTMLResponse("Kayıt kapalı. Kullanıcıları idare oluşturur.", status_code=403)


@app.post("/register")
def register_closed_post():
    return HTMLResponse("Kayıt kapalı. Kullanıcıları idare oluşturur.", status_code=403)


# ---- Login ----
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    # ✅ QR'dan gelen kullanıcı login olunca geri dönebilsin
    next_url = request.query_params.get("next") or ""
    return templates.TemplateResponse("login.html", {"request": request, "next": next_url})


@app.post("/login")
def login(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("", alias="next"),
):
    user = db.query(User).filter(User.username == username.strip()).first()
    if not user or not verify_password(password, user.password_hash):
        return HTMLResponse("Hatalı giriş.", status_code=400)

    token = create_access_token({"sub": str(user.id), "role": user.role, "name": user.full_name})

    # ✅ next varsa oraya dön (QR senaryosu)
    if next and next.startswith("/"):
        redirect_to = next
    else:
        redirect_to = "/teacher" if user.role == "teacher" else "/"

    resp = RedirectResponse(redirect_to, status_code=302)
    resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax")
    return resp



@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie(COOKIE_NAME)
    return resp


# ---- Teacher dashboard ----
@app.get("/teacher", response_class=HTMLResponse)
def teacher_dashboard(request: Request, db: Session = Depends(get_db)):
    payload = require_teacher(request)
    if not payload:
        return RedirectResponse("/login", status_code=302)

    teacher_id = int(payload["sub"])

    active_session = (
        db.query(ClassSession)
        .filter(ClassSession.teacher_id == teacher_id, ClassSession.is_active == True)
        .order_by(desc(ClassSession.started_at))
        .first()
    )

    attendances = []
    qr_url = None
    attendances_view = []

    if active_session:
        qr_url = f"{BASE_URL}/s/{active_session.session_code}"
        attendances = (
            db.query(Attendance)
            .filter(Attendance.session_id == active_session.id)
            .order_by(desc(Attendance.timestamp))
            .all()
        )

        for a in attendances:
            stu = db.query(User).filter(User.id == a.student_id).first()
            attendances_view.append({
                "student_no": stu.username if stu else "",
                "full_name": stu.full_name if stu else "",
                "time_tr": fmt_tr(a.timestamp),
                "status": compute_status(active_session, a, LATE_MINUTES_DEFAULT),
                "timestamp_iso": a.timestamp.isoformat() + "Z",
            })

        # template için TR tarih alanları
        active_session.started_at_tr = fmt_tr(active_session.started_at)
        active_session.expires_at_tr = fmt_tr(active_session.expires_at)

    students_total = db.query(User).filter(User.role == "student").count()
    present_count = len({a.student_id for a in attendances}) if active_session else 0
    late_count = 0
    if active_session:
        for a in attendances:
            if compute_status(active_session, a, LATE_MINUTES_DEFAULT) == "GEÇ":
                late_count += 1
    absent_count = (students_total - present_count) if active_session else students_total

    return templates.TemplateResponse(
        "teacher_dashboard.html",
        {
            "request": request,
            "teacher_name": payload.get("name"),
            "active_session": active_session,
            "qr_url": qr_url,
            "attendances": attendances,
            "attendances_view": attendances_view,
            "late_minutes": LATE_MINUTES_DEFAULT,
            "students_total": students_total,
            "present_count": present_count,
            "late_count": late_count,
            "absent_count": absent_count,
            "expires_at_iso": (active_session.expires_at.isoformat() + "Z") if active_session else None,
            "now_iso": utcnow().isoformat() + "Z",
        },
    )


@app.post("/teacher/start")
def teacher_start(
    request: Request,
    db: Session = Depends(get_db),
    course_name: str = Form(...),
    duration_minutes: int = Form(60),
):
    payload = require_teacher(request)
    if not payload:
        return RedirectResponse("/login", status_code=302)

    teacher_id = int(payload["sub"])

    db.query(ClassSession).filter(
        ClassSession.teacher_id == teacher_id,
        ClassSession.is_active == True
    ).update({"is_active": False})
    db.commit()

    code = secrets.token_urlsafe(8).replace("-", "").replace("_", "")[:10]
    now = utcnow()

    session = ClassSession(
        course_name=course_name.strip(),
        session_code=code,
        teacher_id=teacher_id,
        is_active=True,
        started_at=now,
        expires_at=now + timedelta(minutes=int(duration_minutes)),
    )
    db.add(session)
    db.commit()

    return RedirectResponse("/teacher", status_code=302)


@app.post("/teacher/stop")
def teacher_stop(request: Request, db: Session = Depends(get_db)):
    payload = require_teacher(request)
    if not payload:
        return RedirectResponse("/login", status_code=302)

    teacher_id = int(payload["sub"])
    db.query(ClassSession).filter(
        ClassSession.teacher_id == teacher_id,
        ClassSession.is_active == True
    ).update({"is_active": False})
    db.commit()
    return RedirectResponse("/teacher", status_code=302)


@app.get("/teacher/history", response_class=HTMLResponse)
def teacher_history(request: Request, db: Session = Depends(get_db)):
    payload = require_teacher(request)
    if not payload:
        return RedirectResponse("/login", status_code=302)

    teacher_id = int(payload["sub"])
    sessions = (
        db.query(ClassSession)
        .filter(ClassSession.teacher_id == teacher_id)
        .order_by(desc(ClassSession.started_at))
        .all()
    )

    for s in sessions:
        s.started_at_tr = fmt_tr(s.started_at)
        s.expires_at_tr = fmt_tr(s.expires_at)

    return templates.TemplateResponse(
        "history.html",
        {"request": request, "sessions": sessions}
    )


# ---- Teacher session detail ----
@app.get("/teacher/session/{session_id}", response_class=HTMLResponse)
def teacher_session_detail(session_id: int, request: Request, db: Session = Depends(get_db)):
    payload = require_teacher(request)
    if not payload:
        return RedirectResponse("/login", status_code=302)

    teacher_id = int(payload["sub"])
    session = db.query(ClassSession).filter(ClassSession.id == session_id).first()
    if not session or session.teacher_id != teacher_id:
        return HTMLResponse("Yetkisiz / oturum bulunamadı.", status_code=403)

    students = (
        db.query(User)
        .filter(User.role == "student")
        .order_by(User.username.asc())
        .all()
    )

    attendances = (
        db.query(Attendance)
        .filter(Attendance.session_id == session.id)
        .order_by(Attendance.timestamp.asc())
        .all()
    )
    present_by_student_id = {a.student_id: a for a in attendances}

    present_list = []
    absent_list = []

    for s in students:
        att = present_by_student_id.get(s.id)
        if att:
            present_list.append({
                "username": s.username,
                "full_name": s.full_name,
                "timestamp": att.timestamp,
                "timestamp_tr": fmt_tr(att.timestamp),
                "status": compute_status(session, att, LATE_MINUTES_DEFAULT),
            })
        else:
            absent_list.append({
                "username": s.username,
                "full_name": s.full_name,
            })

    session.started_at_tr = fmt_tr(session.started_at)
    session.expires_at_tr = fmt_tr(session.expires_at)

    return templates.TemplateResponse(
        "session_detail.html",
        {
            "request": request,
            "session": session,
            "present_list": present_list,
            "absent_list": absent_list,
            "late_minutes": LATE_MINUTES_DEFAULT,
        }
    )


# ---- QR PNG ----
@app.get("/qr/{session_code}.png")
def qr_png(session_code: str):
    url = f"{BASE_URL}/s/{session_code}"
    img = qrcode.make(url)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


# ---- Student attend via QR ----
@app.get("/s/{session_code}", response_class=HTMLResponse)
def student_attend_page(session_code: str, request: Request, db: Session = Depends(get_db)):
    payload = require_student(request)
    if not payload:
        # ✅ login sonrası tekrar QR sayfasına dön
        return RedirectResponse(url=f"/login?next=/s/{session_code}", status_code=302)

    session = db.query(ClassSession).filter(ClassSession.session_code == session_code).first()
    if not session:
        return HTMLResponse("Geçersiz QR / oturum kodu.", status_code=404)

    now = utcnow()
    if (not session.is_active) or (now > session.expires_at):
        return HTMLResponse("Bu yoklama oturumu kapalı veya süresi dolmuş.", status_code=400)

    return templates.TemplateResponse(
        "student_attend.html",
        {"request": request, "session": session, "student_name": payload.get("name")}
    )


@app.post("/s/{session_code}/checkin")
async def student_checkin(session_code: str, request: Request, db: Session = Depends(get_db)):
    payload = require_student(request)
    if not payload:
        return RedirectResponse(url=f"/login?next=/s/{session_code}", status_code=302)

    student_id = int(payload["sub"])
    session = db.query(ClassSession).filter(ClassSession.session_code == session_code).first()
    if not session:
        return HTMLResponse("Geçersiz QR.", status_code=404)

    now = utcnow()
    if (not session.is_active) or (now > session.expires_at):
        return HTMLResponse("Oturum kapalı veya süresi dolmuş.", status_code=400)

    exists = db.query(Attendance).filter(
        Attendance.session_id == session.id,
        Attendance.student_id == student_id
    ).first()
    if exists:
        return HTMLResponse("Zaten yoklamaya katıldın.", status_code=200)

    attendance = Attendance(session_id=session.id, student_id=student_id)
    db.add(attendance)
    db.commit()
    db.refresh(attendance)

    student = db.query(User).filter(User.id == student_id).first()
    status = compute_status(session, attendance, LATE_MINUTES_DEFAULT)

    await ws_manager.broadcast(session.id, {
        "username": student.username if student else "",
        "full_name": student.full_name if student else "",
        "timestamp": attendance.timestamp.isoformat() + "Z",
        "time_tr": fmt_tr(attendance.timestamp),
        "status": status,
    })

    # ✅ öğrenciye ders adı da net gelsin
    return HTMLResponse(f"✅ {session.course_name} yoklaması alındı.", status_code=200)



# ---- Export (Resmi Rapor) ----
@app.get("/teacher/session/{session_id}/export.csv")
def export_session_csv(session_id: int, request: Request, db: Session = Depends(get_db)):
    payload = require_teacher(request)
    if not payload:
        return RedirectResponse("/login", status_code=302)

    from starlette.responses import StreamingResponse

    teacher_id = int(payload["sub"])
    teacher = db.query(User).filter(User.id == teacher_id).first()

    session = db.query(ClassSession).filter(ClassSession.id == session_id).first()
    if not session or session.teacher_id != teacher_id:
        return HTMLResponse("Yetkisiz / oturum bulunamadı.", status_code=403)

    late_minutes = LATE_MINUTES_DEFAULT

    attendances = (
        db.query(Attendance)
        .filter(Attendance.session_id == session.id)
        .order_by(Attendance.timestamp.asc())
        .all()
    )
    present_by_student_id = {a.student_id: a for a in attendances}

    students = (
        db.query(User)
        .filter(User.role == "student")
        .order_by(User.username.asc())
        .all()
    )

    def generate():
        sep = ";"
        yield "\ufeff"
        yield "PAMUKKALE ÜNİVERSİTESİ\r\n"
        yield "QR YOKLAMA RAPORU\r\n\r\n"

        yield f"Hoca{sep}{(teacher.full_name if teacher else payload.get('name'))}\r\n"
        yield f"Ders{sep}{session.course_name}\r\n"
        yield f"SessionID{sep}{session.id}\r\n"
        yield f"Başlangıç{sep}{fmt_tr(session.started_at)}\r\n"
        yield f"Bitiş{sep}{fmt_tr(session.expires_at)}\r\n"
        yield f"Geç Kuralı{sep}{late_minutes} dk sonrası GEÇ\r\n\r\n"

        yield "ÖĞRENCİ LİSTESİ\r\n"
        yield f"ÖğrenciNo{sep}AdSoyad{sep}Saat(TR){sep}Durum{sep}İmza\r\n"

        for s in students:
            att = present_by_student_id.get(s.id)
            saat = fmt_tr(att.timestamp) if att else ""
            durum = compute_status(session, att, late_minutes)
            yield f"{s.username}{sep}{s.full_name}{sep}{saat}{sep}{durum}{sep}\r\n"

    filename = f"yoklama_{session.course_name}_{session.id}_rapor.csv".replace(" ", "_")
    return StreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/teacher/session/{session_id}/export.xlsx")
def export_session_excel(session_id: int, request: Request, db: Session = Depends(get_db)):
    payload = require_teacher(request)
    if not payload:
        return RedirectResponse("/login", status_code=302)

    teacher_id = int(payload["sub"])
    teacher = db.query(User).filter(User.id == teacher_id).first()

    session = db.query(ClassSession).filter(ClassSession.id == session_id).first()
    if not session or session.teacher_id != teacher_id:
        return HTMLResponse("Yetkisiz / oturum bulunamadı.", status_code=403)

    late_minutes = LATE_MINUTES_DEFAULT

    attendances = (
        db.query(Attendance)
        .filter(Attendance.session_id == session.id)
        .order_by(Attendance.timestamp.asc())
        .all()
    )
    present_by_student_id = {a.student_id: a for a in attendances}

    students = (
        db.query(User)
        .filter(User.role == "student")
        .order_by(User.username.asc())
        .all()
    )

    def esc(x: str):
        return (
            (x or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;")
        )

    rows = []

    def row(*cells):
        out = "<Row>"
        for c in cells:
            out += f'<Cell><Data ss:Type="String">{esc(str(c))}</Data></Cell>'
        out += "</Row>"
        rows.append(out)

    row("PAMUKKALE ÜNİVERSİTESİ")
    row("QR YOKLAMA RAPORU")
    row("")
    row("Hoca", (teacher.full_name if teacher else payload.get("name")))
    row("Ders", session.course_name)
    row("SessionID", str(session.id))
    row("Başlangıç", fmt_tr(session.started_at))
    row("Bitiş", fmt_tr(session.expires_at))
    row("Geç Kuralı", f"{late_minutes} dk sonrası GEÇ")
    row("")
    row("ÖğrenciNo", "Ad Soyad", "Saat(TR)", "Durum", "İmza")

    for s in students:
        att = present_by_student_id.get(s.id)
        saat = fmt_tr(att.timestamp) if att else ""
        durum = compute_status(session, att, late_minutes)
        row(s.username, s.full_name, saat, durum, "")

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<?mso-application progid="Excel.Sheet"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
 xmlns:o="urn:schemas-microsoft-com:office:office"
 xmlns:x="urn:schemas-microsoft-com:office:excel"
 xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"
 xmlns:html="http://www.w3.org/TR/REC-html40">
 <Worksheet ss:Name="YoklamaRaporu">
  <Table>
   {''.join(rows)}
  </Table>
 </Worksheet>
</Workbook>
"""
    filename = f"yoklama_{session.course_name}_{session.id}_rapor.xls".replace(" ", "_")
    return Response(
        content=xml,
        media_type="application/vnd.ms-excel; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---- WebSocket (teacher realtime) ----
@app.websocket("/ws/session/{session_id}")
async def ws_session(session_id: int, websocket: WebSocket, db: Session = Depends(get_db)):
    token = websocket.cookies.get(COOKIE_NAME)
    if not token:
        await websocket.close(code=4401)
        return

    from jose import jwt, JWTError
    from .auth import SECRET_KEY, ALGORITHM

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        await websocket.close(code=4401)
        return

    if payload.get("role") != "teacher":
        await websocket.close(code=4403)
        return

    teacher_id = int(payload["sub"])
    session = db.query(ClassSession).filter(ClassSession.id == session_id).first()
    if not session or session.teacher_id != teacher_id:
        await websocket.close(code=4403)
        return

    await ws_manager.connect(session_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(session_id, websocket)
