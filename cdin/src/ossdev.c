#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/soundcard.h>
#include <unistd.h>

#include "cdin.h"
#include "log.h"
#include "ossdev.h"

/* log2 of a power of two, or -1 if v is not one. */
static int
log2_exact(size_t v)
{
	int lg = 0;

	if (v == 0 || (v & (v - 1)) != 0)
		return -1;
	while (v > 1) {
		v >>= 1;
		lg++;
	}
	return lg;
}

/* log2 of the largest power of two that is <= v, or -1 for v == 0. */
static int
log2_floor(size_t v)
{
	int lg = -1;

	while (v != 0) {
		v >>= 1;
		lg++;
	}
	return lg;
}

static int
afmt_for_bits(int bits)
{
	switch (bits) {
	case 16: return AFMT_S16_LE;
	case 24: return AFMT_S24_LE;	/* packed 3-byte frames */
	case 32: return AFMT_S32_LE;
	default: return -1;
	}
}

static size_t
bytes_for_bits(int bits)
{
	return bits == 24 ? 3 : (size_t)bits / 8;
}

int
ossdev_open(struct ossdev *d, const char *path, bool capture, int rate,
    int channels, int bits, size_t period_frames, char *err, size_t errsz)
{
	int fmt, want, n, lg;
	size_t period_bytes;

	memset(d, 0, sizeof(*d));
	d->fd = -1;
	snprintf(d->path, sizeof(d->path), "%s", path);
	d->capture = capture;
	d->rate = rate;
	d->channels = channels;
	d->bits = bits;
	d->frame_bytes = bytes_for_bits(bits) * (size_t)channels;
	d->period_frames = period_frames;

	if ((fmt = afmt_for_bits(bits)) == -1) {
		snprintf(err, errsz, "unsupported sample width %d (use 16, 24 or 32)",
		    bits);
		return -1;
	}

	period_bytes = period_frames * d->frame_bytes;
	if (period_bytes < 16) {
		snprintf(err, errsz, "period of %zu frames is only %zu bytes",
		    period_frames, period_bytes);
		return -1;
	}
	/*
	 * SNDCTL_DSP_SETFRAGMENT encodes the fragment size as a power of two in
	 * BYTES, so the period we want is not always expressible: a 24-bit
	 * stereo frame is 6 bytes, and 6 * N is never a power of two for any N.
	 * That is not a reason to refuse the device — the fragment is the
	 * device's interrupt granularity, not our transfer size.  Ask for the
	 * largest power of two that still fits inside the period: the device
	 * then wakes us at least as often as we read, and the read size is
	 * unchanged.  The period the device actually settled on is reported
	 * back through SNDCTL_DSP_GETBLKSIZE below.
	 */
	if ((lg = log2_exact(period_bytes)) == -1) {
		lg = log2_floor(period_bytes);
		log_debug("%s: period of %zu frames is %zu bytes at %d-bit, "
		    "which no fragment size can express; asking for %d bytes",
		    path, period_frames, period_bytes, bits, 1 << lg);
	}

	/* O_NONBLOCK is deliberately NOT used: both threads want to be paced by
	   the device, and blocking transfers are the pacing mechanism. */
	d->fd = open(path, capture ? O_RDONLY : O_WRONLY);
	if (d->fd == -1) {
		snprintf(err, errsz, "%s", strerror(errno));
		return -1;
	}

	/* Fragment size first, as BruteFIR does: some drivers latch the buffer
	   layout on the first format-setting ioctl. */
	n = (0x7FFF << 16) | lg;
	if (ioctl(d->fd, SNDCTL_DSP_SETFRAGMENT, &n) == -1) {
		snprintf(err, errsz, "SNDCTL_DSP_SETFRAGMENT: %s", strerror(errno));
		goto fail;
	}

	want = fmt;
	n = want;
	if (ioctl(d->fd, SNDCTL_DSP_SETFMT, &n) == -1) {
		snprintf(err, errsz, "SNDCTL_DSP_SETFMT: %s", strerror(errno));
		goto fail;
	}
	if (n != want) {
		snprintf(err, errsz, "device rejected %d-bit samples", bits);
		goto fail;
	}

	n = channels;
	if (ioctl(d->fd, SNDCTL_DSP_CHANNELS, &n) == -1) {
		snprintf(err, errsz, "SNDCTL_DSP_CHANNELS: %s", strerror(errno));
		goto fail;
	}
	if (n != channels) {
		snprintf(err, errsz, "device offered %d channels, not %d", n, channels);
		goto fail;
	}

	n = rate;
	if (ioctl(d->fd, SNDCTL_DSP_SPEED, &n) == -1) {
		snprintf(err, errsz, "SNDCTL_DSP_SPEED: %s", strerror(errno));
		goto fail;
	}
	/* Accept the same 1% tolerance BruteFIR accepts: a device may report a
	   nearby rate it will happily run at.  Anything wider is a real mismatch
	   (and would look like enormous drift later). */
	if (n != rate && !((int)(rate * 0.99) < n && (int)(rate / 0.99) > n)) {
		snprintf(err, errsz, "device set %d Hz, not %d Hz", n, rate);
		goto fail;
	}
	if (n != rate)
		log_warn("%s: device reports %d Hz for a requested %d Hz",
		    path, n, rate);

	if (ioctl(d->fd, SNDCTL_DSP_GETBLKSIZE, &n) == -1) {
		snprintf(err, errsz, "SNDCTL_DSP_GETBLKSIZE: %s", strerror(errno));
		goto fail;
	}
	d->hw_period_frames = (size_t)n / d->frame_bytes;

	log_info("%s: opened for %s, %d Hz %d-bit %dch, period %zu frames "
	    "(device %zu)", path, capture ? "capture" : "playback", rate, bits,
	    channels, period_frames, d->hw_period_frames);
	return 0;

fail:
	close(d->fd);
	d->fd = -1;
	return -1;
}

void
ossdev_close(struct ossdev *d)
{
	if (d->fd != -1) {
		close(d->fd);
		d->fd = -1;
		log_debug("%s: closed", d->path);
	}
}

ssize_t
ossdev_read_full(struct ossdev *d, void *buf, size_t n)
{
	size_t done = 0;
	ssize_t rc;

	while (done < n) {
		rc = read(d->fd, (char *)buf + done, n - done);
		if (rc > 0) {
			done += (size_t)rc;
			continue;
		}
		if (rc == 0)			/* EOF: device or carrier gone */
			return (ssize_t)done;
		if (errno == EINTR) {
			/* Resume unless we are shutting down: abandoning a
			   partial transfer would desynchronise the frame
			   boundary for every later read. */
			if (!cdin_stop && !cdin_io_abort)
				continue;
			return -1;
		}
		return -1;
	}
	return (ssize_t)done;
}

ssize_t
ossdev_write_full(struct ossdev *d, const void *buf, size_t n)
{
	size_t done = 0;
	ssize_t rc;

	while (done < n) {
		rc = write(d->fd, (const char *)buf + done, n - done);
		if (rc > 0) {
			done += (size_t)rc;
			continue;
		}
		if (rc == 0)
			return (ssize_t)done;
		if (errno == EINTR) {
			if (!cdin_stop && !cdin_io_abort)
				continue;
			return -1;
		}
		return -1;
	}
	return (ssize_t)done;
}
