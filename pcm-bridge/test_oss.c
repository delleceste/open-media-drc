/* Live isolated-device tests. The runner owns daemon startup and teardown. */
#include <sys/types.h>
#include <sys/ioctl.h>
#include <sys/soundcard.h>
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
static const char *play_path, *capture_path;
static int writer, reader;
static void pair(void)
{
    writer = open(play_path, O_WRONLY | O_NONBLOCK); assert(writer >= 0);
    reader = open(capture_path, O_RDONLY | O_NONBLOCK); assert(reader >= 0);
}
static void reset_close(void)
{
    assert(ioctl(writer, SNDCTL_DSP_RESET, NULL) == 0);
    close(writer); close(reader);
}
static unsigned char input[1048576], output[1048576];
static void *receive(void *arg)
{
    size_t total = *(size_t *)arg, done = 0;
    while (done < total) {
        ssize_t n = read(reader, output + done, total - done > 1032 ? 1032 : total - done);
        assert(n > 0); done += (size_t)n;
    }
    return NULL;
}
static void noop(int sig) { (void)sig; }
static void *interrupt_read(void *arg)
{
    (void)arg;
    unsigned char buf[8];
    errno = 0;
    assert(read(reader, buf, sizeof(buf)) == -1 && errno == EINTR);
    return NULL;
}
int main(int argc, char **argv)
{
    assert(argc == 3 || argc == 4); play_path = argv[1]; capture_path = argv[2];
    if (argc == 4) {
        bool reading = strcmp(argv[3], "block-read") == 0;
        int fd = open(reading ? capture_path : play_path, reading ? O_RDONLY : O_WRONLY);
        assert(fd >= 0);
        puts("blocking client ready"); fflush(stdout);
        if (reading) assert(read(fd, output, 8) == -1);
        else {
            ssize_t n;
            do { n = write(fd, input, sizeof(input)); } while (n > 0);
            assert(n == -1);
        }
        close(fd);
        puts("active client released by server shutdown");
        return 0;
    }
    pair();
    assert(open(play_path, O_WRONLY | O_NONBLOCK) == -1 && errno == EBUSY);
    assert(open(capture_path, O_RDONLY | O_NONBLOCK) == -1 && errno == EBUSY);
    assert(open(play_path, O_RDWR) == -1);
    int n = AFMT_S16_LE; assert(ioctl(writer, SNDCTL_DSP_SETFMT, &n) == -1);
    n = AFMT_S32_LE; assert(ioctl(writer, SNDCTL_DSP_SETFMT, &n) == 0 && n == AFMT_S32_LE);
    n = 44100; assert(ioctl(writer, SNDCTL_DSP_SPEED, &n) == -1);
    n = 48000; assert(ioctl(reader, SNDCTL_DSP_SPEED, &n) == 0 && n == 48000);
    n = 1; assert(ioctl(reader, SNDCTL_DSP_CHANNELS, &n) == -1);
    n = 2; assert(ioctl(reader, SNDCTL_DSP_CHANNELS, &n) == 0 && n == 2);
    n = (4 << 16) | 8; assert(ioctl(reader, SNDCTL_DSP_SETFRAGMENT, &n) == 0);
    assert(ioctl(reader, SNDCTL_DSP_GETBLKSIZE, &n) == 0 && n == 256);
    unsigned char small[8] = {0,1,2,3,4,5,6,7}, got[8];
    assert(read(reader, got, 8) == -1 && errno == EAGAIN);
    assert(write(writer, small, 3) == 3);
    assert(read(reader, got, 8) == -1 && errno == EAGAIN);
    assert(ioctl(writer, SNDCTL_DSP_SYNC, NULL) == -1); /* incomplete frame */
    assert(write(writer, small + 3, 5) == 5);
    struct pollfd p = {.fd=reader, .events=POLLIN};
    assert(poll(&p, 1, 1000) == 1 && (p.revents & POLLIN));
    assert(read(reader, got, 8) == 8 && memcmp(got, small, 8) == 0);
    assert(ioctl(writer, SNDCTL_DSP_SYNC, NULL) == 0);
    audio_buf_info info;
    assert(ioctl(writer, SNDCTL_DSP_GETOSPACE, &info) == 0);
    size_t capacity = (size_t)info.bytes; assert(capacity <= sizeof(input));
    for (size_t i = 0; i < sizeof(input); i++) input[i] = (unsigned char)((i * 37) ^ (i >> 8));
    size_t done = 0;
    while (done < capacity) {
        ssize_t count = write(writer, input + done, capacity - done);
        assert(count > 0); done += (size_t)count;
    }
    assert(write(writer, small, 8) == -1 && errno == EAGAIN);
    p.fd=writer; p.events=POLLOUT; p.revents=0; assert(poll(&p, 1, 0) == 0);
    assert(ioctl(writer, SNDCTL_DSP_RESET, NULL) == 0);
    assert(read(reader, got, 8) == -1 && errno == EAGAIN);
    assert(fcntl(writer, F_SETFL, 0) == 0 && fcntl(reader, F_SETFL, 0) == 0);
    pthread_t thread;
    size_t total = sizeof(input);
    assert(pthread_create(&thread, NULL, receive, &total) == 0);
    done = 0;
    while (done < total) {
        size_t len = total - done > 997 ? 997 : total - done;
        ssize_t count = write(writer, input + done, len); assert(count > 0);
        done += (size_t)count;
    }
    assert(ioctl(writer, SNDCTL_DSP_SYNC, NULL) == 0);
    pthread_join(thread, NULL);
    assert(memcmp(input, output, total) == 0);
    /* Signals must cancel an empty blocking read without killing the daemon. */
    struct sigaction sa = {.sa_handler=noop}; sigemptyset(&sa.sa_mask);
    sigaction(SIGUSR1, &sa, NULL);
    assert(pthread_create(&thread, NULL, interrupt_read, NULL) == 0);
    usleep(100000); pthread_kill(thread, SIGUSR1); pthread_join(thread, NULL);
    reset_close();
    /* Drain-on-close, including data larger than individual read requests. */
    pair(); fcntl(writer, F_SETFL, 0); fcntl(reader, F_SETFL, 0);
    total = 8192; memset(output, 0, total);
    assert(write(writer, input, total) == (ssize_t)total);
    assert(pthread_create(&thread, NULL, receive, &total) == 0);
    assert(close(writer) == 0); pthread_join(thread, NULL);
    assert(memcmp(input, output, total) == 0); close(reader);
    /* A survivor cannot silently attach to a replacement session. */
    pair(); close(reader);
    assert(write(writer, small, 8) == -1);
    assert(open(capture_path, O_RDONLY | O_NONBLOCK) == -1);
    close(writer);
    for (int i = 0; i < 30; i++) { pair(); reset_close(); }
    puts("OSS: exact negotiation, ownership, partial frames, poll, backpressure, 1 MiB byte comparison, signal, drain and reopen passed");
    return 0;
}
