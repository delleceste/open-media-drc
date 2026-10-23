#!/usr/bin/env python3
# Gain and L/R phase of libsoxr 44.1 -> 192 kHz with the recipe MPD uses for
# quality "very high" (SOXR_VHQ = 6, FLOAT32_I).  See
# doc/VIRTUAL-OSS-QUALITY-STUDY.md section 9.
# Run: python3 -I <this file> /path/to/libsoxr.so.0 [recipe]

import ctypes as C, numpy as np, sys
lib=C.CDLL(sys.argv[1])
class IO(C.Structure): _fields_=[("itype",C.c_int),("otype",C.c_int),("scale",C.c_double),("e",C.c_void_p),("flags",C.c_ulong)]
class Q(C.Structure): _fields_=[("precision",C.c_double),("phase_response",C.c_double),("passband_end",C.c_double),("stopband_begin",C.c_double),("e",C.c_void_p),("flags",C.c_ulong)]
lib.soxr_io_spec.restype=IO; lib.soxr_io_spec.argtypes=[C.c_int,C.c_int]
lib.soxr_quality_spec.restype=Q; lib.soxr_quality_spec.argtypes=[C.c_ulong,C.c_ulong]
lib.soxr_oneshot.restype=C.c_char_p
lib.soxr_oneshot.argtypes=[C.c_double,C.c_double,C.c_uint,C.c_void_p,C.c_size_t,C.POINTER(C.c_size_t),C.c_void_p,C.c_size_t,C.POINTER(C.c_size_t),C.POINTER(IO),C.POINTER(Q),C.c_void_p]
SOXR_FLOAT32_I=0; SOXR_VHQ=int(sys.argv[2]) if len(sys.argv)>2 else 6
io=lib.soxr_io_spec(SOXR_FLOAT32_I,SOXR_FLOAT32_I); q=lib.soxr_quality_spec(SOXR_VHQ,0)
print("VHQ spec: precision %.0f phase %.0f passband_end %.4f stopband_begin %.4f"%(q.precision,q.phase_response,q.passband_end,q.stopband_begin))
fi,fo=44100,192000; N=fi*4
t=np.arange(N)/fi
print(" freq Hz   L gain dB    R gain dB   L-R delay (samples@192k)")
for f in (1000,5000,10000,15000,18000,19000,20000,20500,21000,21500):
    L=0.5*np.sin(2*np.pi*f*t); R=0.5*np.sin(2*np.pi*f*t+0.0)
    x=np.empty(2*N,np.float32); x[0::2]=L; x[1::2]=R
    olen=int(N*fo/fi)+1000; y=np.zeros(2*olen,np.float32); od=C.c_size_t(); idn=C.c_size_t()
    err=lib.soxr_oneshot(fi,fo,2,x.ctypes.data,N,C.byref(idn),y.ctypes.data,olen,C.byref(od),C.byref(io),C.byref(q),None)
    assert err is None, err
    y=y[:2*od.value].reshape(-1,2).astype(float); y=y[fo//2:-fo//2]   # drop edges
    tt=np.arange(len(y))/fo
    A=np.c_[np.sin(2*np.pi*f*tt),np.cos(2*np.pi*f*tt)]
    res=[]
    for ch in (0,1):
        c,_,_,_=np.linalg.lstsq(A,y[:,ch],rcond=None); res.append(c)
    g=[20*np.log10(np.hypot(*c)/0.5) for c in res]
    ph=[np.arctan2(c[1],c[0]) for c in res]
    d=(ph[0]-ph[1])/(2*np.pi*f)*fo
    print("%6d   %+9.5f   %+9.5f   %+.2e"%(f,g[0],g[1],d))
