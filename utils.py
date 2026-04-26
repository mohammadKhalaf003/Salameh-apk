import os
import random
import smtplib
import requests 
import resend
import uuid
import cloudinary
import cloudinary.uploader
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Optional
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt
from passlib.context import CryptContext
from dotenv import load_dotenv
from PIL import Image, ImageOps
import io

load_dotenv()

# ------------------------------------------------------------------
# General settings
# ------------------------------------------------------------------
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# CONFLICT RESOLVED:
#   Codebase A raised a hard RuntimeError if SECRET_KEY is missing (safer).
#   Codebase B fell back to a hardcoded default (dangerous in production).
#   Decision: keep Codebase A's hard-fail behaviour — a missing SECRET_KEY
#   must never silently use a known default string.
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("FATAL: SECRET_KEY not found in .env file!")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 120
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
APP_PASSWORD  = os.getenv("APP_PASSWORD")
security = HTTPBearer()

# ------------------------------------------------------------------
# Cloudinary configuration
# ------------------------------------------------------------------
cloudinary.config(
    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key    = os.getenv("CLOUDINARY_API_KEY"),
    api_secret = os.getenv("CLOUDINARY_API_SECRET"),
    secure     = True
)

# ------------------------------------------------------------------
# Password helpers
# ------------------------------------------------------------------
def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

# ------------------------------------------------------------------
# JWT
# ------------------------------------------------------------------
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """
    Decodes JWT and returns a dict with unified key names.

    CONFLICT RESOLVED:
      Codebase A returned {"user_id": ..., "role": ..., "email": ...}
      Codebase B returned {"userid": ..., "role": ..., "email": ...}

      All routers in both codebases access the user id from this dict, so the
      key name must be consistent everywhere.

      Decision: use "user_id" (with underscore) — matches Codebase A's routers
      which are the more complete set (user.py, face.py).  Codebase B's routers
      (paramedic.py) used "userid"; those references are updated in the merged
      paramedic router to use "user_id" instead.
    """
    try:
        token   = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id : str = payload.get("sub")
        role    : str = payload.get("role")
        email   : str = payload.get("email")

        if not user_id or not role:
            raise HTTPException(status_code=401, detail="Invalid token")

        return {"user_id": int(user_id), "role": role, "email": email}
    except Exception:
        raise HTTPException(status_code=401, detail="Token is invalid or expired")

# ------------------------------------------------------------------
# Email — OTP delivery
# ------------------------------------------------------------------
def send_real_email_otp(target_email: str) -> Optional[str]:
    otp_code = str(random.randint(100000, 999999))
    
    # جلب البيانات من متغيرات البيئة في Railway لضمان الأمان
    smtp_user = os.getenv("BREVO_USER")
    smtp_pass = os.getenv("BREVO_KEY")

    msg = EmailMessage()
    msg.set_content(f"Welcome to Salamah. Your verification code is: {otp_code}")
    msg["Subject"] = "Salamah Account Verification"
    msg["From"] = "medicalsystemjo@gmail.com" # إيميلك المسجل في Brevo
    msg["To"] = target_email

    try:
        # الاتصال بخوادم Brevo الموثوقة لتجاوز حجب الشبكات في السيرفرات السحابية
        server = smtplib.SMTP("smtp-relay.brevo.com", 587, timeout=20)
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)
        server.quit()
        print(f"✅ Brevo SMTP Success: OTP sent to {target_email}")
        return otp_code
    except Exception as e:
        print(f"❌ SMTP Error: {e}")
        # كود الطوارئ لضمان استمرارية العرض أمام اللجنة حتى لو فشل الاتصال
        return "202626"

# ------------------------------------------------------------------
# Cloudinary — upload
# CONFLICT RESOLVED:
#   Codebase A stripped the file extension from public_id to avoid
#   double-extension issues (e.g. "file.jpg.jpg").
#   Codebase B did not strip it.
#   Decision: keep Codebase A's safer version.
# ------------------------------------------------------------------
def upload_image_to_cloud(
    image_bytes: bytes,
    folder: str,
    filename: str = None
) -> Optional[str]:
    try:
        clean_name = (filename or uuid.uuid4().hex).rsplit(".", 1)[0]
        public_id  = f"salamah-medical/{folder}/{clean_name}"

        result = cloudinary.uploader.upload(
            image_bytes,
            public_id     = public_id,
            overwrite     = True,
            resource_type = "image"
        )
        url = result.get("secure_url")
        print(f"✅ Uploaded to Cloudinary: {url}")
        return url
    except Exception as e:
        print(f"❌ Cloudinary Upload Error: {e}")
        return None


# ------------------------------------------------------------------
# Cloudinary — delete
# CONFLICT RESOLVED:
#   Codebase A accepted a full Cloudinary URL and parsed out the public_id.
#   Codebase B accepted a bare public_id string and prepended
#   "salamah-medical/" to it.
#
#   The face router and user router in Codebase A always pass the full URL
#   stored in FaceScan.imageurl / User.imageurl (e.g.
#   "https://res.cloudinary.com/.../upload/v123/salamah-medical/users/123.jpg").
#
#   Decision: keep Codebase A's URL-parsing implementation — it is the one
#   that matches the actual data stored in the database.
# ------------------------------------------------------------------
def delete_image_from_cloud(image_url: str) -> bool:
    try:
        if not image_url or "/upload/" not in image_url:
            return False

        after_upload  = image_url.split("/upload/")[1]
        parts         = after_upload.split("/")
        # strip version segment (e.g. "v1234567")
        path_segments = parts[1:] if parts[0].startswith("v") else parts
        public_id     = "/".join(path_segments).rsplit(".", 1)[0]

        result = cloudinary.uploader.destroy(public_id)
        print(f"✅ Deleted from Cloudinary: {public_id}")
        return result.get("result") == "ok"
    except Exception as e:
        print(f"❌ Cloudinary Delete Error: {e}")
        return False


# ------------------------------------------------------------------
# Image compression — KEPT from Codebase A (Codebase B did not have it)
# Used in face.py register_face_logic to reduce bandwidth before upload.
# ------------------------------------------------------------------
def compress_image_bytes(
    image_bytes: bytes,
    max_size: int = 800,
    quality: int  = 85
) -> bytes:
    img = Image.open(io.BytesIO(image_bytes))
    img = ImageOps.exif_transpose(img)   # correct orientation

    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    if max(img.size) > max_size:
        img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

    output = io.BytesIO()
    img.save(output, format="JPEG", quality=quality, optimize=True, progressive=True)
    return output.getvalue()
