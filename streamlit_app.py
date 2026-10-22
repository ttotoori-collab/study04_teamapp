"""팀 편성 웹 앱.

화면 요소의 key 는 과제 공고 2.7절 표를 그대로 따른다.
key 를 바꾸면 자동 채점 프로그램이 요소를 찾지 못해 -5점이다 (6.3절 15번).

이 앱은 API 키나 DB 주소를 화면에 출력하지 않는다 (6.3절 8번, -3점).
"""

import traceback

import psycopg
import streamlit as st

import auth
import db
import roster

st.set_page_config(page_title="팀 편성", layout="wide")


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
# 로그인한 사람 (key = whoami)
# ------------------------------------------------------------

def current_user():
    return st.session_state.get("me")


def render_whoami():
    me = current_user()
    with st.container(key="whoami"):
        if me is None:
            st.write("로그인하지 않음")
        elif me["is_admin"]:
            st.write(f"{me['name']} (관리자)")
        else:
            subs = auth.sub_roles_of(me["id"])
            sub_text = ", ".join(subs) if subs else "없음"
            st.write(f"{me['name']} — 주: {me['main_role']} / 보조: {sub_text}")


# ------------------------------------------------------------
# 로그인 화면
# ------------------------------------------------------------

def render_login():
    st.subheader("로그인")
    with st.form("login_form"):
        st.text_input("학번", key="login_student_no")
        st.text_input("비밀번호", type="password", key="login_password")
        submitted = st.form_submit_button("로그인", key="login_submit")

    if submitted:
        ok, me, message = auth.login(
            st.session_state.get("login_student_no"),
            st.session_state.get("login_password"),
        )
        set_result(message)
        if ok:
            st.session_state["me"] = me
        st.rerun()


def render_logout():
    if st.button("로그아웃", key="logout"):
        st.session_state.pop("me", None)
        set_result("승인: 로그아웃함")
        st.rerun()


# ------------------------------------------------------------
# 학생 화면
# ------------------------------------------------------------

def render_teams(me):
    """팀 목록. 팀 하나가 key = team_1, team_2, ... 인 container 다 (2.7절).

    팀원의 이름과 역할이 보여야 한다. 이름은 명단 CSV의 name 그대로 쓴다.
    """
    teams = roster.teams_with_members()
    if not teams:
        st.caption("아직 팀이 없음. 관리자가 명단을 올리면 팀이 만들어진다.")
        return

    my_team_no = None
    if me is not None and not me["is_admin"]:
        for team in teams:
            if any(m["student_id"] == me["id"] for m in team["members"]):
                my_team_no = team["team_no"]
                break

    if me is not None and not me["is_admin"] and my_team_no is None:
        st.write("아직 팀이 없음")

    for team in teams:
        with st.container(key=f"team_{team['team_no']}", border=True):
            label = team["name"]
            if team["team_no"] == my_team_no:
                label += "  ← 내 팀"
            st.markdown(f"**{label}**  ({len(team['members'])}/4명)")
            if not team["members"]:
                st.write("아직 팀원이 없음")
            for m in team["members"]:
                subs = ", ".join(m["sub_roles"]) if m["sub_roles"] else "없음"
                st.write(f"{m['name']} — 주: {m['main_role']} / 보조: {subs}")


def render_student(me):
    st.subheader("학생 화면")
    render_teams(me)
    st.caption("지원과 투표는 다음 단계에서 만든다.")


# ------------------------------------------------------------
# 관리자 화면 — 명단 올리기 / 마감과 배정 / 현황 (PRD 7.5절)
# ------------------------------------------------------------

def render_admin(me):
    # 화면을 숨기는 것과 별개로, 여기서 권한을 한 번 더 검사한다.
    auth.require_admin(me)

    st.subheader("관리자 화면")

    # ---- 1. 명단 올리기 (과제 공고 2.6절) ----
    st.markdown("### 1. 명단 올리기")
    st.file_uploader(
        "명단 CSV", type=["csv"], key="admin_roster_file"
    )
    st.number_input(
        "팀 수",
        min_value=roster.MIN_TEAM_COUNT,
        max_value=roster.MAX_TEAM_COUNT,
        value=4,
        step=1,
        key="admin_team_count",
    )
    if st.button("명단으로 처음 상태 만들기", key="admin_start"):
        handle_admin_start(me)

    # ---- 2. 마감과 배정 ----
    st.markdown("### 2. 마감과 배정")
    st.caption("다음 단계에서 만든다.")

    # ---- 3. 현황 ----
    st.markdown("### 3. 현황")
    render_teams(me)


def handle_admin_start(me):
    """명단 CSV와 팀 수로 처음 상태를 만든다."""
    try:
        uploaded = st.session_state.get("admin_roster_file")
        if uploaded is None:
            raise roster.RosterError("명단 CSV 파일을 고르세요")
        students = roster.parse_csv(uploaded.getvalue())
        info = roster.apply_roster(
            me, students, st.session_state.get("admin_team_count")
        )
        set_result(
            f"승인: 명단 {info['students']}명, 팀 {info['teams']}개를 만듦"
        )
    except (roster.RosterError, auth.NotAdminError, ValueError) as e:
        set_result(blocked_text(e))
    except psycopg.Error:
        # 예외 메시지에 DB 주소가 들어갈 수 있어 그대로 보여 주지 않는다 (6.3절 8번).
        set_result("막힘: DB에 저장하지 못함")
    except Exception:
        # 어떤 오류가 나도 화면이 죽지 않게 한다.
        # 화면이 죽으면 채점 프로그램이 그 자리에서 멈춘다.
        # 자세한 내용은 서버 기록에만 남기고 화면에는 짧게만 보여 준다.
        traceback.print_exc()
        set_result("막힘: 명단을 처리하지 못함")
    st.rerun()


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
