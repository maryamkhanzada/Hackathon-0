import sys, json
for line in sys.stdin:
    line = line.strip()
    if line.startswith('data:'):
        try:
            d = json.loads(line[5:])
            txt = d.get('result', {}).get('content', [{}])[0].get('text', '')
            for l in txt.split('\n'):
                s = l.strip()
                if s and not s.startswith('#') and not s.startswith('`') and not s.startswith('await'):
                    print('Result:', s)
                    break
        except Exception as e:
            print('Parse error:', e)
