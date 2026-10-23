/*
 * Isolated virtual_oss loopback probe. Run against a separate -f /dev/null
 * instance with -d dsp.omdrcauditplay -L dsp.omdrcauditloop and -b 32.
 * Argument 16 or 32 selects the producer's OSS format. No DAC is opened.
 */
#include <sys/ioctl.h>
#include <sys/soundcard.h>
#include <sys/wait.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc != 2 || (argv[1][0] != '1' && argv[1][0] != '3')) {
        fprintf(stderr, "usage: %s 16|32\n", argv[0]); return 2;
    }
    int use32 = argv[1][0] == '3';
    pid_t child = fork();
    if (child < 0) { perror("fork"); return 2; }
    if (child == 0) {
        int fd = open("/dev/dsp.omdrcauditloop", O_RDONLY);
        if (fd < 0) { perror("open loop"); _exit(2); }
        int fmt = AFMT_S32_LE, channels = 2, rate = 44100;
        if (ioctl(fd, SNDCTL_DSP_SETFMT, &fmt) || fmt != AFMT_S32_LE ||
            ioctl(fd, SNDCTL_DSP_CHANNELS, &channels) || channels != 2 ||
            ioctl(fd, SNDCTL_DSP_SPEED, &rate) || rate != 44100) { perror("loop format"); _exit(2); }
        int32_t buf[2048]; long matched = 0, changed = 0; int32_t min = INT32_MAX, max = INT32_MIN;
        for (int iter = 0; iter < 10000 && matched < 20000; ++iter) {
            ssize_t n = read(fd, buf, sizeof(buf));
            if (n <= 0) { perror("read loop"); _exit(2); }
            for (size_t i = 0; i < (size_t)n / 4; ++i) {
                int32_t residual = buf[i] - (10000 << 16);
                if (residual < -65536 || residual > 65536) continue;
                ++matched; if (residual) ++changed;
                if (residual < min) min = residual;
                if (residual > max) max = residual;
            }
        }
        printf("client S%d -> loop S32: matched=%ld changed=%ld residual_min=%d residual_max=%d\n", use32 ? 32 : 16, matched, changed, min, max);
        fflush(stdout); _exit(matched >= 20000 && ((use32 && changed == 0) || (!use32 && changed > 0)) ? 0 : 1);
    }
    usleep(20000);
    int fd = open("/dev/dsp.omdrcauditplay", O_WRONLY);
    if (fd < 0) { perror("open play"); return 2; }
    int fmt = use32 ? AFMT_S32_LE : AFMT_S16_LE, channels = 2, rate = 44100;
    if (ioctl(fd, SNDCTL_DSP_SETFMT, &fmt) || fmt != (use32 ? AFMT_S32_LE : AFMT_S16_LE) ||
        ioctl(fd, SNDCTL_DSP_CHANNELS, &channels) || channels != 2 ||
        ioctl(fd, SNDCTL_DSP_SPEED, &rate) || rate != 44100) { perror("play format"); return 2; }
    int32_t buf[1024]; if (use32) { for (size_t i = 0; i < 1024; ++i) buf[i] = 10000 << 16; } else { int16_t *b = (int16_t *)buf; for (size_t i = 0; i < 2048; ++i) b[i] = 10000; }
    for (int i = 0; i < 80; ++i) {
        unsigned char *ptr = (unsigned char *)buf; size_t left = sizeof(buf);
        while (left) { ssize_t n = write(fd, ptr, left); if (n <= 0) { perror("write"); return 2; } ptr += n; left -= n; }
    }
    close(fd); int status; waitpid(child, &status, 0);
    return WIFEXITED(status) ? WEXITSTATUS(status) : 2;
}
