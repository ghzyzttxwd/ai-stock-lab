from __future__ import annotations
import json
import os
import urllib.request

SYSTEM = '''你是A股虚拟基金的组合经理。你没有真实证券账户权限，也不能突破风控。
只允许从传入候选股票中选择目标仓位。输出严格JSON，不要输出解释性前缀。
格式：{"targets":[{"symbol":"sh.600000","name":"...","target_weight":0.10,"reason":"..."}],"diary":"一句话总结"}
允许targets为空。target_weight必须在0到0.15之间。'''


def decide_with_api(candidates: list[dict], current: dict, market_score: float) -> dict | None:
    key = os.getenv('AI_API_KEY')
    model = os.getenv('AI_MODEL')
    base = os.getenv('AI_BASE_URL', 'https://api.openai.com/v1').rstrip('/')
    if not key or not model:
        return None
    payload = {
        'model': model,
        'temperature': 0.2,
        'response_format': {'type': 'json_object'},
        'messages': [
            {'role':'system','content':SYSTEM},
            {'role':'user','content':json.dumps({'market_score':market_score,'current':current,'candidates':candidates[:20]},ensure_ascii=False)}
        ]
    }
    req = urllib.request.Request(
        f'{base}/chat/completions',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Authorization':f'Bearer {key}','Content-Type':'application/json'},
        method='POST')
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            obj=json.loads(r.read().decode('utf-8'))
        content=obj['choices'][0]['message']['content']
        return json.loads(content)
    except Exception as e:
        print(f'[AI] fallback because API call failed: {e}')
        return None
