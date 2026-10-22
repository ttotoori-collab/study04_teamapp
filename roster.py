"""명단 CSV 읽기와 처음 상태 만들기 (과제 공고 2.6절).

채점자는 이 기능으로 시나리오마다 명단을 바꾼다. 이 기능이 없으면 채점 자체가
불가능하므로, CSV를 읽을 때 되도록 너그럽게 읽는다.

CSV는 반드시 utf-8-sig 로 읽는다. 나눠 주는 파일은 엑셀에서 한글이 깨지지 않도록
BOM이 붙은 UTF-8 이기 때문이다.
"""

import csv
import io

import auth
import db

HEADER = ["student_no", "name", "main_role", "sub_roles"]

MAX_SUB_ROLES = 2
MIN_TEAM_COUNT = 1
MAX_TEAM_COUNT = 20


class RosterError(Exception):
    """명단을 읽을 수 없을 때. 메시지는 화면에 그대로 보여 준다."""


def _clean(value):
    return (value or "").strip()


def parse_csv(raw_bytes):
    """명단 CSV를 읽어 학생 목록을 돌려준다.

    돌려주는 값: [{"student_no", "name", "main_role", "sub_roles"[]}, ...]
    """
    if not raw_bytes:
        raise RosterError("명단 CSV 파일이 비어 있음")

    # BOM이 있어도 없어도 읽히도록 utf-8-sig 를 쓴다.
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise RosterError(
            "명단 CSV를 UTF-8로 읽을 수 없음. 파일을 UTF-8로 저장하세요"
        )

    rows = [r for r in csv.reader(io.StringIO(text)) if any(_clean(c) for c in r)]
    if not rows:
        raise RosterError("명단 CSV에 내용이 없음")

    header = [_clean(c) for c in rows[0]][: len(HEADER)]
    if header != HEADER:
        raise RosterError(
            "명단 CSV의 첫 줄이 " + ",".join(HEADER) + " 이어야 함"
        )

    students = []
    seen = set()
    for line_no, row in enumerate(rows[1:], start=2):
        # 열이 모자라면 빈 칸으로 채우고, 남는 빈 칸은 버린다.
        cells = [_clean(c) for c in row]
        while len(cells) < len(HEADER):
            cells.append("")
        if len(cells) > len(HEADER):
            extra = cells[len(HEADER):]
            if any(extra):
                raise RosterError(f"{line_no}번째 줄의 열이 너무 많음")
            cells = cells[: len(HEADER)]

        student_no, name, main_role, sub_text = cells

        if not student_no:
            raise RosterError(f"{line_no}번째 줄에 학번이 없음")
        if not name:
            raise RosterError(f"{line_no}번째 줄에 이름이 없음")
        if student_no in seen:
            raise RosterError(f"학번 {student_no} 이 두 번 나옴")
        seen.add(student_no)

        if main_role not in db.ROLES:
            raise RosterError(
                f"{line_no}번째 줄의 주 역할 '{main_role}' 은 정해진 역할이 아님"
            )

        # 보조 역할은 ; 로 구분한다. 없으면 빈 칸이다.
        sub_roles = []
        for piece in sub_text.split(";"):
            role = _clean(piece)
            if not role:
                continue
            if role not in db.ROLES:
                raise RosterError(
                    f"{line_no}번째 줄의 보조 역할 '{role}' 은 정해진 역할이 아님"
                )
            if role not in sub_roles:
                sub_roles.append(role)
        if len(sub_roles) > MAX_SUB_ROLES:
            raise RosterError(
                f"{line_no}번째 줄의 보조 역할이 {len(sub_roles)}개임 "
                f"(최대 {MAX_SUB_ROLES}개)"
            )

        students.append(
            {
                "student_no": student_no,
                "name": name,
                "main_role": main_role,
                "sub_roles": sub_roles,
            }
        )

    if not students:
        raise RosterError("명단에 학생이 없음")
    return students


def apply_roster(me, students, team_count):
    """처음 상태를 만든다 (과제 공고 2.6절).

    지원, 투표, 팀원, 마감 표시를 모두 지우고, 명단의 학생과 빈 팀을 만든다.
    관리자 계정은 남긴다.
    """
    # 화면을 숨기는 것과 별개로 권한을 한 번 더 검사한다 (6.3절 11번).
    auth.require_admin(me)

    team_count = int(team_count)
    if not MIN_TEAM_COUNT <= team_count <= MAX_TEAM_COUNT:
        raise RosterError(
            f"팀 수는 {MIN_TEAM_COUNT}~{MAX_TEAM_COUNT} 사이여야 함"
        )

    # 학생 비밀번호는 모두 1234 다 (공고 2.2절). 해시를 한 번만 만들어 함께 쓴다.
    # 학생마다 bcrypt 를 돌리면 명단 올리기가 몇 초씩 느려지는데,
    # 채점 프로그램이 이 단추를 누르고 기다리므로 빠른 편이 낫다.
    password_hash = auth.hash_password(auth.STUDENT_DEFAULT_PASSWORD)

    with db.transaction():
        db.reset_all()

        db.execute_many(
            """
            insert into tf_students (student_no, name, main_role, password_hash)
                 values (%s, %s, %s, %s)
            """,
            [
                (s["student_no"], s["name"], s["main_role"], password_hash)
                for s in students
            ],
        )

        # 학번 -> id 를 한 번에 읽어 보조 역할을 넣는다.
        id_by_no = {
            row["student_no"]: row["id"]
            for row in db.query_all(
                "select id, student_no from tf_students where is_admin = false"
            )
        }
        sub_rows = [
            (id_by_no[s["student_no"]], role)
            for s in students
            for role in s["sub_roles"]
        ]
        db.execute_many(
            "insert into tf_student_sub_roles (student_id, role) values (%s, %s)",
            sub_rows,
        )

        db.execute_many(
            "insert into tf_teams (team_no, name) values (%s, %s)",
            [(i, f"팀{i}") for i in range(1, team_count + 1)],
        )

        db.set_setting("team_count", team_count)

    return {"students": len(students), "teams": team_count}


def team_count():
    """만들어진 팀 수."""
    row = db.query_one("select count(*) as n from tf_teams")
    return 0 if row is None else row["n"]


def teams_with_members():
    """팀 목록과 팀원을 돌려준다.

    돌려주는 값:
      [{"team_no", "name", "members": [{"student_id","name","main_role","sub_roles"[]}]}]
    """
    teams = db.query_all(
        "select id, team_no, name from tf_teams order by team_no"
    )
    if not teams:
        return []

    members = db.query_all(
        """
        select m.team_id, m.slot_no, s.id as student_id, s.name, s.main_role
          from tf_members m
          join tf_students s on s.id = m.student_id
         order by m.team_id, m.slot_no
        """
    )

    sub_by_student = {}
    for row in db.query_all(
        """
        select r.student_id, r.role
          from tf_student_sub_roles r
          join tf_members m on m.student_id = r.student_id
         order by r.student_id, r.role
        """
    ):
        sub_by_student.setdefault(row["student_id"], []).append(row["role"])

    by_team = {}
    for row in members:
        by_team.setdefault(row["team_id"], []).append(
            {
                "student_id": row["student_id"],
                "name": row["name"],
                "main_role": row["main_role"],
                "sub_roles": sub_by_student.get(row["student_id"], []),
            }
        )

    return [
        {
            "team_no": t["team_no"],
            "name": t["name"],
            "members": by_team.get(t["id"], []),
        }
        for t in teams
    ]
