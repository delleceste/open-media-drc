#!/usr/bin/env python3
# Model of a 16-bit source resampled by MPD (soxr, float) and delivered to
# the loopback: Linux (float->S32) vs FreeBSD (float->S16 truncation, then
# virtual_oss S16->S32 expansion noise).  Pure-tone worst case.  See
# doc/VIRTUAL-OSS-QUALITY-STUDY.md section 5.  Run: python3 -I <this file>

import numpy as np
fs=192000; N=1<<20
# voss noise as before (vectorised by precomputing sequence)
p=0xFFFF1D; s=1; nz=np.empty(N,np.int64)
for i in range(N):
    t=s+(p if s&1 else 0); t//=2; s=t
    t^=0x800000
    if t&0x800000: t|=-0x800000
    nz[i]=(t*128)>>14
t=np.arange(N)/fs
for lvl in (-20,-60):
    f0=fs*round(997*N/fs)/N           # bin-centred tone
    xf=(10**(lvl/20))*np.sin(2*np.pi*f0*t).astype(np.float32)  # MPD float after soxr
    lin = np.clip((xf.astype(np.float64)*2**31).astype(np.int64),-2**31+1,2**31-1)   # Linux: float->S32 trunc
    s16 = np.clip((xf.astype(np.float64)*2**15).astype(np.int64),-32767,32767)       # FreeBSD: MPD float->S16 trunc
    fb_trunc = s16*65536
    fb = fb_trunc+nz                                                                    # + virtual_oss expansion noise
    w=np.blackman(N); ref=xf.astype(np.float64)*2**31
    def report(name,y):
        e=(y-ref)/2**31
        E=np.abs(np.fft.rfft(e*w))**2; f=np.fft.rfftfreq(N,1/fs)
        band=(f>=20)&(f<=20000)
        # harmonic levels of the output itself
        Y=np.abs(np.fft.rfft(y/2**31*w)); Y/=Y.max(); Y=20*np.log10(Y+1e-30)
        k0=int(round(f0*N/fs)); harm=[Y[k0*h-3:k0*h+4].max() for h in (2,3,5)]
        print(f"  {name:28s} error RMS(20-20k) {10*np.log10(E[band].sum()/E.sum()*np.mean(e**2)+1e-300):7.1f} dBFS"
              f"  H2/H3/H5 rel. tone {harm[0]:6.1f} {harm[1]:6.1f} {harm[2]:6.1f} dB")
    print(f"tone {lvl} dBFS @ {f0:.1f} Hz, fs {fs}")
    report("Linux  S32 (aloop)",lin)
    report("FreeBSD MPD->S16 only",fb_trunc)
    report("FreeBSD S16 + voss noise",fb)
