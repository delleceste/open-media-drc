import json, numpy as np
d={int(k):v for k,v in json.load(open("rew.json")).items()}
fs=44100.
name={1:"L lin",2:"L2 lin",3:"R lin",4:"R2 lin",5:"L bsd",6:"L2 bsd",7:"R bsd",8:"R2 bsd"}
def ir(i):
    a=np.array(d[i]["ir"]); t=d[i]["t0"]+np.arange(len(a))*d[i]["dt"]; return a,t
# window: -5 ms .. +300 ms around t=0 (timing reference)
def win(i):
    a,t=ir(i); m=(t>=-0.005)&(t<0.300); return a[m]
def peak_time(i):
    a,t=ir(i); k=np.argmax(np.abs(a)); y0,y1,y2=np.abs(a[k-1:k+2]); off=0.5*(y0-y2)/(y0-2*y1+y2)
    return (t[k]+off*d[i]["dt"])*1e6
print("IR peak time (us, rel. timing ref; sub-sample parabolic):")
for i in range(1,9): print(f"  {name[i]:7s} {peak_time(i):8.2f}")
for os_,(l,r) in (("linux",(1,3)),("linux2",(2,4)),("bsd",(5,7)),("bsd2",(6,8))):
    print(f"  L-R peak offset {os_:6s}: {peak_time(l)-peak_time(r):+.2f} us")
def null(i,j,bands=((20,20000),(20,1000),(1000,5000),(5000,10000),(10000,20000))):
    x=win(i); y=win(j); n=len(x); N=1<<int(np.ceil(np.log2(2*n)))
    X=np.fft.rfft(x,N); Y=np.fft.rfft(y,N); f=np.fft.rfftfreq(N,1/fs)
    # fit complex gain = fractional delay + level using 200-15000 Hz
    m=(f>200)&(f<15000); ph=np.unwrap(np.angle(Y[m]/X[m])); tau=-np.polyfit(2*np.pi*f[m],ph,1)[0]
    Xs=X*np.exp(-2j*np.pi*f*tau); g=np.vdot(Xs[m],Y[m]).real/np.vdot(Xs[m],Xs[m]).real
    R=Y-g*Xs; res=[]
    for lo,hi in bands:
        b=(f>=lo)&(f<hi); res.append(10*np.log10(np.sum(np.abs(R[b])**2)/np.sum(np.abs(Y[b])**2)))
    return tau*1e6, 20*np.log10(g), res
print("\nIR nulls (residual energy rel. signal, dB) in 20-20k | 20-1k | 1-5k | 5-10k | 10-20k ; delay us ; gain dB")
pairs=[("L repeat linux",1,2),("L repeat bsd",5,6),("L cross",1,5),("L cross",2,6),
       ("R repeat linux",3,4),("R repeat bsd",7,8),("R cross",3,7),("R cross",4,8)]
for lab,i,j in pairs:
    tau,g,res=null(i,j)
    print(f"  {lab:15s} {name[i]:>6s}-{name[j]:<6s} "+" ".join(f"{r:7.1f}" for r in res)+f"   {tau:+6.2f} us  {g:+.3f} dB")
print("\nDistortion (mean over band), THD% / noise% / H2% / H3%:")
for i in range(1,9):
    D=np.array(d[i]["dist"]["data"],float); f=D[:,0]
    s=[]
    for lo,hi in ((50,1000),(1000,5000),(5000,10000)):
        m=(f>=lo)&(f<hi); s.append("%.4f/%.4f/%.4f/%.4f"%tuple(D[m][:,[2,3,4,5]].mean(0)))
    print(f"  {name[i]:7s} 50-1k {s[0]}   1-5k {s[1]}   5-10k {s[2]}")
