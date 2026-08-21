PORTNAME=	open-media-drc
DISTVERSIONPREFIX=	v
DISTVERSION=	0.92.0
CATEGORIES=	audio

MAINTAINER=	delleceste@gmail.com
COMMENT=	BruteFIR-based digital room correction (DRC) media-box stack
WWW=		https://github.com/delleceste/open-media-drc

LICENSE=	BSD2CLAUSE
LICENSE_FILE=	${WRKSRC}/LICENSE

# NOTE (draft): the stack is developed against the BruteFIR fork
# github.com/delleceste/brutefir (OSS input fixes).  Before submission the
# fork's delta must either land in audio/brutefir as files/ patches or become
# its own port — see doc/FREEBSD-PORT-PLAN.md Phase 0 in the upstream repo.
RUN_DEPENDS=	bash:shells/bash \
		brutefir:audio/brutefir \
		virtual_oss:audio/virtual_oss \
		musicpd:audio/musicpd \
		mpc:audio/mpc

USES=		shebangfix
SHEBANG_FILES=	drc.sh drc-status.sh scripts/*.sh scripts/*.py

USE_GITHUB=	yes
GH_ACCOUNT=	delleceste

NO_BUILD=	yes
NO_ARCH=	yes

USE_RC_SUBR=	brutefir_drc drc_usb_audio omdrc_sndlink
SUB_FILES=	pkg-message

OPTIONS_DEFINE=		CTRL DOCS
OPTIONS_DEFAULT=	CTRL
OPTIONS_SUB=		yes

CTRL_DESC=	Web control panel (omdrc-ctrl, Flask)

# The web panel is a pure-Python layer on top of the shell core: it only adds
# run dependencies, an rc script and its own share/ + sample config.
CTRL_USES=		python:run
CTRL_RUN_DEPENDS=	${PYTHON_PKGNAMEPREFIX}flask>0:www/py-flask@${PY_FLAVOR} \
			${PYTHON_PKGNAMEPREFIX}Markdown>0:textproc/py-Markdown@${PY_FLAVOR} \
			${PYTHON_PKGNAMEPREFIX}numpy>0:math/py-numpy@${PY_FLAVOR}
# app.py is installed non-executable and started by the bin/omdrcctrl wrapper
# (python3 app.py), so its shebang is never used; normalise it anyway rather
# than ship a /usr/bin/env line.
CTRL_VARS=		USE_RC_SUBR+=omdrcctrl \
			SHEBANG_FILES+=omdrc-ctrl/src/app.py

# The UPnP/OpenHome front-end (upmpdcli) and the video webremote remain
# deferred until the core port has been poudriere-tested.

do-install:
	@cd ${WRKSRC} && ${SETENV} ${MAKE_CMD} install \
		DESTDIR=${STAGEDIR} PREFIX=${PREFIX}

# Separate target so the core install stays free of any Python dependency.
# It installs commands.conf as a .sample (pkg's @sample owns the live copy)
# and bakes no build-host path into it.
post-install-CTRL-on:
	@cd ${WRKSRC} && ${SETENV} ${MAKE_CMD} install-ctrl \
		DESTDIR=${STAGEDIR} PREFIX=${PREFIX} PYTHON_CMD=${PYTHON_CMD}

.include <bsd.port.mk>
