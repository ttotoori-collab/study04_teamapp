"""오픈라우터(OpenRouter) 호출 (과제 공고 4.3절).

지키는 것:

- 무료 텍스트 생성 모델 3개를 1차·2차·3차로 **직접** 정해 쓴다.
  무료 모델로 자동 연결하는 라우터(openrouter/free)는 쓰지 않는다 (감점 14번, -2점).
- 모델 하나마다 **부른 때부터 응답을 다 받을 때까지 전체 120초**를 재서
  넘으면 끊고 다음 모델로 넘어간다 (3장 어려운 해결 1점).
- 응답 없음, 빈 응답, 오류(HTTP 429 등)는 모두 실패로 보고 다음 모델로 넘어간다.
- 추론 모델이 생각하다가 빈 응답을 내는 일을 줄이려고
  reasoning effort 를 low 로, max_tokens 를 넉넉히 준다.
- API 키는 읽기만 하고 화면·기록·예외 메시지에 넣지 않는다 (감점 8번, -3점).
"""

import concurrent.futures
import json
import os
import time

import requests

import db

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

# 1차, 2차, 3차 모델. 모두 이름이 :free 로 끝나는 무료 모델이다.
#
# 2026-10-23 (KST) 에 오픈라우터의 무료 모델을 하나씩 실제로 불러 보고 골랐다.
# 세 모델 모두 응답을 받아 확인했다. 공급자가 서로 달라서,
# 한 공급자가 막혀도 다음 모델로 넘어갈 수 있다.
#
# 순서는 tf_ai_logs 에 쌓인 실제 성공률로 정했다 (2026-10-23 KST 기준).
#
#   모델                                        호출  성공  성공률  성공 평균
#   nvidia/nemotron-3-ultra-550b-a55b:free       1    1   100%   104.0초
#   dots-studio/dots-3-note-preview:free         2    1    50%    62.5초
#   poolside/laguna-s-2.1:free                   4    1    25%    40.4초
#   nvidia/nemotron-3.5-lightning:free           5    0     0%    -      <- 뺐음
#
# 1차는 **성공했을 때 빠른** 모델을 쓴다.
# nemotron-3-ultra 가 성공률은 높았지만(1/1) 104초가 걸려 120초 제한에 너무
# 가깝다. 한 번만 느려져도 끊기고 120초를 버리게 된다. 그래서 62.5초인
# dots-studio 를 1차로, nemotron-3-ultra 를 2차로 둔다.
#
# 세 모델이 모두 120초를 다 쓰더라도 360초라서 10분 제한(감점 18번) 안에 든다.
#
# 뺀 모델: nvidia/nemotron-3.5-lightning:free
#   5번 불러 한 번도 성공하지 못했다. 빈 응답 3번, 배정안 검사 실패 2번.
#
# 쓸 수 없던 모델(기록용):
#   nvidia/nemotron-3-super-120b-a12b:free   - HTTP 404
#   google/gemma-4-31b-it:free               - HTTP 429 (공급자 한도)
#   thinkingmachines/inkling-small:free      - HTTP 403 (agentic harness 전용)
MODELS = (
    "dots-studio/dots-3-note-preview:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "poolside/laguna-s-2.1:free",
)

# 모델 하나에 허용하는 전체 시간 (초)
PER_MODEL_TIMEOUT = 120

# 응답 길이 한도를 넉넉히 준다 (추론에 다 써서 빈 응답이 나오는 일을 줄임)
MAX_TOKENS = 8000

# 연결이 맺어지기를 기다리는 한도 (초)
CONNECT_TIMEOUT = 15

# tf_ai_logs.outcome 에 쓰는 값
OUTCOME_OK = "성공"
OUTCOME_TIMEOUT = "시간초과"
OUTCOME_EMPTY = "빈 응답"
OUTCOME_ERROR = "오류"
OUTCOME_INVALID = "검사 실패"

# 응답을 기록에 남길 때 자르는 길이
EXCERPT_LIMIT = 1500


class AiFailure(Exception):
    """AI 호출이 실패했을 때. outcome 과 사람이 읽을 이유를 들고 있다."""

    def __init__(self, outcome, reason):
        super().__init__(reason)
        self.outcome = outcome
        self.reason = reason


def models():
    """쓸 모델 목록.

    평소에는 위의 MODELS 를 쓴다. 환경변수 TF_AI_MODELS 가 있으면 그것을 쓴다.
    실패 처리(2차·3차로 넘어감, 앱 자체 배정)가 동작하는지 시험할 때,
    없는 모델 이름을 넣어 무료 모델 하루 한도를 쓰지 않고 확인하기 위한 것이다.
    채점에는 쓰지 않는다. 시나리오나 학생 이름과는 아무 상관이 없다.
    """
    raw = (os.environ.get("TF_AI_MODELS") or "").strip()
    if not raw:
        return MODELS
    picked = tuple(name.strip() for name in raw.split(",") if name.strip())
    return picked or MODELS


def has_key():
    return db.has_secret("OPENROUTER_API_KEY")


def call_model(model, prompt, time_left=None):
    """모델 하나를 부른다.

    돌려주는 값: (응답 글자, 걸린 밀리초)
    실패하면 AiFailure 를 올린다.

    전체 시간은 PER_MODEL_TIMEOUT 과 time_left 중 작은 쪽이다.
    time_left 는 배정 전체에 남은 시간이다 (감점 18번: 10분 넘기지 않기).
    """
    key = db.secret("OPENROUTER_API_KEY")
    if not key:
        raise AiFailure(OUTCOME_ERROR, "OPENROUTER_API_KEY 가 없음")

    budget = PER_MODEL_TIMEOUT
    if time_left is not None:
        budget = min(budget, max(1, int(time_left)))

    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": MAX_TOKENS,
        "temperature": 0,
        # 추론에 생각을 다 쓰지 않도록 낮게 준다 (공고 4.3절)
        "reasoning": {"effort": "low"},
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    start = time.monotonic()

    def elapsed_ms():
        return int((time.monotonic() - start) * 1000)

    # 부른 때부터 응답을 다 받을 때까지 **전체** budget 초를 잰다 (공고 3장).
    #
    # requests 의 timeout 은 "멈춰 있는 시간"만 재기 때문에, 그것만으로는
    # 전체 시간을 못 막는다. 응답이 천천히 계속 오면 얼마든지 길어지고,
    # 마지막 조각을 받은 뒤 읽기 제한만큼 더 기다릴 수도 있다.
    # 그래서 주고받는 일 전체를 딴 갈래(스레드)에 맡기고, 여기서 시간을 재다가
    # budget 이 지나면 연결을 닫아 끊는다. 어떤 경우에도 budget 을 넘지 않는다.
    holder = {}

    def fetch():
        response = requests.post(
            ENDPOINT,
            headers=headers,
            json=body,
            stream=True,
            # 소켓 제한도 budget 으로 둔다. 이보다 먼저 끊으면,
            # 늦게라도 답했을 모델을 괜히 버리게 된다.
            timeout=(CONNECT_TIMEOUT, budget),
        )
        holder["response"] = response
        try:
            if response.status_code == 429:
                raise AiFailure(OUTCOME_ERROR, "HTTP 429 (사용량 한도)")
            if response.status_code >= 400:
                raise AiFailure(OUTCOME_ERROR, f"HTTP {response.status_code}")

            chunks = []
            for chunk in response.iter_content(chunk_size=8192):
                if time.monotonic() - start > budget:
                    raise AiFailure(
                        OUTCOME_TIMEOUT, f"{budget}초가 지나 끊음(응답 받는 중)"
                    )
                if chunk:
                    chunks.append(chunk)
            return b"".join(chunks)
        finally:
            try:
                response.close()
            except Exception:
                pass

    worker = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = worker.submit(fetch)
    try:
        raw = future.result(timeout=budget)
    except concurrent.futures.TimeoutError:
        # 시간이 다 됐다. 연결을 닫아 기다리던 갈래를 깨운다.
        response = holder.get("response")
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        raise AiFailure(OUTCOME_TIMEOUT, f"{budget}초가 지나 끊음")
    except requests.Timeout:
        raise AiFailure(OUTCOME_TIMEOUT, f"{budget}초 안에 응답이 끝나지 않음")
    except requests.RequestException:
        # 예외 메시지에 키가 들어 있을 수 있어 원문을 쓰지 않는다.
        raise AiFailure(OUTCOME_ERROR, "연결하지 못함")
    finally:
        # 기다리지 않고 바로 넘어간다. 남은 갈래는 연결이 닫히면 저절로 끝난다.
        worker.shutdown(wait=False)

    if not raw:
        raise AiFailure(OUTCOME_EMPTY, "응답이 비어 있음")

    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        raise AiFailure(OUTCOME_ERROR, "응답이 JSON 이 아님")

    if isinstance(payload.get("error"), dict):
        code = payload["error"].get("code")
        raise AiFailure(OUTCOME_ERROR, f"모델이 오류를 냄 (code {code})")

    choices = payload.get("choices") or []
    if not choices:
        raise AiFailure(OUTCOME_EMPTY, "응답에 내용이 없음")

    message = choices[0].get("message") or {}
    content = (message.get("content") or "").strip()
    if not content:
        # 추론 모델이 생각하는 데 응답 길이를 다 쓰면 이렇게 된다 (공고 4.3절).
        raise AiFailure(OUTCOME_EMPTY, "빈 응답(생각에 길이를 다 씀)")

    return content, elapsed_ms()


def log_call(attempt_no, model, outcome, fail_reason=None, elapsed_ms=None, excerpt=None):
    """AI 호출을 tf_ai_logs 에 남긴다.

    호출이 실패할 때마다 한 행을 남긴다. 성공도 남긴다.
    README 7.1절 5항(AI 호출 실패 사례)의 근거로 쓴다.
    """
    if excerpt:
        excerpt = str(excerpt)[:EXCERPT_LIMIT]
    db.execute(
        """
        insert into tf_ai_logs
               (attempt_no, model, outcome, fail_reason, elapsed_ms, response_excerpt)
        values (%s, %s, %s, %s, %s, %s)
        """,
        (attempt_no, model, outcome, fail_reason, elapsed_ms, excerpt),
    )


def recent_logs(limit=50):
    """AI 호출 기록을 최근 순으로 읽는다 (관리자 현황 표에 쓴다)."""
    return db.query_all(
        """
        select called_at, attempt_no, model, outcome, fail_reason, elapsed_ms
          from tf_ai_logs
         order by called_at desc, id desc
         limit %s
        """,
        (limit,),
    )


def calls_today():
    """오늘(한국 시각) 부른 횟수. 무료 모델 하루 한도를 지키려고 센다."""
    row = db.query_one(
        """
        select count(*) as n from tf_ai_logs
         where attempt_no <= 3
           and (called_at at time zone 'Asia/Seoul')::date
             = (now() at time zone 'Asia/Seoul')::date
        """
    )
    return 0 if row is None else row["n"]
