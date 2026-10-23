"""팀 편성 웹 앱.

화면 요소의 key 는 과제 공고 2.7절 표를 그대로 따른다.
key 를 바꾸면 자동 채점 프로그램이 요소를 찾지 못해 -5점이다 (6.3절 15번).

이 앱은 API 키나 DB 주소를 화면에 출력하지 않는다 (6.3절 8번, -3점).
단추를 눌렀을 때 어떤 오류가 나도 화면에 오류 추적(Traceback)을 보이지 않고
"막힘: ..." 안내만 남긴다. 화면이 죽으면 채점 프로그램이 그 자리에서 멈춘다.
"""

import traceback
from datetime import timedelta, timezone

import psycopg
import streamlit as st

import ai
import assign
import auth
import db
import roster
import teamflow

st.set_page_config(page_title="팀 편성", layout="wide")

# 배포 서버의 시각은 UTC 다. 화면에 보여 줄 때는 한국 시각으로 바꾼다 (공고 4.4절).
KST = timezone(timedelta(hours=9))


# ------------------------------------------------------------
# 앱이 시작할 때 한 번만 하는 일
# ------------------------------------------------------------

@st.cache_resource(show_spinner=False)
def startup():
    """관리자 계정을 만들거나 비밀번호를 갱신한다 (과제 공고 2.1절)."""
    auth.ensure_admin()
    return True


# ------------------------------------------------------------
# 결과 안내 (key = result)
# ------------------------------------------------------------
# 안내는 반드시 승인 / 대기 / 거절 / 막힘 중 하나로 시작한다 (2.7절).

def set_result(message):
    st.session_state["result_message"] = message


def blocked_text(message):
    """'막힘: ' 으로 시작하게 맞춘다."""
    text = str(message)
    return text if text.startswith("막힘") else f"막힘: {text}"


def render_result():
    with st.container(key="result"):
        st.write(st.session_state.get("result_message", ""))


# ------------------------------------------------------------
# 단추 하나를 눌렀을 때 하는 일을 감싸는 공통 처리
# ------------------------------------------------------------

def run_action(func, *args, **kwargs):
    """모든 단추가 이것을 거친다.

    하는 일이 안내 문구를 돌려주면 result 에 넣는다.
    오류가 나면 화면에는 짧은 "막힘: ..." 만 보여 주고,
    자세한 내용은 서버 기록에만 남긴다.
    """
    try:
        message = func(*args, **kwargs)
        if message:
            set_result(message)
    except (roster.RosterError, auth.NotAdminError, ValueError) as e:
        set_result(blocked_text(e))
    except psycopg.Error:
        # 예외 메시지에 DB 주소가 들어갈 수 있어 그대로 보여 주지 않는다 (6.3절 8번).
        traceback.print_exc()
        set_result("막힘: DB에 저장하지 못함")
    except Exception:
        traceback.print_exc()
        set_result("막힘: 처리하지 못함")
    st.rerun()


# ------------------------------------------------------------
# 로그인한 사람 (key = whoami)
# ------------------------------------------------------------

def current_user():
    return st.session_state.get("me")


def describe_person(name, main_role, sub_roles):
    subs = ", ".join(sub_roles) if sub_roles else "없음"
    return f"{name} — 주: {main_role} / 보조: {subs}"


def render_whoami():
    me = current_user()
    with st.container(key="whoami"):
        if me is None:
            st.write("로그인하지 않음")
        elif me["is_admin"]:
            st.write(f"{me['name']} (관리자)")
        else:
            st.write(
                describe_person(
                    me["name"], me["main_role"], auth.sub_roles_of(me["id"])
                )
            )


# ------------------------------------------------------------
# 로그인 화면
# ------------------------------------------------------------

def do_login():
    ok, me, message = auth.login(
        st.session_state.get("login_student_no"),
        st.session_state.get("login_password"),
    )
    if ok:
        st.session_state["me"] = me
    return message


def render_login():
    st.subheader("로그인")
    with st.form("login_form"):
        st.text_input("학번", key="login_student_no")
        st.text_input("비밀번호", type="password", key="login_password")
        submitted = st.form_submit_button("로그인", key="login_submit")
    if submitted:
        run_action(do_login)


def do_logout():
    st.session_state.pop("me", None)
    return "승인: 로그아웃함"


def render_logout():
    if st.button("로그아웃", key="logout"):
        run_action(do_logout)


# ------------------------------------------------------------
# 팀 목록 (key = team_1, team_2, ... / apply_1, apply_2, ...)
# ------------------------------------------------------------

def render_teams(me):
    """팀 하나가 key = team_N 인 container 다. 팀원의 이름과 역할이 보인다.

    학생 화면에는 팀마다 key = apply_N 인 지원 단추를 둔다.
    이미 팀이 있거나, 결과를 기다리는 지원이 있거나, 모집이 마감되었으면
    지원 단추를 아예 그리지 않는다 (공고 2.8절). 채점 프로그램은 단추가
    없으면 "막힘"으로 본다.
    """
    teams = roster.teams_with_members()
    if not teams:
        st.caption("아직 팀이 없음. 관리자가 명단을 올리면 팀이 만들어진다.")
        return

    is_student = me is not None and not me["is_admin"]
    closed = db.is_recruiting_closed()

    my_team_no = None
    can_apply = False
    if is_student:
        mine = teamflow.my_team(me["id"])
        pending = teamflow.my_pending_application(me["id"])
        my_team_no = mine["team_no"] if mine else None
        can_apply = (not closed) and mine is None and pending is None

        if mine is None:
            st.write("아직 팀이 없음")
        if closed:
            st.caption("모집이 마감되어 지원할 수 없음")
        elif mine is not None:
            st.caption("이미 팀이 있어 지원할 수 없음")
        elif pending is not None:
            st.caption(
                f"{pending['name']}에 지원한 결과를 기다리는 중이라 지원할 수 없음"
            )

    for team in teams:
        with st.container(key=f"team_{team['team_no']}", border=True):
            label = team["name"]
            if team["team_no"] == my_team_no:
                label += "  ← 내 팀"
            st.markdown(f"**{label}**  ({len(team['members'])}/{db.TEAM_MAX}명)")
            if not team["members"]:
                st.write("아직 팀원이 없음")
            for m in team["members"]:
                st.write(
                    describe_person(m["name"], m["main_role"], m["sub_roles"])
                )
            if is_student and can_apply:
                if st.button(
                    f"{team['name']}에 지원", key=f"apply_{team['team_no']}"
                ):
                    run_action(teamflow.apply_to_team, me, team["team_no"])


# ------------------------------------------------------------
# 투표 (key = vote_yes_{학번}, vote_no_{학번})
# ------------------------------------------------------------

def render_votes(me):
    team = teamflow.my_team(me["id"])
    if team is None:
        return

    st.markdown("### 내 팀에 들어온 지원")
    pendings = teamflow.pending_applications_for_team(team["id"])
    if not pendings:
        st.write("들어온 지원이 없음")
        return

    my_votes = teamflow.my_votes_on_team(me)
    closed = db.is_recruiting_closed()

    for app in pendings:
        st.write(
            describe_person(app["name"], app["main_role"], app["sub_roles"])
            + " 의 지원"
        )
        already = my_votes.get(app["student_no"])
        if already:
            # 한 번 투표하면 바꿀 수 없다 (공고 2.4절). 단추를 그리지 않는다.
            st.caption(f"내 투표: {already}")
        elif closed:
            st.caption("모집이 마감되어 투표할 수 없음")
        else:
            left, right, _ = st.columns([1, 1, 6])
            with left:
                if st.button("찬성", key=f"vote_yes_{app['student_no']}"):
                    run_action(teamflow.cast_vote, me, app["student_no"], True)
            with right:
                if st.button("거부", key=f"vote_no_{app['student_no']}"):
                    run_action(teamflow.cast_vote, me, app["student_no"], False)


# ------------------------------------------------------------
# 학생 화면
# ------------------------------------------------------------

def render_student(me):
    st.subheader("학생 화면")

    pending = teamflow.my_pending_application(me["id"])
    if pending is not None:
        st.info(f"내 지원: {pending['name']} (대기)")

    render_teams(me)
    render_votes(me)


# ------------------------------------------------------------
# 관리자 화면 — 명단 올리기 / 마감과 배정 / 현황 (PRD 7.5절)
# ------------------------------------------------------------

def do_admin_start(me):
    uploaded = st.session_state.get("admin_roster_file")
    if uploaded is None:
        raise roster.RosterError("명단 CSV 파일을 고르세요")
    students = roster.parse_csv(uploaded.getvalue())
    info = roster.apply_roster(me, students, st.session_state.get("admin_team_count"))
    return f"승인: 명단 {info['students']}명, 팀 {info['teams']}개를 만듦"


def render_admin(me):
    # 화면을 숨기는 것과 별개로, 여기서 권한을 한 번 더 검사한다.
    auth.require_admin(me)

    st.subheader("관리자 화면")

    # ---- 1. 명단 올리기 (과제 공고 2.6절) ----
    st.markdown("### 1. 명단 올리기")
    st.file_uploader("명단 CSV", type=["csv"], key="admin_roster_file")
    st.number_input(
        "팀 수",
        min_value=roster.MIN_TEAM_COUNT,
        max_value=roster.MAX_TEAM_COUNT,
        value=4,
        step=1,
        key="admin_team_count",
    )
    if st.button("명단으로 처음 상태 만들기", key="admin_start"):
        run_action(do_admin_start, me)

    # ---- 2. 마감과 배정 ----
    st.markdown("### 2. 마감과 배정")
    closed = db.is_recruiting_closed()
    if closed:
        st.caption(f"모집이 마감됨 ({db.get_setting('closed_at', '')})")
    if st.button("모집 마감", key="admin_close", disabled=closed):
        run_action(teamflow.close_recruiting, me)

    render_assign(me, closed)

    # ---- 3. 현황 ----
    st.markdown("### 3. 현황")
    render_teams(me)
    render_unassigned()
    render_pending_overview()
    render_ai_logs()


# ------------------------------------------------------------
# AI 배정 (key = admin_assign, assign_status, admin_confirm)
# ------------------------------------------------------------

def lines_to_text(lines):
    return "\n".join(f"- {line}" for line in lines)


def do_assign(me, slot):
    def on_progress(current):
        # 기다리는 동안에도 화면에 바로 보이게 한다.
        slot.markdown(lines_to_text(current))

    result = assign.run_assignment(me, on_progress=on_progress)
    st.session_state["assign_lines"] = result["lines"]
    st.session_state["assignment"] = result.get("assignment") or None
    st.session_state["assignment_by"] = result.get("by")
    return result["message"]


def do_confirm(me):
    assignment = st.session_state.get("assignment")
    message = assign.confirm_assignment(me, assignment or {})
    if message.startswith("승인"):
        st.session_state["assignment"] = None
        st.session_state["assign_lines"] = list(
            st.session_state.get("assign_lines", [])
        ) + ["배정안을 확정해 DB에 저장함"]
    return message


def render_assign(me, closed):
    """AI 배정. 진행 안내는 줄을 지우지 않고 쌓아서 보여 준다."""
    # 배정 진행 안내 (key = assign_status)
    box = st.container(key="assign_status", border=True)
    slot = box.empty()
    lines = st.session_state.get("assign_lines", [])
    slot.markdown(lines_to_text(lines) if lines else "아직 배정하지 않음")

    if st.button("AI 배정", key="admin_assign", disabled=not closed):
        run_action(do_assign, me, slot)

    if not closed:
        st.caption("먼저 모집을 마감해야 배정할 수 있음")

    assignment = st.session_state.get("assignment")
    if assignment:
        st.markdown("#### 배정안 (검사 5가지를 모두 통과함)")
        st.caption(f"만든 곳: {st.session_state.get('assignment_by', '-')}")
        st.dataframe(assign.assignment_table(assignment), hide_index=True)
        if st.button("배정안 확정", key="admin_confirm"):
            run_action(do_confirm, me)


def render_ai_logs():
    """AI 호출 기록 표. 시각은 한국 시각으로 보여 준다."""
    rows = ai.recent_logs(30)
    st.markdown("#### AI 호출 기록")
    if not rows:
        st.write("아직 없음")
        return
    st.dataframe(
        [
            {
                "시각 (한국 시각)": kst_text(r["called_at"]),
                "차수": attempt_text(r["attempt_no"]),
                "모델 이름": r["model"] or "-",
                "결과": r["outcome"],
                "실패 이유": r["fail_reason"] or "-",
                "걸린 시간": (
                    f"{r['elapsed_ms'] / 1000:.1f}초" if r["elapsed_ms"] else "-"
                ),
            }
            for r in rows
        ],
        hide_index=True,
    )


def kst_text(value):
    """DB에는 UTC로 저장하고, 보여 줄 때만 한국 시각으로 바꾼다."""
    if value is None:
        return "-"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")


def attempt_text(attempt_no):
    if attempt_no is None:
        return "-"
    if attempt_no >= 4:
        return "앱 자체 배정"
    return f"{attempt_no}차"


def render_unassigned():
    """팀이 없는 학생 (key = unassigned).

    마감 때 대기 지원이 거절되고 1명뿐인 팀의 학생이 빠진 결과를
    채점 프로그램이 이것으로 확인한다. 이름이 보여야 한다 (공고 2.7절).
    """
    students = teamflow.unassigned_students()
    st.markdown("#### 팀이 없는 학생")
    with st.container(key="unassigned", border=True):
        if not students:
            st.write("없음")
        else:
            for s in students:
                st.write(describe_person(s["name"], s["main_role"], s["sub_roles"]))


def render_pending_overview():
    """대기 중인 지원 목록 (관리자 현황)."""
    rows = db.query_all(
        """
        select t.name as team_name, s.name as student_name, s.main_role,
               a.created_at
          from tf_applications a
          join tf_teams t on t.id = a.team_id
          join tf_students s on s.id = a.student_id
         where a.status = %s
         order by t.team_no, a.created_at
        """,
        (teamflow.STATUS_PENDING,),
    )
    st.markdown("#### 대기 중인 지원")
    if not rows:
        st.write("대기 중인 지원이 없음")
        return
    st.dataframe(
        [
            {
                "팀": r["team_name"],
                "지원자": r["student_name"],
                "주 역할": r["main_role"],
            }
            for r in rows
        ],
        hide_index=True,
    )


# ------------------------------------------------------------
# 화면 그리기
# ------------------------------------------------------------

def main():
    startup()

    st.title("팀 편성")

    render_whoami()
    render_result()
    st.divider()

    me = current_user()
    if me is None:
        render_login()
        return

    render_logout()
    if me["is_admin"]:
        render_admin(me)
    else:
        render_student(me)


main()
