import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta, timezone
from limiter import limiter
from face_system import face_engine
import models, database
from routers import user, auth, face, paramedic, admin
from email.mime.text import MIMEText
from dotenv import load_dotenv
import smtplib

# ---------------------------------------------------------------------------
# Create all tables that do not already exist.
# NOTE: AllAccountsView is mapped with is_view=True and will NOT be created
# by SQLAlchemy — it must exist as a PostgreSQL VIEW in the database already.
# ---------------------------------------------------------------------------
models.Base.metadata.create_all(bind=database.engine)

app = FastAPI(title="Salamah Medical API — Unified")

# ---------------------------------------------------------------------------
# Static files — KEPT from Codebase B
# ---------------------------------------------------------------------------
if not os.path.exists("static"):
    os.makedirs("static")

app.mount("/static", StaticFiles(directory="static"), name="static")

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ---------------------------------------------------------------------------
# CORS
# CONFLICT RESOLVED:
#   Codebase A listed specific allowed origins (localhost, 10.0.2.2, LAN IP).
#   Codebase B used allow_origins=["*"].
#   Decision: list the known safe origins from Codebase A. The wildcard from
#   Codebase B is a security risk in production. Add your production domain
#   or the Flutter device IP to this list as needed.
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://127.0.0.1",
        "http://10.0.2.2",
        "http://192.168.1.13",
        # Add your production domain or additional device IPs below:
        # "https://your-production-domain.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ADDED: admin and paramedic routers (both were missing from Codebase A's main)
# ---------------------------------------------------------------------------
app.include_router(auth.router)
app.include_router(user.router)
app.include_router(face.router)
app.include_router(paramedic.router)
app.include_router(admin.router)


# ---------------------------------------------------------------------------
# APScheduler background jobs
# MERGED: both cleanup jobs from both codebases
# ---------------------------------------------------------------------------

def cleanup_unverified_accounts():
    """Delete citizen accounts that were never verified within 24 hours."""
    db = database.SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        result = db.query(models.User).filter(
            models.User.isverified == False,
            models.User.createdat  <  cutoff
        ).delete(synchronize_session=False)
        db.commit()
        if result > 0:
            print(f"🧹 Cleanup: {result} unverified account(s) deleted.")
    except Exception as e:
        db.rollback()
        print(f"❌ Cleanup Error: {e}")
    finally:
        db.close()


def cleanup_old_scan_logs():
    """Delete ScanLog entries older than 15 days — KEPT from Codebase B."""
    db = database.SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=15)
        result = db.query(models.ScanLog).filter(
            models.ScanLog.scantime < cutoff
        ).delete(synchronize_session=False)
        db.commit()
        if result > 0:
            print(f"🧹 Deleted {result} old scan log(s).")
    except Exception as e:
        db.rollback()
        print(f"❌ ScanLog Cleanup Error: {e}")
    finally:
        db.close()


scheduler = BackgroundScheduler()
scheduler.add_job(cleanup_unverified_accounts, "interval", hours=24)
scheduler.add_job(cleanup_old_scan_logs,       "interval", hours=24)
scheduler.start()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/")
def home():
    return {"message": "Salamah Medical API is running ✅"}



class Report(BaseModel):
    name: str
    national_id: str
    blood_type: str | None = None
    allergies: str | None = None
    gender: str | None = "N/A"
    nationality: str | None = "N/A"
    dob: str | None = "N/A"
    age: str | int | None = "N/A"
    emergency_contact: str | None = "N/A"
    chief_complaint: str
    symptoms: str
    mechanism: str
    diagnosis: str

    bp: str
    pulse: str
    rr: str
    temp: str
    spo2: str
    gcs: str

    severity: int
    consciousness_idx: int

    medications: str
    iv_access: str
    treatment: str
    notes: str
    doctor_name: str
    cpr_performed: bool  = False
    oxygen_given: bool   = False
    defibrillator_used: bool  = False
    iv_established: bool = False
    splint_applied: bool = False
    wound_dressed: bool = False
  

    chronic_diseases: str | None = None
    malignant_history: str | None = None

def build_html(data: Report):
    # 0: Alert, 1: Verbal, 2: Pain, 3: Unresponsive
    consciousness_map = {
        0: {"text": "Alert (واعي)", "color": "#22C55E"},        # أخضر
        1: {"text": "Verbal Response (استجابة لفظية)", "color": "#F59E0B"}, # برتقالي
        2: {"text": "Pain Response (استجابة للألم)", "color": "#F59E0B"},   # برتقالي
        3: {"text": "Unresponsive (غير مستجيب)", "color": "#DC2626"}      # أحمر
    }

    # جلب البيانات بناءً على الاندكس المرسل، مع وضع قيمة افتراضية في حال الخطأ
    con_data = consciousness_map.get(data.consciousness_idx, {"text": "Unknown", "color": "#64748B"})
    # نحدد مسبقاً القائمة أو نستخدم logic لكل حالة
    if data.severity == 0:
        color = "#DC2626"   # 🔴 Critical
        severity_text = "Critical"
        icon = "🚨"
    elif data.severity == 1:
        color = "#E11D48"   # 🔴 Severe
        severity_text = "Severe"
        icon = "⚠️"
    elif data.severity == 2:
        color = "#F59E0B"   # 🟠 Moderate
        severity_text = "Moderate"
        icon = "🟠"
    else:
        color = "#22C55E"   # 🟢 Mild
        severity_text = "Mild"
        icon = "🟢"

    return f"""
    <html>
    <body style="font-family:Arial;background:#f4f7fb;padding:20px;">
    <div style="max-width:750px;margin:auto;background:white;border-radius:20px;padding:25px;">

    <h2 style="color:#1A3A5C;text-align:center;">🚑 Emergency Report</h2>
     <div style="background:#EEF5FF;padding:10px;border-radius:10px;text-align:center;margin-bottom:10px;">
    👨‍⚕️ Dr. {data.doctor_name}
     </div>

    <div style="background:{color};color:white;padding:15px;border-radius:12px;text-align:center;font-size:20px;">
        {icon} {severity_text}
    </div>
    
    <h3>👤 Patient Info</h3>
    <p><b>Name:</b> {data.name}</p>
    <tr><td><b>Gender:</b> {data.gender}</td><td><b>Nationality:</b> {data.nationality}</td></tr>
    <p><b>ID:</b> {data.national_id}</p>
    <p><b>Blood Type:</b> {data.blood_type or 'N/A'}</p>
    <tr><td><b>DOB:</b> {data.dob}</td></tr>
    <p></p>
    <p><b>emergency_contact:</b> {data.emergency_contact}</p>

    <p><b>Allergies:</b> {data.allergies or 'None'}</p>
    <p><b>Malignant History:</b> {data.malignant_history}</p>
    <p><b>Chronic Diseases:</b> {data.chronic_diseases}</p>

    <h3>🧬 Chief Complaint:</h3>
    <p> {data.chief_complaint}</p>
    
    <h3>🩺 Diagnosis</h3>
    <p>{data.diagnosis}</p>
    <h3>📋 Clinical Info</h3>
        
        <p><b>Symptoms:</b> {data.symptoms}</p>
        <p><b>Mechanism:</b> {data.mechanism}</p>
    
    

    <h3>❤️ Vital Signs</h3>
    <ul>
        <li>BP: {data.bp}</li>
        <li>Pulse: {data.pulse}</li>
        <li>RR: {data.rr}</li>
        <li>Temp: {data.temp}</li>
        <li>SpO2: {data.spo2}</li>
        <li>GCS: {data.gcs}</li>
    </ul>

    <h3>🚑 Interventions Performed</h3>
        <ul>
            {"<li>CPR Performed</li>" if data.cpr_performed else ""}
            {"<li>Oxygen Given</li>" if data.oxygen_given else ""}
            {"<li>Defibrillator Used</li>" if data.defibrillator_used else ""}
            {"<li>IV Access Established</li>" if data.iv_established else ""}
            {"<li>Splint Applied</li>" if data.splint_applied else ""}
            {"<li>Wound Dressed</li>" if data.wound_dressed else ""}
        </ul>
    <h3>🧠 Level of Consciousness</h3>
            <div style="display:inline-block; padding:8px 15px; border-radius:8px; background:{con_data['color']}; color:white; font-weight:bold;">
                {con_data['text']}
            </div>
    <h3>💊 Treatment</h3>
    <p><b>Medications:</b> {data.medications}</p>
    <p><b>IV Access:</b> {data.iv_access}</p>
    <p><b>Treatment:</b> {data.treatment}</p>

    <h3>📝 Notes</h3>
    <p>{data.notes}</p>

    </div>
    </body>
    </html>
    """
@app.post("/send-report-email")
def send_email(data: Report):
    sender = os.getenv("SENDER_EMAIL")
    password = os.getenv("APP_PASSWORD")

    receiver = os.getenv("SENDER_EMAIL")

    html = build_html(data)

    msg = MIMEText(html, "html")
    msg["Subject"] = "Emergency Report"
    msg["From"] = sender
    msg["To"] = receiver

    server = smtplib.SMTP("smtp.gmail.com", 587)
    server.starttls()
    server.login(sender, password)
    server.send_message(msg)
    server.quit()

    return {"message": "sent"}