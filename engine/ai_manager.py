from __future__ import annotations
import json
import os
import urllib.error
import urllib.request

SYSTEM = '''你是A股虚拟基金的组合经理。你没有真实证券账户权限，也不能突破风控。
只允许从传入候选股票中选择目标仓位。输出严格JSON，不要输出解释性前缀。
格式：{"targets":[{"symbol":"sh.600000","name":"...","target_weight":0.10,"reason":"..."}],"diary":"一句话总结"}
允许targets为空。target_weight必须在0到0.15之间。'''


def _request(base: str, key: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f'{base}/chat/completions',
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Authorization':f'Bearer {key}',
            'Content-Type':'application/json',
            'User-Agent':'ai-stock-lab/0.4',
        },
        method='POST')
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode('utf-8'))


def decide_with_api(candidates: list[dict], current: dict, market_score: float) -> dict | None:
    key = os.getenv('AI_API_KEY')
    model = os.getenv('AI_MODEL')
    base = os.getenv('AI_BASE_URL', 'https://api.openai.com/v1').rstrip('/')
    if not key or not model:
        return None

    messages = [
        {'role':'system','content':SYSTEM},
        {'role':'user','content':json.dumps({'market_score':market_score,'current':current,'candidates':candidates[:20]},ensure_ascii=False)}
    ]
    payload = {
        'model': model,
        'temperature': 0.2,
        'response_format': {'type': 'json_object'},
        'messages': messages,
    }
    try:
        try:
            obj=_request(base,key,payload)
        except urllib.error.HTTPError as e:
            # Some OpenAI-compatible relays/models reject response_format or temperature.
            # Retry once with the smallest common Chat Completions payload.
            body=e.read().decode('utf-8','replace')
            print(f'[AI] first request HTTP {e.code}; retrying compatible payload: {body[:300]}')
            obj=_request(base,key,{'model':model,'messages':messages})
        content=obj['choices'][0]['message']['content']
        if isinstance(content,dict):
            return content
        text=str(content).strip()
        if text.startswith('```'):
            text=text.strip('`').strip()
            if text.lower().startswith('json'):
                text=text[4:].strip()
        return json.loads(text)
    except Exception as e:
        print(f'[AI] fallback because API call failed: {e}')
        return None


def smoke_test_api() -> str:
    """Small paid call used only by the manual connectivity workflow."""
    key=os.getenv('AI_API_KEY')
    model=os.getenv('AI_MODEL')
    base=os.getenv('AI_BASE_URL','').rstrip('/')
    if not key or not model or not base:
        raise RuntimeError('AI_API_KEY / AI_BASE_URL / AI_MODEL is incomplete')
    messages=[{'role':'user','content':'只返回严格JSON：{"ok":true}'}]
    try:
        obj=_request(base,key,{'model':model,'messages':messages,'response_format':{'type':'json_object'}})
    except urllib.error.HTTPError:
        obj=_request(base,key,{'model':model,'messages':messages})
    content=obj['choices'][0]['message']['content']
    if not content:
        raise RuntimeError('AI API returned empty content')
    return str(content)[:300]
