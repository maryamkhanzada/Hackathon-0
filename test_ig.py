#!/usr/bin/env python3
import http.client, json, time

def _call(tool, args, timeout=40):
    try:
        conn = http.client.HTTPConnection('localhost', 8808, timeout=timeout+5)
        conn.request('POST','/mcp',
            json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'p','version':'1'}}}).encode(),
            {'Content-Type':'application/json','Accept':'application/json, text/event-stream'})
        r=conn.getresponse(); sid=r.getheader('Mcp-Session-Id') or ''; r.read()
        if not sid: return {}
        conn.request('POST','/mcp',
            json.dumps({'jsonrpc':'2.0','method':'notifications/initialized'}).encode(),
            {'Content-Type':'application/json','Accept':'application/json, text/event-stream','Mcp-Session-Id':sid})
        conn.getresponse().read(); time.sleep(0.2)
        conn.request('POST','/mcp',
            json.dumps({'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':tool,'arguments':args}}).encode(),
            {'Content-Type':'application/json','Accept':'application/json, text/event-stream','Mcp-Session-Id':sid})
        r3=conn.getresponse(); data=b''
        while True:
            c=r3.read(4096)
            if not c: break
            data+=c
        conn.close()
        for line in data.decode('utf-8','replace').split('\n'):
            if line.startswith('data:'):
                return json.loads(line[5:].strip())
    except Exception as e:
        return {'_err':str(e)}
    return {}

def ev(js, t=35):
    d=_call('browser_evaluate',{'function':js},t)
    content=d.get('result',{}).get('content',[])
    full='\n'.join(c.get('text','') for c in content if c.get('type')=='text')
    for ln in full.split('\n'):
        s=ln.strip()
        if s and not s.startswith('#') and not s.startswith('```') and not s.startswith('-'):
            return s.strip('"')
    return 'EMPTY:len='+str(len(full))

# Check current page first
print('Current page:', ev('() => document.title + " | " + location.hostname'))

# Navigate to Instagram
print('Navigating to Instagram...')
_call('browser_evaluate',
    {'function': '() => { window.location.replace("https://www.instagram.com/"); return "nav"; }'},
    timeout=8)
print('Waiting 120s for Instagram to load...')
for i in range(12):
    time.sleep(10)
    print(f'  {(i+1)*10}s...')
print('Checking Instagram title...')
title = ev('() => document.title + " | " + location.hostname')
print('Title:', title[:100])
