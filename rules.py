"""팀 규칙 판정 (과제 공고 2.3절, 2.4절).

이 파일은 **화면과 DB를 모르는 순수 함수만** 담는다.
사람은 다음 꼴의 dict 로 넘긴다:

    {"id": 1, "student_no": "2026001", "name": "가나다",
     "main_role": "기획자", "sub_roles": ["보고서 작성자"]}

규칙을 화면과 섞으면 어디서 틀렸는지 찾기 어려워진다.
그래서 판정을 먼저 여기서 맞춘 뒤 화면에 붙인다.

이 파일은 학생의 이름이나 단계 순서를 보지 않는다. 규칙만 본다 (감점 19번).
"""

from db import REQUIRED_ROLES, TEAM_MAX, TEAM_MIN

# 막히는 이유 (화면 안내에 그대로 쓴다)
CLOSED = "모집이 마감됨"
ALREADY_IN_TEAM = "이미 팀이 있음"
HAS_PENDING = "결과를 기다리는 지원이 있음"
TEAM_FULL = "팀이 꽉 참"
SAME_MAIN_ROLE = "같은 주 역할의 팀원이 있음"
REQUIRED_SLOT_SHORT = "필수 포함 역할을 맡을 학생의 자리가 모자람"


def can_take(person, role):
    """그 사람이 이 역할을 맡을 수 있는지.

    주 역할이거나 보조 역할이면 맡을 수 있다 (공고 2.3절).
    """
    if not person:
        return False
    if person.get("main_role") == role:
        return True
    return role in (person.get("sub_roles") or ())


def max_required_matching(people):
    """필수 포함 역할 2개에 **서로 다른 사람**을 앉힐 수 있는 최대 수.

    한 사람이 기획자와 웹 프로그래머를 둘 다 할 수 있어도 **한 자리만** 센다.
    기획자와 웹 프로그래머는 서로 다른 학생이 맡아야 하기 때문이다 (공고 2.3절).
    """
    planner, programmer = REQUIRED_ROLES
    best = 0
    for i, person in enumerate(people):
        if can_take(person, planner):
            best = max(best, 1)
            for j, other in enumerate(people):
                if i != j and can_take(other, programmer):
                    return 2
        if can_take(person, programmer):
            best = max(best, 1)
    return best


def unmet_required(people):
    """이 사람들로 아직 채우지 못한 필수 포함 역할 수 (0, 1, 2)."""
    return len(REQUIRED_ROLES) - max_required_matching(people)


def main_role_duplicated(members, applicant):
    """주 역할이 같은 팀원이 있는지.

    **주 역할만** 본다. 보조 역할은 중복 판정에 쓰지 않는다 (공고 2.3절).
    """
    return any(m.get("main_role") == applicant.get("main_role") for m in members)


def required_slot_short(members, applicant):
    """지원자가 들어가면 필수 포함 역할을 채울 자리가 모자라는지.

        남는 자리 = 4 - (팀원 수 + 1)
        모자람    = 남는 자리 < 아직 못 채운 필수 포함 역할 수

    이것이 3장 "필수 포함 역할을 지킬 수 없는 팀"의 어려운 해결(7점)이다.
    """
    after = list(members) + [applicant]
    free_slots = TEAM_MAX - len(after)
    return free_slots < unmet_required(after)


def check_apply(applicant, members, closed, has_team, has_pending):
    """지원할 수 있는지 본다. 막히면 이유를, 괜찮으면 None 을 돌려준다.

    이유를 보는 순서는 공고 2.4절·2.8절의 순서를 따른다.
    """
    if closed:
        return CLOSED
    if has_team:
        return ALREADY_IN_TEAM
    if has_pending:
        return HAS_PENDING
    if len(members) >= TEAM_MAX:
        return TEAM_FULL
    if main_role_duplicated(members, applicant):
        return SAME_MAIN_ROLE
    if required_slot_short(members, applicant):
        return REQUIRED_SLOT_SHORT
    return None


def check_approve(applicant, members):
    """승인 직전에 규칙을 다시 본다 (공고 2.4절 "승인 직전").

    같은 팀에 대기 중인 지원이 여럿이면 앞의 지원이 먼저 승인되어 팀이 바뀐다.
    그래서 승인하려는 그 순간에 정원·주 역할 중복·필수 포함 역할 자리를 다시 검사한다.
    이것이 3장 "같은 팀에 대기 중인 지원이 여럿"의 어려운 해결(3점)이다.
    """
    if len(members) >= TEAM_MAX:
        return TEAM_FULL
    if main_role_duplicated(members, applicant):
        return SAME_MAIN_ROLE
    if required_slot_short(members, applicant):
        return REQUIRED_SLOT_SHORT
    return None


def check_team_final(members):
    """확정된 팀 하나가 규칙을 지키는지 본다 (공고 2.3절).

    마감 뒤 배정안 검사(2.5절 4항)와 마지막 확인에 쓴다.
    """
    problems = []
    if not TEAM_MIN <= len(members) <= TEAM_MAX:
        problems.append(f"인원이 {len(members)}명 (2~4명이어야 함)")

    seen = {}
    for m in members:
        role = m.get("main_role")
        seen[role] = seen.get(role, 0) + 1
    for role, count in seen.items():
        if count >= 2:
            problems.append(f"주 역할 '{role}' 이 {count}명")

    missing = unmet_required(members)
    if missing:
        planner, programmer = REQUIRED_ROLES
        if max_required_matching(members) == 0:
            problems.append(f"'{planner}' 와 '{programmer}' 를 맡을 학생이 없음")
        else:
            lack = [
                role
                for role in REQUIRED_ROLES
                if not any(can_take(m, role) for m in members)
            ]
            if lack:
                problems.append(f"'{lack[0]}' 를 맡을 학생이 없음")
            else:
                problems.append(
                    f"'{planner}' 와 '{programmer}' 를 서로 다른 학생이 맡을 수 없음"
                )
    return problems
