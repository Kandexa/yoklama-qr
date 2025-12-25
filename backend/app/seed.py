from sqlalchemy.orm import Session
from .models import User
from .auth import hash_password

TEACHER_USERNAME = "yavuz"
TEACHER_PASSWORD = "YavuzSumer@123"
TEACHER_NAME = "Dr. Yavuz Sümer"

def seed_users(db: Session):
    # Teacher
    t = db.query(User).filter(User.username == TEACHER_USERNAME).first()
    if not t:
        db.add(User(
            username=TEACHER_USERNAME,
            full_name=TEACHER_NAME,
            password_hash=hash_password(TEACHER_PASSWORD),
            role="teacher"
        ))

    # 30 Students: 2025001..2025030
    # Password: Sifre2025!001..Sifre2025!030
    for i in range(1, 31):
        username = f"2025{str(i).zfill(3)}"
        full_name = f"Ogrenci {str(i).zfill(2)}"
        password = f"Sifre2025!{str(i).zfill(3)}"

        s = db.query(User).filter(User.username == username).first()
        if not s:
            db.add(User(
                username=username,
                full_name=full_name,
                password_hash=hash_password(password),
                role="student"
            ))

    db.commit()
