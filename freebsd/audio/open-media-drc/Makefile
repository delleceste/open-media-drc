PORTNAME=	open-media-drc
DISTVERSIONPREFIX=	v
DISTVERSION=	0.90.0
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

USE_RC_SUBR=	brutefir_drc drc_usb_audio
SUB_FILES=	pkg-message

# The UPnP/OpenHome front-end (upmpdcli) and the web control panel
# (omdrc-ctrl, flask) are optional layers; wire them up as OPTIONS
# (UPNP: net/upmpdcli; CTRL: py-flask py-Markdown py-numpy + the omdrc-ctrl
# CMake install) once the core port has been poudriere-tested.

do-install:
	@cd ${WRKSRC} && ${SETENV} ${MAKE_CMD} install \
		DESTDIR=${STAGEDIR} PREFIX=${PREFIX}

.include <bsd.port.mk>
