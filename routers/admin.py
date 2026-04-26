from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import models, schemas, database, utils

# CONFLICT RESOLVED:
#   Codebase A had an EMPTY admin.py (0 bytes).
#   Codebase B had a stub that used the raw supabase-py client with hardcoded
#   placeholder credentials ("YOUR_SUPABASE_URL", "YOUR_SUPABASE_ANON_KEY").
#   That stub would have raised a connection error at startup or at request time.
#
#   Decision: replace the supabase-py stub with proper SQLAlchemy equivalents
#   that use the existing unified database connection. All functionality is
#   preserved — only the DB access method changed (from supabase-py to ORM).
#
#   The supabase-py package is NOT added to requirements.txt because the rest
#   of the project uses SQLAlchemy exclusively. Adding a second DB client would
#   create two separate connection pools to the same Supabase instance.

router = APIRouter(prefix="/admin", tags=["Admin Operations"])


# =============================================================================
# GET /admin/paramedic/all
# Formerly /admin-api/paramedic/all in Codebase B.
# Returns all paramedics visible to the authenticated admin.
#
# CHANGE: prefix changed from /admin-api to /admin for consistency with the
# rest of the project's routing convention.
# If Codebase B's Flutter frontend calls /admin-api/paramedic/all, update
# that single API call in api_service.dart to /admin/paramedic/all.
# =============================================================================
@router.get("/paramedic/all")
def get_all_paramedics(
    current_user: dict    = Depends(utils.get_current_user),
    db          : Session = Depends(database.get_db)
):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Access denied.")

    paramedics = db.query(models.Paramedic).filter(
        models.Paramedic.adminid == current_user["user_id"]
    ).all()

    return {"paramedics": paramedics}


# =============================================================================
# GET /admin/users/all
# Returns all registered citizens (for admin dashboard).
# =============================================================================
@router.get("/users/all")
def get_all_users(
    current_user: dict    = Depends(utils.get_current_user),
    db          : Session = Depends(database.get_db)
):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Access denied.")

    users = db.query(models.User).filter(
        models.User.isverified == True
    ).all()

    return {
        "count": len(users),
        "users": [
            {
                "userid"       : u.userid,
                "fullname"     : u.fullname,
                "email"        : u.email,
                "nationalityid": u.nationalityid,
                "mobile"       : u.mobile,
                "status"       : u.status,
                "createdat"    : str(u.createdat)
            }
            for u in users
        ]
    }


# =============================================================================
# GET /admin/profile
# Returns the authenticated admin's own profile.
# =============================================================================
@router.get("/profile", response_model=schemas.AdminOut)
def get_admin_profile(
    current_user: dict    = Depends(utils.get_current_user),
    db          : Session = Depends(database.get_db)
):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Access denied.")

    admin = db.query(models.Admin).filter(
        models.Admin.adminid == current_user["user_id"]
    ).first()

    if not admin:
        raise HTTPException(status_code=404, detail="Admin not found.")

    return admin
