"""
Utility script to create the first admin account.
Run once from the project root:
    python create_admin.py
"""
from dotenv import load_dotenv
load_dotenv()

from database import SessionLocal
from passlib.context import CryptContext
import models

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def create_new_admin():
    db = database.SessionLocal()
    print("--- Create a new admin account for the Salamah Medical System ---")

    try:
        email    = input("\n Enter the new admin's email: ").strip().lower()
        fullname = input(" Enter the full name: ").strip()
        password = input(" Enter the password: ").strip()

        if not email or not password:
            print("\n❌ Error: Email and password are required!")
            return

        existing = db.query(models.Admin).filter(models.Admin.email == email).first()
        if existing:
            print(f"\n❌ Error: Email '{email}' is already registered to another admin.")
            return

        hashed = pwd_context.hash(password)
        new_admin = models.Admin(
            email        = email,
            fullname     = fullname,
            passwordhash = hashed
        )
        db.add(new_admin)
        db.commit()

        print(f"\n✅ Admin '{fullname}' created successfully!")
        print(f"   Login with: {email}")

    except Exception as e:
        db.rollback()
        print(f"\n❌ Error: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    import database   # noqa: E402  (import after load_dotenv)
    create_new_admin()
