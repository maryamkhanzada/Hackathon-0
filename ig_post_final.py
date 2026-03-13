import http.client, json, time, os, sys
os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.stdout.reconfigure(encoding='utf-8')

IG_IMAGE = "D:/Hackathon-0/ig_post.png"
IG_CAPTION = (
    "The best investment you will ever make is in your own education.\n\n"
    "Every book you read, every course you take, every question you ask - it compounds over time.\n\n"
    "Small steps forward every single day lead to results that seem impossible at the start.\n\n"
    "What are you learning right now? Tell us below\n\n"
    "#Education #Learning #StudyMotivation #GrowthMindset #LearnEveryday "
    "#KnowledgeIsPower #LifelongLearning #NeverStopLearning #OnlineLearning #Curiosity"
)

def _call(tool, args, timeout=40):
    try:
        conn = http.client.HTTPConnection('localhost', 8808, timeout=timeout+5)
        conn.request('POST', '/mcp',
            json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize',
                'params':{'protocolVersion':'2024-11-05','capabilities':{},
                          'clientInfo':{'name':'ig','version':'1'}}}).encode(),
            {'Content-Type':'application/json','Accept':'application/json, text/event-stream'})
        r = conn.getresponse()
        sid = r.getheader('Mcp-Session-Id') or ''
        r.read()
        if not sid: conn.close(); return {'_err':'no_sid'}
        conn.request('POST', '/mcp',
            json.dumps({'jsonrpc':'2.0','method':'notifications/initialized'}).encode(),
            {'Content-Type':'application/json','Accept':'application/json, text/event-stream','Mcp-Session-Id':sid})
        conn.getresponse().read()
        time.sleep(0.2)
        conn.request('POST', '/mcp',
            json.dumps({'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':tool,'arguments':args}}).encode(),
            {'Content-Type':'application/json','Accept':'application/json, text/event-stream','Mcp-Session-Id':sid})
        r3 = conn.getresponse()
        data = b''
        while True:
            c = r3.read(4096)
            if not c: break
            data += c
        conn.close()
        raw = data.decode('utf-8','replace')
        for line in raw.split('\n'):
            if line.startswith('data:'):
                try: return json.loads(line[5:].strip())
                except: pass
        return {'_err': 'no_data len=' + str(len(raw))}
    except Exception as e:
        return {'_err': str(e)[:80]}

def get_text(r):
    content = r.get('result',{}).get('content',[])
    return '\n'.join(c.get('text','') for c in content if c.get('type')=='text')

def click(ref, label=''):
    r = _call('browser_click', {'element': label or ref, 'ref': ref}, timeout=20)
    t = get_text(r)
    print("  click [" + label + "]: " + t[:100])
    return t

def snap():
    r = _call('browser_snapshot', {}, timeout=35)
    return get_text(r)

def snap_filtered(*keywords):
    s = snap()
    for line in s.split('\n'):
        if any(k in line.lower() for k in keywords):
            print(line)

# Step 1: Upload image using setInputFiles via browser_run_code
print("=== Uploading image via setInputFiles ===")
ig_path = IG_IMAGE.replace("\\", "/")
code = 'async (page) => { const inp = page.locator(\'input[type="file"]\').first(); await inp.setInputFiles("' + ig_path + '"); return "uploaded"; }'
r = _call('browser_run_code', {'code': code}, timeout=30)
print("upload result:", get_text(r)[:150])
time.sleep(5)

print("\n=== Snapshot after file set ===")
snap_filtered('next','crop','aspect','ratio','button','ok','share','caption','select')

# Step 2: Click OK on crop/aspect ratio if it appears, then Next
print("\n=== Looking for OK or Next button ===")
s = snap()
ok_ref = None
next_ref = None
for line in s.split('\n'):
    if 'button "ok"' in line.lower() and 'ref=' in line:
        ok_ref = line.split('ref=')[1].split(']')[0].strip()
    if 'button "next"' in line.lower() and 'ref=' in line:
        next_ref = line.split('ref=')[1].split(']')[0].strip()
print("OK ref:", ok_ref, "  Next ref:", next_ref)

if ok_ref:
    print("Clicking OK...")
    click(ok_ref, 'OK')
    time.sleep(2)

# Click Next (up to 3 times to go through crop -> filter -> caption steps)
for i in range(3):
    s = snap()
    next_ref = None
    for line in s.split('\n'):
        if 'button "next"' in line.lower() and 'ref=' in line:
            next_ref = line.split('ref=')[1].split(']')[0].strip()
            break
    if next_ref:
        print("Clicking Next (" + str(i+1) + ")...")
        click(next_ref, 'Next')
        time.sleep(3)
    else:
        print("No Next button found at step " + str(i+1))
        break

# Step 3: Add caption
print("\n=== Adding caption ===")
s = snap()
caption_ref = None
for line in s.split('\n'):
    ll = line.lower()
    if ('textarea' in ll or 'caption' in ll or 'text' in ll) and 'ref=' in line and 'contenteditable' in ll:
        caption_ref = line.split('ref=')[1].split(']')[0].strip()
        break
print("Caption ref:", caption_ref)

if caption_ref:
    r = _call('browser_type', {'element': 'caption field', 'ref': caption_ref, 'text': IG_CAPTION}, timeout=30)
    print("type result:", get_text(r)[:100])
    time.sleep(2)
else:
    # Try evaluate approach
    cap_json = json.dumps(IG_CAPTION)
    r = _call('browser_evaluate', {
        'function': '() => { var ta = document.querySelector("[aria-label*=caption i]") || document.querySelector("textarea"); if(ta){ta.focus();document.execCommand("selectAll",false,null);document.execCommand("insertText",false,' + cap_json + ');return "ok len="+ta.value.length;} return "no_field"; }'
    }, timeout=20)
    print("caption eval:", get_text(r)[:100])
    time.sleep(2)

# Step 4: Click Share/Post
print("\n=== Clicking Share ===")
s = snap()
share_ref = None
for line in s.split('\n'):
    ll = line.lower()
    if ('button "share"' in ll or 'button "post"' in ll) and 'ref=' in line:
        share_ref = line.split('ref=')[1].split(']')[0].strip()
        break
print("Share ref:", share_ref)

if share_ref:
    click(share_ref, 'Share/Post')
    time.sleep(5)
    print("\n=== Final title ===")
    r = _call('browser_evaluate', {'function': '() => document.title'}, timeout=20)
    print(get_text(r)[:100])
    print("\nINSTAGRAM POST: DONE!")
else:
    print("No Share button found. Current snapshot buttons:")
    snap_filtered('button','share','post')
