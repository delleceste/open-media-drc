/*
 * omdrc-cdin — lossless sample-width widening.
 *
 * A CD is 16-bit, and the DRC chain downstream runs S32_LE (virtual_oss -b 32,
 * BruteFIR sample: "S32_LE").  With bitperfect=1 the format feeder is gone by
 * design (feeder_chain.c makes origin and target identical), so the device will
 * not convert for us and the daemon must.
 *
 * Widening is left-justification, which for little-endian PCM is pure byte
 * placement — no arithmetic, so it is provably lossless and has no sign,
 * rounding or overflow behaviour to get wrong:
 *
 *     16 -> 32:  {0, 0, s0, s1}
 *     16 -> 24:  {0, s0, s1}
 *     24 -> 32:  {0, s0, s1, s2}
 *
 * Narrowing is never done: it would need truncation or dither, and silently
 * losing bits is precisely what this daemon exists to avoid.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>

/* True when dst_bits can hold src_bits losslessly.  Equal widths return false:
   the caller should skip conversion entirely and keep the memcpy path. */
bool convert_can_widen(int src_bits, int dst_bits);

void convert_widen(const void *src, int src_bits, void *dst, int dst_bits,
	    size_t nsamples);
