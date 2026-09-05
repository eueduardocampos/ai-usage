"""Financial equivalence: cache and reasoning must never be double-counted."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codex_usage import equivalent_usd, CodexUsage

def event(model='gpt-6-astra', inp=100000, cached=80000, out=1000):
    return dict(model=model,input_tokens=inp,cached_input_tokens=cached,
                output_tokens=out,reasoning_output_tokens=500,total_tokens=inp+out,
                timestamp='2026-09-05T12:00:00Z')
assert abs(equivalent_usd(event()) - .33) < 1e-9
assert abs(equivalent_usd(event(inp=300000,cached=200000)) - 2.475) < 1e-9
assert equivalent_usd(event(model='codex-auto-review')) is None
summary=CodexUsage._summarize([event(),event(model='unknown')])
assert summary['total_tokens']==202000
assert summary['unpriced_tokens']==101000
assert abs(summary['equivalent_usd']-.33)<1e-9
assert len(summary['by_model'])==2
print('PRICING PASSED')

# Direct quota snapshots survive restart; a failed refresh preserves last data.
import json
import tempfile
from unittest.mock import patch, mock_open
from io import BytesIO
import urllib.error
with tempfile.TemporaryDirectory() as tmp:
    path = str(Path(tmp) / 'usage.db')
    collector = CodexUsage(path)
    response = {'plan_type':'test','rate_limit':{'primary_window':{
        'used_percent':12,'limit_window_seconds':604800,'reset_at':1789213943}},
        'credits':{'balance':'0','has_credits':False}}
    auth = json.dumps({'auth_mode':'chatgpt','tokens':{'access_token':'test','account_id':'test'}})
    with patch('builtins.open',mock_open(read_data=auth)), patch('codex_usage.urllib.request.urlopen',return_value=BytesIO(json.dumps(response).encode())) as get:
        collector.refresh_limits(force=True)
        collector.refresh_limits()
        assert get.call_count == 1
    assert collector.state()['limits'][0]['primary']['window_minutes']==10080
    assert len(CodexUsage(path).limit_history())==1
    with patch('builtins.open',mock_open(read_data=auth)), patch('codex_usage.urllib.request.urlopen',side_effect=urllib.error.URLError('offline')):
        collector.refresh_limits(force=True)
    assert collector.state()['limits'][0]['primary']['used_percent']==12
    assert collector.state()['limits_error']
print('QUOTA PERSISTENCE PASSED')
