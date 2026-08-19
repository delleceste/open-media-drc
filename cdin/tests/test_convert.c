/*
 * Width-widening tests.
 *
 * This is the only arithmetic-free transform in the data path, and that is
 * exactly why it needs testing: a wrong byte index does not crash, does not
 * warn, and does not show up in the stats — it silently rescales or byte-swaps
 * every sample on its way to the DAC.
 *
 * The strong check is the VALUE check.  Left-justification means the widened
 * sample must equal the source sample shifted up by the width difference, so
 * each case is verified by reconstructing the little-endian integer and
 * comparing against src << (dst_bits - src_bits).  That holds regardless of
 * how the bytes are placed, which is the property the daemon actually depends
 * on: same audio, same scale, no rounding, no dither.
 */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "convert.h"

static int failures;

#define CHECK(cond, ...) do {                                            \
	if (!(cond)) {                                                   \
		printf("FAIL %s:%d: ", __func__, __LINE__);              \
		printf(__VA_ARGS__);                                     \
		printf("\n");                                            \
		failures++;                                              \
	}                                                                \
} while (0)

#define GUARD 0xA5		/* canary byte around every destination */

/* Signed little-endian reader for 2, 3 and 4 byte samples. */
static int32_t
rd_le(const unsigned char *p, int bytes)
{
	uint32_t v = 0;
	int i;

	for (i = 0; i < bytes; i++)
		v |= (uint32_t)p[i] << (8 * i);
	/* sign-extend from the top of the sample width */
	if (v & (1u << (8 * bytes - 1)))
		v |= ~((1u << (8 * bytes - 1)) | ((1u << (8 * bytes - 1)) - 1));
	return (int32_t)v;
}

static void
wr_le(unsigned char *p, int bytes, int32_t v)
{
	int i;

	for (i = 0; i < bytes; i++)
		p[i] = (unsigned char)((uint32_t)v >> (8 * i));
}

static int
width_bytes(int bits)
{
	return bits == 24 ? 3 : bits / 8;
}

/*
 * Feed a set of representative samples through one conversion and assert both
 * the value relation and that nothing outside the destination was touched.
 */
static void
check_widen(int src_bits, int dst_bits)
{
	static const int32_t vals[] = {
		0, 1, -1, 2, -2, 0x1234, -0x1234, 0x7F7F, -0x7F80
	};
	const size_t n = sizeof(vals) / sizeof(vals[0]);
	int sb = width_bytes(src_bits), db = width_bytes(dst_bits);
	int shift = dst_bits - src_bits;
	unsigned char src[64], dst[128];
	size_t i;

	CHECK(convert_can_widen(src_bits, dst_bits),
	    "%d -> %d should be a legal widening", src_bits, dst_bits);

	memset(dst, GUARD, sizeof(dst));
	/* Every value fits in 16 bits, so the same table exercises both source
	   widths and the expected result can be read back from the source. */
	for (i = 0; i < n; i++)
		wr_le(src + i * (size_t)sb, sb, vals[i]);

	convert_widen(src, src_bits, dst, dst_bits, n);

	for (i = 0; i < n; i++) {
		int32_t in = rd_le(src + i * (size_t)sb, sb);
		int32_t out = rd_le(dst + i * (size_t)db, db);

		CHECK(out == (int32_t)((uint32_t)in << shift),
		    "%d->%d sample %zu: in %d, out %d, expected %d",
		    src_bits, dst_bits, i, in, out,
		    (int32_t)((uint32_t)in << shift));
	}

	/* Every byte past the converted region must be untouched: an off-by-one
	   in the destination stride would show up here and nowhere else. */
	for (i = n * (size_t)db; i < sizeof(dst); i++)
		CHECK(dst[i] == GUARD, "%d->%d wrote past the destination at "
		    "byte %zu", src_bits, dst_bits, i);
}

static void
test_value_relation(void)
{
	check_widen(16, 32);
	check_widen(16, 24);
	check_widen(24, 32);
}

/* The exact byte placement, stated independently of the value check so that a
   change to one has to be a deliberate change to both. */
static void
test_byte_placement(void)
{
	const unsigned char s16[2] = { 0x34, 0x12 };	/* 0x1234 LE */
	const unsigned char s24[3] = { 0x56, 0x34, 0x12 };
	unsigned char d[4];

	convert_widen(s16, 16, d, 32, 1);
	CHECK(d[0] == 0x00 && d[1] == 0x00 && d[2] == 0x34 && d[3] == 0x12,
	    "16->32 placement %02x %02x %02x %02x", d[0], d[1], d[2], d[3]);

	convert_widen(s16, 16, d, 24, 1);
	CHECK(d[0] == 0x00 && d[1] == 0x34 && d[2] == 0x12,
	    "16->24 placement %02x %02x %02x", d[0], d[1], d[2]);

	convert_widen(s24, 24, d, 32, 1);
	CHECK(d[0] == 0x00 && d[1] == 0x56 && d[2] == 0x34 && d[3] == 0x12,
	    "24->32 placement %02x %02x %02x %02x", d[0], d[1], d[2], d[3]);
}

/* Full-scale must survive: this is where a signed/unsigned slip would show. */
static void
test_extremes(void)
{
	const unsigned char neg_full[2] = { 0x00, 0x80 };	/* -32768 */
	const unsigned char pos_full[2] = { 0xFF, 0x7F };	/*  32767 */
	unsigned char d[4];

	convert_widen(neg_full, 16, d, 32, 1);
	CHECK(rd_le(d, 4) == INT32_MIN, "-full scale became %d", rd_le(d, 4));

	convert_widen(pos_full, 16, d, 32, 1);
	CHECK(rd_le(d, 4) == 32767 * 65536, "+full scale became %d", rd_le(d, 4));
}

/* An interleaved stereo block is just 2n samples; nothing may be reordered. */
static void
test_interleave_preserved(void)
{
	unsigned char src[4 * 2 * 2];	/* 4 frames x 2ch x 16-bit */
	unsigned char dst[4 * 2 * 4];
	size_t i;

	for (i = 0; i < 8; i++)
		wr_le(src + i * 2, 2, (int32_t)(i + 1) * (i % 2 ? -1 : 1));

	convert_widen(src, 16, dst, 32, 8);
	for (i = 0; i < 8; i++) {
		int32_t want = rd_le(src + i * 2, 2);

		CHECK(rd_le(dst + i * 4, 4) == want * 65536,
		    "sample %zu moved: %d vs %d", i, rd_le(dst + i * 4, 4),
		    want * 65536);
	}
}

/*
 * The policy, not the mechanics: equal widths must report "no", so the caller
 * keeps its memcpy path, and narrowing must never be offered — it cannot be
 * done without truncation or dither, which is what this daemon exists to
 * avoid.
 */
static void
test_can_widen_policy(void)
{
	CHECK(!convert_can_widen(16, 16), "16->16 is a memcpy, not a widening");
	CHECK(!convert_can_widen(24, 24), "24->24 is a memcpy, not a widening");
	CHECK(!convert_can_widen(32, 32), "32->32 is a memcpy, not a widening");

	CHECK(!convert_can_widen(32, 24), "32->24 narrows");
	CHECK(!convert_can_widen(32, 16), "32->16 narrows");
	CHECK(!convert_can_widen(24, 16), "24->16 narrows");

	CHECK(convert_can_widen(16, 24), "16->24 widens");
	CHECK(convert_can_widen(16, 32), "16->32 widens");
	CHECK(convert_can_widen(24, 32), "24->32 widens");

	/* Widths the WAV parser and OSS never produce must not sneak through. */
	CHECK(!convert_can_widen(8, 16), "8-bit is not a supported source");
	CHECK(!convert_can_widen(20, 32), "20-bit is not a supported source");
	CHECK(!convert_can_widen(16, 20), "20-bit is not a supported target");
}

/* The gated fallback: unsupported pairs must emit silence of the right size,
   not garbage and not a wrongly sized memset. */
static void
test_unsupported_pair_is_silent(void)
{
	unsigned char src[8], dst[16];
	size_t i;

	memset(src, 0x5A, sizeof(src));
	memset(dst, GUARD, sizeof(dst));
	convert_widen(src, 32, dst, 24, 4);	/* narrowing: refused */

	for (i = 0; i < 4 * 3; i++)
		CHECK(dst[i] == 0, "byte %zu not silenced: %02x", i, dst[i]);
	for (i = 4 * 3; i < sizeof(dst); i++)
		CHECK(dst[i] == GUARD, "silencing overran at byte %zu", i);
}

static void
test_zero_samples(void)
{
	unsigned char src[2] = { 0xFF, 0xFF }, dst[4];

	memset(dst, GUARD, sizeof(dst));
	convert_widen(src, 16, dst, 32, 0);
	CHECK(dst[0] == GUARD && dst[3] == GUARD,
	    "a zero-sample conversion wrote something");
}

int
main(void)
{
	test_value_relation();
	test_byte_placement();
	test_extremes();
	test_interleave_preserved();
	test_can_widen_policy();
	test_unsupported_pair_is_silent();
	test_zero_samples();

	if (failures == 0)
		printf("all convert tests passed\n");
	return failures != 0;
}
