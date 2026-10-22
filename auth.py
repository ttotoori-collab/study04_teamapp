"""로그인과 비밀번호.

비밀번호는 bcrypt 로 해시해서 저장한다 (과제 공고 6.3절 10번, -2점).
해시는 되돌릴 수 없는 다른 문자열이라, DB가 새어 나가도 원래 비밀번호를 알 수 없다.

이 파일은 비밀번호나 키를 화면에 출력하지 않는다 (6.3절 8번, -3점).
"""

import bcrypt

import db

# 관리자 학번 (과제 공고 2.1절)
ADMIN_STUDENT_NO = "admin"
ADMIN_NAME = "관리자"

# 학생 비밀번호는 모두 1234 (과제 공고 2.2절)
STUDENT_DEFAULT_PASSWORD = "1234"

# bcrypt 가 받는 비밀번호 길이 한도
BCRYPT_MAX_BYTES = 72


class NotAdminError(Exception):
    """관리자가 아닌 사람이 관리자 기능을 쓰려 할 때."""


def hash_password(password):
    """비밀번호를 해시로 바꾼다."""
    raw = password.encode("utf-8")
    if len(raw) > BCRYPT_MAX_BYTES:
        raise ValueError(
            f"비밀번호가 너무 깁니다. {BCRYPT_MAX_BYTES}바이트 이하로 정하세요."
        )
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("ascii")


def verify_password(password, password_hash):
    """비밀번호가 해시와 맞는지 확인한다. 틀리면 False."""
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"), password_hash.encode("ascii")
        )
    except (ValueError, TypeError):
        # 해시 꼴이 깨져 있으면 로그인시키지 않는다.
        return False


def ensure_admin():
    """앱이 시작할 때 관리자 계정을 만들거나 비밀번호를 갱신한다.

    과제 공고 2.1절: ADMIN_PASSWORD 로 관리자 계정을 만들거나 비밀번호를 갱신하고,
    명단을 다시 올려도 관리자 계정은 남긴다.
    """
    password = db.secret("ADMIN_PASSWORD")
    if not password:
        raise RuntimeError(
            "ADMIN_PASSWORD 가 없습니다. 로컬에서는 .env, "
            "배포에서는 Streamlit Secrets 를 확인하세요."
        )
    db.execute(
        """
        insert into tf_students (student_no, name, password_hash, is_admin)
             values (%s, %s, %s, true)
        on conflict (student_no) do update
                set password_hash = excluded.password_hash,
                    is_admin      = true
        """,
        (ADMIN_STUDENT_NO, ADMIN_NAME, hash_password(password)),
    )


def find_student(student_no):
    """학번으로 한 사람을 찾는다. 없으면 None."""
    return db.query_one(
        """
        select id, student_no, name, main_role, password_hash, is_admin
          from tf_students
         where student_no = %s
        """,
        (student_no,),
    )


def login(student_no, password):
    """로그인을 시도한다.

    돌려주는 값: (성공했는지, 사람 정보 또는 None, 안내 문구)

    학번만으로는 절대 로그인되지 않는다 (6.3절 12번, -3점).
    """
    student_no = (student_no or "").strip()
    if not student_no or not password:
        return False, None, "막힘: 학번과 비밀번호를 모두 넣으세요"

    row = find_student(student_no)
    if row is None:
        # 있는 학번인지 알려 주지 않는다.
        return False, None, "막힘: 학번 또는 비밀번호가 맞지 않음"

    if not verify_password(password, row["password_hash"]):
        return False, None, "막힘: 학번 또는 비밀번호가 맞지 않음"

    me = {
        "id": row["id"],
        "student_no": row["student_no"],
        "name": row["name"],
        "main_role": row["main_role"],
        "is_admin": bool(row["is_admin"]),
    }
    return True, me, f"승인: {me['name']}(으)로 로그인함"


def sub_roles_of(student_id):
    """보조 역할 목록."""
    rows = db.query_all(
        "select role from tf_student_sub_roles where student_id = %s order by role",
        (student_id,),
    )
    return [r["role"] for r in rows]


def require_admin(me):
    """관리자만 지나갈 수 있다.

    화면에서 단추를 숨기는 것만으로는 부족하다. 관리자 기능을 실제로 하는
    함수 안에서 이 검사를 한 번 더 한다 (6.3절 11번, -3점).
    """
    if not me or not me.get("is_admin"):
        raise NotAdminError("막힘: 관리자만 쓸 수 있음")
    return True
