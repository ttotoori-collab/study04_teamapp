"""규칙 판정 함수 검사 (구현 계획 4-1단계).

DB도 브라우저도 쓰지 않는다. 그래서 언제든 빠르게 돌릴 수 있다.

쓰는 법:
    python tests/check_rules.py

!! 이 폴더(tests/)의 파일은 앱이 절대 읽지 않는다 (감점 19번). !!
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rules  # noqa: E402

ok = 0
bad = 0


def check(label, passed, detail=""):
    global ok, bad
    if passed:
        ok += 1
        print(f"  [통과] {label}" + (f"  ({detail})" if detail else ""))
    else:
        bad += 1
        print(f"  [실패] {label}" + (f"  ({detail})" if detail else ""))


PL = "기획자"
WP = "웹 프로그래머"
FD = "프론트엔드 디자이너"
TT = "테스터"
RW = "보고서 작성자"
PT = "발표자"


def p(name, main, *subs):
    return {"name": name, "main_role": main, "sub_roles": list(subs)}


print("=" * 70)
print("4-1 규칙 판정 함수 확인")
print("=" * 70)

# ---------- can_take ----------
print("\n[역할을 맡을 수 있는지]")
s = p("테스터겸기획", TT, PL)
check("주 역할로 맡음", rules.can_take(p("기획", PL), PL))
check("보조 역할로 맡음", rules.can_take(s, PL))
check("못 맡는 역할", not rules.can_take(s, WP))

# ---------- 필수 포함 역할 세기 ----------
print("\n[필수 포함 역할 세기]")
check("빈 팀은 2개 모자람", rules.unmet_required([]) == 2)
check(
    "기획자 1명만 있으면 1개 모자람",
    rules.unmet_required([p("가", PL)]) == 1,
)
check(
    "기획자+웹프로그래머면 0개 모자람",
    rules.unmet_required([p("가", PL), p("나", WP)]) == 0,
)
# 공고 6장 예시: 주 역할 기획자, 보조 웹 프로그래머인 학생 혼자면 1개만 채운 것
check(
    "한 사람이 둘 다 할 수 있어도 1자리만 셈",
    rules.unmet_required([p("둘다", PL, WP)]) == 1,
    f"모자람 {rules.unmet_required([p('둘다', PL, WP)])}개",
)
check(
    "보조 역할로도 필수 역할을 채움",
    rules.unmet_required([p("가", TT, PL), p("나", RW, WP)]) == 0,
)
check(
    "테스터와 발표자만 있으면 2개 모자람",
    rules.unmet_required([p("가", TT), p("나", PT)]) == 2,
)

# ---------- 주 역할 중복 ----------
print("\n[주 역할 중복]")
check(
    "주 역할이 같으면 중복",
    rules.main_role_duplicated([p("가", PL)], p("나", PL)),
)
check(
    "주 역할이 다르면 중복 아님",
    not rules.main_role_duplicated([p("가", PL)], p("나", WP)),
)
check(
    "보조 역할은 중복 판정에 쓰지 않음",
    not rules.main_role_duplicated([p("가", TT, PL)], p("나", PL)),
    "팀원 보조가 기획자, 지원자 주 역할이 기획자 -> 중복 아님",
)

# ---------- 필수 포함 역할 자리 ----------
print("\n[필수 포함 역할 자리 - 어려운 해결 7점]")
# 공고 6장 예시: 테스터와 발표자만 있는 팀(2명)에 보고서 작성자가 지원 -> 막음
check(
    "테스터·발표자 2명 팀에 보고서 작성자 지원 -> 모자람",
    rules.required_slot_short([p("가", TT), p("나", PT)], p("다", RW)),
    "남는 자리 1 < 모자란 필수 역할 2",
)
# 부록 3, 11단계: 팀3 = 학생11(TT;PT), 학생15(PT;FD), 지원자 학생14(RW;TT)
team3 = [p("학생11", TT, PT), p("학생15", PT, FD)]
check(
    "부록3 11단계: 학생14 지원 -> 모자람",
    rules.required_slot_short(team3, p("학생14", RW, TT)),
)
# 같은 팀에 웹 프로그래머가 지원하면? 남는 자리 1, 모자란 필수 1(기획자) -> 통과
check(
    "같은 팀에 웹 프로그래머 지원 -> 괜찮음",
    not rules.required_slot_short(team3, p("누구", WP)),
)
# 부록 3, 6단계: 팀2 = 학생2(PL;PT), 학생8(FD;TT)
team2 = [p("학생2", PL, PT), p("학생8", FD, TT)]
check(
    "부록3 6단계: 학생5(WP;FD) 지원 -> 괜찮음",
    not rules.required_slot_short(team2, p("학생5", WP, FD)),
)
check(
    "부록3 6단계: 학생6(WP;RW) 지원 -> 괜찮음",
    not rules.required_slot_short(team2, p("학생6", WP, RW)),
)
# 3명 팀(필수 2개 다 채움)에 네 번째가 지원 -> 남는 자리 0, 모자람 0 -> 통과
full3 = [p("가", PL), p("나", WP), p("다", FD)]
check(
    "필수 역할 다 찬 3명 팀에 4번째 지원 -> 괜찮음",
    not rules.required_slot_short(full3, p("라", TT)),
)

# ---------- check_apply ----------
print("\n[지원 가능 판정 - 막히는 이유 순서]")
applicant = p("지원자", TT)
check(
    "마감이면 '모집이 마감됨'",
    rules.check_apply(applicant, [], True, False, False) == rules.CLOSED,
)
check(
    "이미 팀이 있으면 '이미 팀이 있음'",
    rules.check_apply(applicant, [], False, True, False) == rules.ALREADY_IN_TEAM,
)
check(
    "대기 지원이 있으면 '결과를 기다리는 지원이 있음'",
    rules.check_apply(applicant, [], False, False, True) == rules.HAS_PENDING,
)
check(
    "4명이면 '팀이 꽉 참'",
    rules.check_apply(
        applicant, [p("1", PL), p("2", WP), p("3", FD), p("4", RW)], False, False, False
    )
    == rules.TEAM_FULL,
)
# 부록 2, 3단계: 팀1 = 학생1(PL;PT;TT), 학생5(WP;TT) / 지원자 학생2(PL;TT)
check(
    "부록2 3단계: 주 역할 중복으로 막힘",
    rules.check_apply(
        p("학생2", PL, TT),
        [p("학생1", PL, PT, TT), p("학생5", WP, TT)],
        False,
        False,
        False,
    )
    == rules.SAME_MAIN_ROLE,
)
check(
    "부록3 11단계: 필수 포함 역할 자리로 막힘",
    rules.check_apply(p("학생14", RW, TT), team3, False, False, False)
    == rules.REQUIRED_SLOT_SHORT,
)
check(
    "빈 팀에 지원 -> 막히지 않음",
    rules.check_apply(applicant, [], False, False, False) is None,
)
# 부록 3, 13단계: 팀1 3명에 학생13 지원 -> 괜찮음(대기)
team1_hard = [p("학생1", PL, RW), p("학생4", WP, PL), p("학생7", FD, PT, TT)]
check(
    "부록3 13단계: 학생13 지원 -> 막히지 않음",
    rules.check_apply(p("학생13", RW, PT, FD), team1_hard, False, False, False) is None,
)

# ---------- check_approve (승인 직전 재검사) ----------
print("\n[승인 직전 재검사 - 어려운 해결 3점]")
# 부록 3, 7단계 뒤 팀2 = 학생2(PL;PT), 학생8(FD;TT), 학생5(WP;FD)
team2_after = team2 + [p("학생5", WP, FD)]
check(
    "부록3 8단계: 학생6(WP;RW)은 주 역할 중복으로 거절",
    rules.check_approve(p("학생6", WP, RW), team2_after) == rules.SAME_MAIN_ROLE,
)
check(
    "부록3 7단계: 학생5는 승인 가능",
    rules.check_approve(p("학생5", WP, FD), team2) is None,
)
check(
    "4명 찬 팀이면 승인 직전에 '팀이 꽉 참'",
    rules.check_approve(
        p("늦은지원자", TT), [p("1", PL), p("2", WP), p("3", FD), p("4", RW)]
    )
    == rules.TEAM_FULL,
)

# ---------- check_team_final ----------
print("\n[확정된 팀 검사]")
check(
    "정상 팀은 문제 없음",
    rules.check_team_final([p("가", PL), p("나", WP)]) == [],
)
check(
    "1명이면 인원 문제",
    any("인원" in x for x in rules.check_team_final([p("가", PL)])),
    str(rules.check_team_final([p("가", PL)])),
)
check(
    "주 역할 중복이면 문제",
    any("주 역할" in x for x in rules.check_team_final([p("가", PL), p("나", PL)])),
    str(rules.check_team_final([p("가", PL), p("나", PL)])),
)
check(
    "웹 프로그래머가 없으면 문제",
    any("웹 프로그래머" in x for x in rules.check_team_final([p("가", PL), p("나", TT)])),
    str(rules.check_team_final([p("가", PL), p("나", TT)])),
)
check(
    "한 사람이 둘 다 할 수 있어도 2명이면 문제 없음(다른 사람이 채움)",
    rules.check_team_final([p("둘다", PL, WP), p("나", WP)]) == [],
    str(rules.check_team_final([p("둘다", PL, WP), p("나", WP)])),
)
check(
    "보조 역할로 필수 역할을 채운 팀은 문제 없음",
    rules.check_team_final([p("가", TT, PL), p("나", RW, WP)]) == [],
)

print("=" * 70)
print(f"결과: 통과 {ok}개 / 실패 {bad}개")
print("=" * 70)
sys.exit(1 if bad else 0)
