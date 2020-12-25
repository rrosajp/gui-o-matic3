#!/bin/sh
#
# SPDX-FileCopyrightText: © 2016-2018 Mailpile ehf. <team@mailpile.is>
# SPDX-FileCopyrightText: © 2016-2018 Bjarni Rúnar Einarsson <bre@godthaab.is>
# SPDX-FileCopyrightText: 🄯 2020 Peter J. Mello <admin@petermello.net>
#
# SPDX-License-Identifier: LGPL-3.0-only
#
# This script updates the version numberss in setup.py and
# gui_o_matic/__init__.py based on the length of the git commit log.

MAIN_VERSION="0.3"
VERSION="${MAIN_VERSION}.$((1 + $(git log --pretty=oneline | wc -l)))"

perl -i -npe "s/^VERSION =.*/VERSION = '${VERSION}'/m" setup.py

perl -i -npe "s/^__version__ =.*/__version__ = '${VERSION}'/m" \
  gui_o_matic/__init__.py

git add setup.py gui_o_matic/__init__.py
git commit -m "This is version ${VERSION}"
git tag -f "${VERSION}"
