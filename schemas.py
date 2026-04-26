from pydantic import BaseModel, EmailStr, ConfigDict
from typing import Optional, List
from datetime import date, datetime


# =============================================================================
# Medical Profile — for adult users
# =============================================================================
class MedicalProfileBase(BaseModel):
    bloodtype        : Optional[str] = None
    allergies        : Optional[str] = None
    chronicdiseases  : Optional[str] = None
    malignanthistory : Optional[str] = None
    medications      : Optional[str] = None
    notes            : Optional[str] = None

class MedicalProfileUpdate(MedicalProfileBase):
    pass

class MedicalProfileOut(MedicalProfileBase):
    profileid: int
    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# User (Citizen)
# =============================================================================
class UserBase(BaseModel):
    fullname      : str
    email         : EmailStr
    nationalityid : str
    mobile        : Optional[str] = None
    birthdate     : Optional[date] = None

class UserCreate(UserBase):
    password: str

class UserUpdate(BaseModel):
    fullname         : Optional[str]  = None
    mobile           : Optional[str]  = None
    birthdate        : Optional[date] = None
    gender           : Optional[str]  = None
    nationality      : Optional[str]  = None
    address          : Optional[str]  = None
    emergency_contact: Optional[str]  = None
    nationalityid    : Optional[str]  = None   # locked after first set (enforced in endpoint)

class UserOut(BaseModel):
    userid           : int
    fullname         : Optional[str]  = None
    email            : str
    nationalityid    : Optional[str]  = None
    mobile           : Optional[str]  = None
    birthdate        : Optional[date] = None
    gender           : Optional[str]  = None
    nationality      : Optional[str]  = None
    address          : Optional[str]  = None
    emergency_contact: Optional[str]  = None
    imageurl         : Optional[str]  = None
    status           : str            = "active"
    isverified       : bool
    createdat        : datetime
    medical_profile  : Optional[MedicalProfileOut] = None
    model_config = ConfigDict(from_attributes=True)


class ReactivateRequest(BaseModel):
    email       : EmailStr
    otp         : str
    national_id : str


# =============================================================================
# Child
# =============================================================================
class ChildCreate(BaseModel):
    fullname         : str
    nationalityid    : str
    birthdate        : date
    gender           : Optional[str]      = "Not Specified"
    nationality      : Optional[str]      = "Jordanian"
    address          : Optional[str]      = None
    emergencyphone   : str
    email            : Optional[EmailStr] = None
    # medical
    bloodtype        : Optional[str] = None
    allergies        : Optional[str] = "None"
    chronicdiseases  : Optional[str] = "None"
    malignanthistory : Optional[str] = "None"
    medications      : Optional[str] = "None"
    notes            : Optional[str] = "No special instructions."
    # KEPT from Codebase A — allows face registration in the same add-child call
    image_base64     : Optional[str] = None

class ChildUpdate(BaseModel):
    fullname         : Optional[str]      = None
    emergencyphone   : Optional[str]      = None
    email            : Optional[EmailStr] = None
    gender           : Optional[str]      = None
    nationality      : Optional[str]      = None
    address          : Optional[str]      = None
    bloodtype        : Optional[str]      = None
    allergies        : Optional[str]      = None
    chronicdiseases  : Optional[str]      = None
    malignanthistory : Optional[str]      = None
    medications      : Optional[str]      = None
    notes            : Optional[str]      = None

class ChildOut(BaseModel):
    childid          : int
    userid           : int
    fullname         : str
    nationalityid    : str
    birthdate        : Optional[date]     = None
    gender           : Optional[str]      = None
    nationality      : Optional[str]      = None
    address          : Optional[str]      = None
    emergencyphone   : Optional[str]      = None
    email            : Optional[str]      = None
    bloodtype        : Optional[str]      = None
    allergies        : Optional[str]      = None
    chronicdiseases  : Optional[str]      = None
    # CONFLICT RESOLVED: Codebase B had malignanthistory declared TWICE (duplicate
    # field bug). Keeping only one declaration here.
    malignanthistory : Optional[str]      = None
    medications      : Optional[str]      = None
    notes            : Optional[str]      = None
    imageurl         : Optional[str]      = None
    has_face         : bool               = False
    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# FaceScan
# =============================================================================
class FaceScanCreate(BaseModel):
    encoding  : List[float]
    imageurl  : Optional[str] = None
    userid    : Optional[int] = None
    childid   : Optional[int] = None

class FaceScanOut(BaseModel):
    faceid    : int
    createdat : datetime
    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# Admin
# =============================================================================
class AdminCreate(BaseModel):
    fullname : str
    email    : EmailStr
    password : str

class AdminOut(BaseModel):
    adminid  : int
    fullname : str
    email    : str
    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# Paramedic
# =============================================================================
class ParamedicCreate(BaseModel):
    fullname : str
    email    : EmailStr
    password : str
    badgeid  : str
    phone    : Optional[str] = None
    # ADDED from Codebase B — these fields exist in the router create flow
    station  : Optional[str] = None
    role     : Optional[str] = "paramedic"

class ParamedicUpdate(BaseModel):
    fullname : Optional[str] = None
    phone    : Optional[str] = None
    status   : Optional[str] = None
    # ADDED from Codebase B — admin can update role
    role     : Optional[str] = None

class ParamedicOut(BaseModel):
    paramedicid : int
    fullname    : str
    badgeid     : str
    email       : str
    status      : str
    # ADDED from Codebase B — role is returned to Flutter frontend
    role        : Optional[str] = "paramedic"
    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# Auth
# =============================================================================
class LoginRequest(BaseModel):
    email       : str
    password    : str
    national_id : Optional[str] = None

class TokenResponse(BaseModel):
    access_token : str
    token_type   : str = "bearer"
    role         : str
    # CONFLICT RESOLVED:
    #   Codebase A used "user_id" as the key name in the response dict AND
    #   the schema field name.
    #   Codebase B used "userid" (no underscore) in both.
    #   Decision: use "user_id" in the schema (matches Codebase A's Flutter
    #   frontend which was already consuming "user_id"). The auth router
    #   returns this key consistently.
    user_id      : int


# =============================================================================
# ScanLog
# =============================================================================
class ScanLogOut(BaseModel):
    logid          : int
    paramedicid    : int
    matcheduserid  : Optional[int]   = None
    matchedchildid : Optional[int]   = None
    confidence     : Optional[float] = None
    result         : str
    scantime       : datetime
    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# OTP & Password
# =============================================================================
class OTPVerify(BaseModel):
    email : EmailStr
    otp   : str

class PasswordReset(BaseModel):
    email        : EmailStr
    otp          : str
    new_password : str

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class ChangePasswordRequest(BaseModel):
    old_password : str
    new_password : str

class DeleteAccountRequest(BaseModel):
    password: str


# =============================================================================
# Face
# =============================================================================
class RegisterFaceRequest(BaseModel):
    image_base64: str

class FaceSearchRequest(BaseModel):
    image_base64: str
