/*
 * Standalone audit of virtual_oss's integer mixer, not a CUSE runtime test.
 * See doc/FREEBSD-AUDIO-SOURCE-AUDIT-2026-09-11.md for scope and results.
 * Build: cc -O2 -Wall -Wextra -Werror virtual-oss-noise.c -lm -o /tmp/voss-noise-audit
 * vclient_noise is copied from FreeBSD /usr/src/usr.sbin/virtual_oss/
 * virtual_oss/main.c:191-224; mix_sample implements the unity-gain branch
 * of virtual_oss.c:139-202 and the shift calculation at :545-551.
 *
 * Copyright (c) 2012-2022 Hans Petter Selasky
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions
 * are met:
 * 1. Redistributions of source code must retain the above copyright
 *    notice, this list of conditions and the following disclaimer.
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND
 * ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
 * IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
 * ARE DISCLAIMED. IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE
 * FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
 * DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
 * OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
 * HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
 * LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
 * OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
 * SUCH DAMAGE.
 */
#include <inttypes.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>

#define VVOLUME_UNIT_SHIFT 7
#define __predict_false(x) (x)

int64_t
vclient_noise(uint32_t *pnoise, int64_t volume, int8_t shift)
{
	const uint32_t prime = 0xFFFF1DU;
	int64_t temp;

	/* compute next noise sample */
	temp = *pnoise;
	if (temp & 1)
		temp += prime;
	temp /= 2;
	*pnoise = temp;

	/* unsigned to signed conversion */
	temp ^= 0x800000ULL;
	if (temp & 0x800000U)
		temp |= -0x800000ULL;

	/* properly amplify */
	temp *= volume;

	/* bias shift */
	shift -= 23 + VVOLUME_UNIT_SHIFT;

	/* range check and shift noise */
	if (__predict_false(shift < -63 || shift > 63))
		temp = 0;
	else if (shift < 0)
		temp >>= -shift;
	else
		temp <<= shift;

	return (temp);
}

static int64_t mix_sample(int64_t input, unsigned bits, uint32_t *noise)
{
    const int shift_orig = 32 - bits;
    const int shift = shift_orig - VVOLUME_UNIT_SHIFT;
    const int64_t volume = 1 << VVOLUME_UNIT_SHIFT;
    int64_t output = input * volume;
    /* Multiplication avoids negative signed-left-shift UB in the harness. */
    output = shift < 0 ? output >> -shift : output * (INT64_C(1) << shift);
    if (shift_orig > 0)
        output += vclient_noise(noise, volume, shift_orig);
    return output;
}

int main(void)
{
    const unsigned widths[] = {16, 24, 32};
    const unsigned n = 10000000;
    for (unsigned b = 0; b < 3; ++b) {
        uint32_t state = 1;
        int64_t minimum = INT64_MAX, maximum = INT64_MIN;
        long double sum = 0, squares = 0;
        unsigned nonzero = 0;
        for (unsigned i = 0; i < n; ++i) {
            const int64_t out = mix_sample(0, widths[b], &state);
            if (out < minimum) minimum = out;
            if (out > maximum) maximum = out;
            sum += out;
            squares += (long double)out * out;
            nonzero += out != 0;
        }
        printf("S%u -> S32 silence: min=%" PRId64 " max=%" PRId64
               " mean=%.5Lf rms=%.5Lf dBFS=%.5Lf nonzero=%u/%u\n",
               widths[b], minimum, maximum, sum / n, sqrtl(squares / n),
               20 * log10l(sqrtl(squares / n) / 2147483648.0L), nonzero, n);
    }
    return 0;
}
