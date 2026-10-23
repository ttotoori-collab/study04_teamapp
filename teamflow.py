"""지원과 투표 처리 (과제 공고 2.4절).

규칙 판정은 rules.py 가 하고, 이 파일은 DB를 읽고 쓰는 일을 한다.

돌려주는 문구는 모두 승인 / 대기 / 거절 / 막힘 중 하나로 시작한다 (공고 2.7절).
"""

import psycopg

import db
import rules

VOTE_YES = "찬성"
VOTE_NO = "거부"

STATUS_PENDING = "대기"
STATUS_APPROVED = "승인"
STATUS_REJECTED = "거절"


# ------------------------------------------------------------
# 읽기
# ------------------------------------------------------------

def _attach_sub_roles(people):
    """사람 목록에 보조 역할을 붙인다. 질의를 한 번만 한다."""
    people = [dict(p) for p in people]
    if not people:
        return people
    ids = [p["id"] for p in people]
    rows = db.query_all(
        "select student_id, role from tf_student_sub_roles"
        " where student_id = any(%s) order by student_id, role",
        (ids,),
    )
    by_id = {}
    for row in rows:
        by_id.setdefault(row["student_id"], []).append(row["role"])
    for person in people:
        person["sub_roles"] = by_id.get(person["id"], [])
    return people


def person_of(student_id):
    """한 사람의 정보(보조 역할 포함)."""
    row = db.query_one(
        "select id, student_no, name, main_role from tf_students where id = %s",
        (student_id,),
    )
    if row is None:
        return None
    return _attach_sub_roles([row])[0]


def team_by_no(team_no):
    return db.query_one(
        "select id, team_no, name from tf_teams where team_no = %s", (team_no,)
    )


def team_members(team_id):
    """그 팀의 지금 팀원. 자리 번호 순서."""
    rows = db.query_all(
        """
        select s.id, s.student_no, s.name, s.main_role, m.slot_no
          from tf_members m
          join tf_students s on s.id = m.student_id
         where m.team_id = %s
         order by m.slot_no
        """,
        (team_id,),
    )
    return _attach_sub_roles(rows)


def my_team(student_id):
    """내가 속한 팀. 없으면 None."""
    return db.query_one(
        """
        select t.id, t.team_no, t.name
          from tf_members m
          join tf_teams t on t.id = m.team_id
         where m.student_id = %s
        """,
        (student_id,),
    )


def my_pending_application(student_id):
    """결과를 기다리는 내 지원. 없으면 None."""
    return db.query_one(
        """
        select a.id, a.team_id, t.team_no, t.name
          from tf_applications a
          join tf_teams t on t.id = a.team_id
         where a.student_id = %s and a.status = %s
        """,
        (student_id, STATUS_PENDING),
    )


def pending_applications_for_team(team_id):
    """그 팀에 들어온 대기 중인 지원. 지원한 순서."""
    rows = db.query_all(
        """
        select a.id as application_id, a.created_at,
               s.id, s.student_no, s.name, s.main_role
          from tf_applications a
          join tf_students s on s.id = a.student_id
         where a.team_id = %s and a.status = %s
         order by a.created_at, a.id
        """,
        (team_id, STATUS_PENDING),
    )
    return _attach_sub_roles(rows)


def votes_of(application_id):
    """그 지원에 들어온 투표. {투표한 사람 id: '찬성'|'거부'}"""
    rows = db.query_all(
        "select voter_id, vote from tf_votes where application_id = %s",
        (application_id,),
    )
    return {r["voter_id"]: r["vote"] for r in rows}


def application_status(application_id):
    row = db.query_one(
        "select status, reason from tf_applications where id = %s", (application_id,)
    )
    return row


# ------------------------------------------------------------
# 쓰기
# ------------------------------------------------------------

def _place_member(team_id, student_id):
    """빈 자리에 팀원을 넣는다. 넣었으면 True.

    자리는 DB가 지켜 준다. (team_id, slot_no) 기본 키 때문에 같은 자리에
    두 명이 들어갈 수 없고, student_id 유일 제약 때문에 한 학생은 한 팀에만
    속한다. 먼저 도착한 쪽만 기록된다.
    (3장 "마지막 자리에 두 명이 동시에 들어감" 어려운 해결)
    """
    for slot_no in range(1, db.TEAM_MAX + 1):
        try:
            # 넣기를 저장점(savepoint) 안에서 한다.
            # 이렇게 해야 자리가 겹쳐 실패해도 그 시도만 되돌려지고,
            # 바깥 묶음 처리(배정 확정 등)가 함께 중단되지 않는다.
            with db.transaction():
                db.execute(
                    "insert into tf_members (team_id, slot_no, student_id)"
                    " values (%s, %s, %s)",
                    (team_id, slot_no, student_id),
                )
            return True
        except psycopg.errors.UniqueViolation as e:
            name = (e.diag.constraint_name or "").lower()
            if "student_id" in name:
                # 이미 어느 팀에 들어가 있다. 더 시도하지 않는다.
                return False
            # 그 자리는 이미 찼다. 다음 자리를 본다.
            continue
    return False


def place_member(team_id, student_id):
    """빈 자리에 팀원을 넣는다 (배정 확정에서도 쓴다)."""
    return _place_member(team_id, student_id)


def _set_application(application_id, status, reason=None):
    db.execute(
        """
        update tf_applications
           set status = %s, reason = %s, resolved_at = now()
         where id = %s
        """,
        (status, reason, application_id),
    )


def apply_to_team(me, team_no):
    """팀에 지원한다. 안내 문구를 돌려준다."""
    if me.get("is_admin"):
        return "막힘: 관리자는 지원할 수 없음"

    team = team_by_no(team_no)
    if team is None:
        return "막힘: 그런 팀이 없음"

    applicant = person_of(me["id"])
    if applicant is None:
        return "막힘: 학생 정보를 찾을 수 없음"

    members = team_members(team["id"])
    reason = rules.check_apply(
        applicant,
        members,
        closed=db.is_recruiting_closed(),
        has_team=my_team(me["id"]) is not None,
        has_pending=my_pending_application(me["id"]) is not None,
    )
    if reason:
        # 막힌 지원은 접수하지 않는다. DB에 기록하지 않아도 된다 (공고 2.4절).
        return f"막힘: {reason}"

    if not members:
        # 빈 팀에 지원하면 투표할 팀원이 없으므로 바로 승인된다 (공고 2.4절).
        row = db.query_one(
            """
            insert into tf_applications (team_id, student_id, status, resolved_at)
                 values (%s, %s, %s, now())
              returning id
            """,
            (team["id"], me["id"], STATUS_APPROVED),
        )
        if not _place_member(team["id"], me["id"]):
            _set_application(row["id"], STATUS_REJECTED, rules.TEAM_FULL)
            return f"거절: {rules.TEAM_FULL}"
        return f"승인: {team['name']}에 들어감"

    db.execute(
        "insert into tf_applications (team_id, student_id, status) values (%s, %s, %s)",
        (team["id"], me["id"], STATUS_PENDING),
    )
    return f"대기: {team['name']} 팀원들이 투표하는 중"


def cast_vote(me, applicant_student_no, agree):
    """내 팀에 들어온 지원에 찬성 또는 거부한다. 안내 문구를 돌려준다."""
    if me.get("is_admin"):
        return "막힘: 관리자는 투표할 수 없음"
    if db.is_recruiting_closed():
        return "막힘: 모집이 마감됨"

    team = my_team(me["id"])
    if team is None:
        return "막힘: 팀이 없어 투표할 수 없음"

    app = db.query_one(
        """
        select a.id, a.team_id, a.student_id
          from tf_applications a
          join tf_students s on s.id = a.student_id
         where a.team_id = %s and s.student_no = %s and a.status = %s
        """,
        (team["id"], applicant_student_no, STATUS_PENDING),
    )
    if app is None:
        return "막힘: 투표할 지원이 없음"

    members = team_members(team["id"])
    if me["id"] not in [m["id"] for m in members]:
        return "막힘: 그 팀의 팀원만 투표할 수 있음"

    try:
        db.execute(
            "insert into tf_votes (application_id, voter_id, vote) values (%s, %s, %s)",
            (app["id"], me["id"], VOTE_YES if agree else VOTE_NO),
        )
    except psycopg.errors.UniqueViolation:
        # 한 번 투표하면 바꿀 수 없다 (공고 2.4절). DB 기본 키가 막아 준다.
        return "막힘: 이미 투표했음"

    return resolve_application(app["id"])


def resolve_application(application_id):
    """투표 결과를 보고 승인·거절·대기를 정한다.

    투표할 사람은 **투표하는 때의 팀원**이다. 내가 지원한 뒤에 다른 지원자가
    먼저 승인되어 팀원이 되면, 그 사람도 내 지원에 투표해야 한다 (공고 2.4절).
    """
    app = db.query_one(
        """
        select a.id, a.team_id, a.student_id, a.status, t.name as team_name
          from tf_applications a
          join tf_teams t on t.id = a.team_id
         where a.id = %s
        """,
        (application_id,),
    )
    if app is None:
        return "막힘: 지원을 찾을 수 없음"
    if app["status"] != STATUS_PENDING:
        return f"{app['status']}: 이미 결과가 나온 지원"

    members = team_members(app["team_id"])
    votes = votes_of(app["id"])

    # 한 명이라도 거부하면 다른 팀원의 투표를 기다리지 않고 바로 거절한다.
    if any(vote == VOTE_NO for vote in votes.values()):
        _set_application(app["id"], STATUS_REJECTED, "팀원이 거부함")
        return "거절: 팀원이 거부함"

    member_ids = [m["id"] for m in members]
    if not all(votes.get(mid) == VOTE_YES for mid in member_ids):
        return "대기: 아직 투표하지 않은 팀원이 있음"

    # 모든 팀원이 찬성했다. 승인 직전에 규칙을 다시 검사한다 (어려운 해결 3점).
    applicant = person_of(app["student_id"])
    reason = rules.check_approve(applicant, members)
    if reason:
        _set_application(app["id"], STATUS_REJECTED, f"승인 직전 검사: {reason}")
        return f"거절: {reason}"

    if not _place_member(app["team_id"], app["student_id"]):
        _set_application(app["id"], STATUS_REJECTED, f"승인 직전 검사: {rules.TEAM_FULL}")
        return f"거절: {rules.TEAM_FULL}"

    _set_application(app["id"], STATUS_APPROVED)
    return f"승인: {app['team_name']} 팀원이 됨"


def unassigned_students():
    """팀이 없는 학생 (공고 2.5절 2항). 관리자는 빼고 센다."""
    rows = db.query_all(
        """
        select s.id, s.student_no, s.name, s.main_role
          from tf_students s
          left join tf_members m on m.student_id = s.id
         where s.is_admin = false and m.student_id is null
         order by s.student_no
        """
    )
    return _attach_sub_roles(rows)


def close_recruiting(me):
    """모집을 마감한다 (공고 2.5절 1~2항).

    1. 그 뒤로는 지원과 투표를 할 수 없다.
    2. 대기 중인 지원은 거절된다.
    3. 1명뿐인 팀의 학생은 팀에서 빠져 "팀이 없는 학생"이 된다.
    """
    import auth  # 순환 수입을 피하려고 여기서 들여온다

    auth.require_admin(me)

    if db.is_recruiting_closed():
        return "막힘: 이미 마감됨"

    with db.transaction():
        db.set_setting("closed", "true")
        db.set_setting("closed_at", _now_text())

        rejected = db.execute(
            """
            update tf_applications
               set status = %s, reason = %s, resolved_at = now()
             where status = %s
            """,
            (STATUS_REJECTED, "모집 마감", STATUS_PENDING),
        )

        removed = db.execute(
            """
            delete from tf_members
             where team_id in (
                   select team_id from tf_members
                    group by team_id having count(*) = 1
             )
            """
        )

    return (
        f"승인: 모집을 마감함 (대기 지원 {rejected}건 거절, "
        f"1명뿐인 팀에서 {removed}명 빠짐)"
    )


def _now_text():
    """지금 시각을 한국 시각 글자로."""
    from datetime import datetime, timedelta, timezone

    return datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S")


def my_votes_on_team(me):
    """내 팀의 대기 지원에 내가 어떻게 투표했는지. {지원자 학번: '찬성'|'거부'}"""
    team = my_team(me["id"])
    if team is None:
        return {}
    rows = db.query_all(
        """
        select s.student_no, v.vote
          from tf_votes v
          join tf_applications a on a.id = v.application_id
          join tf_students s on s.id = a.student_id
         where v.voter_id = %s and a.team_id = %s
        """,
        (me["id"], team["id"]),
    )
    return {r["student_no"]: r["vote"] for r in rows}
