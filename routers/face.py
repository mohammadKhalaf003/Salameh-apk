from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import base64
import time
import models
import schemas
import database
import utils
from face_system import face_engine

router = APIRouter(prefix="/face", tags=["Face"])


# =============================================================================
# Shared logic — register or update a face for a user or child
#
# CONFLICT RESOLVED:
#   Codebase A: added image compression (compress_image_bytes), timestamped
#               filenames, deleted old Cloudinary image before uploading a new
#               one, and also updated User.imageurl / Child.imageurl.
#   Codebase B: no compression, static filename (could overwrite wrongly on
#               CDN cache), did NOT delete old image, did NOT update the
#               parent model's imageurl field.
#   Decision: keep Codebase A's complete implementation.
#
#   Duplicate threshold: A used 0.45, B used 0.85.
#   Decision: use 0.45 (Codebase A) — stricter, avoids registering near-
#   identical faces for different people.
#
#   Return key: A returned "imageurl", B returned "image_url".
#   Decision: return both keys so both Flutter frontends continue working
#   without any changes.
# =============================================================================
async def register_face_logic(
    image_base64: str,
    db          : Session,
    userid      : int = None,
    childid     : int = None
) -> dict:

    img_rgb = face_engine.decode_base64(image_base64)
    if img_rgb is None:
        raise HTTPException(status_code=400, detail="Invalid image format.")

    result = face_engine.get_encoding(img_rgb)

    if result["status"] == "no_face":
        raise HTTPException(status_code=400, detail="No face detected. Please take a clear photo.")
    if result["status"] == "multiple_faces":
        raise HTTPException(status_code=400, detail="Multiple faces detected. Please take a photo alone.")
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail="Face processing error.")

    encoding = result["encoding"]

    # Duplicate check — exclude this person's own record when updating
    is_dup = face_engine.is_duplicate(
        encoding        = encoding,
        db              = db,
        threshold       = 0.45,
        exclude_userid  = userid,
        exclude_childid = childid
    )
    if is_dup:
        raise HTTPException(
            status_code=400,
            detail="This face is already registered in the system for another person."
        )

    # Fetch existing scan (if any) to delete old Cloudinary image
    existing_scan = None
    if userid:
        existing_scan = db.query(models.FaceScan).filter(
            models.FaceScan.userid == userid
        ).first()
    elif childid:
        existing_scan = db.query(models.FaceScan).filter(
            models.FaceScan.childid == childid
        ).first()

    if existing_scan and existing_scan.imageurl:
        utils.delete_image_from_cloud(existing_scan.imageurl)

    # Build unique filename with timestamp (avoids CDN cache collisions)
    timestamp = int(time.time())
    folder    = "users" if userid else "children"
    filename  = f"{userid or childid}_{timestamp}.jpg"

    image_bytes      = base64.b64decode(
        image_base64.split(",")[1] if "," in image_base64 else image_base64
    )
    compressed_bytes = utils.compress_image_bytes(image_bytes)
    image_url        = utils.upload_image_to_cloud(
        image_bytes = compressed_bytes,
        folder      = folder,
        filename    = filename
    )

    if not image_url:
        raise HTTPException(status_code=500, detail="Failed to upload image.")

    if existing_scan:
        existing_scan.encoding = encoding
        existing_scan.imageurl = image_url
    else:
        db.add(models.FaceScan(
            userid   = userid,
            childid  = childid,
            imageurl = image_url,
            encoding = encoding
        ))

    # Also update the denormalised imageurl on the parent record
    if userid:
        user_record = db.query(models.User).filter(models.User.userid == userid).first()
        if user_record:
            user_record.imageurl = image_url
    elif childid:
        child_record = db.query(models.Child).filter(models.Child.childid == childid).first()
        if child_record:
            child_record.imageurl = image_url

    db.commit()

    # Return both key variants so both Flutter frontends are satisfied
    return {
        "message"  : "Face registered successfully.",
        "imageurl" : image_url,   # Codebase A Flutter key
        "image_url": image_url    # Codebase B Flutter key
    }


# =============================================================================
# POST /face/user/register
# Register the authenticated citizen's own face
# =============================================================================
@router.post("/user/register")
async def register_user_face(
    data        : schemas.RegisterFaceRequest,
    current_user: dict    = Depends(utils.get_current_user),
    db          : Session = Depends(database.get_db)
):
    if current_user["role"] != "citizen":
        raise HTTPException(status_code=403, detail="Only citizens can register their face.")

    return await register_face_logic(
        image_base64 = data.image_base64,
        db           = db,
        userid       = current_user["user_id"]
    )


# =============================================================================
# POST /face/child/register
# Register a child's face (citizen must own the child record)
# =============================================================================
@router.post("/child/register")
async def register_child_face(
    data        : schemas.RegisterFaceRequest,
    child_id    : int,
    current_user: dict    = Depends(utils.get_current_user),
    db          : Session = Depends(database.get_db)
):
    if current_user["role"] != "citizen":
        raise HTTPException(status_code=403, detail="Only citizens can register their children's face.")

    child = db.query(models.Child).filter(
        models.Child.childid == child_id,
        models.Child.userid  == current_user["user_id"]
    ).first()

    if not child:
        raise HTTPException(status_code=404, detail="Child not found or access denied.")

    return await register_face_logic(
        image_base64 = data.image_base64,
        db           = db,
        childid      = child_id
    )


# =============================================================================
# POST /face/scan
# Face recognition scan — paramedic or doctor only.
#
# CONFLICT RESOLVED:
#   Codebase A returned rich medical data but did NOT write a ScanLog entry.
#   Codebase B DID write a ScanLog entry but returned fewer medical fields.
#   Decision: merge both — write ScanLog (from B) AND return full data (from A).
# =============================================================================
@router.post("/scan")
async def scan_face(
    data        : schemas.FaceSearchRequest,
    current_user: dict    = Depends(utils.get_current_user),
    db          : Session = Depends(database.get_db)
):
    if current_user["role"] not in ["paramedic", "doctor"]:
        raise HTTPException(status_code=403, detail="Only medical staff can scan faces.")

    img_rgb = face_engine.decode_base64(data.image_base64)
    if img_rgb is None:
        raise HTTPException(status_code=400, detail="Invalid image format.")

    result = face_engine.find_match(img_rgb, db)

    if result["status"] == "no_face":
        raise HTTPException(status_code=400, detail="No face detected.")
    if result["status"] == "multiple_faces":
        raise HTTPException(status_code=400, detail="Multiple faces detected.")
    if result["status"] == "empty_db":
        raise HTTPException(status_code=404, detail="No faces registered in system.")
    if result["status"] in ["unknown", "error"]:
        raise HTTPException(status_code=404, detail="Person not found in system.")

    if result["type"] == "child":
        person = db.query(models.Child).filter(
            models.Child.nationalityid == result["identity_id"]
        ).first()

        if not person:
            raise HTTPException(status_code=404, detail="Person not found.")

        parent = db.query(models.User).filter(
            models.User.userid == person.userid
        ).first()

        # Write ScanLog (added from Codebase B)
        db.add(models.ScanLog(
            paramedicid    = current_user["user_id"],
            matchedchildid = person.childid,
            matcheduserid  = None,
            result         = "found",
            confidence     = result["accuracy"]
        ))
        db.commit()

        return {
            "status"  : "found",
            "type"    : "child",
            "accuracy": result["accuracy"],
            "data"    : {
                "name"            : person.fullname,
                "national_id"     : person.nationalityid,
                "birthdate"       : str(person.birthdate),
                "blood_type"      : person.bloodtype,
                "allergies"       : person.allergies,
                "chronic_diseases": person.chronicdiseases,
                "malignant_history": person.malignanthistory,
                "medications"     : person.medications,
                "notes"           : person.notes,
                "emergency_phone" : person.emergencyphone,
                "image_url"       : person.face_scan.imageurl if person.face_scan else None,
                "parent_name"     : parent.fullname if parent else None,
                "parent_phone"    : parent.mobile   if parent else None,
            }
        }

    elif result["type"] == "user":
        person = db.query(models.User).filter(
            models.User.email == result["identity_id"]
        ).first()

        if not person:
            raise HTTPException(status_code=404, detail="Person not found.")

        # Write ScanLog (added from Codebase B)
        db.add(models.ScanLog(
            paramedicid    = current_user["user_id"],
            matcheduserid  = person.userid,
            matchedchildid = None,
            result         = "found",
            confidence     = result["accuracy"]
        ))
        db.commit()

        m = person.medical_profile
        return {
            "status"  : "found",
            "type"    : "user",
            "accuracy": result["accuracy"],
            "data"    : {
                "name"            : person.fullname,
                "national_id"     : person.nationalityid,
                "email"           : person.email,
                "mobile"          : person.mobile,
                "image_url"       : person.face_scan.imageurl if person.face_scan else None,
                "blood_type"      : m.bloodtype        if m else None,
                "allergies"       : m.allergies        if m else None,
                "chronic_diseases": m.chronicdiseases  if m else None,
                "malignant_history": m.malignanthistory if m else None,
                "medications"     : m.medications      if m else None,
                "notes"           : m.notes            if m else None,
            }
        }

    raise HTTPException(status_code=404, detail="Person not found.")


# =============================================================================
# DELETE /face/user/remove
# Remove the authenticated citizen's face registration
# =============================================================================
@router.delete("/user/remove")
def remove_user_face(
    current_user: dict    = Depends(utils.get_current_user),
    db          : Session = Depends(database.get_db)
):
    face_scan = db.query(models.FaceScan).filter(
        models.FaceScan.userid == current_user["user_id"]
    ).first()

    if not face_scan:
        raise HTTPException(status_code=404, detail="No face registered.")

    if face_scan.imageurl:
        utils.delete_image_from_cloud(face_scan.imageurl)

    db.delete(face_scan)
    db.commit()

    return {"message": "Face removed successfully."}
