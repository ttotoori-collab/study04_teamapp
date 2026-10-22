-- ============================================================
-- 팀 편성 웹 앱 - DB 스키마
--
-- 과제 공고 5장(DB 최소 참고안)과 docs/PRD.md 9장을 따른다.
-- 모든 테이블 이름은 tf_ 로 시작한다 (6.3절 13번).
-- 6장 실습 테이블과 같은 Supabase 프로젝트를 쓰므로 tf_ 로 구분한다.
--
-- 이 파일은 여러 번 실행해도 안전하다 (create ... if not exists).
-- 기존 테이블을 지우지 않으므로 6장 실습 테이블에 영향이 없다.
--
-- 시각은 모두 timestamptz 로 저장한다 (UTC 기준으로 보관).
-- 화면에 보여 줄 때만 KST(UTC+9)로 변환한다.
-- ============================================================


-- ------------------------------------------------------------
-- 1. tf_students : 학생과 관리자
--
-- 관리자(student_no = 'admin')는 역할이 없으므로 main_role 이 null 이다.
-- 비밀번호는 반드시 해시로 저장한다 (6.3절 10번, -2점).
-- ------------------------------------------------------------
create table if not exists tf_students (
    id            bigserial   primary key,
    student_no    text        not null unique,
    name          text        not null,
    main_role     text,
    password_hash text        not null,
    is_admin      boolean     not null default false,
    created_at    timestamptz not null default now(),

    -- 6개 역할 밖의 값을 막는다. 관리자는 null 을 허용한다.
    constraint tf_students_main_role_check check (
        main_role is null or main_role in (
            '기획자', '웹 프로그래머', '프론트엔드 디자이너',
            '테스터', '보고서 작성자', '발표자'
        )
    ),

    -- 관리자가 아닌 학생은 주 역할이 반드시 있어야 한다.
    constraint tf_students_role_required_check check (
        is_admin = true or main_role is not null
    )
);


-- ------------------------------------------------------------
-- 2. tf_student_sub_roles : 보조 역할
--
-- 학생 1명에 0~2행. "0~2개"는 앱에서 검사한다
-- (행 수 제한은 SQL 제약으로 표현하기 어렵다).
-- 기본 키가 (student_id, role) 이므로 같은 보조 역할이 두 번 들어가지 않는다.
-- ------------------------------------------------------------
create table if not exists tf_student_sub_roles (
    student_id bigint not null references tf_students(id) on delete cascade,
    role       text   not null,

    primary key (student_id, role),

    constraint tf_student_sub_roles_role_check check (
        role in (
            '기획자', '웹 프로그래머', '프론트엔드 디자이너',
            '테스터', '보고서 작성자', '발표자'
        )
    )
);


-- ------------------------------------------------------------
-- 3. tf_teams : 팀
--
-- team_no 는 화면 key(team_1, team_2, ...)와 시나리오의 팀 번호에 맞춘다.
-- ------------------------------------------------------------
create table if not exists tf_teams (
    id      bigserial primary key,
    team_no integer   not null unique,
    name    text      not null unique,

    constraint tf_teams_team_no_check check (team_no >= 1)
);


-- ------------------------------------------------------------
-- 4. tf_members : 팀원
--
-- 이 테이블의 제약이 3장 "마지막 자리에 두 명이 동시에 들어감"의
-- 어려운 해결(DB 유일성 제약으로 먼저 도착한 학생만 기록)을 담당한다.
--
--   - (team_id, slot_no) 기본 키 : 같은 자리에 두 명이 못 들어간다
--   - student_id unique          : 한 학생은 한 팀에만 소속된다
--   - slot_no 1~4 검사           : 다섯 번째 자리를 막는다
-- ------------------------------------------------------------
create table if not exists tf_members (
    team_id    bigint      not null references tf_teams(id)    on delete cascade,
    slot_no    integer     not null,
    student_id bigint      not null unique
                                references tf_students(id) on delete cascade,
    joined_at  timestamptz not null default now(),

    primary key (team_id, slot_no),

    constraint tf_members_slot_no_check check (slot_no between 1 and 4)
);


-- ------------------------------------------------------------
-- 5. tf_applications : 지원
--
-- 막힌 지원은 접수하지 않으므로 이 테이블에 넣지 않는다 (2.4절).
-- resolved_at 은 승인 직전 재검사의 처리 순서(투표가 끝난 순서)에 쓴다.
-- ------------------------------------------------------------
create table if not exists tf_applications (
    id          bigserial   primary key,
    team_id     bigint      not null references tf_teams(id)    on delete cascade,
    student_id  bigint      not null references tf_students(id) on delete cascade,
    status      text        not null default '대기',
    reason      text,
    created_at  timestamptz not null default now(),
    resolved_at timestamptz,

    constraint tf_applications_status_check check (
        status in ('대기', '승인', '거절')
    )
);

-- 한 학생은 결과를 기다리는 지원을 하나만 가질 수 있다 (2.4절 "한 학생의 지원").
-- 부분 유일 인덱스로 DB에서도 막는다.
create unique index if not exists tf_applications_one_pending_per_student
    on tf_applications (student_id)
    where status = '대기';

-- 팀별 지원 조회를 빠르게 한다.
create index if not exists tf_applications_team_status_idx
    on tf_applications (team_id, status);


-- ------------------------------------------------------------
-- 6. tf_votes : 투표
--
-- (application_id, voter_id) 기본 키가 "한 번 투표하면 바꿀 수 없음"을
-- DB에서 지켜 준다 (2.4절).
-- ------------------------------------------------------------
create table if not exists tf_votes (
    application_id bigint      not null references tf_applications(id) on delete cascade,
    voter_id       bigint      not null references tf_students(id)     on delete cascade,
    vote           text        not null,
    voted_at       timestamptz not null default now(),

    primary key (application_id, voter_id),

    constraint tf_votes_vote_check check (vote in ('찬성', '거부'))
);


-- ------------------------------------------------------------
-- 7. tf_settings : 앱 설정
--
-- 쓰는 키:
--   'closed'       -> 'true' 면 모집 마감됨
--   'closed_at'    -> 마감한 시각
--   'team_count'   -> 명단을 올릴 때 넣은 팀 수
-- ------------------------------------------------------------
create table if not exists tf_settings (
    key   text primary key,
    value text
);


-- ------------------------------------------------------------
-- 8. tf_ai_logs : AI 호출 기록
--
-- AI 호출이 실패할 때마다 한 행을 남긴다. 성공한 호출도 남긴다.
-- README 7.1절 5항(AI 호출 실패 사례)의 근거로 쓴다.
--
-- attempt_no : 1, 2, 3 = 몇 차 모델인지 / 4 = 3차까지 실패해 앱이 스스로 배정
--
-- 주의: 이 테이블에 API 키를 넣지 않는다 (6.3절 8번, -3점).
-- ------------------------------------------------------------
create table if not exists tf_ai_logs (
    id               bigserial   primary key,
    called_at        timestamptz not null default now(),
    attempt_no       integer     not null,
    model            text,
    outcome          text        not null,
    fail_reason      text,
    elapsed_ms       integer,
    response_excerpt text,

    constraint tf_ai_logs_attempt_no_check check (attempt_no between 1 and 4),

    constraint tf_ai_logs_outcome_check check (
        outcome in ('성공', '시간초과', '빈 응답', '오류', '검사 실패')
    )
);

create index if not exists tf_ai_logs_called_at_idx
    on tf_ai_logs (called_at desc);
