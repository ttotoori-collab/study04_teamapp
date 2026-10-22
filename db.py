"""DB 연결과 기본 질의.

중요한 두 가지:

1. 연결은 한 번만 만들고 다시 쓴다 (과제 공고 4.2절).
   질의마다 연결을 새로 만들면 화면 한 번 그리는 데 몇 초가 더 걸린다.

2. DATABASE_URL 같은 비밀 값은 화면이나 로그에 절대 출력하지 않는다
   (과제 공고 6.3절 8번, -3점). 이 파일은 비밀 값을 읽기만 하고
   어디에도 다시 내보내지 않는다.
"""

import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

try:
    import streamlit as st
except ModuleNotFoundError:  # 터미널에서 쓸 때
    st = None


# 학생이 가질 수 있는 역할 6개 (과제 공고 2.2절)
ROLES = (
    "기획자",
    "웹 프로그래머",
    "프론트엔드 디자이너",
    "테스터",
    "보고서 작성자",
    "발표자",
)

# 필수 포함 역할 (과제 공고 2.3절). 서로 다른 사람이 1명씩 맡아야 한다.
REQUIRED_ROLES = ("기획자", "웹 프로그래머")

TEAM_MAX = 4
TEAM_MIN = 2


# ------------------------------------------------------------
# 비밀 값 읽기
# ------------------------------------------------------------

def _from_env_file(name):
    """.env 에서 한 값을 읽는다. 값은 돌려주기만 하고 출력하지 않는다."""
    path = Path(__file__).with_name(".env")
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() != name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        return value or None
    return None


def secret(name):
    """비밀 값을 읽는다. 배포(Streamlit Secrets) -> 환경변수 -> .env 순서."""
    if st is not None:
        try:
            value = st.secrets[name]
        except Exception:
            value = None
        if value:
            return str(value)

    value = os.environ.get(name)
    if value:
        return value

    return _from_env_file(name)


def has_secret(name):
    """값이 있는지만 알려 준다. 값 자체는 돌려주지 않는다."""
    return bool(secret(name))


# ------------------------------------------------------------
# 연결 (한 번만 만들고 다시 쓴다)
# ------------------------------------------------------------

def _new_connection():
    url = secret("DATABASE_URL")
    if not url:
        # 메시지에 값을 넣지 않는다.
        raise RuntimeError(
            "DATABASE_URL 이 없습니다. 로컬에서는 .env, 배포에서는 "
            "Streamlit Secrets 를 확인하세요."
        )
    return psycopg.connect(
        url,
        autocommit=True,
        connect_timeout=20,
        row_factory=dict_row,
    )


if st is not None:
    @st.cache_resource(show_spinner=False)
    def _get_cached_connection():
        return _new_connection()

    def _drop_cached_connection():
        _get_cached_connection.clear()

else:
    _holder = {}

    def _get_cached_connection():
        conn = _holder.get("conn")
        if conn is None or conn.closed:
            conn = _new_connection()
            _holder["conn"] = conn
        return conn

    def _drop_cached_connection():
        conn = _holder.pop("conn", None)
        if conn is not None and not conn.closed:
            try:
                conn.close()
            except psycopg.Error:
                pass


def get_conn():
    """다시 쓰는 연결을 돌려준다."""
    conn = _get_cached_connection()
    if conn.closed:
        _drop_cached_connection()
        conn = _get_cached_connection()
    return conn


def _run(sql, params, mode):
    """질의를 실행한다. 연결이 끊겨 있으면 한 번만 다시 연결해 재시도한다."""
    last_error = None
    for attempt in (1, 2):
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                if mode == "all":
                    return cur.fetchall()
                if mode == "one":
                    return cur.fetchone()
                return cur.rowcount
        except psycopg.OperationalError as e:
            # 연결이 끊긴 경우에만 재시도한다. 제약 위반은 그대로 올려 보낸다.
            last_error = e
            _drop_cached_connection()
            if attempt == 2:
                raise
    raise last_error


def query_all(sql, params=None):
    """여러 행을 읽는다. 각 행은 dict 이다."""
    return _run(sql, params, "all")


def query_one(sql, params=None):
    """한 행을 읽는다. 없으면 None."""
    return _run(sql, params, "one")


def execute(sql, params=None):
    """행을 바꾼다. 바뀐 행 수를 돌려준다."""
    return _run(sql, params, "none")


def transaction():
    """여러 질의를 한 묶음으로 처리할 때 쓴다.

    쓰는 법:
        with db.transaction() as tx:
            tx.execute(...)
    """
    return get_conn().transaction()


def ping():
    """연결이 살아 있는지 확인한다. 성공하면 True."""
    try:
        query_one("select 1 as ok")
        return True
    except psycopg.Error:
        return False


# ------------------------------------------------------------
# tf_settings (마감 여부 등)
# ------------------------------------------------------------

def get_setting(key, default=None):
    row = query_one("select value from tf_settings where key = %s", (key,))
    return default if row is None else row["value"]


def set_setting(key, value):
    execute(
        """
        insert into tf_settings (key, value) values (%s, %s)
        on conflict (key) do update set value = excluded.value
        """,
        (key, None if value is None else str(value)),
    )


def delete_setting(key):
    execute("delete from tf_settings where key = %s", (key,))


def is_recruiting_closed():
    """모집이 마감되었는지."""
    return get_setting("closed") == "true"


# ------------------------------------------------------------
# 초기화 (명단 올리기에서 쓴다 - 과제 공고 2.6절)
# ------------------------------------------------------------

def reset_all():
    """지원, 투표, 팀원, 팀, 학생, 마감 표시를 모두 지운다.

    관리자 계정(is_admin = true)은 남긴다.
    tf_ai_logs 는 README 근거로 쓰므로 지우지 않는다.
    """
    with transaction():
        execute("delete from tf_votes")
        execute("delete from tf_applications")
        execute("delete from tf_members")
        execute("delete from tf_student_sub_roles")
        execute("delete from tf_students where is_admin = false")
        execute("delete from tf_teams")
        execute("delete from tf_settings where key in ('closed', 'closed_at', 'team_count')")
