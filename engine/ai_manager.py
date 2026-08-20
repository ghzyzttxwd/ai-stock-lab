from __future__ import annotations
import json
import math
import os
import signal
import time
from contextlib import contextmanager

import requests

SYSTEM = '''你是A股虚拟基金的组合经理。你没有真实证券账户权限，也不能突破风控。
目标不是把仓位填满，而是寻找未来1到3个交易日具有相对优势、且没有明显过热的机会；没有合格机会时允许targets为空并保留现金。
只允许从传入候选股票中选择目标仓位。重点参考opportunity_score、market_relative_1/3/5、r1/r3/r5、close_position、amount_ratio_3_20、overheat_score、risk、valuation等实际字段，不要因为过去20/60日涨得多就机械追高。
价格触发、止损止盈由后续确定性条件计划引擎负责，你只决定候选和最大目标仓位。
输出严格JSON，不要输出解释性前缀。
格式：{"targets":[{"symbol":"sh.600000","name":"...","target_weight":0.10,"reason":"..."}],"diary":"一句话总结"}
target_weight必须在0到0.15之间。'''

DEFAULT_AI_DECISION_BUDGET_SECONDS = 180.0
MIN_AI_DECISION_BUDGET_SECONDS = 30.0
MAX_AI_DECISION_BUDGET_SECONDS = 300.0


class TransientAIError(RuntimeError):
    """A relay/stream condition worth retrying once."""


class AIDecisionTimeout(RuntimeError):
    """The whole AI decision exceeded its production wall-clock budget."""


def _decision_budget_seconds() -> float:
    raw = os.getenv('AI_DECISION_BUDGET_SECONDS', str(DEFAULT_AI_DECISION_BUDGET_SECONDS))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = DEFAULT_AI_DECISION_BUDGET_SECONDS
    if not math.isfinite(value):
        value = DEFAULT_AI_DECISION_BUDGET_SECONDS
    return max(MIN_AI_DECISION_BUDGET_SECONDS, min(MAX_AI_DECISION_BUDGET_SECONDS, value))


@contextmanager
def _wall_clock_limit(seconds: float):
    """Hard-cap a blocking AI request on Linux while preserving any prior alarm."""
    seconds = max(0.001, float(seconds))
    if not hasattr(signal, 'SIGALRM') or not hasattr(signal, 'setitimer'):
        yield
        return

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    started = time.monotonic()

    def _alarm(_signum, _frame):
        raise AIDecisionTimeout(f'AI decision exceeded {seconds:.1f}s wall-clock budget')

    signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            elapsed = max(0.0, time.monotonic() - started)
            remaining = max(0.001, previous_timer[0] - elapsed)
            signal.setitimer(signal.ITIMER_REAL, remaining, previous_timer[1])


def _headers(key: str) -> dict:
    return {
        'Authorization': f'Bearer {key}',
        'Content-Type': 'application/json',
        'Accept': 'text/event-stream, application/json',
        'User-Agent': 'ai-stock-lab/0.7',
    }


def _request_json(base: str, key: str, payload: dict) -> dict:
    r = requests.post(
        f'{base}/chat/completions',
        headers=_headers(key),
        json=payload,
        timeout=(15, 90),
    )
    r.raise_for_status()
    return r.json()


def _stream_chat(base: str, key: str, payload: dict, *, max_seconds: float | None = None) -> str:
    started = time.monotonic()
    limit = float(max_seconds if max_seconds is not None else DEFAULT_AI_DECISION_BUDGET_SECONDS)
    limit = max(0.001, limit)
    connect_timeout = max(1.0, min(15.0, limit))
    read_timeout = max(1.0, min(180.0, limit))
    body = {**payload, 'stream': True}
    body_bytes = len(json.dumps(body, ensure_ascii=False).encode('utf-8'))
    print(f'[AI] stream request bytes={body_bytes} max_seconds={limit:.1f}')

    with _wall_clock_limit(limit):
        with requests.post(
            f'{base}/chat/completions',
            headers=_headers(key),
            json=body,
            stream=True,
            timeout=(connect_timeout, read_timeout),
        ) as r:
            r.encoding = 'utf-8'
            if r.status_code >= 400:
                preview = r.text[:300].replace('\n', ' ')
                print(f'[AI] HTTP {r.status_code}: {preview}')
                r.raise_for_status()

            sse_parts: list[str] = []
            plain_lines: list[str] = []
            saw_sse = False
            first_event = None
            event_count = 0
            reasoning_chars = 0
            finish_reasons: list[str] = []
            for raw in r.iter_lines(decode_unicode=True):
                elapsed_now = time.monotonic() - started
                if elapsed_now >= limit:
                    raise AIDecisionTimeout(f'AI decision exceeded {limit:.1f}s wall-clock budget')
                if raw is None:
                    continue
                line = str(raw).strip()
                if not line:
                    continue
                if first_event is None:
                    first_event = time.monotonic()
                    print(f'[AI] first response event after {first_event-started:.2f}s')
                if line.startswith('data:'):
                    saw_sse = True
                    data = line[5:].strip()
                    if data == '[DONE]':
                        break
                    event_count += 1
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if obj.get('error'):
                        raise RuntimeError(f"AI stream error: {str(obj.get('error'))[:240]}")
                    choices = obj.get('choices') or []
                    if not choices:
                        continue
                    choice = choices[0]
                    finish = choice.get('finish_reason')
                    if finish:
                        finish_reasons.append(str(finish))
                    delta = choice.get('delta') or {}
                    content = delta.get('content')
                    if content:
                        sse_parts.append(str(content))
                    elif choice.get('message', {}).get('content'):
                        sse_parts.append(str(choice['message']['content']))
                    else:
                        reasoning = delta.get('reasoning_content') or delta.get('reasoning')
                        if reasoning:
                            reasoning_chars += len(str(reasoning))
                else:
                    plain_lines.append(line)

            elapsed = time.monotonic() - started
            if saw_sse:
                text = ''.join(sse_parts).strip()
                print(
                    f'[AI] stream completed in {elapsed:.2f}s chars={len(text)} '
                    f'events={event_count} reasoning_chars={reasoning_chars} finish={finish_reasons[-1:]}'
                )
                if not text:
                    raise TransientAIError('AI stream completed but returned no final content')
                return text

            raw_text = '\n'.join(plain_lines).strip()
            if not raw_text:
                raise TransientAIError('AI response was empty')
            obj = json.loads(raw_text)
            content = obj['choices'][0]['message']['content']
            print(f'[AI] non-SSE response completed in {elapsed:.2f}s')
            return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)


def _parse_json_content(content: str | dict) -> dict:
    if isinstance(content, dict):
        return content
    text = str(content).strip()
    if text.startswith('```'):
        text = text.strip('`').strip()
        if text.lower().startswith('json'):
            text = text[4:].strip()
    return json.loads(text)


def _sleep_before_retry(deadline: float, reason: str) -> bool:
    remaining = deadline - time.monotonic()
    if remaining <= 6.0:
        print(f'[AI] skip retry; only {max(0.0, remaining):.1f}s budget remains after {reason}')
        return False
    delay = min(5.0, max(0.0, remaining - 1.0))
    print(f'[AI] retry once after {delay:.1f}s; remaining_budget={remaining:.1f}s reason={reason}')
    time.sleep(delay)
    return True


def decide_with_api(candidates: list[dict], current: dict, market_score: float) -> dict | None:
    key = os.getenv('AI_API_KEY')
    model = os.getenv('AI_MODEL')
    base = os.getenv('AI_BASE_URL', 'https://api.openai.com/v1').rstrip('/')
    if not key or not model:
        return None

    compact=[]
    keys=(
        'symbol','name','score_d','opportunity_score','market_relative_1','market_relative_3','market_relative_5',
        'r1','r3','r5','trend','momentum','risk','quality','valuation','close_position','amount_ratio_3_20',
        'overheat_score','atr14_pct','close','peTTM','pbMRQ',
    )
    for row in candidates[:24]:
        compact.append({k:row.get(k) for k in keys if k in row})
    messages = [
        {'role': 'system', 'content': SYSTEM},
        {'role': 'user', 'content': json.dumps({
            'market_score': market_score,
            'current': current,
            'candidates': compact,
        }, ensure_ascii=False)},
    ]
    payload = {'model': model, 'messages': messages}

    budget = _decision_budget_seconds()
    deadline = time.monotonic() + budget
    print(f'[AI] decision wall-clock budget={budget:.1f}s')

    for attempt in (1, 2):
        remaining = deadline - time.monotonic()
        if remaining <= 1.0:
            print('[AI] fallback because decision budget is exhausted before request')
            return None
        try:
            content = _stream_chat(base, key, payload, max_seconds=remaining)
            result = _parse_json_content(content)
            if not isinstance(result.get('targets'), list):
                raise ValueError('AI JSON missing targets list')
            print(f'[AI] formal decision success attempt={attempt} targets={len(result["targets"])}')
            return result
        except AIDecisionTimeout as e:
            print(f'[AI] fallback because wall-clock budget expired: {e}')
            return None
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else None
            if attempt == 1 and code in {502, 503, 504}:
                if _sleep_before_retry(deadline, f'HTTP {code}'):
                    continue
            print(f'[AI] fallback because API HTTP failed: {code}')
            return None
        except (requests.RequestException, TransientAIError, json.JSONDecodeError) as e:
            if attempt == 1 and _sleep_before_retry(deadline, type(e).__name__):
                continue
            print(f'[AI] fallback because API call failed after retry/budget check: {e}')
            return None
        except (ValueError, RuntimeError) as e:
            print(f'[AI] fallback because API call failed: {e}')
            return None
    return None


def smoke_test_api() -> str:
    key = os.getenv('AI_API_KEY')
    model = os.getenv('AI_MODEL')
    base = os.getenv('AI_BASE_URL', '').rstrip('/')
    if not key or not model or not base:
        raise RuntimeError('AI_API_KEY / AI_BASE_URL / AI_MODEL is incomplete')
    messages = [{'role': 'user', 'content': '只返回严格JSON：{"ok":true}'}]
    obj = _request_json(base, key, {'model': model, 'messages': messages})
    content = obj['choices'][0]['message']['content']
    if not content:
        raise RuntimeError('AI API returned empty content')
    return str(content)[:300]
