"""마감 뒤 배정 (과제 공고 2.5절 3~8항).

흐름:

1. 먼저 **앱이** 규칙을 지키는 배정이 있는지 모든 경우를 따져 확인한다.
   없으면 AI를 부르지 않고 "규칙을 지키는 배정이 없음"과 이유를 보여 준다 (2.5절 8항).
2. 있으면 AI(1차 → 2차 → 3차)에게 배정안을 받아 검사 5가지를 돌린다.
3. 3차까지 실패하면 1번에서 찾아 둔 배정을 쓴다 (3장 어려운 해결).

배정은 **팀이 없는 학생만** 넣는다. 이미 팀에 있는 학생은 옮기지 않는다.
"""

import json
import re
import time

import ai
import auth
import db
import rules
import teamflow

# 배정 전체에 쓰는 시간 한도 (초). 10분을 넘기면 감점 18번이라 9분으로 둔다.
GLOBAL_DEADLINE = 540


# ------------------------------------------------------------
# 지금 상태 읽기
# ------------------------------------------------------------

def snapshot():
    """(팀 목록, 팀이 없는 학생 목록)"""
    teams = []
    for row in db.query_all("select id, team_no, name from tf_teams order by team_no"):
        teams.append(
            {
                "id": row["id"],
                "team_no": row["team_no"],
                "name": row["name"],
                "members": teamflow.team_members(row["id"]),
            }
        )
    return teams, teamflow.unassigned_students()


# ------------------------------------------------------------
# 배정안 검사 (공고 2.5절 4항의 다섯 가지)
# ------------------------------------------------------------

def validate(assignment, teams, unassigned):
    """배정안의 문제를 목록으로 돌려준다. 빈 목록이면 통과.

    1. 팀이 없는 학생이 빠짐없이 배정됨
    2. 한 학생은 한 팀에만 배정됨
    3. 모든 팀이 2~4명
    4. 팀 안에서 주 역할 중복 없음
    5. 필수 포함 역할 충족
    """
    problems = []
    wanted = {s["student_no"] for s in unassigned}
    given = set(assignment)

    missing = wanted - given
    if missing:
        problems.append(f"배정되지 않은 학생이 {len(missing)}명 있음")
    extra = given - wanted
    if extra:
        problems.append(f"팀이 없는 학생이 아닌 사람이 {len(extra)}명 배정됨")

    team_nos = {t["team_no"] for t in teams}
    wrong_team = [no for no, tn in assignment.items() if tn not in team_nos]
    if wrong_team:
        problems.append(f"없는 팀 번호가 {len(wrong_team)}개 있음")

    if problems:
        return problems

    by_no = {s["student_no"]: s for s in unassigned}
    for team in teams:
        members = list(team["members"]) + [
            by_no[no] for no, tn in assignment.items() if tn == team["team_no"]
        ]
        for problem in rules.check_team_final(members):
            problems.append(f"{team['name']}: {problem}")
    return problems


# ------------------------------------------------------------
# 앱이 스스로 찾는 배정 (모든 경우를 따짐)
# ------------------------------------------------------------

def solve(teams, unassigned):
    """검사 5가지를 모두 통과하는 배정을 찾는다. 없으면 None.

    팀이 없는 학생은 8명을 넘지 않으므로(공고 6.2절 9항) 모든 경우를 따져도 된다.
    가지치기를 넣어 쓸모없는 경우는 일찍 버린다.
    """
    state = [
        {"team_no": t["team_no"], "members": list(t["members"])} for t in teams
    ]
    # 보조 역할이 적은(=넣기 까다로운) 학생을 먼저 배정하면 일찍 가지를 친다.
    students = sorted(
        unassigned, key=lambda s: (len(s.get("sub_roles") or []), s["student_no"])
    )
    assignment = {}

    def still_possible(index):
        remaining = len(students) - index
        need = sum(max(0, db.TEAM_MIN - len(t["members"])) for t in state)
        if need > remaining:
            return False
        capacity = sum(db.TEAM_MAX - len(t["members"]) for t in state)
        if remaining > capacity:
            return False
        for team in state:
            # 자리가 꽉 찼는데 필수 포함 역할이 모자라면 끝까지 못 고친다.
            if len(team["members"]) == db.TEAM_MAX and rules.unmet_required(
                team["members"]
            ):
                return False
        return True

    def step(index):
        if not still_possible(index):
            return False
        if index == len(students):
            return all(not rules.check_team_final(t["members"]) for t in state)
        student = students[index]
        for team in state:
            if len(team["members"]) >= db.TEAM_MAX:
                continue
            if rules.main_role_duplicated(team["members"], student):
                continue
            team["members"].append(student)
            assignment[student["student_no"]] = team["team_no"]
            if step(index + 1):
                return True
            team["members"].pop()
            del assignment[student["student_no"]]
        return False

    return dict(assignment) if step(0) else None


def why_impossible(teams, unassigned):
    """규칙을 지키는 배정이 없는 이유 (공고 2.5절 8항)."""
    reasons = []
    for team in teams:
        members = team["members"]
        if len(members) == db.TEAM_MAX and rules.unmet_required(members):
            for role in db.REQUIRED_ROLES:
                if not any(rules.can_take(m, role) for m in members):
                    reasons.append(f"{team['name']}에 {role}를 맡을 학생이 없음")

    need = sum(max(0, db.TEAM_MIN - len(t["members"])) for t in teams)
    if need > len(unassigned):
        reasons.append(
            f"모든 팀을 2명 이상으로 만들려면 {need}명이 필요한데 "
            f"팀이 없는 학생은 {len(unassigned)}명"
        )
    capacity = sum(db.TEAM_MAX - len(t["members"]) for t in teams)
    if len(unassigned) > capacity:
        reasons.append(
            f"팀의 빈 자리는 {capacity}개인데 팀이 없는 학생은 {len(unassigned)}명"
        )
    if not reasons:
        reasons.append("규칙을 모두 지키는 배정을 찾지 못함")
    return reasons


# ------------------------------------------------------------
# AI 에게 물어보기
# ------------------------------------------------------------

def build_prompt(teams, unassigned):
    """AI 에게 보낼 글. 지금의 팀 구성, 팀이 없는 학생, 규칙을 담는다."""
    planner, programmer = db.REQUIRED_ROLES

    team_lines = []
    for team in teams:
        if team["members"]:
            people = "; ".join(
                f"{m['name']}(주 역할 {m['main_role']}"
                + (f", 보조 역할 {', '.join(m['sub_roles'])}" if m["sub_roles"] else "")
                + ")"
                for m in team["members"]
            )
        else:
            people = "비어 있음"
        team_lines.append(
            f"- 팀 번호 {team['team_no']} ({team['name']}): {len(team['members'])}명 - {people}"
        )

    student_lines = []
    for s in unassigned:
        subs = ", ".join(s["sub_roles"]) if s["sub_roles"] else "없음"
        student_lines.append(
            f"- 학번 {s['student_no']} {s['name']}: 주 역할 {s['main_role']}, 보조 역할 {subs}"
        )

    return f"""너는 대학 수업의 팀 편성을 돕는다. 아래 규칙을 모두 지키는 배정을 만들어라.

[지금의 팀 구성]
{chr(10).join(team_lines)}

[팀이 없는 학생 - 이 학생들만 팀에 넣어라]
{chr(10).join(student_lines)}

[규칙]
1. 팀이 없는 학생을 한 명도 빠뜨리지 말고 모두 어느 한 팀에 넣어라.
2. 한 학생은 한 팀에만 들어간다.
3. 배정이 끝난 뒤 모든 팀의 인원은 2명 이상 4명 이하여야 한다.
4. 한 팀 안에 주 역할이 같은 학생이 2명 이상 있으면 안 된다.
   (보조 역할은 이 규칙과 상관없다)
5. 모든 팀에는 '{planner}'를 맡을 학생과 '{programmer}'를 맡을 학생이
   서로 다른 사람으로 각각 1명씩 있어야 한다.
   주 역할이든 보조 역할이든 그 역할을 맡을 수 있으면 된다.
   한 학생이 둘 다 맡을 수 있어도 한 자리만 채운 것으로 센다.
6. 이미 팀에 있는 학생은 절대 다른 팀으로 옮기지 마라.

[답하는 방법]
설명을 쓰지 말고 아래 꼴의 JSON 만 출력하라.

{{"assignments": [{{"student_no": "학번", "team_no": 팀번호}}]}}
"""


def parse_assignment(content):
    """AI 응답에서 배정안을 꺼낸다. {학번: 팀번호}"""
    text = (content or "").strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("응답에서 JSON 을 찾을 수 없음")
    data = json.loads(match.group(0))

    items = data.get("assignments")
    if items is None:
        items = data.get("배정") or data.get("result")
    if not isinstance(items, list):
        raise ValueError("assignments 목록이 없음")

    result = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("배정 항목의 꼴이 다름")
        number = item.get("student_no", item.get("학번"))
        team = item.get("team_no", item.get("팀"))
        if number is None or team is None:
            raise ValueError("학번이나 팀 번호가 빠진 항목이 있음")
        digits = re.search(r"\d+", str(team))
        if not digits:
            raise ValueError("팀 번호를 읽을 수 없음")
        result[str(number).strip()] = int(digits.group(0))
    if not result:
        raise ValueError("배정안이 비어 있음")
    return result


# ------------------------------------------------------------
# 배정 전체 흐름
# ------------------------------------------------------------

def run_assignment(me, on_progress=None, models=None):
    """AI 배정을 실행한다.

    돌려주는 값 dict:
        ok         통과한 배정안을 얻었는지
        assignment {학번: 팀번호}
        by         누가 만든 배정인지 (사람에게 보여 줄 글)
        lines      assign_status 에 쌓아서 보여 줄 글 목록
        message    result 에 보여 줄 안내
    """
    auth.require_admin(me)

    if not db.is_recruiting_closed():
        return {
            "ok": False,
            "lines": ["먼저 모집을 마감해야 배정할 수 있음"],
            "message": "막힘: 먼저 모집을 마감하세요",
        }

    teams, unassigned = snapshot()
    lines = []

    def say(text):
        lines.append(text)
        if on_progress:
            on_progress(list(lines))

    if not teams:
        return {
            "ok": False,
            "lines": ["팀이 없음. 먼저 명단을 올려야 함"],
            "message": "막힘: 팀이 없음",
        }

    if not unassigned:
        problems = []
        for team in teams:
            problems += [f"{team['name']}: {p}" for p in rules.check_team_final(team["members"])]
        if problems:
            say("규칙을 지키는 배정이 없음")
            for problem in problems:
                say(f"- {problem}")
            return {
                "ok": False,
                "lines": lines,
                "message": "막힘: 규칙을 지키는 배정이 없음",
            }
        say("팀이 없는 학생이 없음. 배정할 것이 없음")
        return {
            "ok": True,
            "assignment": {},
            "by": "배정할 학생 없음",
            "lines": lines,
            "message": "승인: 배정할 학생이 없음",
        }

    # 1. 규칙을 지키는 배정이 있는지 먼저 확인한다 (공고 2.5절 8항).
    say(f"팀이 없는 학생 {len(unassigned)}명. 규칙을 지키는 배정이 있는지 확인하는 중")
    started = time.monotonic()
    fallback = solve(teams, unassigned)
    say(f"확인에 {time.monotonic() - started:.1f}초 걸림")

    if fallback is None:
        say("규칙을 지키는 배정이 없음")
        for reason in why_impossible(teams, unassigned):
            say(f"- {reason}")
        say("AI를 부르지 않음")
        return {
            "ok": False,
            "lines": lines,
            "message": "막힘: 규칙을 지키는 배정이 없음",
        }

    say("규칙을 지키는 배정이 있음. AI에게 배정안을 받는다")

    # 2. AI 에게 물어본다. 1차 → 2차 → 3차.
    model_list = list(models if models is not None else ai.MODELS)
    deadline = time.monotonic() + GLOBAL_DEADLINE
    prompt = build_prompt(teams, unassigned)

    for attempt_no, model in enumerate(model_list, start=1):
        time_left = deadline - time.monotonic()
        if time_left < 15:
            say("전체 시간이 거의 다 되어 AI를 더 부르지 않음")
            break

        say(f"{attempt_no}차 모델의 응답을 기다리는 중")
        next_text = (
            "다음 모델로 넘어감"
            if attempt_no < len(model_list)
            else "앱이 스스로 배정함"
        )

        try:
            content, elapsed_ms = ai.call_model(model, prompt, time_left)
        except ai.AiFailure as failure:
            ai.log_call(attempt_no, model, failure.outcome, failure.reason)
            say(f"{attempt_no}차 모델 실패({failure.reason}) → {next_text}")
            continue

        try:
            assignment = parse_assignment(content)
            problems = validate(assignment, teams, unassigned)
        except (ValueError, json.JSONDecodeError) as e:
            problems = [f"응답을 읽을 수 없음: {e}"]

        if problems:
            ai.log_call(
                attempt_no,
                model,
                ai.OUTCOME_INVALID,
                "; ".join(problems)[:500],
                elapsed_ms,
                content,
            )
            say(f"{attempt_no}차 모델 실패(검사 통과 못 함: {problems[0]}) → {next_text}")
            continue

        ai.log_call(attempt_no, model, ai.OUTCOME_OK, None, elapsed_ms, content)
        say(f"{attempt_no}차 모델의 배정안이 검사 5가지를 모두 통과함")
        return {
            "ok": True,
            "assignment": assignment,
            "by": f"{attempt_no}차 모델 {model}",
            "lines": lines,
            "message": "승인: 배정안을 만듦",
        }

    # 3. 3차까지 실패하면 앱이 스스로 찾아 둔 배정을 쓴다.
    ai.log_call(
        4,
        None,
        ai.OUTCOME_OK,
        "AI가 3차까지 실패해 앱이 스스로 배정함",
        None,
        None,
    )
    say("AI가 3차까지 실패함 → 앱이 모든 경우를 따져 찾은 배정을 씀")
    return {
        "ok": True,
        "assignment": fallback,
        "by": "앱 자체 배정 (AI 3차까지 실패)",
        "lines": lines,
        "message": "승인: 앱이 스스로 배정안을 만듦",
    }


def assignment_table(assignment):
    """배정안을 화면 표로 보여 줄 꼴로 바꾼다 (학생 이름, 역할, 배정된 팀)."""
    if not assignment:
        return []
    _, unassigned = snapshot()
    by_no = {s["student_no"]: s for s in unassigned}
    team_name = {
        row["team_no"]: row["name"]
        for row in db.query_all("select team_no, name from tf_teams")
    }
    rows = []
    for number, team_no in sorted(assignment.items(), key=lambda kv: (kv[1], kv[0])):
        person = by_no.get(number)
        if person is None:
            continue
        rows.append(
            {
                "학생 이름": person["name"],
                "주 역할": person["main_role"],
                "보조 역할": ", ".join(person["sub_roles"]) or "없음",
                "배정된 팀": team_name.get(team_no, f"팀{team_no}"),
            }
        )
    return rows


def confirm_assignment(me, assignment):
    """배정안을 DB에 저장한다 (공고 2.5절 7항)."""
    auth.require_admin(me)

    if not assignment:
        return "막힘: 확정할 배정안이 없음"

    teams, unassigned = snapshot()
    problems = validate(assignment, teams, unassigned)
    if problems:
        # 확정 직전에 한 번 더 검사한다. 통과하지 못한 배정은 저장하지 않는다.
        return f"막힘: 배정안이 규칙을 어김 ({problems[0]})"

    id_by_no = {s["student_no"]: s["id"] for s in unassigned}
    team_id_by_no = {t["team_no"]: t["id"] for t in teams}

    with db.transaction():
        for number, team_no in assignment.items():
            if not teamflow.place_member(team_id_by_no[team_no], id_by_no[number]):
                raise RuntimeError("자리에 넣지 못했습니다")

    return f"승인: 배정안을 확정함 ({len(assignment)}명)"
