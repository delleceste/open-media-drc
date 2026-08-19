/*
 * omdrc-cdin — logging.
 *
 * Levels are ordered; the configured level admits everything at or below it.
 * Output goes to stderr and, when -l was given, to a log file as well, so a
 * foreground run and a daemon(8) run produce the same record.
 *
 * Not named LOG_ERR/LOG_INFO on purpose: those are <syslog.h> macros, and a
 * future syslog backend should not have to rename every call site.
 */
#pragma once

enum cdin_loglevel {
	CDL_ERR = 0,
	CDL_WARN,
	CDL_INFO,
	CDL_DEBUG,
};

/* path may be NULL (stderr only).  Returns 0, or -1 if the file could not be
   opened — logging still works on stderr in that case. */
int  cdin_log_init(const char *path, enum cdin_loglevel level);
void cdin_log_close(void);
void cdin_log_setlevel(enum cdin_loglevel level);

void cdin_log(enum cdin_loglevel level, const char *fmt, ...)
	__attribute__((format(printf, 2, 3)));

#define log_err(...)   cdin_log(CDL_ERR,   __VA_ARGS__)
#define log_warn(...)  cdin_log(CDL_WARN,  __VA_ARGS__)
#define log_info(...)  cdin_log(CDL_INFO,  __VA_ARGS__)
#define log_debug(...) cdin_log(CDL_DEBUG, __VA_ARGS__)
