from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from limiter import limiter
import models, schemas, database, utils

router = APIRouter(prefix="/auth", tags=["Authentication"])


# =============================================================================
# POST /auth/login
# Unified login for all roles: citizen | paramedic | admin
#
# CONFLICT RESOLVED:
#   Codebase A queried each table (User → Paramedic → Admin) individually.
#   Codebase B used the AllAccountsView DB view for a single query, but its
#   implementation was incomplete — the OTP reactivation block was left with
#   a "..." comment placeholder and the OTP was never saved to the DB.
#
#   Decision: keep Codebase A's complete, safe per-table lookup as the
#   primary implementation. The AllAccountsView approach requires the view
#   to exist in the database and is not portable; Codebase A's approach works
#   against the raw tables and handles every edge case correctly.
#
#   TokenResponse key: "user_id" (unified — see schemas.py and utils.py notes).
# =============================================================================
@router.post("/login", response_model=schemas.TokenResponse)
@limiter.limit("5/minute")
def login(
    request : Request,
    data    : schemas.LoginRequest,
    db      : Session = Depends(database.get_db)
):
    user = None
    role = None

    # 1. Search User table
    found = db.query(models.User).filter(
        models.User.email == data.email.strip().lower()
    ).first()
    if found:
        user, role = found, "citizen"

    # 2. Search Paramedic table (by email or badge ID)
    if not user:
        found = db.query(models.Paramedic).filter(
            (models.Paramedic.email   == data.email) |
            (models.Paramedic.badgeid == data.email)
        ).first()
        if found:
            user, role = found, "paramedic"

    # 3. Search Admin table
    if not user:
        found = db.query(models.Admin).filter(
            models.Admin.email == data.email.strip().lower()
        ).first()
        if found:
            user, role = found, "admin"

    if not user:
        raise HTTPException(status_code=401, detail="Incorrect email address or password.")

    if not utils.verify_password(data.password, user.passwordhash):
        raise HTTPException(status_code=401, detail="Incorrect email address or password.")

    # ── Citizen-specific checks ──────────────────────────────────────────────
    if role == "citizen":
        if not user.isverified or user.status == "pending":
            raise HTTPException(status_code=403, detail="Account not verified.")

        if user.status == "disabled":
            otp_code = utils.send_real_email_otp(user.email)
            if not otp_code:
                raise HTTPException(status_code=500, detail="Failed to send code.")

            db.query(models.OTPCode).filter(
                models.OTPCode.email   == user.email,
                models.OTPCode.purpose == "reactivate_account"
            ).delete()

            db.add(models.OTPCode(
                email      = user.email,
                otp        = otp_code,
                purpose    = "reactivate_account",
                expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
            ))
            db.commit()

            raise HTTPException(status_code=403, detail="needs_reactivation")

    # ── Paramedic-specific checks ────────────────────────────────────────────
    # ADDED from Codebase B: "pending" status check (paramedic not yet verified)
    if role == "paramedic":
        if user.status == "pending":
            raise HTTPException(status_code=403, detail="pending")
        if user.status != "active":
            raise HTTPException(
                status_code=403,
                detail="Your account is disabled. Please contact the administration."
            )

    # ── Resolve numeric ID across role types ─────────────────────────────────
    user_id = (
        user.userid      if role == "citizen"
        else user.paramedicid if role == "paramedic"
        else user.adminid
    )

    token = utils.create_access_token(data={
        "sub"  : str(user_id),
        "role" : role,
        "email": user.email
    })

    return {
        "access_token": token,
        "token_type"  : "bearer",
        "role"        : role,
        "user_id"     : user_id      # unified key — matches schemas.TokenResponse
    }


# =============================================================================
# POST /auth/forgot-password
# Send OTP to reset password (citizens only)
# =============================================================================
@router.post("/forgot-password")
@limiter.limit("3/minute")
def forgot_password(
    request : Request,
    data    : schemas.ForgotPasswordRequest,
    db      : Session = Depends(database.get_db)
):
    user = db.query(models.User).filter(models.User.email == data.email).first()
    if not user:
        raise HTTPException(status_code=404, detail="This email address is not registered.")

    db.query(models.OTPCode).filter(
        models.OTPCode.email   == data.email,
        models.OTPCode.purpose == "reset_password"
    ).delete()

    otp_code = utils.send_real_email_otp(data.email)
    if not otp_code:
        raise HTTPException(status_code=500, detail="Email sending failed.")

    db.add(models.OTPCode(
        email      = data.email,
        otp        = otp_code,
        purpose    = "reset_password",
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    ))
    db.commit()

    return {"message": "A verification code has been sent to your email address."}


# =============================================================================
# POST /auth/verify-reset-code
# Validate the OTP before allowing password reset
# =============================================================================
@router.post("/verify-reset-code")
@limiter.limit("5/minute")
def verify_reset_code(
    request : Request,
    data    : schemas.OTPVerify,
    db      : Session = Depends(database.get_db)
):
    otp_record = db.query(models.OTPCode).filter(
        models.OTPCode.email   == data.email,
        models.OTPCode.otp     == data.otp,
        models.OTPCode.purpose == "reset_password",
        models.OTPCode.used    == False
    ).first()

    if not otp_record:
        raise HTTPException(status_code=400, detail="Verification code is invalid.")

    # CONFLICT RESOLVED:
    #   Codebase A used timezone-aware comparison: datetime.now(timezone.utc)
    #   vs expires_at.replace(tzinfo=timezone.utc).
    #   Codebase B used naive datetime.utcnow() — this can silently break
    #   when the DB returns timezone-aware timestamps.
    #   Decision: use Codebase A's timezone-aware approach throughout.
    if datetime.now(timezone.utc) > otp_record.expires_at.replace(tzinfo=timezone.utc):
        raise HTTPException(status_code=400, detail="Verification code has expired.")

    otp_record.used = True
    db.commit()

    return {"message": "Code verified successfully."}


# =============================================================================
# POST /auth/reset-password
# Set new password after OTP was verified
# =============================================================================
@router.post("/reset-password")
@limiter.limit("3/minute")
def reset_password(
    request : Request,
    data    : schemas.PasswordReset,
    db      : Session = Depends(database.get_db)
):
    otp_record = db.query(models.OTPCode).filter(
        models.OTPCode.email   == data.email,
        models.OTPCode.purpose == "reset_password",
        models.OTPCode.used    == True
    ).first()

    if not otp_record:
        raise HTTPException(
            status_code=400,
            detail="Please verify your reset code first."
        )

    user = db.query(models.User).filter(models.User.email == data.email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    user.passwordhash = utils.hash_password(data.new_password)
    db.delete(otp_record)
    db.commit()

    return {"message": "Password changed successfully."}


# =============================================================================
# POST /auth/verify-reactivation
# Reactivate a disabled citizen account via OTP + National ID
# =============================================================================
@router.post("/verify-reactivation")
def verify_reactivation(
    data: schemas.ReactivateRequest,
    db  : Session = Depends(database.get_db)
):
    user = db.query(models.User).filter(models.User.email == data.email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    if user.nationalityid != data.national_id:
        raise HTTPException(status_code=400, detail="National ID is incorrect.")

    otp_record = db.query(models.OTPCode).filter(
        models.OTPCode.email   == data.email,
        models.OTPCode.otp     == data.otp,
        models.OTPCode.purpose == "reactivate_account",
        models.OTPCode.used    == False
    ).first()

    if not otp_record or datetime.now(timezone.utc) > otp_record.expires_at.replace(tzinfo=timezone.utc):
        raise HTTPException(status_code=400, detail="Invalid or expired OTP.")

    user.status    = "active"
    otp_record.used = True
    db.commit()

    return {"message": "Account reactivated successfully. You can now login."}
