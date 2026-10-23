"""DB 연결과 기본 질의.

중요한 두 가지:

1. 연결은 한 번만 만들고 다시 쓴다 (과제 공고 4.2절).
   질의마다 연결을 새로 만들면 화면 한 번 그리는 데 몇 초가 더 걸린다.

2. DATABASE_URL 같은 비밀 값은 화면이나 로그에 절대 출력하지 않는다
   (과제 공고 6.3절 8번, -3점). 이 파일은 비밀 값을 읽기만 하고
   어디에도 다시 내보내지 않는다.
"""

import contextlib
import os
import threading
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

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
        # 감싼 따옴표를 떼어 낸다.
        # 한쪽 따옴표만 있어도(예: 닫는 따옴표를 빠뜨린 경우) 떼어 낸다.
        # 키·주소·비밀번호에 따옴표가 들어가는 경우는 없으므로 안전하다.
        if value[:1] in ("\"", "'"):
            value = value[1:]
        if value[-1:] in ("\"", "'"):
            value = value[:-1]
        return value.strip() or None
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

# 연결은 한 번만 만들어 두고 다시 쓴다 (과제 공고 4.2절).
#
# 다만 연결 "하나"를 모든 사용자가 함께 쓰면 안 된다. 앱 화면이 5초마다
# 저절로 갱신되므로, 학생 16명이 동시에 접속하면 1초에 수십 번 질의가 몰린다.
# 연결 하나로 그걸 받으면 질의가 줄을 서느라 화면이 몇 초씩 늦어지고,
# 한 사람의 실패가 다른 사람의 묶음 처리까지 깨뜨린다.
#
# 그래서 연결 몇 개를 미리 만들어 두고 돌려 쓰는 "연결 풀" 을 쓴다.
# 질의마다 새로 연결하는 것이 아니므로 공고가 경고한 느려짐은 생기지 않는다.
POOL_MIN = 2
POOL_MAX = 10

# 한 묶음 처리(transaction) 안에서는 같은 연결을 계속 써야 한다.
# 지금 이 작업 흐름이 빌려 쓰는 연결을 여기에 둔다.
_local = threading.local()


def _make_pool():
    url = secret("DATABASE_URL")
    if not url:
        # 메시지에 값을 넣지 않는다.
        raise RuntimeError(
            "DATABASE_URL 이 없습니다. 로컬에서는 .env, 배포에서는 "
            "Streamlit Secrets 를 확인하세요."
        )
    return ConnectionPool(
        url,
        min_size=POOL_MIN,
        max_size=POOL_MAX,
        timeout=30,
        max_lifetime=1800,
        # autocommit 으로 둔다. 질의 하나마다 묶음을 열고 닫으면
        # 왕복이 세 배가 되어 화면이 느려진다.
        # 여러 질의를 한 묶음으로 처리할 때는 db.transaction() 이 직접 연다.
        kwargs={
            "row_factory": dict_row,
            "connect_timeout": 20,
            "autocommit": True,
        },
        open=True,
    )


if st is not None:
    @st.cache_resource(show_spinner=False)
    def _get_pool():
        return _make_pool()

    def _drop_pool():
        _get_pool.clear()

else:
    _holder = {}

    def _get_pool():
        pool = _holder.get("pool")
        if pool is None:
            pool = _make_pool()
            _holder["pool"] = pool
        return pool

    def _drop_pool():
        pool = _holder.pop("pool", None)
        if pool is not None:
            try:
                pool.close()
            except Exception:
                pass


@contextlib.contextmanager
def borrow():
    """연결 하나를 빌린다.

    이미 묶음 처리 안이면 그 연결을 그대로 쓴다. 그래야 묶음이 쪼개지지 않는다.
    """
    existing = getattr(_local, "conn", None)
    if existing is not None:
        yield existing
        return
    with _get_pool().connection() as conn:
        _local.conn = conn
        try:
            yield conn
        finally:
            _local.conn = None


def get_conn():
    """지금 빌려 쓰는 연결. 묶음 처리 안에서만 뜻이 있다."""
    existing = getattr(_local, "conn", None)
    if existing is not None:
        return existing
    raise RuntimeError("db.borrow() 또는 db.transaction() 안에서만 쓸 수 있습니다")


def _run(sql, params, mode):
    """질의를 실행한다. 연결이 끊겨 있으면 한 번만 다시 빌려 재시도한다."""
    in_transaction = getattr(_local, "conn", None) is not None
    last_error = None
    for attempt in (1, 2):
        try:
            with borrow() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    if mode == "all":
                        return cur.fetchall()
                    if mode == "one":
                        return cur.fetchone()
                    return cur.rowcount
        except psycopg.OperationalError as e:
            # 연결이 끊긴 경우에만 재시도한다. 제약 위반은 그대로 올려 보낸다.
            # 묶음 처리 안이라면 되돌리지 않고 그대로 올려 보낸다.
            last_error = e
            if in_transaction or attempt == 2:
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


def execute_many(sql, params_seq):
    """같은 질의를 여러 값으로 한 번에 보낸다.

    명단 올리기처럼 행이 많을 때 쓴다. 한 줄씩 보내면 왕복이 많아 느리다.
    """
    params_seq = list(params_seq)
    if not params_seq:
        return 0
    with borrow() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, params_seq)
            return cur.rowcount


@contextlib.contextmanager
def transaction():
    """여러 질의를 한 묶음으로 처리할 때 쓴다.

    묶음이 끝날 때까지 같은 연결을 쓴다. 다른 사용자는 다른 연결을 쓰므로
    서로 끼어들지 않는다.

    쓰는 법:
        with db.transaction():
            db.execute(...)
    """
    with borrow() as conn:
        with conn.transaction() as tx:
            yield tx


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
