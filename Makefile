# Install target for the PACKAGED (FreeBSD port / pkg) layout.  POSIX make —
# works with both BSD make and GNU make.
#
# Run-from-repo users do NOT need this: keep using ./install.sh, which renders
# the *.in templates in place and leaves everything in the checkout.
#
# Layout installed here (hier(7)):
#   $(PREFIX)/bin/omdrc, omdrc-status         thin wrappers
#   $(PREFIX)/libexec/omdrc/                  drc.sh, drc-status.sh, scripts/
#   $(PREFIX)/etc/open-media-drc/             omdrc.conf.sample,
#                                             configs/flat/*.conf,
#                                             brutefir_defaults.conf.sample
#   $(PREFIX)/etc/devd/                       usb-audio-drc.conf.sample
#   $(PREFIX)/share/examples/open-media-drc/  mpd/upmpdcli config templates
#   $(PREFIX)/share/doc/open-media-drc/       docs
#
# rc.d scripts are NOT installed here — the port ships them via USE_RC_SUBR
# (see freebsd/audio/open-media-drc/files/).  .sample suffixes let the port's
# @sample plist keyword manage user-edited copies.

PREFIX?=	/usr/local
DESTDIR?=

BINDIR=		$(DESTDIR)$(PREFIX)/bin
LIBEXECDIR=	$(DESTDIR)$(PREFIX)/libexec/omdrc
ETCDIR=		$(DESTDIR)$(PREFIX)/etc/open-media-drc
DEVDDIR=	$(DESTDIR)$(PREFIX)/etc/devd
EXAMPLESDIR=	$(DESTDIR)$(PREFIX)/share/examples/open-media-drc
DOCSDIR=	$(DESTDIR)$(PREFIX)/share/doc/open-media-drc

INSTALL_SCRIPT=	install -m 755
INSTALL_DATA=	install -m 644

all:
	@echo "Nothing to build.  Targets: install [DESTDIR=... PREFIX=...]"
	@echo "Run-from-repo setup: ./install.sh (renders *.in templates in place)."

install:
	mkdir -p $(BINDIR) $(LIBEXECDIR)/scripts $(ETCDIR)/configs/flat \
	         $(DEVDDIR) $(EXAMPLESDIR) $(DOCSDIR)
	$(INSTALL_SCRIPT) drc.sh drc-status.sh $(LIBEXECDIR)
	$(INSTALL_SCRIPT) scripts/REW2raw.sh scripts/REW2raw-all-rates.sh \
	                  scripts/verify-bitperfect.sh scripts/headroom_calc.py \
	                  $(LIBEXECDIR)/scripts
	printf '#!/bin/sh\nexec %s/libexec/omdrc/drc.sh "$$@"\n' "$(PREFIX)" \
	    > $(BINDIR)/omdrc
	printf '#!/bin/sh\nexec %s/libexec/omdrc/drc-status.sh "$$@"\n' "$(PREFIX)" \
	    > $(BINDIR)/omdrc-status
	chmod 755 $(BINDIR)/omdrc $(BINDIR)/omdrc-status
	$(INSTALL_DATA) omdrc.conf.sample $(ETCDIR)/omdrc.conf.sample
	$(INSTALL_DATA) configs/flat/brutefir-44100.conf \
	                configs/flat/brutefir-48000.conf \
	                configs/flat/brutefir-88200.conf \
	                configs/flat/brutefir-96000.conf \
	                configs/flat/brutefir-192000.conf \
	                $(ETCDIR)/configs/flat
	$(INSTALL_DATA) brutefir_defaults.conf $(ETCDIR)/brutefir_defaults.conf.sample
	$(INSTALL_DATA) etc/devd/usb-audio-drc.conf $(DEVDDIR)/usb-audio-drc.conf.sample
	$(INSTALL_DATA) mpd/musicpd.conf.in mpd/mpd.conf.in $(EXAMPLESDIR)
	$(INSTALL_DATA) upmpdcli/upmpdcli.conf.in $(EXAMPLESDIR)
	$(INSTALL_DATA) README.md FILTERS_AND_DRC.md \
	                doc/BIT-PERFECT-VERIFICATION.md doc/GLITCH-DETECTION.md \
	                $(DOCSDIR)

.PHONY: all install
