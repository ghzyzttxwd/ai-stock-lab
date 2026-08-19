from __future__ import annotations
import json
import os
import time
import requests

SYSTEM = '''你是A股虚拟基金的组合经理。你没有真实证券账户权限，也不能突破风控。
目标不是把仓位填满，而是寻找未来1到3个交易日具有相对优势、且没有明显过热的机会；没有合格机会时允许targets为空并保留现金。
只允许从传入候选股票中选择目标仓位。重点参考opportunity_score、market_relative_1/3/5、r1/r3/r5、close_position、amount_ratio_3_20、overheat_score、risk、valuation等实际字段，不要因为过去20/60日涨得多就机械追高。
价格触发、止损止盈由后续确定性条件计划引擎负责，你只决定候选和最大目标仓位。
输出严格JSON，不要输出解释性前缀。
格式：{"targets":[{"symbol":"sh.600000","name":"...","target_weight":0.10,"reason":"..."}],"diary":"一句话总结"}
target_weight必须在0到0.15之间。'''


class TransientAIError(RuntimeError):
    """A relay/stream condition worth retrying once."""


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


def _stream_chat(base: str, key: str, payload: dict) -> str:
    started = time.monotonic()
    body = {**payload, 'stream': True}
    body_bytes = len(json.dumps(body, ensure_ascii=False).encode('utf-8'))
    print(f'[AI] stream request bytes={body_bytes}')
    with requests.post(
        f'{base}/chat/completions',
        headers=_headers(key),
        json=body,
        stream=True,
        timeout=(15, 180),
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

    for attempt in (1, 2):
        try:
            content = _stream_chat(base, key, payload)
            result = _parse_json_content(content)
            if not isinstance(result.get('targets'), list):
                raise ValueError('AI JSON missing targets list')
            print(f'[AI] formal decision success attempt={attempt} targets={len(result["targets"])}')
            return result
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else None
            if attempt == 1 and code in {502, 503, 504}:
                print(f'[AI] transient HTTP {code}; retry once after 5s')
                time.sleep(5)
                continue
            print(f'[AI] fallback because API HTTP failed: {code}')
            return None
        except (requests.RequestException, TransientAIError, json.JSONDecodeError) as e:
            if attempt == 1:
                print(f'[AI] transient/invalid response; retry once after 5s: {e}')
                time.sleep(5)
                continue
            print(f'[AI] fallback because API call failed after retry: {e}')
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
