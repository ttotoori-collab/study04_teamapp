"""Supabase 프로젝트가 일시정지되지 않게 매일 DB에 질의를 보낸다.

과제 공고 4.4절: Supabase 무료 프로젝트는 1주일 동안 DB 요청이 적으면
일시정지된다. 채점 기간(11/3~11/4)에 일시정지되어 있으면 시나리오 점수가
0점이다(6.3절 16번). 프로젝트 주인만 다시 켤 수 있어서 채점자가 손쓸 수 없다.

이 파일은 3장 "DB(Supabase)가 일시정지되지 않게"의 어려운 해결
(외부 예약 서비스로 매일 DB에 질의를 보냄)이다.

DB 주소는 깃허브 저장소의 Secrets 에서 환경변수로 받는다.
어떤 경우에도 주소를 출력하지 않는다 (공고 6.3절 8번, -3점).
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import psycopg

KST = timezone(timedelta(hours=9))


def main():
    url = os.environ.get("DATABASE_URL", "").strip()
    # 혹시 따옴표째로 들어갔으면 떼어 낸다.
    if url[:1] in ("\"", "'"):
        url = url[1:]
    if url[-1:] in ("\"", "'"):
        url = url[:-1]

    if not url:
        print("DATABASE_URL 비밀값이 비어 있습니다.")
        print("저장소 Settings > Secrets and variables > Actions 에서 넣으세요.")
        return 1

    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with psycopg.connect(url, connect_timeout=30) as conn:
            with conn.cursor() as cur:
                cur.execute("select count(*) from tf_students")
                students = cur.fetchone()[0]
                cur.execute("select count(*) from tf_teams")
                teams = cur.fetchone()[0]
    except psycopg.Error as e:
        # 예외 메시지에 주소나 사용자 이름이 들어갈 수 있어 종류만 적는다.
        print(f"{now} (KST) DB 질의 실패: {type(e).__name__}")
        return 1

    print(f"{now} (KST) DB 질의 성공 - tf_students {students}행, tf_teams {teams}행")
    return 0


if __name__ == "__main__":
    sys.exit(main())
