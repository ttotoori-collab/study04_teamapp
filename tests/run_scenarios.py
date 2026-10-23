"""연습 시나리오 자동 테스트 (구현 계획 9단계).

부록의 연습 시나리오를 처음부터 끝까지 자동으로 돌려서, 단계마다
"기대 결과 / 실제 결과 / 맞음·틀림" 을 표로 보여 준다.

채점 프로그램과 **같은 방식**으로 동작한다.
- 화면 요소를 공고 2.7절의 Streamlit key 로만 찾는다
- 학생마다 브라우저 창(컨텍스트)을 따로 쓴다
- 단추가 없거나 비활성이면 "막힘" 으로 본다

!! 중요 (감점 19번, 그 시나리오 0점) !!
이 폴더(tests/)의 파일은 **앱이 절대 읽지 않는다.** 기대 결과를 앱에 알려
주지 않는다. 앱은 규칙만 보고 판단해야 한다.

쓰는 법:
    python tests/run_scenarios.py --all
    python tests/run_scenarios.py --scenario 어려움
    python tests/run_scenarios.py --all --skip-ai        # AI 호출 아끼기
    python tests/run_scenarios.py --all --out docs/테스트결과.md
"""

import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

PROJECT = Path(__file__).resolve().parent.parent
SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"

STUDENT_PASSWORD = "1234"
ADMIN_STUDENT_NO = "admin"

# 채점 프로그램은 단추가 나타나기를 7초까지 기다린다 (공고 2.7절).
WAIT_FOR_BUTTON_MS = 8000

RESULT = ".st-key-result"
WHOAMI = ".st-key-whoami"
STATUS_WIDGET = '[data-testid="stStatusWidget"]'


# ------------------------------------------------------------
# 비밀 값 읽기 (값은 절대 출력하지 않는다)
# ------------------------------------------------------------

def read_env_value(name):
    path = PROJECT / ".env"
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
        if value[:1] in ("\"", "'"):
            value = value[1:]
        if value[-1:] in ("\"", "'"):
            value = value[:-1]
        return value.strip() or None
    return None


# ------------------------------------------------------------
# 화면 다루기
# ------------------------------------------------------------

def wait_idle(page, timeout_ms=45000):
    """앱이 다시 그리기를 끝낼 때까지 기다린다."""
    try:
        page.wait_for_selector(STATUS_WIDGET, timeout=2500)
    except PlaywrightTimeout:
        pass
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if page.query_selector(STATUS_WIDGET) is None:
            break
        page.wait_for_timeout(300)
    page.wait_for_timeout(500)


def first_word(text):
    text = (text or "").strip()
    if not text:
        return "(빈 안내)"
    return text.split(":")[0].strip().split()[0] if text.split(":")[0].strip() else "(빈 안내)"


def read_result(page):
    element = page.query_selector(RESULT)
    return element.inner_text().strip() if element else ""


def login(context, app_url, student_no, password):
    page = context.new_page()
    page.goto(app_url, wait_until="domcontentloaded")
    page.wait_for_selector(".st-key-login_submit button", timeout=90000)
    page.fill(".st-key-login_student_no input", student_no)
    page.fill(".st-key-login_password input", password)
    page.click(".st-key-login_submit button")
    wait_idle(page)
    page.wait_for_selector(WHOAMI, timeout=30000)
    text = read_result(page)
    if not text.startswith("승인"):
        raise RuntimeError(f"로그인 실패({student_no}): {text}")
    return page


def click_if_possible(page, selector):
    """단추가 있으면 누르고 True, 없거나 비활성이면 False.

    채점 프로그램처럼 단추가 나타나기를 잠깐 기다린다.

    앱이 5초마다 저절로 다시 그리기 때문에, 찾아 둔 요소가 누르는 사이에
    사라질 수 있다. 그래서 요소를 미리 붙잡지 않고 로케이터로 그때그때
    다시 찾게 한다. 한 번 실패하면 한 번 더 시도한다.
    """
    locator = page.locator(selector).first
    try:
        locator.wait_for(state="visible", timeout=WAIT_FOR_BUTTON_MS)
    except PlaywrightTimeout:
        return False
    # 앱이 다시 그리는 중에 누르면 눌림이 사라질 수 있다.
    # 조용해질 때까지 기다린 뒤 누른다.
    wait_quiet(page)
    for attempt in (1, 2):
        try:
            if not locator.is_enabled(timeout=3000):
                return False
            locator.click(timeout=WAIT_FOR_BUTTON_MS)
            return True
        except Exception:
            if attempt == 2:
                return False
            page.wait_for_timeout(1500)
    return False


def wait_quiet(page, timeout_ms=20000):
    """앱이 다시 그리고 있지 않은 순간을 기다린다."""
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if page.query_selector(STATUS_WIDGET) is None:
            return True
        page.wait_for_timeout(200)
    return False


def click_and_read(page, selector, idle_ms=45000, gone_selector=None, retry=True):
    """단추를 누르고 결과 안내를 읽는다.

    앱이 5초마다 저절로 다시 그리기 때문에, 눌림이 사라지는 일이 있다.
    그래서 "안내가 바뀌었는지" 또는 "단추가 사라졌는지" 를 눌림의 증거로 보고,
    증거가 없으면 한 번만 다시 누른다.

    돌려주는 값: (첫 단어, 안내 전체). 단추가 아예 없으면 (None, None).
    """
    before = read_result(page)
    if not click_if_possible(page, selector):
        return None, None

    for attempt in (1, 2):
        # AI 배정처럼 오래 걸리는 동작은 idle_ms 만큼 넉넉히 기다린다.
        budget = max(idle_ms / 1000, 25) if attempt == 1 else 20
        deadline = time.monotonic() + budget
        while time.monotonic() < deadline:
            wait_idle(page, timeout_ms=int(min(idle_ms, 20000)))
            text = read_result(page)
            if text != before:
                return first_word(text), text
            if gone_selector and page.query_selector(gone_selector) is None:
                return first_word(text), text
            page.wait_for_timeout(700)
        if not retry or attempt == 2:
            break
        # 눌림이 사라진 것으로 보고 한 번만 더 누른다.
        if not click_if_possible(page, selector):
            break

    text = read_result(page)
    return first_word(text), text


# ------------------------------------------------------------
# 동작 하나 실행
# ------------------------------------------------------------

class Runner:
    def __init__(self, browser, app_url, admin_password, skip_ai):
        self.browser = browser
        self.app_url = app_url
        self.admin_password = admin_password
        self.skip_ai = skip_ai
        self.pages = {}
        self.contexts = {}

    def page_for(self, student_no):
        """학생마다 브라우저 창을 따로 쓴다 (채점 프로그램과 같게)."""
        if student_no in self.pages:
            page = self.pages[student_no]
            # 탭을 앞으로 가져와야 자동 갱신 타이머가 제대로 돈다.
            try:
                page.bring_to_front()
            except Exception:
                pass
            return page
        context = self.browser.new_context()
        password = (
            self.admin_password
            if student_no == ADMIN_STUDENT_NO
            else STUDENT_PASSWORD
        )
        page = login(context, self.app_url, student_no, password)
        self.contexts[student_no] = context
        self.pages[student_no] = page
        return page

    def close(self):
        for context in self.contexts.values():
            try:
                context.close()
            except Exception:
                pass
        self.pages.clear()
        self.contexts.clear()

    # ---- 명단 올리기 ----
    def upload_roster(self, csv_path, team_count):
        page = self.page_for(ADMIN_STUDENT_NO)
        page.set_input_files(".st-key-admin_roster_file input[type=file]", str(csv_path))
        wait_idle(page)
        page.fill(".st-key-admin_team_count input", str(team_count))
        page.keyboard.press("Enter")
        wait_idle(page)
        if not click_if_possible(page, ".st-key-admin_start button"):
            raise RuntimeError("명단 올리기 단추를 누를 수 없음")
        wait_idle(page)
        text = read_result(page)
        if not text.startswith("승인"):
            raise RuntimeError(f"명단 올리기 실패: {text}")
        return text

    # ---- 동작들 ----
    def do_apply(self, action):
        page = self.page_for(action["who"])
        selector = f".st-key-apply_{action['team']} button"
        # 지원이 접수되면 지원 단추가 사라진다(결과를 기다리는 중이 되므로).
        word, text = click_and_read(page, selector, gone_selector=selector)
        if word is None:
            # 단추가 없거나 비활성 -> 채점 프로그램도 막힘으로 본다
            return "막힘", "단추가 없거나 비활성(막힘으로 봄)"
        return word, text

    def do_vote(self, action):
        page = self.page_for(action["who"])
        key = "vote_yes" if action["agree"] else "vote_no"
        selector = f".st-key-{key}_{action['applicant']} button"
        # 투표가 접수되면 그 지원의 투표 단추가 사라지고 "내 투표: ..." 로 바뀐다.
        word, text = click_and_read(page, selector, gone_selector=selector)
        if word is None:
            return "막힘", "투표 단추가 없거나 비활성(막힘으로 봄)"
        return word, text

    def do_close(self, action):
        page = self.page_for(ADMIN_STUDENT_NO)
        word, text = click_and_read(page, ".st-key-admin_close button")
        if word is None:
            return "막힘", "마감 단추가 없거나 비활성"
        return word, text

    def do_assign(self, action):
        page = self.page_for(ADMIN_STUDENT_NO)
        if self.skip_ai:
            return "건너뜀", "--skip-ai 로 건너뜀"
        # AI 호출은 오래 걸린다. 모델마다 120초 x 3개 = 최대 6분.
        # 다시 누르면 AI를 한 번 더 부르게 되므로 retry 를 끈다.
        word, text = click_and_read(
            page, ".st-key-admin_assign button", idle_ms=420000, retry=False
        )
        if word is None:
            return "막힘", "AI 배정 단추가 없거나 비활성"
        status = page.query_selector(".st-key-assign_status")
        detail = status.inner_text().strip().replace("\n", " / ") if status else ""
        return word, f"{text}  [진행: {detail[:300]}]"

    def do_confirm(self, action):
        page = self.page_for(ADMIN_STUDENT_NO)
        if self.skip_ai:
            return "건너뜀", "--skip-ai 로 건너뜀"
        word, text = click_and_read(
            page, ".st-key-admin_confirm button", idle_ms=90000
        )
        if word is None:
            return "막힘", "확정 단추가 없거나 비활성"
        return word, text

    def run_action(self, action):
        kind = action["type"]
        if kind == "apply":
            return self.do_apply(action)
        if kind == "vote":
            return self.do_vote(action)
        if kind == "close":
            return self.do_close(action)
        if kind == "assign":
            return self.do_assign(action)
        if kind == "confirm":
            return self.do_confirm(action)
        raise RuntimeError(f"모르는 동작: {kind}")

    # ---- 마지막 확인 ----
    def unassigned_count(self):
        page = self.page_for(ADMIN_STUDENT_NO)
        # 페이지를 새로 고치면 로그인이 풀린다. 화면이 5초마다 저절로
        # 갱신되므로 조금 기다리기만 한다.
        page.wait_for_timeout(6000)
        wait_idle(page)
        # 팀이 없는 학생 표시는 자동 갱신 조각 안에 있어서 잠깐 늦게 나온다.
        try:
            page.wait_for_selector(".st-key-unassigned", timeout=25000)
        except PlaywrightTimeout:
            return None
        element = page.query_selector(".st-key-unassigned")
        if element is None:
            return None
        text = element.inner_text().strip()
        if text in ("없음", ""):
            return 0
        return len([line for line in text.splitlines() if " — 주: " in line])

    def final_check(self, team_count):
        """브라우저를 새로 열어 확정된 팀 구성이 규칙을 지키는지 본다.

        채점 절차 6.2절 5항과 같다. DB를 보지 않고 화면만 읽는다.
        """
        context = self.browser.new_context()
        page = login(context, self.app_url, ADMIN_STUDENT_NO, self.admin_password)
        problems = []
        try:
            try:
                page.wait_for_selector(".st-key-team_1", timeout=25000)
                page.wait_for_selector(".st-key-unassigned", timeout=25000)
            except PlaywrightTimeout:
                problems.append("현황 표시가 제때 나타나지 않음")
            for team_no in range(1, team_count + 1):
                element = page.query_selector(f".st-key-team_{team_no}")
                if element is None:
                    problems.append(f"팀{team_no} 표시를 찾을 수 없음")
                    continue
                lines = [
                    line.strip()
                    for line in element.inner_text().splitlines()
                    if " — 주: " in line
                ]
                if not 2 <= len(lines) <= 4:
                    problems.append(f"팀{team_no} 인원이 {len(lines)}명 (2~4명이어야 함)")

                mains = []
                can_plan = 0
                can_program = 0
                both = 0
                for line in lines:
                    name, _, roles = line.partition(" — 주: ")
                    main_role, _, subs = roles.partition(" / 보조: ")
                    main_role = main_role.strip()
                    sub_list = [
                        s.strip()
                        for s in subs.split(",")
                        if s.strip() and s.strip() != "없음"
                    ]
                    mains.append(main_role)
                    all_roles = {main_role} | set(sub_list)
                    plan = "기획자" in all_roles
                    program = "웹 프로그래머" in all_roles
                    if plan and program:
                        both += 1
                    elif plan:
                        can_plan += 1
                    elif program:
                        can_program += 1
                for role in set(mains):
                    if mains.count(role) >= 2:
                        problems.append(f"팀{team_no} 주 역할 '{role}' 이 {mains.count(role)}명")
                # 기획자와 웹 프로그래머를 서로 다른 사람이 맡을 수 있는지
                filled = 0
                if can_plan and can_program:
                    filled = 2
                elif both and (can_plan or can_program or both >= 2):
                    filled = 2
                elif can_plan or can_program or both:
                    filled = 1
                if filled < 2:
                    problems.append(
                        f"팀{team_no} 에 기획자와 웹 프로그래머를 서로 다른 학생이 맡을 수 없음"
                    )

            left = page.query_selector(".st-key-unassigned")
            left_text = left.inner_text().strip() if left else ""
            left_count = (
                0
                if left_text in ("없음", "")
                else len([x for x in left_text.splitlines() if " — 주: " in x])
            )
            if left_count:
                problems.append(f"팀이 없는 학생이 아직 {left_count}명 있음")
        finally:
            context.close()
        return problems


# ------------------------------------------------------------
# 시나리오 하나 돌리기
# ------------------------------------------------------------

def run_scenario(browser, app_url, admin_password, scenario_path, skip_ai):
    data = json.loads(scenario_path.read_text(encoding="utf-8"))
    runner = Runner(browser, app_url, admin_password, skip_ai)
    rows = []
    try:
        runner.upload_roster(PROJECT / data["csv"], data["team_count"])

        blocked_applicants = set()
        for step in data["steps"]:
            default_expect = step.get("expect")
            results = []
            notes = []
            skipped = False

            for action in step["actions"]:
                # 지원이 막혔으면 그 지원에 대한 뒤의 투표는 건너뛴다 (부록 규칙).
                if (
                    action["type"] == "vote"
                    and action["applicant"] in blocked_applicants
                ):
                    results.append(("건너뜀", "건너뜀"))
                    notes.append("앞의 지원이 막혀 건너뜀")
                    skipped = True
                    continue

                expect = action.get("expect", default_expect)
                actual, detail = runner.run_action(action)
                results.append((expect, actual))
                notes.append(detail)

                if action["type"] == "apply" and actual == "막힘":
                    blocked_applicants.add(action["who"])

            expect = results[-1][0] if results else default_expect
            actual = results[-1][1] if results else "(없음)"
            if skip_ai and actual == "건너뜀":
                verdict = "건너뜀"
            elif skipped and all(a == "건너뜀" for _, a in results):
                verdict = "건너뜀"
            else:
                verdict = "맞음" if all(e == a for e, a in results) else "틀림"

            after = step.get("after") or {}
            if verdict == "맞음" and "unassigned_count" in after:
                got = runner.unassigned_count()
                if got != after["unassigned_count"]:
                    verdict = "틀림"
                    notes.append(
                        f"팀이 없는 학생 수가 기대 {after['unassigned_count']}명 / 실제 {got}명"
                    )
                else:
                    notes.append(f"팀이 없는 학생 {got}명 확인")

            rows.append(
                {
                    "no": step["no"],
                    "what": step["what"],
                    "expect": expect,
                    "actual": actual,
                    "verdict": verdict,
                    "note": " | ".join(n for n in notes if n)[:400],
                }
            )

        final_problems = [] if skip_ai else runner.final_check(data["team_count"])
    finally:
        runner.close()

    return data["title"], rows, final_problems


# ------------------------------------------------------------
# 출력
# ------------------------------------------------------------

def print_table(title, rows, final_problems):
    print()
    print("=" * 96)
    print(f"{title}")
    print("=" * 96)
    print(f"{'단계':<5}{'하는 일':<48}{'기대':<8}{'실제':<8}판정")
    print("-" * 96)
    for row in rows:
        print(
            f"{row['no']:<5}{row['what'][:46]:<48}{row['expect']:<8}"
            f"{row['actual']:<8}{row['verdict']}"
        )
        if row["verdict"] == "틀림":
            print(f"      → {row['note']}")
    right = sum(1 for r in rows if r["verdict"] == "맞음")
    wrong = sum(1 for r in rows if r["verdict"] == "틀림")
    skipped = sum(1 for r in rows if r["verdict"] == "건너뜀")
    print("-" * 96)
    print(f"{len(rows)}단계 중 맞음 {right}개 / 틀림 {wrong}개 / 건너뜀 {skipped}개")
    if wrong:
        print("틀린 단계: " + ", ".join(str(r["no"]) for r in rows if r["verdict"] == "틀림"))
    print()
    print("마지막 확인 (브라우저를 새로 열어 확정된 팀 구성 검사)")
    if final_problems:
        for problem in final_problems:
            print(f"  - {problem}")
    else:
        print("  문제 없음")


def markdown_report(results, skip_ai):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S")
    out = [
        "# 연습 시나리오 자동 테스트 결과",
        "",
        f"- 실행 시각: {now} (KST)",
        f"- AI 호출: {'건너뜀(--skip-ai)' if skip_ai else '실제로 호출함'}",
        "- 실행 방법: `python tests/run_scenarios.py --all --out docs/테스트결과.md`",
        "- 채점 프로그램과 같은 방식으로 브라우저를 자동 조작하고, 화면 요소는"
        " 공고 2.7절의 Streamlit `key` 로만 찾았다.",
        "",
    ]
    total_right = total_wrong = total_skip = 0
    for title, rows, final_problems in results:
        right = sum(1 for r in rows if r["verdict"] == "맞음")
        wrong = sum(1 for r in rows if r["verdict"] == "틀림")
        skipped = sum(1 for r in rows if r["verdict"] == "건너뜀")
        total_right += right
        total_wrong += wrong
        total_skip += skipped
        out += [
            f"## {title}",
            "",
            f"**{len(rows)}단계 중 맞음 {right}개 / 틀림 {wrong}개 / 건너뜀 {skipped}개**",
            "",
            "| 단계 | 하는 일 | 기대 결과 | 실제 결과 | 판정 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for row in rows:
            mark = {"맞음": "맞음", "틀림": "**틀림**", "건너뜀": "건너뜀"}[row["verdict"]]
            out.append(
                f"| {row['no']} | {row['what']} | {row['expect']} | {row['actual']} | {mark} |"
            )
        out.append("")
        out.append("마지막 확인 (브라우저를 새로 열어 확정된 팀 구성 검사)")
        out.append("")
        if final_problems:
            for problem in final_problems:
                out.append(f"- 문제: {problem}")
        else:
            out.append("- 문제 없음 (모든 팀이 2~4명, 주 역할 중복 없음, "
                       "기획자·웹 프로그래머 각 1명, 팀이 없는 학생 없음)")
        out.append("")

    out = out[:6] + [
        "## 전체 결과",
        "",
        f"| 맞음 | 틀림 | 건너뜀 |",
        "| --- | --- | --- |",
        f"| {total_right} | {total_wrong} | {total_skip} |",
        "",
    ] + out[6:]
    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", default="http://localhost:8501")
    parser.add_argument("--scenario", default=None, help="쉬움 / 중간 / 어려움")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--skip-ai", action="store_true", help="AI 배정·확정을 건너뜀")
    parser.add_argument("--out", default=None, help="결과를 적을 마크다운 파일")
    parser.add_argument("--headed", action="store_true", help="브라우저를 보면서 돌림")
    args = parser.parse_args()

    admin_password = read_env_value("ADMIN_PASSWORD")
    if not admin_password:
        print("ADMIN_PASSWORD 를 .env 에서 찾을 수 없습니다.")
        return 2

    names = ["쉬움", "중간", "어려움"] if args.all or not args.scenario else [args.scenario]

    results = []
    with sync_playwright() as playwright:
        # 뒤에 있는 탭은 브라우저가 타이머를 늦춘다. 그러면 앱의 5초 자동
        # 갱신도 느려져서, 다른 학생의 지원이 제때 나타나지 않는다.
        # 학생마다 창을 따로 쓰는 시험에서는 이 늦춤을 꺼야 한다.
        browser = playwright.chromium.launch(
            headless=not args.headed,
            args=[
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
            ],
        )
        try:
            for name in names:
                path = SCENARIO_DIR / f"{name}.json"
                if not path.exists():
                    print(f"시나리오 파일이 없습니다: {path}")
                    return 2
                title, rows, final_problems = run_scenario(
                    browser, args.app, admin_password, path, args.skip_ai
                )
                print_table(title, rows, final_problems)
                results.append((title, rows, final_problems))
        finally:
            browser.close()

    wrong = sum(1 for _, rows, _ in results for r in rows if r["verdict"] == "틀림")
    problems = sum(len(p) for _, _, p in results)

    print()
    print("=" * 96)
    print("전체 결과")
    print("=" * 96)
    for title, rows, final_problems in results:
        right = sum(1 for r in rows if r["verdict"] == "맞음")
        bad = sum(1 for r in rows if r["verdict"] == "틀림")
        skipped = sum(1 for r in rows if r["verdict"] == "건너뜀")
        print(
            f"  {title:<16} 맞음 {right:>2} / 틀림 {bad:>2} / 건너뜀 {skipped:>2}"
            f" / 마지막 확인 문제 {len(final_problems)}개"
        )
    print(f"  합계: 틀림 {wrong}개, 마지막 확인 문제 {problems}개")

    if args.out:
        target = PROJECT / args.out
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(markdown_report(results, args.skip_ai), encoding="utf-8")
        print(f"  결과를 {args.out} 에 적었습니다.")

    return 0 if (wrong == 0 and problems == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
