from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session, joinedload
from datetime import date, datetime, timedelta, timezone
from limiter import limiter
import base64
import models, schemas, database, utils
from .face import register_face_logic

router = APIRouter(prefix="/user", tags=["User"])


# =============================================================================
# POST /user/register/initiate
# Step 1 of registration — age check, duplicate checks, send OTP
# =============================================================================
@router.post("/register/initiate")
@limiter.limit("3/minute")
def initiate_registration(
    request : Request,
    user    : schemas.UserCreate,
    db      : Session = Depends(database.get_db)
):
    today = date.today()
    age   = today.year - user.birthdate.year - (
        (today.month, today.day) < (user.birthdate.month, user.birthdate.day)
    )
    if age < 18:
        raise HTTPException(status_code=400, detail="You must be 18 years of age or older.")

    existing_user = db.query(models.User).filter(models.User.email == user.email).first()
    if existing_user and existing_user.isverified:
        raise HTTPException(status_code=400, detail="This email address is already registered.")

    existing_national = db.query(models.User).filter(
        models.User.nationalityid == user.nationalityid,
        models.User.isverified    == True
    ).first()
    if existing_national:
        raise HTTPException(status_code=400, detail="The national ID number is already registered.")

    # KEPT from Codebase A — prevents a national ID already used for a child
    # from being re-registered as an adult account
    existing_in_children = db.query(models.Child).filter(
        models.Child.nationalityid == user.nationalityid
    ).first()
    if existing_in_children:
        raise HTTPException(
            status_code=400,
            detail="This national ID is already registered as a dependent."
        )

    otp_code = utils.send_real_email_otp(user.email)
    if not otp_code:
        raise HTTPException(status_code=500, detail="Email sending failed.")

    db.query(models.OTPCode).filter(
        models.OTPCode.email   == user.email,
        models.OTPCode.purpose == "verify_account"
    ).delete()

    db.add(models.OTPCode(
        email      = user.email,
        otp        = otp_code,
        purpose    = "verify_account",
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    ))

    if existing_user:
        existing_user.fullname      = user.fullname
        existing_user.mobile        = user.mobile
        existing_user.birthdate     = user.birthdate
        existing_user.nationalityid = user.nationalityid
        existing_user.passwordhash  = utils.hash_password(user.password)
    else:
        db.add(models.User(
            fullname      = user.fullname,
            email         = user.email,
            nationalityid = user.nationalityid,
            mobile        = user.mobile,
            birthdate     = user.birthdate,
            passwordhash  = utils.hash_password(user.password),
            isverified    = False,
            status        = "pending"
        ))

    db.commit()
    return {"message": "A verification code has been sent to your email address."}


# =============================================================================
# POST /user/register/verify
# Step 2 of registration — validate OTP, activate account, create medical profile
# =============================================================================
@router.post("/register/verify")
@limiter.limit("5/minute")
def verify_registration(
    request : Request,
    data    : schemas.OTPVerify,
    db      : Session = Depends(database.get_db)
):
    user = db.query(models.User).filter(models.User.email == data.email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    otp_record = db.query(models.OTPCode).filter(
        models.OTPCode.email   == data.email,
        models.OTPCode.otp     == data.otp,
        models.OTPCode.purpose == "verify_account",
        models.OTPCode.used    == False
    ).first()

    if not otp_record:
        raise HTTPException(status_code=400, detail="Verification code is invalid.")

    if datetime.now(timezone.utc) > otp_record.expires_at.replace(tzinfo=timezone.utc):
        raise HTTPException(status_code=400, detail="Verification code has expired.")

    user.isverified = True
    user.status     = "active"
    otp_record.used = True

    if not user.medical_profile:
        db.add(models.MedicalProfile(userid=user.userid))

    db.commit()
    return {"message": "Your account has been successfully activated. Welcome to Salamah."}


# =============================================================================
# GET /user/profile
# =============================================================================
@router.get("/profile", response_model=schemas.UserOut)
def get_profile(
    current_user: dict    = Depends(utils.get_current_user),
    db          : Session = Depends(database.get_db)
):
    
    
    user = db.query(models.User).options(
        joinedload(models.User.medical_profile),
        joinedload(models.User.face_scan)
    ).filter(
        # CONFLICT RESOLVED: Codebase B used current_user["userid"],
        # Codebase A used current_user["user_id"].
        # Unified to "user_id" everywhere (see utils.py).
        models.User.userid == current_user["user_id"]
    ).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    return user


# =============================================================================
# PUT /user/profile
# =============================================================================
@router.put("/profile")
def update_profile(
    data        : schemas.UserUpdate,
    current_user: dict    = Depends(utils.get_current_user),
    db          : Session = Depends(database.get_db)
):
    user = db.query(models.User).filter(
        models.User.userid == current_user["user_id"]
    ).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    if data.nationalityid:
        if user.nationalityid:
            raise HTTPException(
                status_code=400,
                detail="National ID is already set and cannot be changed."
            )
        taken = db.query(models.User).filter(
            models.User.nationalityid == data.nationalityid,
            models.User.userid        != user.userid
        ).first()
        if taken:
            raise HTTPException(status_code=400, detail="This National ID is already registered.")
        user.nationalityid = data.nationalityid

    if data.fullname          is not None: user.fullname          = data.fullname
    if data.mobile            is not None: user.mobile            = data.mobile
    if data.birthdate         is not None: user.birthdate         = data.birthdate
    if data.gender            is not None: user.gender            = data.gender
    if data.nationality       is not None: user.nationality       = data.nationality
    if data.address           is not None: user.address           = data.address
    if data.emergency_contact is not None: user.emergency_contact = data.emergency_contact

    db.commit()
    return {"message": "Profile updated successfully."}


# =============================================================================
# PUT /user/medical-profile
# =============================================================================
@router.put("/medical-profile")
def update_medical_profile(
    data        : schemas.MedicalProfileUpdate,
    current_user: dict    = Depends(utils.get_current_user),
    db          : Session = Depends(database.get_db)
):
    user = db.query(models.User).filter(
        models.User.userid == current_user["user_id"]
    ).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    if not user.medical_profile:
        profile = models.MedicalProfile(userid=user.userid)
        db.add(profile)
        db.flush()
    else:
        profile = user.medical_profile

    if data.bloodtype        is not None: profile.bloodtype        = data.bloodtype
    if data.allergies        is not None: profile.allergies        = data.allergies
    if data.chronicdiseases  is not None: profile.chronicdiseases  = data.chronicdiseases
    if data.malignanthistory is not None: profile.malignanthistory = data.malignanthistory
    if data.medications      is not None: profile.medications      = data.medications
    if data.notes            is not None: profile.notes            = data.notes

    db.commit()
    return {"message": "Medical profile updated successfully."}


# =============================================================================
# PUT /user/change-password
# =============================================================================
@router.put("/change-password")
def change_password(
    data        : schemas.ChangePasswordRequest,
    current_user: dict    = Depends(utils.get_current_user),
    db          : Session = Depends(database.get_db)
):
    user = db.query(models.User).filter(
        models.User.userid == current_user["user_id"]
    ).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    if not utils.verify_password(data.old_password, user.passwordhash):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")

    user.passwordhash = utils.hash_password(data.new_password)
    db.commit()
    return {"message": "Password changed successfully."}


# =============================================================================
# POST /user/disable-account
# =============================================================================
@router.post("/disable-account")
def disable_account(
    data        : schemas.DeleteAccountRequest,
    current_user: dict    = Depends(utils.get_current_user),
    db          : Session = Depends(database.get_db)
):
    user = db.query(models.User).filter(
        models.User.userid == current_user["user_id"]
    ).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    if not utils.verify_password(data.password, user.passwordhash):
        raise HTTPException(status_code=400, detail="Password is incorrect.")

    user.status = "disabled"
    db.commit()
    return {"message": "Account deactivated. You can reactivate it by logging in with your National ID."}


# =============================================================================
# DELETE /user/delete-account
#
# CONFLICT RESOLVED:
#   Codebase A used path "/delete-account".
#   Codebase B used path "/account".
#   Decision: use "/delete-account" (Codebase A) — more explicit and less
#   likely to accidentally be called. If Flutter frontend B used "/account",
#   update the Dart API service call to "/user/delete-account".
# =============================================================================
@router.delete("/delete-account")
def delete_account(
    data        : schemas.DeleteAccountRequest,
    current_user: dict    = Depends(utils.get_current_user),
    db          : Session = Depends(database.get_db)
):
    user = db.query(models.User).options(
        joinedload(models.User.children).joinedload(models.Child.face_scan),
        joinedload(models.User.face_scan)
    ).filter(
        models.User.userid == current_user["user_id"]
    ).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    if not utils.verify_password(data.password, user.passwordhash):
        raise HTTPException(status_code=400, detail="Password is incorrect.")

    for child in user.children:
        if child.face_scan and child.face_scan.imageurl:
            utils.delete_image_from_cloud(child.face_scan.imageurl)

    if user.face_scan and user.face_scan.imageurl:
        utils.delete_image_from_cloud(user.face_scan.imageurl)

    db.query(models.OTPCode).filter(
        models.OTPCode.email == user.email
    ).delete()

    db.delete(user)
    db.commit()
    return {"message": "Account and all associated data have been permanently deleted."}


# =============================================================================
# GET /user/children
# =============================================================================
@router.get("/children")
def get_my_children(
    current_user: dict    = Depends(utils.get_current_user),
    db          : Session = Depends(database.get_db)
):
    if current_user["role"] != "citizen":
        raise HTTPException(status_code=403, detail="Access denied.")

    children = db.query(models.Child).options(
        joinedload(models.Child.face_scan)
    ).filter(
        models.Child.userid == current_user["user_id"]
    ).all()

    return {
        "message": "Children fetched successfully.",
        "count"  : len(children),
        "data"   : [_serialize_child(c) for c in children]
    }


# =============================================================================
# GET /user/children/{child_id}
# =============================================================================
@router.get("/children/{child_id}")
def get_child_details(
    child_id    : int,
    current_user: dict    = Depends(utils.get_current_user),
    db          : Session = Depends(database.get_db)
):
    child = _get_child_or_404(child_id, current_user["user_id"], db)
    return {"message": "Child fetched successfully.", "data": _serialize_child(child)}


# =============================================================================
# POST /user/children
# KEPT from Codebase A — supports inline face registration via image_base64.
# Codebase B's version did NOT support image_base64 (face had to be uploaded
# separately). Codebase A's version is a superset: if image_base64 is omitted
# it behaves identically to Codebase B.
# =============================================================================
@router.post("/children")
@limiter.limit("5/minute")
async def add_child(
    request     : Request,
    data        : schemas.ChildCreate,
    current_user: dict    = Depends(utils.get_current_user),
    db          : Session = Depends(database.get_db)
):
    if current_user["role"] != "citizen":
        raise HTTPException(status_code=403, detail="Access denied.")

    existing = db.query(models.Child).filter(
        models.Child.nationalityid == data.nationalityid
    ).first()

    if existing:
        # Allow replacing an orphaned (faceless) child record owned by this user
        if existing.face_scan is None and existing.userid == current_user["user_id"]:
            db.delete(existing)
            db.flush()
        else:
            raise HTTPException(
                status_code=400,
                detail="A child with this National ID is already registered."
            )

    parent = db.query(models.User).filter(
        models.User.userid == current_user["user_id"]
    ).first()

    if parent and parent.birthdate and data.birthdate:
        age_diff = (data.birthdate - parent.birthdate).days / 365.25
        if age_diff < 15:
            raise HTTPException(
                status_code=400,
                detail="Biological Impossibility: Parent must be at least 15 years older than the child."
            )

    child = models.Child(
        userid           = current_user["user_id"],
        nationalityid    = data.nationalityid,
        fullname         = data.fullname.strip().upper(),
        birthdate        = data.birthdate,
        gender           = data.gender,
        nationality      = data.nationality,
        address          = data.address,
        emergencyphone   = data.emergencyphone,
        email            = data.email,
        bloodtype        = data.bloodtype,
        allergies        = data.allergies,
        chronicdiseases  = data.chronicdiseases,
        malignanthistory = data.malignanthistory,
        medications      = data.medications,
        notes            = data.notes,
    )
    db.add(child)
    db.flush()

    if data.image_base64:
        try:
            result = await register_face_logic(
                image_base64 = data.image_base64,
                db           = db,
                userid       = None,
                childid      = child.childid
            )
            if not result.get("imageurl"):
                db.rollback()
                raise HTTPException(
                    status_code=400,
                    detail=result.get("error", "Face registration failed.")
                )
        except HTTPException as e:
            db.rollback()
            raise e
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Face processing error: {str(e)}")

    db.commit()
    db.refresh(child)

    return {
        "message"       : f"Child {child.fullname} registered successfully.",
        "child_id"      : child.childid,
        "face_registered": data.image_base64 is not None
    }


# =============================================================================
# PUT /user/children/{child_id}
# =============================================================================
@router.put("/children/{child_id}")
def update_child(
    child_id    : int,
    data        : schemas.ChildUpdate,
    current_user: dict    = Depends(utils.get_current_user),
    db          : Session = Depends(database.get_db)
):
    child = _get_child_or_404(child_id, current_user["user_id"], db)

    if data.fullname         is not None: child.fullname         = data.fullname.strip().upper()
    if data.emergencyphone   is not None: child.emergencyphone   = data.emergencyphone
    if data.email            is not None: child.email            = data.email
    if data.gender           is not None: child.gender           = data.gender
    if data.nationality      is not None: child.nationality      = data.nationality
    if data.address          is not None: child.address          = data.address
    if data.bloodtype        is not None: child.bloodtype        = data.bloodtype
    if data.allergies        is not None: child.allergies        = data.allergies
    if data.chronicdiseases  is not None: child.chronicdiseases  = data.chronicdiseases
    if data.malignanthistory is not None: child.malignanthistory = data.malignanthistory
    if data.medications      is not None: child.medications      = data.medications
    if data.notes            is not None: child.notes            = data.notes

    db.commit()
    return {"message": "Child profile updated successfully."}


# =============================================================================
# DELETE /user/children/{child_id}
# =============================================================================
@router.delete("/children/{child_id}")
def delete_child(
    child_id    : int,
    current_user: dict    = Depends(utils.get_current_user),
    db          : Session = Depends(database.get_db)
):
    child = _get_child_or_404(child_id, current_user["user_id"], db)

    if child.face_scan and child.face_scan.imageurl:
        utils.delete_image_from_cloud(child.face_scan.imageurl)

    db.delete(child)
    db.commit()
    return {"message": "Child record deleted successfully."}


# =============================================================================
# DELETE /user/children/cleanup/orphaned   (admin only)
# KEPT from Codebase A — Codebase B did not have this endpoint
# =============================================================================
@router.delete("/children/cleanup/orphaned")
def cleanup_orphaned_children(
    current_user: dict    = Depends(utils.get_current_user),
    db          : Session = Depends(database.get_db)
):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)

    orphaned = db.query(models.Child).outerjoin(
        models.FaceScan,
        models.FaceScan.childid == models.Child.childid
    ).filter(
        models.FaceScan.faceid  == None,
        models.Child.createdat  <= cutoff
    ).all()

    count = len(orphaned)
    for child in orphaned:
        db.delete(child)

    db.commit()
    return {"message": f"Cleaned up {count} orphaned child records."}


# =============================================================================
# Internal helpers
# =============================================================================
def _get_child_or_404(child_id: int, user_id: int, db: Session) -> models.Child:
    child = db.query(models.Child).options(
        joinedload(models.Child.face_scan)
    ).filter(
        models.Child.childid == child_id,
        models.Child.userid  == user_id
    ).first()

    if not child:
        raise HTTPException(status_code=404, detail="Child not found or access denied.")
    return child


def _serialize_child(c: models.Child) -> dict:
    return {
        "childid"         : c.childid,
        "fullname"        : c.fullname,
        "nationalityid"   : c.nationalityid,
        "birthdate"       : str(c.birthdate) if c.birthdate else None,
        "gender"          : c.gender,
        "nationality"     : c.nationality,
        "address"         : c.address,
        "emergencyphone"  : c.emergencyphone,
        "email"           : c.email,
        "bloodtype"       : c.bloodtype,
        "allergies"       : c.allergies,
        "chronicdiseases" : c.chronicdiseases,
        "malignanthistory": c.malignanthistory,
        "medications"     : c.medications,
        "notes"           : c.notes,
        # "imageurl" key used here (matches Codebase A Flutter frontend)
        "imageurl"        : c.face_scan.imageurl if c.face_scan else None,
        "has_face"        : c.face_scan is not None
    }
