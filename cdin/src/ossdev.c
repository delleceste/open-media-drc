#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/soundcard.h>
#include <unistd.h>

#include "cdin.h"
#include "log.h"
#include "ossdev.h"

/* Round an application transfer up to an OSS fragment size.  Transfers may
   span fragments; keeping these concepts separate is what makes packed S24
   stereo (six-byte frames) possible. */
size_t
ossdev_fragment_bytes(size_t period_bytes)
{
	size_t v = 16;

	while (v < period_bytes && v < 65536)
		v <<= 1;
	return v;
}

static int
log2_power(size_t v)
{
	int lg = 0;

	while (v > 1) {
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

	if (period_frames == 0 || period_frames > SIZE_MAX / d->frame_bytes) {
		snprintf(err, errsz, "invalid period of %zu frames", period_frames);
		return -1;
	}
	period_bytes = period_frames * d->frame_bytes;
	lg = log2_power(ossdev_fragment_bytes(period_bytes));

	/* O_NONBLOCK is deliberately NOT used: both threads want to be paced by
	   the device, and blocking transfers are the pacing mechanism. */
	d->fd = open(path, capture ? O_RDONLY : O_WRONLY);
	if (d->fd == -1) {
		snprintf(err, errsz, "%s", strerror(errno));
		return -1;
	}

	/* Fragment size first, as BruteFIR does: some drivers latch the buffer
	   layout on the first format-setting ioctl.  It is independent of the
	   application transfer size; the latter need only stay frame-aligned. */
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
	/* The lead proof assumes nominally equal rates differing only by clock ppm.
	   A percent-scale substitution drains the default lead inside one track. */
	if (n != rate) {
		snprintf(err, errsz, "device set %d Hz, not %d Hz", n, rate);
		goto fail;
	}

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
			if (!cdin_stop && !atomic_load(&cdin_io_abort))
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
			if (!cdin_stop && !atomic_load(&cdin_io_abort) &&
			    !atomic_load(&cdin_output_abort))
				continue;
			return -1;
		}
		return -1;
	}
	return (ssize_t)done;
}
