#include <string.h>

#include "convert.h"

bool
convert_can_widen(int src_bits, int dst_bits)
{
	if (src_bits == dst_bits)
		return false;
	if (src_bits == 16 && (dst_bits == 24 || dst_bits == 32))
		return true;
	if (src_bits == 24 && dst_bits == 32)
		return true;
	return false;
}

void
convert_widen(const void *src, int src_bits, void *dst, int dst_bits,
    size_t nsamples)
{
	const unsigned char *s = src;
	unsigned char *d = dst;
	size_t i;

	if (src_bits == 16 && dst_bits == 32) {
		for (i = 0; i < nsamples; i++, s += 2, d += 4) {
			d[0] = 0; d[1] = 0; d[2] = s[0]; d[3] = s[1];
		}
	} else if (src_bits == 16 && dst_bits == 24) {
		for (i = 0; i < nsamples; i++, s += 2, d += 3) {
			d[0] = 0; d[1] = s[0]; d[2] = s[1];
		}
	} else if (src_bits == 24 && dst_bits == 32) {
		for (i = 0; i < nsamples; i++, s += 3, d += 4) {
			d[0] = 0; d[1] = s[0]; d[2] = s[1]; d[3] = s[2];
		}
	} else {
		/* convert_can_widen() gates this; nothing else is lossless.
		   Silence is the only safe answer, and it must be the right
		   number of bytes: nsamples is a sample count, not a size. */
		memset(dst, 0, nsamples * (size_t)(dst_bits == 24 ? 3 : dst_bits / 8));
	}
}
