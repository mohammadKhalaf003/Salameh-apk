import os
import random
import requests 
import uuid
import cloudinary
import cloudinary.uploader
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt
from passlib.context import CryptContext
from dotenv import load_dotenv
from PIL import Image, ImageOps
import io
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

load_dotenv()

# ------------------------------------------------------------------
# General settings
# ------------------------------------------------------------------
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

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
# Email — OTP delivery via SendGrid (SMTP-free, works on Railway)
# ------------------------------------------------------------------
def send_real_email_otp(target_email: str) -> Optional[str]:
    otp_code = str(random.randint(100000, 999999))

    api_key = os.getenv("SENDGRID_API_KEY")
    sender  = os.getenv("SENDER_EMAIL", "medicalsystemjo@gmail.com")

    if not api_key:
        print("❌ SENDGRID_API_KEY not found in environment variables")
        return None

    html_content = f"""
    <div style="font-family: Arial, sans-serif; max-width: 480px; margin: auto;
                background: #f4f7fb; padding: 30px; border-radius: 12px;">
      <div style="background: #1A3A5C; padding: 20px; border-radius: 10px 10px 0 0; text-align: center;">
        <h2 style="color: white; margin: 0;">🚑 Salamah Medical</h2>
      </div>
      <div style="background: white; padding: 30px; border-radius: 0 0 10px 10px; text-align: center;">
        <p style="color: #444; font-size: 16px;">Your verification code is:</p>
        <div style="font-size: 42px; font-weight: bold; letter-spacing: 10px;
                    color: #FF6A2B; margin: 20px 0;">{otp_code}</div>
        <p style="color: #888; font-size: 13px;">This code expires in 10 minutes.<br>
           If you did not request this, please ignore this email.</p>
      </div>
    </div>
    """

    message = Mail(
        from_email=sender,
        to_emails=target_email,
        subject="Salamah — Verification Code",
        html_content=html_content,
    )

    try:
        sg = SendGridAPIClient(api_key)
        response = sg.send(message)
        print(f"✅ SendGrid OTP sent to {target_email} | Status: {response.status_code}")
        return otp_code

    except Exception as e:
        print(f"❌ SendGrid Error sending to {target_email}: {e}")
        if hasattr(e, 'body'):
            print(f"   SendGrid body: {e.body}")
        return None


# ------------------------------------------------------------------
# Email — Generic HTML email via SendGrid
# Used by /send-report-email in main.py (replaces smtplib completely)
# ------------------------------------------------------------------
def send_html_email(to_email: str, subject: str, html_content: str) -> bool:
    api_key = os.getenv("SENDGRID_API_KEY")
    sender  = os.getenv("SENDER_EMAIL", "medicalsystemjo@gmail.com")

    if not api_key:
        print("❌ SENDGRID_API_KEY not found")
        return False

    message = Mail(
        from_email=sender,
        to_emails=to_email,
        subject=subject,
        html_content=html_content,
    )

    try:
        sg = SendGridAPIClient(api_key)
        response = sg.send(message)
        print(f"✅ SendGrid HTML email sent to {to_email} | Status: {response.status_code}")
        return True
    except Exception as e:
        print(f"❌ SendGrid HTML email error: {e}")
        if hasattr(e, 'body'):
            print(f"   SendGrid body: {e.body}")
        return False


# ------------------------------------------------------------------
# Cloudinary — upload
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
# ------------------------------------------------------------------
def delete_image_from_cloud(image_url: str) -> bool:
    try:
        if not image_url or "/upload/" not in image_url:
            return False

        after_upload  = image_url.split("/upload/")[1]
        parts         = after_upload.split("/")
        path_segments = parts[1:] if parts[0].startswith("v") else parts
        public_id     = "/".join(path_segments).rsplit(".", 1)[0]

        result = cloudinary.uploader.destroy(public_id)
        print(f"✅ Deleted from Cloudinary: {public_id}")
        return result.get("result") == "ok"
    except Exception as e:
        print(f"❌ Cloudinary Delete Error: {e}")
        return False


# ------------------------------------------------------------------
# Image compression
# ------------------------------------------------------------------
def compress_image_bytes(
    image_bytes: bytes,
    max_size: int = 800,
    quality: int  = 85
) -> bytes:
    img = Image.open(io.BytesIO(image_bytes))
    img = ImageOps.exif_transpose(img)

    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    if max(img.size) > max_size:
        img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

    output = io.BytesIO()
    img.save(output, format="JPEG", quality=quality, optimize=True, progressive=True)
    return output.getvalue()
