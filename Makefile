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
#   $(PREFIX)/etc/devd/                       omdrc-audio.conf.sample
#   $(PREFIX)/etc/rc.conf.d/musicpd/           omdrc_audio post-start hook
#   $(PREFIX)/share/examples/open-media-drc/  mpd/upmpdcli config templates
#   $(PREFIX)/share/doc/open-media-drc/       docs
#
# rc.d scripts are NOT installed here — the port ships them via USE_RC_SUBR
# (see freebsd/audio/open-media-drc/files/).  .sample suffixes let the port's
# @sample plist keyword manage user-edited copies.

PREFIX?=	/usr/local
DESTDIR?=
# Interpreter baked into the omdrcctrl launcher; the port passes PYTHON_CMD
# from USES=python:run so the wrapper matches the flavour pkg depends on.
PYTHON_CMD?=	python3

BINDIR=		$(DESTDIR)$(PREFIX)/bin
LIBEXECDIR=	$(DESTDIR)$(PREFIX)/libexec/omdrc
ETCDIR=		$(DESTDIR)$(PREFIX)/etc/open-media-drc
DEVDDIR=	$(DESTDIR)$(PREFIX)/etc/devd
MUSICPDRCCONFDIR=	$(DESTDIR)$(PREFIX)/etc/rc.conf.d/musicpd
EXAMPLESDIR=	$(DESTDIR)$(PREFIX)/share/examples/open-media-drc
DOCSDIR=	$(DESTDIR)$(PREFIX)/share/doc/open-media-drc
CTRLDIR=	$(DESTDIR)$(PREFIX)/share/omdrc-ctrl

INSTALL_SCRIPT=	install -m 755
INSTALL_DATA=	install -m 644

all:
	@echo "Nothing to build.  Targets: install [DESTDIR=... PREFIX=...]"
	@echo "Run-from-repo setup: ./install.sh (renders *.in templates in place)."

install:
	/bin/sh scripts/prepare-musicpd-rc-conf-dir.sh "$(MUSICPDRCCONFDIR)"
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
	$(INSTALL_DATA) etc/devd/omdrc-audio.conf $(DEVDDIR)/omdrc-audio.conf.sample
	$(INSTALL_DATA) etc/rc.conf.d/musicpd/omdrc_audio $(MUSICPDRCCONFDIR)
	$(INSTALL_DATA) mpd/musicpd.conf.in mpd/mpd.conf.in $(EXAMPLESDIR)
	$(INSTALL_DATA) upmpdcli/upmpdcli.conf.in $(EXAMPLESDIR)
	$(INSTALL_DATA) README.md FILTERS_AND_DRC.md \
	                doc/BIT-PERFECT-VERIFICATION.md doc/GLITCH-DETECTION.md \
	                $(DOCSDIR)

# omdrc-ctrl (web control panel) — the port's CTRL option calls this separately,
# so the core install stays dependency-free.  Run-from-repo does NOT use this
# target: it keeps building omdrc-ctrl with CMake into the checkout/~/.local,
# which is unchanged.
#
# Two port rules are honoured here that the CMake flow does not need:
#   - the config is installed as commands.conf.sample (pkg @sample owns the
#     live copy), so a user edit never modifies a packaged file;
#   - no build-host path is baked in — @OMDRC_REPO_DIR@ is replaced by the
#     installed bin/omdrc wrappers rather than a checkout location.
install-ctrl:
	mkdir -p $(CTRLDIR)/templates $(CTRLDIR)/static $(ETCDIR) $(BINDIR) $(DOCSDIR) \
	         $(LIBEXECDIR)/filter-tools
	$(INSTALL_DATA) omdrc-ctrl/src/app.py omdrc-ctrl/src/configuration.py $(CTRLDIR)
	$(INSTALL_DATA) omdrc-ctrl/src/templates/index.html \
	                omdrc-ctrl/src/templates/details.html \
	                omdrc-ctrl/src/templates/filter_response.html \
	                omdrc-ctrl/src/templates/configuration.html \
	                $(CTRLDIR)/templates
	$(INSTALL_DATA) scripts/new_filter_design.py scripts/deploy_filter.py \
	                scripts/remove_filter_design.py scripts/verify_filter_bundle.py \
	                scripts/console_ui.py \
	                scripts/filter_workflow_next.py $(LIBEXECDIR)/filter-tools
	$(INSTALL_SCRIPT) scripts/REW2raw.sh $(LIBEXECDIR)/filter-tools
	$(INSTALL_SCRIPT) scripts/omdrc-config-helper.py $(LIBEXECDIR)/omdrc-config-helper
	$(INSTALL_DATA) omdrc-ctrl/src/static/chart.umd.min.js $(CTRLDIR)/static
	$(INSTALL_DATA) omdrc-ctrl/SPECTRUM_ANALYZER.md $(DOCSDIR)
	$(INSTALL_DATA) omdrc-ctrl/README.md $(DOCSDIR)/OMDRC-CTRL.md
	sed -e 's,@OMDRC_REPO_DIR@/drc-status.sh,$(PREFIX)/bin/omdrc-status,g' \
	    -e 's,@OMDRC_REPO_DIR@/drc.sh,$(PREFIX)/bin/omdrc,g' \
	    -e 's,@OMDRC_CONFIG_SITE_ROOT@,$(PREFIX)/etc/open-media-drc,g' \
	    -e 's,@OMDRC_CONFIG_DESIGN_ROOT@,@AUDIO_HOME@/.local/share/omdrc/site-data,g' \
	    -e 's,@OMDRC_FILTER_TOOLS_ROOT@,$(PREFIX)/libexec/omdrc/filter-tools,g' \
	    -e 's,@OMDRC_CONFIG_HELPER@,$(PREFIX)/libexec/omdrc/omdrc-config-helper,g' \
	    -e 's,@OMDRC_CONFIG_STATE_ROOT@,/var/db/omdrc/configuration,g' \
	    -e 's,@AUDIO_HOME@,/var/db/omdrc,g' \
	    omdrc-ctrl/src/commands.conf.in > $(ETCDIR)/commands.conf.sample
	printf '#!/bin/sh\nexec %s %s/share/omdrc-ctrl/app.py --config %s/etc/open-media-drc/commands.conf "$$@"\n' \
	    "$(PYTHON_CMD)" "$(PREFIX)" "$(PREFIX)" > $(BINDIR)/omdrcctrl
	chmod 755 $(BINDIR)/omdrcctrl

.PHONY: all install install-ctrl
