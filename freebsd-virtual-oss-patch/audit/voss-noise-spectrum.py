#!/usr/bin/env python3
# Spectrum of virtual_oss vclient_noise() as added when an S16 client is
# expanded into a -b 32 mixer (volume 128, shift_orig 16).  See
# doc/VIRTUAL-OSS-QUALITY-STUDY.md section 4.  Run: python3 -I <this file>

import numpy as np, sys
N=1<<21
# replicate vclient_noise(), volume=128, shift_orig=16 (S16 client into -b 32)
p=0xFFFF1D; s=1; out=np.empty(N,np.int64)
for i in range(N):
    t=s
    if t&1: t+=p
    t//=2; s=t
    t^=0x800000
    if t&0x800000: t|=-0x800000
    t*=128
    out[i]=t>>(30-16)   # shift = 16-30 = -14
x=out/2**31
print("min/max S32 units", out.min(), out.max(), "mean", out.mean())
print("RMS dBFS %.2f"%(20*np.log10(np.sqrt((x**2).mean()))))
print("lag-1 autocorr %.3f"%(np.corrcoef(x[:-1],x[1:])[0,1]))
X=np.abs(np.fft.rfft(x*np.hanning(N)))**2
for fs in (44100,192000):
    f=np.fft.rfftfreq(N,1/fs); band=(f>=20)&(f<=20000)
    frac=X[band].sum()/X.sum()
    print("fs=%d: fraction of noise power in 20Hz-20kHz %.3f -> in-band RMS %.2f dBFS (white would be %.2f)"%(
        fs,frac,20*np.log10(np.sqrt((x**2).mean()*frac)),20*np.log10(np.sqrt((x**2).mean()*(19980/(fs/2))))))
    # tilt: power density near DC vs near Nyquist
    lo=X[(f>20)&(f<1000)].mean(); hi=X[f>fs/2*0.9].mean()
    print("   PSD 20-1000 Hz vs top 10%% of band: %+.1f dB"%(10*np.log10(lo/hi)))
