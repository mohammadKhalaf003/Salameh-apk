from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, Text, Date, TIMESTAMP, Float
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from database import Base
from pgvector.sqlalchemy import Vector


# =============================================================================
# Admin
# =============================================================================
class Admin(Base):
    __tablename__ = "admin"

    adminid       = Column(Integer, primary_key=True, index=True)
    email         = Column(String(255), unique=True, nullable=False)
    fullname      = Column(String(255), nullable=False)
    passwordhash  = Column(String(255), nullable=False)

    # CONFLICT RESOLVED:
    #   Codebase A named this relationship "paramedics" (plural).
    #   Codebase B named it "paramedic" (singular).
    #   Decision: use "paramedics" (plural) — it is semantically correct (one
    #   admin owns many paramedics) and matches Codebase A's back_populates.
    paramedics = relationship("Paramedic", back_populates="creator_admin")


# =============================================================================
# Paramedic
# =============================================================================
class Paramedic(Base):
    __tablename__ = "paramedic"

    paramedicid  = Column(Integer, primary_key=True, index=True)
    adminid      = Column(Integer, ForeignKey("admin.adminid"))
    fullname     = Column(String(255), nullable=False)
    badgeid      = Column(String(50), unique=True)
    phone        = Column(String(20))
    email        = Column(String(255), unique=True, nullable=False)
    passwordhash = Column(String(255))
    status       = Column(String(50), default="active")   # active | pending | disabled

    # ADDED from Codebase B — required by AllAccountsView login flow and
    # paramedic router role checks. Codebase A omitted this column.
    role         = Column(String(50), default="paramedic")

    # back_populates must match Admin.paramedics (resolved above)
    creator_admin = relationship("Admin", back_populates="paramedics")
    logs          = relationship("ScanLog", back_populates="operator_paramedic")


# =============================================================================
# User (Citizen)
# =============================================================================
class User(Base):
    __tablename__ = "user"

    userid            = Column(Integer, primary_key=True, index=True)
    fullname          = Column(String(255))
    email             = Column(String(255), unique=True, nullable=False)
    nationalityid     = Column(String(50), unique=True, nullable=True)
    mobile            = Column(String(20))
    birthdate         = Column(Date)
    gender            = Column(String(20))
    nationality       = Column(String(100))
    address           = Column(Text)
    emergency_contact = Column(String(100))
    imageurl          = Column(String(500))
    status            = Column(String(20), default="active")   # active | disabled | pending
    passwordhash      = Column(String(255), nullable=False)
    isverified        = Column(Boolean, default=False)
    createdat         = Column(TIMESTAMP, server_default=func.now())

    # CASCADE options kept from Codebase A — safer for delete operations
    medical_profile = relationship(
        "MedicalProfile",
        back_populates="owner_user",
        uselist=False,
        cascade="all, delete-orphan"
    )
    children = relationship(
        "Child",
        back_populates="parent_user",
        passive_deletes=True,
        cascade="all, delete-orphan"
    )
    face_scan = relationship(
        "FaceScan",
        back_populates="scanned_user",
        uselist=False,
        passive_deletes=True,
        cascade="all, delete-orphan"
    )


# =============================================================================
# MedicalProfile  — for adult users only
# =============================================================================
class MedicalProfile(Base):
    __tablename__ = "medicalprofile"

    profileid        = Column(Integer, primary_key=True, index=True)
    userid           = Column(Integer, ForeignKey("user.userid", ondelete="CASCADE"))
    bloodtype        = Column(String(10))
    allergies        = Column(Text)
    chronicdiseases  = Column(Text)
    malignanthistory = Column(Text)
    medications      = Column(Text)
    notes            = Column(Text)

    owner_user = relationship("User", back_populates="medical_profile")


# =============================================================================
# Child  — medical data embedded directly
# =============================================================================
class Child(Base):
    __tablename__ = "child"

    childid          = Column(Integer, primary_key=True, index=True)
    userid           = Column(Integer, ForeignKey("user.userid", ondelete="CASCADE"))
    nationalityid    = Column(String(50), unique=True, nullable=False)
    fullname         = Column(String(255), nullable=False)
    birthdate        = Column(Date)
    gender           = Column(String(20))
    nationality      = Column(String(100))
    address          = Column(Text)
    emergencyphone   = Column(String(20))
    email            = Column(String(255))

    # KEPT from Codebase A — imageurl on Child itself (used by _serialize_child)
    imageurl         = Column(String(500), nullable=True)

    # Embedded medical data
    bloodtype        = Column(String(10))
    allergies        = Column(Text,    default="None")
    chronicdiseases  = Column(Text,    default="None")
    malignanthistory = Column(Text,    default="None")
    medications      = Column(Text,    default="None")
    notes            = Column(Text,    default="No special instructions.")

    # KEPT from Codebase A — used by cleanup_orphaned_children scheduler job
    createdat = Column(TIMESTAMP, server_default=func.now())

    parent_user = relationship("User", back_populates="children")
    face_scan   = relationship(
        "FaceScan",
        back_populates="scanned_child",
        uselist=False,
        cascade="all, delete-orphan"
    )


# =============================================================================
# FaceScan  — pgvector encoding + image URL
# =============================================================================
class FaceScan(Base):
    __tablename__ = "facescan"

    faceid    = Column(Integer, primary_key=True, index=True)
    # ondelete="CASCADE" kept from Codebase A for FK integrity
    userid    = Column(Integer, ForeignKey("user.userid",  ondelete="CASCADE"), unique=True, nullable=True)
    childid   = Column(Integer, ForeignKey("child.childid", ondelete="CASCADE"), unique=True, nullable=True)
    imageurl  = Column(String(500))
    encoding  = Column(Vector(512))
    createdat = Column(TIMESTAMP, server_default=func.now())

    scanned_user  = relationship("User",  back_populates="face_scan")
    scanned_child = relationship("Child", back_populates="face_scan")


# =============================================================================
# ScanLog  — paramedic scan history
# =============================================================================
class ScanLog(Base):
    __tablename__ = "scanlog"

    logid          = Column(Integer, primary_key=True, index=True)
    paramedicid    = Column(Integer, ForeignKey("paramedic.paramedicid"))
    matcheduserid  = Column(Integer, ForeignKey("user.userid"),   nullable=True)
    matchedchildid = Column(Integer, ForeignKey("child.childid"),  nullable=True)
    faceid         = Column(Integer, ForeignKey("facescan.faceid"), nullable=True)
    scantime       = Column(TIMESTAMP, server_default=func.now())
    confidence     = Column(Float, nullable=True)
    result         = Column(String(50))

    operator_paramedic = relationship("Paramedic", back_populates="logs")


# =============================================================================
# OTPCode
# =============================================================================
class OTPCode(Base):
    __tablename__ = "otpcode"

    otpid      = Column(Integer, primary_key=True, index=True)
    email      = Column(String(255), nullable=False, index=True)
    otp        = Column(String(10),  nullable=False)
    purpose    = Column(String(30),  nullable=False)   # verify_account | reset_password | verify_paramedic | reactivate_account
    expires_at = Column(TIMESTAMP,   nullable=False)
    used       = Column(Boolean, default=False)
    createdat  = Column(TIMESTAMP, server_default=func.now())


# =============================================================================
# AllAccountsView  — Codebase B DB View for unified login
# Maps to a PostgreSQL VIEW that unions admin + paramedic + user rows.
# This is NOT created by SQLAlchemy — it must exist in the DB already.
# =============================================================================
class AllAccountsView(Base):
    __tablename__ = "all_accounts"

    id           = Column(Integer, primary_key=True)
    email        = Column(String(255))
    passwordhash = Column(String(255))
    role         = Column(String(50))
    fullname     = Column(String(255))

    __table_args__ = {"info": dict(is_view=True)}
