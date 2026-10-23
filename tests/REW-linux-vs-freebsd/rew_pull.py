import json, urllib.request, base64, numpy as np
B="http://localhost:4735"
def get(p): return json.load(urllib.request.urlopen(B+p, timeout=60))
out={}
for i in range(1,9):
    ir=get(f"/measurements/{i}/impulse-response?unit=percent")
    raw=base64.b64decode(ir["data"])
    a=np.frombuffer(raw,'>f4').astype(float)
    dist=get(f"/measurements/{i}/distortion?ppo=3")
    out[i]=dict(ir=a.tolist(), t0=ir["startTime"], dt=ir["sampleInterval"], dist=dist)
json.dump(out,open("rew.json","w"))
print({i:len(v["ir"]) for i,v in out.items()})
