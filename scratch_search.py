import urllib.request, json
req = urllib.request.Request(
    'https://rapidapi.com/api/gateway/graphql', 
    data=json.dumps({'query':'query { search(term:"tiktok user posts") { items { id name slug owner { username } } } }'}).encode('utf-8'), 
    headers={'Content-Type': 'application/json'}
)
res = urllib.request.urlopen(req)
print(res.read().decode('utf-8')[:1000])
