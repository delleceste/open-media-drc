#include <errno.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

#include "log.h"

static FILE *logfile;
static enum cdin_loglevel loglevel = CDL_INFO;
static pthread_mutex_t logmu = PTHREAD_MUTEX_INITIALIZER;

static const char *const levelname[] = {
	[CDL_ERR] = "ERR", [CDL_WARN] = "WRN",
	[CDL_INFO] = "INF", [CDL_DEBUG] = "DBG",
};

int
cdin_log_init(const char *path, enum cdin_loglevel level)
{
	loglevel = level;
	if (path == NULL)
		return 0;
	if ((logfile = fopen(path, "ae")) == NULL) {
		fprintf(stderr, "cdin: cannot open log file %s: %s\n",
		    path, strerror(errno));
		return -1;
	}
	/* Line buffering: a tail -f on the log must show progress live, and an
	   abrupt kill must not lose the last stats line. */
	setvbuf(logfile, NULL, _IOLBF, 0);
	return 0;
}

void
cdin_log_setlevel(enum cdin_loglevel level)
{
	loglevel = level;
}

void
cdin_log_close(void)
{
	pthread_mutex_lock(&logmu);
	if (logfile != NULL) {
		fclose(logfile);
		logfile = NULL;
	}
	pthread_mutex_unlock(&logmu);
}

void
cdin_log(enum cdin_loglevel level, const char *fmt, ...)
{
	char stamp[32], msg[1024];
	struct timespec ts;
	struct tm tm;
	va_list ap;

	if (level > loglevel)
		return;

	va_start(ap, fmt);
	vsnprintf(msg, sizeof(msg), fmt, ap);
	va_end(ap);

	clock_gettime(CLOCK_REALTIME, &ts);
	localtime_r(&ts.tv_sec, &tm);
	strftime(stamp, sizeof(stamp), "%Y-%m-%d %H:%M:%S", &tm);

	pthread_mutex_lock(&logmu);
	fprintf(stderr, "%s.%03ld [%s] %s\n", stamp, ts.tv_nsec / 1000000,
	    levelname[level], msg);
	if (logfile != NULL)
		fprintf(logfile, "%s.%03ld [%s] %s\n", stamp,
		    ts.tv_nsec / 1000000, levelname[level], msg);
	pthread_mutex_unlock(&logmu);
}
