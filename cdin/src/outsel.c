#include "outsel.h"

#include <string.h>
#include <ctype.h>
#include <stdio.h>

static char *
trim(char *s)
{
	char *end;

	while (*s != '\0' && isspace((unsigned char)*s))
		s++;
	if (*s == '\0')
		return s;
	end = s + strlen(s);
	while (end > s && isspace((unsigned char)end[-1]))
		end--;
	*end = '\0';
	return s;
}

int
outsel_parse(struct outsel *o, const char *list)
{
	char *cursor, *comma, *entry;

	if (o == NULL || list == NULL)
		return -1;
	memset(o, 0, sizeof(*o));
	if (strlen(list) >= sizeof(o->storage))
		return -1;
	memcpy(o->storage, list, strlen(list) + 1);

	cursor = o->storage;
	for (;;) {
		comma = strchr(cursor, ',');
		if (comma != NULL)
			*comma = '\0';
		entry = trim(cursor);
		if (*entry != '\0') {
			if (o->count == OUTSEL_MAX)
				return -1;
			o->path[o->count++] = entry;
		}
		if (comma == NULL)
			break;
		cursor = comma + 1;
	}
	return o->count > 0 ? o->count : -1;
}

const char *
outsel_describe(const struct outsel *o, char *buf, size_t bufsz)
{
	size_t used = 0;
	int i;

	if (bufsz == 0)
		return buf;
	buf[0] = '\0';
	for (i = 0; i < o->count && used < bufsz; i++) {
		int n = snprintf(buf + used, bufsz - used, "%s%s",
		    i == 0 ? "" : " or ", o->path[i]);
		if (n < 0)
			break;
		used += (size_t)n;
	}
	return buf;
}
