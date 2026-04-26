from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session, joinedload
from datetime import datetime, timedelta, timezone
from limiter import limiter
import models, schemas, database, utils
from database import SessionLocal
from face_system import face_engine

# KEPT from Codebase B — all paramedic management and scanning logic lives here.
# Codebase A had an empty paramedic.py; this file is entirely new to the merge.
router = APIRouter(prefix="/paramedic", tags=["Paramedic Staff"])


# =============================================================================
# POST /paramedic/register/initiate
# Admin creates a paramedic account — sends OTP to paramedic's email.
#
# CHANGES FROM CODEBASE B:
#   - current_user["userid"] → current_user["user_id"] (unified key)
#   - Removed station= and shift= fields: these do not exist on the Paramedic
#     model in either codebase. They were referenced in Codebase B's
#     auth.py create-paramedic stub but were never added to the ORM model.
#     Adding them would require a DB migration; they are removed here to avoid
#     a startup AttributeError. Add a DB column + model field if needed later.
# =============================================================================
@router.post("/register/initiate")
@limiter.limit("3/minute")
def initiate_paramedic_registration(
    request     : Request,
    paramedic   : schemas.ParamedicCreate,
    db          : Session = Depends(database.get_db),
    current_user: dict    = Depends(utils.get_current_user)
):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can create paramedic accounts.")

    adminid = current_user["user_id"]   # FIXED: was current_user["userid"]

    existing_p = db.query(models.Paramedic).filter(
        models.Paramedic.email == paramedic.email
    ).first()
    if existing_p:
        raise HTTPException(status_code=400, detail="An account with this email already exists.")

    existing_badge = db.query(models.Paramedic).filter(
        models.Paramedic.badgeid == paramedic.badgeid
    ).first()
    if existing_badge:
        raise HTTPException(status_code=400, detail="The Badge ID is already registered.")

    otp_code = utils.send_real_email_otp(paramedic.email)
    if not otp_code:
        raise HTTPException(status_code=500, detail="Email sending failed.")

    db.query(models.OTPCode).filter(
        models.OTPCode.email   == paramedic.email,
        models.OTPCode.purpose == "verify_paramedic"
    ).delete()

    db.add(models.OTPCode(
        email      = paramedic.email,
        otp        = otp_code,
        purpose    = "verify_paramedic",
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    ))

    new_paramedic = models.Paramedic(
        fullname     = paramedic.fullname,
        email        = paramedic.email,
        adminid      = adminid,
        badgeid      = paramedic.badgeid,
        phone        = paramedic.phone,
        passwordhash = utils.hash_password(paramedic.password),
        status       = "pending",
        role         = "paramedic",
    )
    db.add(new_paramedic)
    db.commit()

    return {"message": "A verification code has been sent to the paramedic's email."}


# =============================================================================
# POST /paramedic/register/verify
# Paramedic activates their own account with the OTP sent to their email.
#
# CHANGES FROM CODEBASE B:
#   - Removed paramedic.isverified = True — the Paramedic model has no
#     isverified column in either codebase. Setting it would raise an
#     AttributeError. Status "active" is sufficient to grant login access.
#   - datetime.utcnow() → datetime.now(timezone.utc) (timezone-aware, unified)
# =============================================================================
@router.post("/register/verify")
def verify_paramedic(
    request: Request,
    data   : schemas.OTPVerify,
    db     : Session = Depends(database.get_db)
):
    paramedic = db.query(models.Paramedic).filter(
        models.Paramedic.email == data.email
    ).first()
    if not paramedic:
        raise HTTPException(status_code=404, detail="Paramedic not found.")

    otp_record = db.query(models.OTPCode).filter(
        models.OTPCode.email   == data.email,
        models.OTPCode.otp     == data.otp,
        models.OTPCode.purpose == "verify_paramedic",
        models.OTPCode.used    == False
    ).order_by(models.OTPCode.createdat.desc()).first()

    if not otp_record or (
        datetime.now(timezone.utc) > otp_record.expires_at.replace(tzinfo=timezone.utc)
    ):
        raise HTTPException(status_code=400, detail="Invalid or expired code.")

    paramedic.status  = "active"
    otp_record.used   = True
    db.commit()

    return {"message": "Paramedic account activated. You can now log in."}


# =============================================================================
# POST /paramedic/register/resend-otp
# Resend OTP to a paramedic whose account is still pending.
#
# CHANGES FROM CODEBASE B:
#   - datetime.utcnow() → datetime.now(timezone.utc)
# =============================================================================
@router.post("/register/resend-otp")
@limiter.limit("3/minute")
def resend_paramedic_otp(
    request: Request,
    data   : schemas.ForgotPasswordRequest,
    db     : Session = Depends(database.get_db)
):
    clean_email = data.email.strip().lower()

    paramedic = db.query(models.Paramedic).filter(
        models.Paramedic.email  == clean_email,
        models.Paramedic.status == "pending"
    ).first()
    if not paramedic:
        raise HTTPException(
            status_code=404,
            detail="Paramedic not found or already active."
        )

    db.query(models.OTPCode).filter(
        models.OTPCode.email   == data.email,
        models.OTPCode.purpose == "verify_paramedic"
    ).delete()

    otp_code = utils.send_real_email_otp(data.email)
    if not otp_code:
        raise HTTPException(status_code=500, detail="Email sending failed.")

    db.add(models.OTPCode(
        email      = data.email,
        otp        = otp_code,
        purpose    = "verify_paramedic",
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    ))
    db.commit()

    return {"message": "OTP resent successfully."}


# =============================================================================
# GET /paramedic/profile
# =============================================================================
@router.get("/profile", response_model=schemas.ParamedicOut)
def get_paramedic_profile(
    current_user: dict    = Depends(utils.get_current_user),
    db          : Session = Depends(database.get_db)
):
    if current_user["role"] != "paramedic":
        raise HTTPException(status_code=403, detail="Access denied.")

    p = db.query(models.Paramedic).filter(
        models.Paramedic.paramedicid == current_user["user_id"]   # FIXED
    ).first()

    if not p:
        raise HTTPException(status_code=404, detail="Paramedic not found.")
    return p


# =============================================================================
# GET /paramedic/all
# Admin retrieves all paramedics they created.
# =============================================================================
@router.get("/all")
def get_all_paramedics(
    db          : Session = Depends(database.get_db),
    current_user: dict    = Depends(utils.get_current_user)
):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Access denied.")

    adminid    = current_user["user_id"]   # FIXED
    paramedics = db.query(models.Paramedic).filter(
        models.Paramedic.adminid == adminid
    ).all()

    return {"paramedics": paramedics}


# =============================================================================
# DELETE /paramedic/delete/{paramedic_id}
# Admin deletes a paramedic they own.
# =============================================================================
@router.delete("/delete/{paramedic_id}")
def delete_paramedic(
    paramedic_id: int,
    db          : Session = Depends(database.get_db),
    current_user: dict    = Depends(utils.get_current_user)
):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Access denied.")

    paramedic = db.query(models.Paramedic).filter(
        models.Paramedic.paramedicid == paramedic_id,
        models.Paramedic.adminid     == current_user["user_id"]   # FIXED
    ).first()

    if not paramedic:
        raise HTTPException(status_code=404, detail="Paramedic not found.")

    db.delete(paramedic)
    db.commit()
    return {"message": "Paramedic deleted successfully."}


# =============================================================================
# PUT /paramedic/status/{paramedic_id}
# Admin updates a paramedic's status (active | pending | disabled).
# =============================================================================
@router.put("/status/{paramedic_id}")
def update_paramedic_status(
    paramedic_id: int,
    data        : dict,
    db          : Session = Depends(database.get_db),
    current_user: dict    = Depends(utils.get_current_user)
):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Access denied.")

    new_status = data.get("status")
    if new_status not in ["active", "pending", "disabled"]:
        raise HTTPException(status_code=400, detail="Invalid status.")

    paramedic = db.query(models.Paramedic).filter(
        models.Paramedic.paramedicid == paramedic_id,
        models.Paramedic.adminid     == current_user["user_id"]   # FIXED
    ).first()

    if not paramedic:
        raise HTTPException(status_code=404, detail="Paramedic not found.")

    paramedic.status = new_status
    db.commit()

    return {"message": "Status updated successfully.", "status": paramedic.status}


# =============================================================================
# PUT /paramedic/update/{paramedic_id}
# Admin updates paramedic details.
# =============================================================================
@router.put("/update/{paramedic_id}")
def update_paramedic(
    paramedic_id: int,
    data        : dict,
    db          : Session = Depends(database.get_db),
    current_user: dict    = Depends(utils.get_current_user)
):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Access denied.")

    paramedic = db.query(models.Paramedic).filter(
        models.Paramedic.paramedicid == paramedic_id,
        models.Paramedic.adminid     == current_user["user_id"]   # FIXED
    ).first()

    if not paramedic:
        raise HTTPException(status_code=404, detail="Paramedic not found.")

    paramedic.fullname = data.get("fullname", paramedic.fullname)
    paramedic.badgeid  = data.get("badgeid",  paramedic.badgeid)
    paramedic.phone    = data.get("phone",    paramedic.phone)
    paramedic.status   = data.get("status",   paramedic.status)

    db.commit()
    return {"message": "Updated successfully."}


# =============================================================================
# GET /paramedic/scan-logs
# Admin retrieves all scan logs for paramedics they manage.
# =============================================================================
@router.get("/scan-logs")
def get_scan_logs(
    db          : Session = Depends(database.get_db),
    current_user: dict    = Depends(utils.get_current_user)
):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Access denied.")

    adminid = current_user["user_id"]   # FIXED

    paramedic_ids = [
        p[0] for p in db.query(models.Paramedic.paramedicid).filter(
            models.Paramedic.adminid == adminid
        ).all()
    ]

    logs = db.query(models.ScanLog).filter(
        models.ScanLog.paramedicid.in_(paramedic_ids)
    ).order_by(models.ScanLog.scantime.desc()).all()

    paramedics = {
        p.paramedicid: p.fullname
        for p in db.query(models.Paramedic).filter(
            models.Paramedic.paramedicid.in_(paramedic_ids)
        ).all()
    }

    users = {u.userid: u.fullname for u in db.query(models.User).all()}
    children = {c.childid: c.fullname for c in db.query(models.Child).all()}

    result = []
    for log in logs:
        patient_name = "Unknown"
        if log.matcheduserid and log.matcheduserid in users:
            patient_name = users[log.matcheduserid]
        elif log.matchedchildid and log.matchedchildid in children:
            patient_name = children[log.matchedchildid]

        result.append({
            "logid"         : log.logid,
            "scantime"      : (log.scantime + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M"),
            "paramedic_name": paramedics.get(log.paramedicid, "Unknown"),
            "patient_name"  : patient_name,
            "accuracy"      : getattr(log, "confidence", 0),
            "result"        : log.result,
        })

    return {"logs": result}


# =============================================================================
# GET /paramedic/my-logs
# Paramedic retrieves their own last-12-hours scan history.
# =============================================================================
@router.get("/my-logs")
def get_my_logs(
    db          : Session = Depends(database.get_db),
    current_user: dict    = Depends(utils.get_current_user)
):
    if current_user["role"] != "paramedic":
        raise HTTPException(status_code=403, detail="Access denied.")

    logs = db.query(models.ScanLog).filter(
        models.ScanLog.paramedicid == current_user["user_id"],   # FIXED
        models.ScanLog.scantime    >= datetime.now(timezone.utc) - timedelta(hours=12)
    ).order_by(models.ScanLog.scantime.desc()).all()

    seen_ids = set()
    result   = []

    for log in logs:
        data = {
            "name": "Unknown", "image_url": "", "logid": log.logid,
            "national_id": "", "blood_type": "Unknown", "allergies": "None",
            "chronic_diseases": "None", "malignant_history": "None",
            "medications": "None", "notes": "", "emergency_contact": "N/A",
            "mobile": "N/A", "email": "N/A", "address": "N/A",
            "birthdate": "N/A", "gender": "N/A", "nationality": "Jordanian",
            "type": "adult"
        }
        unique_id = None

        if log.matcheduserid:
            patient = db.query(models.User).filter(
                models.User.userid == log.matcheduserid
            ).first()
            if patient:
                unique_id = f"user_{patient.userid}"
                m = patient.medical_profile
                data.update({
                    "name"            : patient.fullname,
                    "national_id"     : patient.nationalityid,
                    "image_url"       : patient.face_scan.imageurl if patient.face_scan else "",
                    "blood_type"      : m.bloodtype        if m else "Unknown",
                    "allergies"       : m.allergies        if m else "None",
                    "chronic_diseases": m.chronicdiseases  if m else "None",
                    "malignant_history": m.malignanthistory if m else "None",
                    "medications"     : m.medications      if m else "None",
                    "notes"           : m.notes            if m else "",
                    "emergency_contact": patient.emergency_contact or "N/A",
                    "mobile"          : patient.mobile     or "N/A",
                    "email"           : patient.email      or "N/A",
                    "address"         : patient.address    or "Not Provided",
                    "birthdate"       : str(patient.birthdate) if patient.birthdate else "N/A",
                    "gender"          : patient.gender     or "N/A",
                    "type"            : "adult"
                })

        elif log.matchedchildid:
            child = db.query(models.Child).filter(
                models.Child.childid == log.matchedchildid
            ).first()
            if child:
                unique_id = f"child_{child.childid}"
                data.update({
                    "name"            : child.fullname,
                    "national_id"     : child.nationalityid,
                    "image_url"       : child.face_scan.imageurl if child.face_scan else "",
                    "blood_type"      : child.bloodtype    or "Unknown",
                    "allergies"       : child.allergies    or "None",
                    "chronic_diseases": child.chronicdiseases or "None",
                    "malignant_history": child.malignanthistory or "None",
                    "medications"     : child.medications  or "None",
                    "notes"           : child.notes        or "",
                    "emergency_contact": child.emergencyphone or "N/A",
                    "mobile"          : "N/A",
                    "email"           : child.email        or "N/A",
                    "address"         : child.address      or "Not Provided",
                    "birthdate"       : str(child.birthdate) if child.birthdate else "N/A",
                    "gender"          : child.gender       or "N/A",
                    "type"            : "child"
                })

        if unique_id and unique_id not in seen_ids:
            seen_ids.add(unique_id)
            result.append(data)

    return {"logs": result}


# =============================================================================
# POST /paramedic/search
# Manual text-based patient search by national ID or name.
# =============================================================================
@router.post("/search")
def search_patient(
    data        : dict,
    db          : Session = Depends(database.get_db),
    current_user: dict    = Depends(utils.get_current_user)
):
    if current_user["role"] != "paramedic":
        raise HTTPException(status_code=403, detail="Access denied.")

    query = data.get("query")
    if not query:
        raise HTTPException(status_code=400, detail="Query is required.")

    query = query.strip()

    patient  = db.query(models.User).filter(
        (models.User.nationalityid == query) |
        (models.User.fullname.ilike(f"%{query}%"))
    ).first()

    is_child = False
    if not patient:
        patient  = db.query(models.Child).filter(
            (models.Child.nationalityid == query) |
            (models.Child.fullname.ilike(f"%{query}%"))
        ).first()
        is_child = True

    if not patient:
        return {"status": "unknown"}

    db.add(models.ScanLog(
        paramedicid    = current_user["user_id"],   # FIXED
        matcheduserid  = patient.userid  if not is_child else None,
        matchedchildid = patient.childid if is_child  else None,
        result         = "found",
        confidence     = 100
    ))
    db.commit()

    if is_child:
        full_data = {
            "name"            : patient.fullname,
            "national_id"     : patient.nationalityid,
            "birthdate"       : str(patient.birthdate) if patient.birthdate else "N/A",
            "gender"          : patient.gender       or "Not Specified",
            "nationality"     : patient.nationality  or "Jordanian",
            "mobile"          : "N/A",
            "email"           : patient.email        or "N/A",
            "address"         : patient.address      or "Not Provided",
            "blood_type"      : patient.bloodtype    or "Unknown",
            "allergies"       : patient.allergies    or "None",
            "chronic_diseases": patient.chronicdiseases or "None",
            "malignant_history": patient.malignanthistory or "None",
            "medications"     : patient.medications  or "None",
            "notes"           : patient.notes        or "No special instructions.",
            "emergency_contact": patient.emergencyphone,
            "type"            : "child"
        }
    else:
        medical   = patient.medical_profile
        full_data = {
            "name"            : patient.fullname,
            "national_id"     : patient.nationalityid,
            "birthdate"       : str(patient.birthdate) if patient.birthdate else "N/A",
            "gender"          : patient.gender          or "Not Specified",
            "nationality"     : patient.nationality     or "Jordanian",
            "mobile"          : patient.mobile          or "N/A",
            "email"           : patient.email           or "N/A",
            "address"         : patient.address         or "Not Provided",
            "blood_type"      : medical.bloodtype       if medical else "Unknown",
            "allergies"       : medical.allergies       if medical else "None",
            "chronic_diseases": medical.chronicdiseases if medical else "None",
            "malignant_history": medical.malignanthistory if medical else "None",
            "medications"     : medical.medications     if medical else "None",
            "notes"           : medical.notes           if medical else "No special instructions.",
            "emergency_phone" : patient.emergency_contact or "N/A",
            "type"            : "adult"
        }

    return {
        "status"  : "found",
        "patient" : {
            **full_data,
            "image_url": patient.face_scan.imageurl if patient.face_scan else None
        },
        "accuracy": 100
    }


# =============================================================================
# POST /paramedic/scan
# Face-scan endpoint on the paramedic side (delegates to face_engine).
# NOTE: /face/scan also performs a scan — this endpoint is kept for Codebase B
# Flutter frontend compatibility which may call /paramedic/scan directly.
# =============================================================================
@router.post("/scan")
def scan_face(
    data        : dict,
    db          : Session = Depends(database.get_db),
    current_user: dict    = Depends(utils.get_current_user)
):
    if current_user["role"] != "paramedic":
        raise HTTPException(status_code=403, detail="Access denied.")

    image_base64 = data.get("image_base64")
    if not image_base64:
        return {"status": "no_image"}

    img_rgb = face_engine.decode_base64(image_base64)
    if img_rgb is None:
        return {"status": "invalid_image"}

    result = face_engine.find_match(img_rgb, db, threshold=0.6)

    if result["status"] == "found":
        is_child = (result["type"] == "child")

        if not is_child:
            person = db.query(models.User).filter(
                models.User.email == result["identity_id"]
            ).first()
            if not person:
                return {"status": "unknown"}

            m = person.medical_profile
            full_data = {
                "name"            : person.fullname,
                "national_id"     : person.nationalityid,
                "birthdate"       : str(person.birthdate) if person.birthdate else "N/A",
                "gender"          : person.gender          or "N/A",
                "nationality"     : person.nationality     or "Jordanian",
                "mobile"          : person.mobile          or "N/A",
                "email"           : person.email           or "N/A",
                "address"         : person.address         or "Not Provided",
                "blood_type"      : m.bloodtype            if m else "Unknown",
                "allergies"       : m.allergies            if m else "None",
                "chronic_diseases": m.chronicdiseases      if m else "None",
                "malignant_history": m.malignanthistory    if m else "None",
                "medications"     : m.medications          if m else "None",
                "notes"           : m.notes                if m else "",
                "emergency_contact": person.emergency_contact or "N/A",
                "type"            : "adult"
            }
        else:
            person = db.query(models.Child).filter(
                models.Child.nationalityid == result["identity_id"]
            ).first()
            if not person:
                return {"status": "unknown"}

            full_data = {
                "name"            : person.fullname,
                "national_id"     : person.nationalityid,
                "birthdate"       : str(person.birthdate) if person.birthdate else "N/A",
                "gender"          : person.gender          or "N/A",
                "nationality"     : person.nationality     or "Jordanian",
                "mobile"          : "N/A",
                "email"           : person.email           or "N/A",
                "address"         : person.address         or "Not Provided",
                "blood_type"      : person.bloodtype       or "Unknown",
                "allergies"       : person.allergies       or "None",
                "chronic_diseases": person.chronicdiseases or "None",
                "malignant_history": person.malignanthistory or "None",
                "medications"     : person.medications     or "None",
                "notes"           : person.notes           or "",
                "emergency_contact": person.emergencyphone or "N/A",
                "type"            : "child"
            }

        db.add(models.ScanLog(
            paramedicid    = current_user["user_id"],   # FIXED
            matcheduserid  = person.userid  if not is_child else None,
            matchedchildid = person.childid if is_child  else None,
            result         = "found",
            confidence     = result["accuracy"]
        ))
        db.commit()

        return {
            "status"  : "found",
            "accuracy": result["accuracy"],
            "patient" : {
                **full_data,
                "image_url": person.face_scan.imageurl if person.face_scan else None
            }
        }

    return {"status": result["status"]}
